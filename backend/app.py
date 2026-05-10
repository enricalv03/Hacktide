from datetime import date
from pathlib import Path
import sqlite3
from statistics import mean, pstdev
from typing import Optional
import json
import sys
import threading

# Permitir `import forecast_por_tipo` aunque uvicorn se lance desde la raíz del repo
# (p. ej. `uvicorn backend.app:app`) en lugar de solo desde backend/.
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "interhack.db"


def _resolve_model_vs_rules_path() -> Path:
    """Tras la reorg de db/ (raw/ reports/ models/), el JSON vive en db/models/.
    Mantenemos fallback a la ubicación legacy db/ por compatibilidad."""
    candidates = (
        ROOT / "db" / "models" / "model_vs_rules.json",
        ROOT / "db" / "model_vs_rules.json",
    )
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


MODEL_VS_RULES_PATH = _resolve_model_vs_rules_path()

app = FastAPI(title="INTERHACK API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # No combinar allow_origins=["*"] con allow_credentials=True: el navegador bloquea
    # las respuestas en peticiones cross-origin (Vite :5173 → API :8000) y fetch falla
    # aunque /health funcione al abrirlo directamente en la pestaña.
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OutcomePayload(BaseModel):
    estado: str = Field(pattern="^(contactado|sin_respuesta|recuperado|perdido|pendiente)$")
    notas: Optional[str] = None
    usuario_operador: Optional[str] = None
    es_demo: int = Field(default=0, ge=0, le=1)
    fecha_contacto: Optional[date] = None


class ProductEscalationPayload(BaseModel):
    cliente_id: str
    producto_id: str
    prioridad: str = Field(default="ALTA", pattern="^(ALTA|MEDIA|BAJA)$")
    motivo: Optional[str] = None


_indexes_lock = threading.Lock()
_indexes_done = False

# Una conexión SQLite por hilo del pool de Starlette/Uvicorn (evita abrir/cerrar en cada petición).
_sqlite_tls = threading.local()

_ml_tables_cache: dict[str, bool] = {}


def _has_ml_tables(conn: sqlite3.Connection) -> bool:
    """Las tablas ml_* las crea `db/load_model_outputs.py` y son opcionales.

    Si no existen, los endpoints siguen respondiendo (sin segmento ni forecast)
    para no romper la app cuando el pipeline ML aún no se ha lanzado.
    """
    cached = _ml_tables_cache.get("ok")
    if cached is True:
        return True
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN ('ml_client_segment', 'ml_client_product_risk', 'ml_client_forecast')
        """
    ).fetchone()
    ok = (row["n"] if row else 0) == 3
    _ml_tables_cache["ok"] = ok
    return ok


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -128000")
    conn.execute("PRAGMA temp_store = MEMORY")
    try:
        conn.execute("PRAGMA mmap_size = 268435456")
    except sqlite3.OperationalError:
        pass


def _ensure_perf_indexes(conn: sqlite3.Connection) -> None:
    global _indexes_done
    if _indexes_done:
        return
    with _indexes_lock:
        if _indexes_done:
            return
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_alerta_cliente_id
                ON alerta_cliente_producto (cliente_id);
            CREATE INDEX IF NOT EXISTS idx_seguimiento_fecha_demo
                ON seguimiento_alerta (fecha_contacto) WHERE es_demo = 0;
            """
        )
        conn.commit()
        _indexes_done = True


# ── Cartera materializada (snapshot) ──────────────────────────────────────────
# `vw_cliente_semaforo_riesgo` y `vw_alerta_caida_frecuencia` hacen self-joins
# sobre 149k filas mensuales: 2-3s POR petición, lo cual hace que pestañas como
# Usuarios «se queden cargando» todo el rato. Las dependencias solo cambian al
# rebuild de DB / run_alerts (semanal), así que materializamos la cartera en una
# tabla plana con los campos justos para `/clients` y la refrescamos cuando
# cambia el mtime del fichero SQLite.
_cartera_lock = threading.Lock()
_cartera_mtime: float | None = None
_cartera_lock_global = threading.Lock()  # sentinela cross-thread (se serializan refresh)


def _db_mtime() -> float:
    try:
        return DB_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _ensure_cartera_snapshot(conn: sqlite3.Connection) -> None:
    """Crea/refresca `mv_cartera_clientes`. Idempotente y barato si está al día."""
    global _cartera_mtime
    current_mtime = _db_mtime()

    # Fast path: si ya construida en esta conexión y la DB no ha cambiado, no hacer nada.
    if _cartera_mtime is not None and abs(current_mtime - _cartera_mtime) < 1e-6:
        # Comprueba que la tabla siga existiendo (rebuild manual sin tocar mtime, raro).
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv_cartera_clientes'"
        ).fetchone()
        if row:
            return

    with _cartera_lock_global:
        if _cartera_mtime is not None and abs(current_mtime - _cartera_mtime) < 1e-6:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv_cartera_clientes'"
            ).fetchone()
            if row:
                return

        has_ml = _has_ml_tables(conn)
        ml_cols = (
            "ml.segment AS segmento_ml,"
            "ml.predicho_6m,"
            "ml.variacio_6m_pct,"
            "ml.n_prod_critical,"
            "ml.n_prod_warning"
            if has_ml
            else "NULL AS segmento_ml,"
            "NULL AS predicho_6m,"
            "NULL AS variacio_6m_pct,"
            "NULL AS n_prod_critical,"
            "NULL AS n_prod_warning"
        )
        ml_join = (
            "LEFT JOIN ml_client_segment ml ON ml.cliente_id = c.cliente_id"
            if has_ml
            else ""
        )

        conn.executescript(
            f"""
            DROP TABLE IF EXISTS mv_cartera_clientes;
            CREATE TABLE mv_cartera_clientes AS
            WITH alerta_por_cliente AS (
                SELECT
                    cliente_id,
                    MAX(CASE prioridad WHEN 'ALTA' THEN 3 WHEN 'MEDIA' THEN 2 ELSE 1 END) AS prioridad_num
                FROM vw_alertas_operativas_final
                GROUP BY cliente_id
            )
            SELECT
                c.cliente_id,
                COALESCE(c.provincia_nombre, 'Sin provincia') AS provincia_nombre,
                ce.estado_cliente,
                ce.semaforo_riesgo,
                ce.compras_total,
                ce.devoluciones_total,
                ce.fecha_ultima_compra,
                CASE COALESCE(ap.prioridad_num, 1)
                    WHEN 3 THEN 'ALTA'
                    WHEN 2 THEN 'MEDIA'
                    ELSE 'BAJA'
                END AS prioridad,
                CASE ce.semaforo_riesgo
                    WHEN 'ROJO' THEN 1
                    WHEN 'AMARILLO' THEN 2
                    WHEN 'VERDE' THEN 3
                    ELSE 4
                END AS semaforo_orden,
                {ml_cols}
            FROM dim_cliente c
            JOIN vw_cliente_semaforo_riesgo ce ON ce.cliente_id = c.cliente_id
            LEFT JOIN alerta_por_cliente ap ON ap.cliente_id = c.cliente_id
            {ml_join};

            CREATE INDEX IF NOT EXISTS idx_mv_cartera_estado
                ON mv_cartera_clientes (estado_cliente);
            CREATE INDEX IF NOT EXISTS idx_mv_cartera_prioridad
                ON mv_cartera_clientes (prioridad);
            CREATE INDEX IF NOT EXISTS idx_mv_cartera_semaforo
                ON mv_cartera_clientes (semaforo_riesgo);
            CREATE INDEX IF NOT EXISTS idx_mv_cartera_id
                ON mv_cartera_clientes (cliente_id);
            """
        )
        conn.commit()
        _cartera_mtime = current_mtime


def invalidate_cartera_snapshot() -> None:
    """Útil tras un POST que muta datos relevantes (p.ej. seguimiento de alerta)."""
    global _cartera_mtime
    _cartera_mtime = None


def get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Database not found at {DB_PATH}")
    conn = getattr(_sqlite_tls, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            str(DB_PATH),
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        _configure_sqlite(conn)
        _ensure_perf_indexes(conn)
        _sqlite_tls.conn = conn
    return conn


@app.on_event("startup")
def _warm_cartera_snapshot() -> None:
    """Construye `mv_cartera_clientes` en background al arrancar el server.

    Evita que la primera petición a `/clients` desde el navegador pague los
    ~3s de las views agregadas. Si falla (DB no migrada, etc.), se silencia y
    el primer request tomará el camino lento como antes.
    """

    def _worker():
        try:
            if not DB_PATH.exists():
                return
            conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                _configure_sqlite(conn)
                _ensure_cartera_snapshot(conn)
            finally:
                conn.close()
        except Exception:
            # No queremos bloquear el arranque por un fallo de warmup.
            pass

    threading.Thread(target=_worker, name="cartera-warmup", daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True}


_CLIENTS_SORT_SQL = {
    "prioridad_desc": """
        CASE prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
        cliente_id
    """,
    "prioridad_asc": """
        CASE prioridad WHEN 'ALTA' THEN 3 WHEN 'MEDIA' THEN 2 ELSE 1 END,
        cliente_id
    """,
    "id_asc": "cliente_id ASC",
    "semaforo": "semaforo_orden, cliente_id",
}


@app.get("/clients")
def list_clients(
    search: str = "",
    estado: str = Query(default=""),
    semaforo: str = Query(default=""),
    prioridad: str = Query(default=""),
    sort: str = Query(default="prioridad_desc"),
    limit: int = Query(default=100, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
):
    conn = get_conn()
    # Snapshot plano (indexado) construido la primera vez tras cada rebuild de DB.
    # Saca el coste de las views (2-3s) del path caliente del endpoint.
    _ensure_cartera_snapshot(conn)

    where = []
    params: list = []
    if search.strip():
        where.append("(cliente_id LIKE ? OR provincia_nombre LIKE ?)")
        token = f"%{search.strip()}%"
        params.extend([token, token])
    if estado.strip():
        where.append("estado_cliente = ?")
        params.append(estado.strip())
    if semaforo.strip():
        where.append("semaforo_riesgo = ?")
        params.append(semaforo.strip().upper())
    if prioridad.strip():
        where.append("prioridad = ?")
        params.append(prioridad.strip().upper())

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = _CLIENTS_SORT_SQL.get(sort, _CLIENTS_SORT_SQL["prioridad_desc"])

    total_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM mv_cartera_clientes {where_sql}", params
    ).fetchone()
    total = int(total_row["n"] or 0) if total_row else 0
    if total == 0:
        return {"total": 0, "items": []}

    rows = conn.execute(
        f"""
        SELECT
            cliente_id, provincia_nombre, estado_cliente, semaforo_riesgo,
            compras_total, devoluciones_total, fecha_ultima_compra, prioridad,
            segmento_ml, predicho_6m, variacio_6m_pct,
            n_prod_critical, n_prod_warning
        FROM mv_cartera_clientes
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    return {"total": total, "items": [dict(r) for r in rows]}

@app.get("/clients/options")
def client_options(limit: int = Query(default=10000, ge=1, le=20000)):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT cliente_id
        FROM vw_clientes_estado
        WHERE estado_cliente = 'CLIENTE'
        ORDER BY cliente_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r["cliente_id"] for r in rows]

def _clients_pulse_sql(conn: sqlite3.Connection, limit: int) -> list:
    """Pulse desde vistas analíticas (fact_venta_linea debe estar alimentado desde Master)."""
    rows = conn.execute(
        """
        WITH cliente_producto AS (
            SELECT
                cpm.cliente_id,
                dp.categoria_producto,
                SUM(cpm.unidades_compra) AS compra_total,
                ROW_NUMBER() OVER (
                    PARTITION BY cpm.cliente_id
                    ORDER BY SUM(cpm.unidades_compra) DESC
                ) AS rn
            FROM vw_cliente_producto_mensual cpm
            LEFT JOIN dim_producto dp ON dp.producto_id = cpm.producto_id
            GROUP BY cpm.cliente_id, dp.categoria_producto
        )
        SELECT
            c.cliente_id,
            COALESCE(d.provincia_nombre, 'Sin provincia') AS provincia_nombre,
            c.estado_cliente,
            c.semaforo_riesgo,
            c.dias_desde_ultima_compra,
            c.recomendacion_estado,
            c.compras_total,
            c.fecha_ultima_compra,
            COALESCE(p.perfil_cliente, 'SIN_CLASIFICAR') AS perfil_cliente,
            COALESCE(cp.categoria_producto, 'SIN_CATEGORIA') AS categoria_top,
            CASE
                WHEN UPPER(COALESCE(cp.categoria_producto, '')) LIKE '%TEC%' THEN 'TECNICO'
                ELSE 'DIARIO'
            END AS tipo_producto
        FROM vw_cliente_semaforo_riesgo c
        LEFT JOIN dim_cliente d ON d.cliente_id = c.cliente_id
        LEFT JOIN vw_perfil_cliente_recurrencia p ON p.cliente_id = c.cliente_id
        LEFT JOIN cliente_producto cp ON cp.cliente_id = c.cliente_id AND cp.rn = 1
        ORDER BY
            CASE c.semaforo_riesgo WHEN 'ROJO' THEN 1 WHEN 'AMARILLO' THEN 2 WHEN 'VERDE' THEN 3 ELSE 4 END,
            COALESCE(c.dias_desde_ultima_compra, 9999) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/clients/pulse")
def clients_pulse(limit: int = Query(default=3000, ge=1, le=10000)):
    """Radar operativo: mismas vistas que el resto del panel (ventas cargadas vía Master → SQLite)."""
    conn = get_conn()
    return _clients_pulse_sql(conn, limit)

@app.get("/dashboard/pulse-stats")
def pulse_stats():
    conn = get_conn()
    total = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vw_cliente_semaforo_riesgo
        WHERE estado_cliente = 'CLIENTE'
        """
    ).fetchone()["n"]
    alerts = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vw_alertas_operativas_final
        """
    ).fetchone()["n"]
    ref_row = conn.execute("SELECT MAX(fecha) AS d FROM fact_venta_linea").fetchone()
    ref = ref_row["d"] if ref_row else None
    with_sales = conn.execute(
        """
        SELECT COUNT(DISTINCT cliente_id) AS n
        FROM fact_venta_linea
        WHERE tipo_movimiento = 'COMPRA'
        """
    ).fetchone()["n"]
    out: dict = {
        "clientes_total": int(total or 0),
        "alertas_activas": int(alerts or 0),
        "fecha_referencia_master": str(ref) if ref else None,
        "clientes_en_master": int(with_sales or 0),
    }
    return out


@app.get("/dashboard/segments-summary")
def segments_summary():
    """Reparto LEAL/PROMETEDOR/RISC + spend 12m y forecast 6m agregados.

    Si las tablas ml_* no están cargadas (pipeline no se ha ejecutado), devuelve
    un payload vacío para que el front simplemente oculte la sección.
    """
    conn = get_conn()
    if not _has_ml_tables(conn):
        return {
            "available": False,
            "fecha_pipeline": None,
            "total_clientes": 0,
            "segments": [],
            "totales": {
                "spend_12m": 0.0,
                "predicho_6m": 0.0,
                "n_prod_critical": 0,
                "n_prod_warning": 0,
            },
        }

    rows = conn.execute(
        """
        SELECT
            segment,
            COUNT(*)                                AS n,
            COALESCE(SUM(spend_12m), 0)             AS spend_12m,
            COALESCE(SUM(predicho_6m), 0)           AS predicho_6m,
            COALESCE(SUM(n_prod_critical), 0)       AS n_prod_critical,
            COALESCE(SUM(n_prod_warning), 0)        AS n_prod_warning
        FROM ml_client_segment
        GROUP BY segment
        """
    ).fetchall()

    seg_order = {"RISC": 0, "PROMETEDOR": 1, "LEAL": 2}
    segments = sorted(
        [
            {
                "segment": r["segment"],
                "n": int(r["n"] or 0),
                "spend_12m": float(r["spend_12m"] or 0),
                "predicho_6m": float(r["predicho_6m"] or 0),
                "n_prod_critical": int(r["n_prod_critical"] or 0),
                "n_prod_warning": int(r["n_prod_warning"] or 0),
            }
            for r in rows
        ],
        key=lambda x: seg_order.get(x["segment"], 99),
    )

    total_clientes = sum(s["n"] for s in segments)
    totales = {
        "spend_12m": sum(s["spend_12m"] for s in segments),
        "predicho_6m": sum(s["predicho_6m"] for s in segments),
        "n_prod_critical": sum(s["n_prod_critical"] for s in segments),
        "n_prod_warning": sum(s["n_prod_warning"] for s in segments),
    }

    fecha_row = conn.execute(
        "SELECT MAX(fecha_pipeline) AS d FROM ml_client_segment"
    ).fetchone()
    fecha_pipeline = fecha_row["d"] if fecha_row else None

    return {
        "available": True,
        "fecha_pipeline": fecha_pipeline,
        "total_clientes": total_clientes,
        "segments": segments,
        "totales": totales,
    }


@app.get("/dashboard/pulse-board")
def pulse_board(top_n: int = Query(default=12, ge=4, le=40)):
    """Datos agregados para la pantalla Pulse (rediseñada).

    Devuelve KPIs ejecutivos + matriz Segmento×Semáforo + lista de clientes
    más urgentes (riesgo crítico) y candidatos de recuperación (medio riesgo).
    Una sola llamada para que la UI pinte todo de golpe sin coordinar 5 fetches.
    """
    conn = get_conn()
    _ensure_cartera_snapshot(conn)
    has_ml = _has_ml_tables(conn)

    # KPIs principales (cartera, alertas, último mes con datos)
    kpi_row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM mv_cartera_clientes WHERE estado_cliente = 'CLIENTE')
                AS clientes_activos,
            (SELECT COUNT(*) FROM mv_cartera_clientes
                WHERE estado_cliente = 'CLIENTE' AND semaforo_riesgo = 'ROJO')
                AS clientes_rojo,
            (SELECT COUNT(*) FROM vw_alertas_operativas_final) AS alertas_total,
            (SELECT COUNT(*) FROM vw_alertas_operativas_final WHERE prioridad = 'ALTA')
                AS alertas_alta,
            (SELECT MAX(fecha) FROM fact_venta_linea) AS fecha_referencia
        """
    ).fetchone()

    kpis = {
        "clientes_activos": int(kpi_row["clientes_activos"] or 0),
        "clientes_rojo": int(kpi_row["clientes_rojo"] or 0),
        "alertas_total": int(kpi_row["alertas_total"] or 0),
        "alertas_alta": int(kpi_row["alertas_alta"] or 0),
        "fecha_referencia": str(kpi_row["fecha_referencia"]) if kpi_row["fecha_referencia"] else None,
        "spend_12m": 0.0,
        "predicho_6m": 0.0,
    }

    if has_ml:
        ml_kpi = conn.execute(
            """
            SELECT
                COALESCE(SUM(spend_12m), 0)   AS spend_12m,
                COALESCE(SUM(predicho_6m), 0) AS predicho_6m
            FROM ml_client_segment
            """
        ).fetchone()
        kpis["spend_12m"] = float(ml_kpi["spend_12m"] or 0)
        kpis["predicho_6m"] = float(ml_kpi["predicho_6m"] or 0)

    # Matriz Segmento (LEAL/PROMETEDOR/RISC) × Semáforo (ROJO/AMARILLO/VERDE).
    # Si no hay tablas ML, devolvemos solo distribución de semáforo.
    matrix_rows = []
    semaforo_dist = {"ROJO": 0, "AMARILLO": 0, "VERDE": 0}
    sem_q = conn.execute(
        """
        SELECT semaforo_riesgo, COUNT(*) AS n
        FROM mv_cartera_clientes
        WHERE estado_cliente = 'CLIENTE'
        GROUP BY semaforo_riesgo
        """
    ).fetchall()
    for r in sem_q:
        s = (r["semaforo_riesgo"] or "OTRO").upper()
        if s in semaforo_dist:
            semaforo_dist[s] = int(r["n"] or 0)

    if has_ml:
        cells = conn.execute(
            """
            SELECT
                COALESCE(c.segmento_ml, 'SIN_SEGMENTO') AS segment,
                c.semaforo_riesgo,
                COUNT(*) AS n
            FROM mv_cartera_clientes c
            WHERE c.estado_cliente = 'CLIENTE'
            GROUP BY 1, 2
            """
        ).fetchall()
        for r in cells:
            matrix_rows.append(
                {
                    "segment": r["segment"],
                    "semaforo": r["semaforo_riesgo"] or "OTRO",
                    "n": int(r["n"] or 0),
                }
            )

    # `dias_desde_ultima_compra` lo calculamos inline contra la fecha máxima de
    # ventas (mismo criterio que `vw_cliente_semaforo_riesgo`). Evitamos hacer
    # JOIN con esa vista (149k filas → 5s) y nos quedamos con el snapshot rápido.
    as_of_row = conn.execute("SELECT MAX(fecha) AS d FROM fact_venta_linea").fetchone()
    as_of = as_of_row["d"] if as_of_row and as_of_row["d"] else None
    days_expr = (
        "CAST(julianday(?) - julianday(c.fecha_ultima_compra) AS INTEGER)"
        if as_of else "NULL"
    )
    days_param = (as_of,) if as_of else tuple()

    if has_ml:
        top_critical = conn.execute(
            f"""
            SELECT
                c.cliente_id, c.provincia_nombre, c.semaforo_riesgo,
                c.prioridad,
                c.segmento_ml, c.predicho_6m, c.variacio_6m_pct,
                c.n_prod_critical, c.n_prod_warning,
                {days_expr} AS dias_desde_ultima_compra,
                c.fecha_ultima_compra
            FROM mv_cartera_clientes c
            WHERE c.estado_cliente = 'CLIENTE'
              AND (
                  c.segmento_ml = 'RISC'
                  OR c.semaforo_riesgo = 'ROJO'
              )
            ORDER BY
                CASE COALESCE(c.segmento_ml, 'X') WHEN 'RISC' THEN 0 WHEN 'PROMETEDOR' THEN 1 WHEN 'LEAL' THEN 2 ELSE 3 END,
                CASE c.semaforo_riesgo WHEN 'ROJO' THEN 0 WHEN 'AMARILLO' THEN 1 WHEN 'VERDE' THEN 2 ELSE 3 END,
                COALESCE(c.n_prod_critical, 0) DESC,
                COALESCE({days_expr}, 0) DESC
            LIMIT ?
            """,
            (*days_param, *days_param, top_n),
        ).fetchall()

        recovery_candidates = conn.execute(
            f"""
            SELECT
                c.cliente_id, c.provincia_nombre, c.semaforo_riesgo,
                NULL AS prioridad,
                c.segmento_ml, c.predicho_6m, c.variacio_6m_pct,
                c.n_prod_critical, c.n_prod_warning,
                {days_expr} AS dias_desde_ultima_compra,
                c.fecha_ultima_compra
            FROM mv_cartera_clientes c
            WHERE c.estado_cliente = 'CLIENTE'
              AND c.segmento_ml = 'PROMETEDOR'
              AND c.semaforo_riesgo IN ('AMARILLO', 'ROJO')
            ORDER BY
                COALESCE(c.predicho_6m, 0) DESC,
                COALESCE({days_expr}, 0) DESC
            LIMIT ?
            """,
            (*days_param, *days_param, top_n),
        ).fetchall()
    else:
        top_critical = conn.execute(
            f"""
            SELECT
                c.cliente_id, c.provincia_nombre, c.semaforo_riesgo,
                c.prioridad,
                NULL AS segmento_ml, NULL AS predicho_6m, NULL AS variacio_6m_pct,
                NULL AS n_prod_critical, NULL AS n_prod_warning,
                {days_expr} AS dias_desde_ultima_compra,
                c.fecha_ultima_compra
            FROM mv_cartera_clientes c
            WHERE c.estado_cliente = 'CLIENTE'
              AND c.prioridad = 'ALTA'
            ORDER BY
                CASE c.semaforo_riesgo WHEN 'ROJO' THEN 0 WHEN 'AMARILLO' THEN 1 ELSE 2 END,
                COALESCE({days_expr}, 0) DESC
            LIMIT ?
            """,
            (*days_param, *days_param, top_n),
        ).fetchall()
        recovery_candidates = []

    def serialize_client_row(r):
        return {
            "cliente_id": r["cliente_id"],
            "provincia_nombre": r["provincia_nombre"],
            "semaforo_riesgo": r["semaforo_riesgo"],
            "prioridad": r["prioridad"] if "prioridad" in r.keys() else None,
            "segmento_ml": r["segmento_ml"],
            "predicho_6m": float(r["predicho_6m"]) if r["predicho_6m"] is not None else None,
            "variacio_6m_pct": float(r["variacio_6m_pct"]) if r["variacio_6m_pct"] is not None else None,
            "n_prod_critical": int(r["n_prod_critical"] or 0) if r["n_prod_critical"] is not None else None,
            "n_prod_warning": int(r["n_prod_warning"] or 0) if r["n_prod_warning"] is not None else None,
            "dias_desde_ultima_compra": int(r["dias_desde_ultima_compra"] or 0) if r["dias_desde_ultima_compra"] is not None else None,
            "fecha_ultima_compra": r["fecha_ultima_compra"],
        }

    return {
        "kpis": kpis,
        "semaforo_distribution": semaforo_dist,
        "matrix": matrix_rows,
        "top_critical": [serialize_client_row(r) for r in top_critical],
        "recovery_candidates": [serialize_client_row(r) for r in recovery_candidates],
        "ml_available": has_ml,
    }


@app.get("/dashboard/pulse-board/cell-clients")
def pulse_board_cell_clients(
    segment: str = Query(..., description="LEAL, PROMETEDOR, RISC o SIN_SEGMENTO"),
    semaforo: str = Query(..., description="ROJO, AMARILLO o VERDE"),
    limit: int = Query(default=30, ge=1, le=200),
):
    """Lista paginada (top N) de clientes en una celda concreta de la matriz
    Segmento × Semáforo del Resumen ejecutivo.

    Resuelve el bug donde una celda mostraba `2.5%` pero al pulsarla aparecían
    `0 clientes`: las listas «Llamar primero» y «Recuperación» solo cubren
    casos accionables, mientras que la celda incluye toda la cartera.
    """
    conn = get_conn()
    _ensure_cartera_snapshot(conn)
    has_ml = _has_ml_tables(conn)

    seg_norm = (segment or "").upper().strip()
    sem_norm = (semaforo or "").upper().strip()

    # Para clientes sanos (VERDE) ordenamos por spend para que se vea valor;
    # para riesgo (ROJO) ordenamos por antigüedad sin compra; AMARILLO por
    # caída de productos. El usuario debería ver las llamadas más útiles
    # arriba en cada caso.
    as_of_row = conn.execute("SELECT MAX(fecha) AS d FROM fact_venta_linea").fetchone()
    as_of = as_of_row["d"] if as_of_row and as_of_row["d"] else None
    days_expr = (
        "CAST(julianday(?) - julianday(c.fecha_ultima_compra) AS INTEGER)"
        if as_of else "NULL"
    )
    days_param = (as_of,) if as_of else tuple()

    if sem_norm == "VERDE":
        order_by = "COALESCE(c.predicho_6m, 0) DESC"
        extra_param = tuple()
    elif sem_norm == "AMARILLO":
        order_by = "COALESCE(c.n_prod_warning, 0) DESC, COALESCE(c.predicho_6m, 0) DESC"
        extra_param = tuple()
    else:  # ROJO o cualquier otro
        order_by = (
            f"COALESCE(c.n_prod_critical, 0) DESC, COALESCE({days_expr}, 0) DESC"
        )
        extra_param = days_param

    if has_ml:
        if seg_norm == "SIN_SEGMENTO":
            seg_cond = "(c.segmento_ml IS NULL OR c.segmento_ml = '')"
            seg_param = tuple()
        else:
            seg_cond = "c.segmento_ml = ?"
            seg_param = (seg_norm,)
    else:
        # Sin ML: ignoramos el filtro de segmento (devuelve mismo conjunto que
        # filtrar por semáforo).
        seg_cond = "1 = 1"
        seg_param = tuple()

    sql = f"""
        SELECT
            c.cliente_id, c.provincia_nombre, c.semaforo_riesgo,
            c.prioridad,
            c.segmento_ml, c.predicho_6m, c.variacio_6m_pct,
            c.n_prod_critical, c.n_prod_warning,
            {days_expr} AS dias_desde_ultima_compra,
            c.fecha_ultima_compra,
            c.compras_total
        FROM mv_cartera_clientes c
        WHERE c.estado_cliente = 'CLIENTE'
          AND c.semaforo_riesgo = ?
          AND {seg_cond}
        ORDER BY {order_by}
        LIMIT ?
    """
    params = (*days_param, sem_norm, *seg_param, *extra_param, limit)
    rows = conn.execute(sql, params).fetchall()

    # Contador total para que la UI pueda decir «mostrando 30 de 5825».
    count_sql = f"""
        SELECT COUNT(*) AS n
        FROM mv_cartera_clientes c
        WHERE c.estado_cliente = 'CLIENTE'
          AND c.semaforo_riesgo = ?
          AND {seg_cond}
    """
    total_n = conn.execute(count_sql, (sem_norm, *seg_param)).fetchone()["n"]

    items = []
    for r in rows:
        items.append(
            {
                "cliente_id": r["cliente_id"],
                "provincia_nombre": r["provincia_nombre"],
                "semaforo_riesgo": r["semaforo_riesgo"],
                "prioridad": r["prioridad"],
                "segmento_ml": r["segmento_ml"],
                "predicho_6m": float(r["predicho_6m"]) if r["predicho_6m"] is not None else None,
                "variacio_6m_pct": float(r["variacio_6m_pct"]) if r["variacio_6m_pct"] is not None else None,
                "n_prod_critical": int(r["n_prod_critical"] or 0) if r["n_prod_critical"] is not None else None,
                "n_prod_warning": int(r["n_prod_warning"] or 0) if r["n_prod_warning"] is not None else None,
                "dias_desde_ultima_compra": int(r["dias_desde_ultima_compra"] or 0) if r["dias_desde_ultima_compra"] is not None else None,
                "fecha_ultima_compra": r["fecha_ultima_compra"],
                "compras_total": int(r["compras_total"] or 0),
            }
        )

    return {
        "segment": seg_norm,
        "semaforo": sem_norm,
        "total": int(total_n or 0),
        "shown": len(items),
        "items": items,
    }


def _synth_contact(cliente_id: str) -> dict:
    """Datos de contacto demo, deterministas a partir del `cliente_id`.

    El dataset original NO incluye teléfono/email del cliente. Para que la
    pestaña Alertas y Contacto funcione visualmente, sintetizamos un contacto
    estable y MARCAMOS `_demo: true` para que el front avise al usuario de que
    son datos placeholder. Reemplazar por integración con CRM cuando esté.
    """
    cid = str(cliente_id)
    digest = 0
    for ch in cid:
        digest = (digest * 131 + ord(ch)) & 0xFFFFFFFF
    # Móvil ES: 6XX XXX XXX (9 dígitos). Garantizamos 8 dígitos detrás del "6".
    movil_tail = str(digest % 100_000_000).zfill(8)
    movil_str = f"+34 6{movil_tail[:2]} {movil_tail[2:5]} {movil_tail[5:8]}"
    # Fijo ES: 9XX XXX XXX (9 dígitos)
    fijo_tail = str((digest // 7) % 100_000_000).zfill(8)
    fijo_str = f"+34 9{fijo_tail[:2]} {fijo_tail[2:5]} {fijo_tail[5:8]}"
    nombres = [
        "Carmen Ruiz", "Javier López", "María García", "Antonio Pérez",
        "Lucía Fernández", "David Martín", "Elena Sánchez", "Pablo Romero",
        "Marta Jiménez", "Ricardo Alonso", "Beatriz Navarro", "Sergio Iglesias",
    ]
    nombre = nombres[digest % len(nombres)]
    handle = nombre.lower().replace(" ", ".").translate(
        str.maketrans("áéíóúñ", "aeioun")
    )
    email = f"{handle}@cliente-{cid[-4:]}.es"
    return {
        "telefono_movil": movil_str,
        "telefono_fijo": fijo_str,
        "email": email,
        "contacto_principal": nombre,
        "_demo": True,
    }


@app.get("/clients/{cliente_id}/contact")
def client_contact(cliente_id: str):
    """Información de contacto + resumen comercial para la pestaña Alertas y Contacto.

    Combina datos reales (provincia, semáforo, perfil, productos en riesgo, ML)
    con un bloque de contacto sintético claramente marcado `_demo: true`.
    """
    conn = get_conn()
    base = conn.execute(
        """
        SELECT
            c.cliente_id,
            COALESCE(c.provincia_nombre, 'Sin provincia') AS provincia_nombre,
            COALESCE(c.provincia_codigo, '') AS provincia_codigo,
            s.estado_cliente, s.semaforo_riesgo, s.recomendacion_estado,
            s.dias_desde_ultima_compra, s.fecha_ultima_compra,
            s.compras_total, s.devoluciones_total,
            COALESCE(p.perfil_cliente, 'SIN_CLASIFICAR') AS perfil_cliente
        FROM dim_cliente c
        LEFT JOIN vw_cliente_semaforo_riesgo s ON s.cliente_id = c.cliente_id
        LEFT JOIN vw_perfil_cliente_recurrencia p ON p.cliente_id = c.cliente_id
        WHERE c.cliente_id = ?
        """,
        (cliente_id,),
    ).fetchone()
    if not base:
        raise HTTPException(status_code=404, detail="Client not found")

    ml = _ml_client_payload(conn, cliente_id)

    productos_top = ml.get("productos_riesgo", [])[:5]
    if ml.get("predicho_6m") is not None and ml.get("variacio_6m_pct") is not None:
        forecast_line = (
            f"Forecast 6m: {ml['predicho_6m']:.0f} € "
            f"({'+' if ml['variacio_6m_pct'] >= 0 else ''}{ml['variacio_6m_pct']:.1f}% vs 6m anteriores)."
        )
    else:
        forecast_line = ""

    motivo_principal = base["recomendacion_estado"] or "Cliente con seguimiento estándar"
    productos_str = ", ".join(p["producto_id"] for p in productos_top[:3]) if productos_top else ""
    guion = (
        f"Cliente {cliente_id} ({base['provincia_nombre']}). "
        f"Lleva {base['dias_desde_ultima_compra'] or 0} días sin compra "
        f"(última: {base['fecha_ultima_compra'] or 'N/D'}). "
        f"Segmento ML: {ml.get('segmento_ml') or 'sin segmento'}. "
        f"{motivo_principal}. "
        + (f"Producto(s) críticos: {productos_str}. " if productos_str else "")
        + forecast_line
    ).strip()

    return {
        "cliente_id": cliente_id,
        "provincia_nombre": base["provincia_nombre"],
        "provincia_codigo": base["provincia_codigo"],
        "perfil_cliente": base["perfil_cliente"],
        "estado_cliente": base["estado_cliente"],
        "semaforo_riesgo": base["semaforo_riesgo"],
        "dias_desde_ultima_compra": int(base["dias_desde_ultima_compra"] or 0)
            if base["dias_desde_ultima_compra"] is not None else None,
        "fecha_ultima_compra": base["fecha_ultima_compra"],
        "recomendacion_estado": base["recomendacion_estado"],
        "compras_total": int(base["compras_total"] or 0),
        "devoluciones_total": int(base["devoluciones_total"] or 0),
        "ml": {
            "segmento_ml": ml.get("segmento_ml"),
            "predicho_6m": ml.get("predicho_6m"),
            "variacio_6m_pct": ml.get("variacio_6m_pct"),
            "n_prod_critical": ml.get("n_prod_critical"),
            "n_prod_warning": ml.get("n_prod_warning"),
            "fecha_pipeline": ml.get("fecha_pipeline"),
        },
        "productos_riesgo_top": productos_top,
        "contacto": _synth_contact(cliente_id),
        "guion_sugerido": guion,
    }


@app.get("/dashboard/pending-clients")
def pending_clients(limit: int = Query(default=10, ge=1, le=100)):
    conn = get_conn()
    rows = conn.execute(
        """
        WITH ult AS (
            SELECT
                s.alerta_id,
                s.estado,
                ROW_NUMBER() OVER (
                    PARTITION BY s.alerta_id
                    ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC
                ) AS rn
            FROM seguimiento_alerta s
            WHERE s.es_demo = 0
        ),
        ranked AS (
            SELECT
                a.alerta_id,
                a.cliente_id,
                c.semaforo_riesgo,
                c.dias_desde_ultima_compra,
                c.recomendacion_estado,
                COALESCE(d.provincia_nombre, 'Sin provincia') AS provincia_nombre,
                a.prioridad,
                ROW_NUMBER() OVER (
                    PARTITION BY a.cliente_id
                    ORDER BY
                        CASE a.prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
                        COALESCE(c.dias_desde_ultima_compra, 0) DESC,
                        a.alerta_id DESC
                ) AS rn
            FROM vw_alertas_operativas_final a
            JOIN vw_cliente_semaforo_riesgo c ON c.cliente_id = a.cliente_id
            JOIN dim_cliente d ON d.cliente_id = c.cliente_id
            LEFT JOIN ult u ON u.alerta_id = a.alerta_id AND u.rn = 1
            WHERE c.estado_cliente = 'CLIENTE'
              AND c.semaforo_riesgo IN ('ROJO', 'AMARILLO')
              AND COALESCE(u.estado, 'pendiente') = 'pendiente'
        )
        SELECT
            alerta_id,
            cliente_id,
            semaforo_riesgo,
            dias_desde_ultima_compra,
            recomendacion_estado,
            provincia_nombre,
            prioridad
        FROM ranked
        WHERE rn = 1
        ORDER BY
            CASE prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
            CASE semaforo_riesgo WHEN 'ROJO' THEN 1 ELSE 2 END,
            COALESCE(dias_desde_ultima_compra, 0) DESC,
            cliente_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

@app.get("/dashboard/kpi-today")
def kpi_today():
    conn = get_conn()
    today = date.today().isoformat()
    today_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN estado <> 'pendiente' THEN 1 ELSE 0 END) AS gestionadas_hoy,
            SUM(CASE WHEN estado = 'recuperado' THEN 1 ELSE 0 END) AS recuperados_hoy,
            SUM(CASE WHEN estado = 'contactado' THEN 1 ELSE 0 END) AS contactados_hoy,
            SUM(CASE WHEN estado = 'sin_respuesta' THEN 1 ELSE 0 END) AS sin_respuesta_hoy,
            SUM(CASE WHEN estado = 'perdido' THEN 1 ELSE 0 END) AS perdidos_hoy
        FROM seguimiento_alerta
        WHERE es_demo = 0
          AND fecha_contacto = ?
        """,
        (today,),
    ).fetchone()
    yesterday_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN estado <> 'pendiente' THEN 1 ELSE 0 END) AS gestionadas_ayer,
            SUM(CASE WHEN estado = 'recuperado' THEN 1 ELSE 0 END) AS recuperados_ayer,
            SUM(CASE WHEN estado = 'contactado' THEN 1 ELSE 0 END) AS contactados_ayer,
            SUM(CASE WHEN estado = 'sin_respuesta' THEN 1 ELSE 0 END) AS sin_respuesta_ayer,
            SUM(CASE WHEN estado = 'perdido' THEN 1 ELSE 0 END) AS perdidos_ayer
        FROM seguimiento_alerta
        WHERE es_demo = 0
          AND fecha_contacto = date(?, '-1 day')
        """,
        (today,),
    ).fetchone()
    pendientes_hoy = conn.execute(
        """
        WITH ult AS (
            SELECT
                s.alerta_id,
                s.estado,
                ROW_NUMBER() OVER (
                    PARTITION BY s.alerta_id
                    ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC
                ) AS rn
            FROM seguimiento_alerta s
            WHERE s.es_demo = 0
        ),
        candidatos AS (
            SELECT a.alerta_id
            FROM vw_alertas_operativas_final a
            JOIN vw_cliente_semaforo_riesgo c ON c.cliente_id = a.cliente_id
            LEFT JOIN ult u ON u.alerta_id = a.alerta_id AND u.rn = 1
            WHERE c.estado_cliente = 'CLIENTE'
              AND c.semaforo_riesgo IN ('ROJO', 'AMARILLO')
              AND COALESCE(u.estado, 'pendiente') = 'pendiente'
        ),
        por_cliente AS (
            SELECT
                a.alerta_id,
                ROW_NUMBER() OVER (
                    PARTITION BY a.cliente_id
                    ORDER BY
                        CASE a.prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
                        a.alerta_id DESC
                ) AS rn
            FROM vw_alertas_operativas_final a
            INNER JOIN candidatos k ON k.alerta_id = a.alerta_id
        )
        SELECT COUNT(*) AS pendientes_hoy
        FROM por_cliente
        WHERE rn = 1
        """
    ).fetchone()["pendientes_hoy"]
    series_rows = conn.execute(
        """
        WITH RECURSIVE days(offset, day) AS (
            SELECT 6, date(?)
            UNION ALL
            SELECT offset - 1, date(day, '-1 day')
            FROM days
            WHERE offset > 0
        ),
        agg AS (
            SELECT
                fecha_contacto AS day,
                SUM(CASE WHEN estado <> 'pendiente' THEN 1 ELSE 0 END) AS gestionadas,
                SUM(CASE WHEN estado = 'recuperado' THEN 1 ELSE 0 END) AS recuperados
            FROM seguimiento_alerta
            WHERE es_demo = 0
              AND fecha_contacto BETWEEN date(?, '-6 day') AND date(?)
            GROUP BY fecha_contacto
        )
        SELECT
            d.day,
            COALESCE(a.gestionadas, 0) AS gestionadas,
            COALESCE(a.recuperados, 0) AS recuperados
        FROM days d
        LEFT JOIN agg a ON a.day = d.day
        ORDER BY d.day
        """,
        (today, today, today),
    ).fetchall()

    gestionadas_hoy = int(today_row["gestionadas_hoy"] or 0)
    recuperados_hoy = int(today_row["recuperados_hoy"] or 0)
    gestionadas_ayer = int(yesterday_row["gestionadas_ayer"] or 0)
    recuperados_ayer = int(yesterday_row["recuperados_ayer"] or 0)

    return {
        "fecha": today,
        "gestionadas_hoy": gestionadas_hoy,
        "recuperados_hoy": recuperados_hoy,
        "contactados_hoy": int(today_row["contactados_hoy"] or 0),
        "sin_respuesta_hoy": int(today_row["sin_respuesta_hoy"] or 0),
        "perdidos_hoy": int(today_row["perdidos_hoy"] or 0),
        "pendientes_totales": int(pendientes_hoy or 0),
        "gestionadas_ayer": gestionadas_ayer,
        "recuperados_ayer": recuperados_ayer,
        "delta_gestionadas": gestionadas_hoy - gestionadas_ayer,
        "delta_recuperados": recuperados_hoy - recuperados_ayer,
        "serie_7d": [
            {
                "fecha": r["day"],
                "gestionadas": int(r["gestionadas"] or 0),
                "recuperados": int(r["recuperados"] or 0),
            }
            for r in series_rows
        ],
    }

@app.get("/dashboard/model-vs-rules")
def model_vs_rules():
    if not MODEL_VS_RULES_PATH.exists():
        raise HTTPException(status_code=404, detail="model_vs_rules.json not found. Run weekly pipeline first.")
    return json.loads(MODEL_VS_RULES_PATH.read_text(encoding="utf-8"))


def build_product_analysis(conn: sqlite3.Connection, cliente_id: str, producto_id: str) -> dict:
    series_rows = conn.execute(
        """
        SELECT year_month, unidades_compra
        FROM vw_cliente_producto_mensual
        WHERE cliente_id = ? AND producto_id = ?
        ORDER BY year_month DESC
        LIMIT 12
        """,
        (cliente_id, producto_id),
    ).fetchall()
    series_rows = list(reversed(series_rows))
    monthly_product = [
        {"periodo": r["year_month"], "valor": float(r["unidades_compra"] or 0)}
        for r in series_rows
    ]
    values = [r["valor"] for r in monthly_product]
    avg_val = mean(values) if values else 0.0
    std_val = pstdev(values) if len(values) > 1 else 0.0
    low_limit = max(0.0, avg_val - std_val)
    high_limit = avg_val + std_val
    current = values[-1] if values else 0.0

    overall_rows = conn.execute(
        """
        SELECT year_month, SUM(unidades_compra) AS total_unidades
        FROM vw_cliente_producto_mensual
        WHERE cliente_id = ?
        GROUP BY year_month
        ORDER BY year_month DESC
        LIMIT 12
        """,
        (cliente_id,),
    ).fetchall()
    overall_rows = list(reversed(overall_rows))
    monthly_overall = [
        {"periodo": r["year_month"], "valor": float(r["total_unidades"] or 0)}
        for r in overall_rows
    ]

    next_purchase = conn.execute(
        """
        SELECT fecha_esperada_compra, dias_retraso
        FROM vw_proxima_compra_esperada
        WHERE cliente_id = ? AND producto_id = ?
        LIMIT 1
        """,
        (cliente_id, producto_id),
    ).fetchone()

    risk_state = "VERDE"
    if current < low_limit:
        risk_state = "ROJO"
    elif current < avg_val:
        risk_state = "AMARILLO"

    return {
        "cliente_id": cliente_id,
        "producto_id": producto_id,
        "monthly_product": monthly_product,
        "monthly_overall": monthly_overall,
        "limits": {
            "low": round(low_limit, 2),
            "high": round(high_limit, 2),
            "average": round(avg_val, 2),
            "current": round(current, 2),
        },
        "prediction": {
            "risk_state": risk_state,
            "should_worry": risk_state == "ROJO",
            "fecha_esperada_compra": next_purchase["fecha_esperada_compra"] if next_purchase else None,
            "dias_retraso": int(next_purchase["dias_retraso"]) if next_purchase else None,
        },
    }


def _ml_client_payload(conn: sqlite3.Connection, cliente_id: str) -> dict:
    """Devuelve segmento ML + productos en riesgo (top) + forecast 6m si las tablas
    ml_* están cargadas; en caso contrario un payload vacío seguro para el front."""
    payload: dict = {
        "segmento_ml": None,
        "predicho_6m": None,
        "variacio_6m_pct": None,
        "n_prod_critical": None,
        "n_prod_warning": None,
        "n_prod_risc_total": None,
        "rao_segment": None,
        "alertes_segment": None,
        "fecha_pipeline": None,
        "productos_riesgo": [],
        "forecast_6m": [],
    }
    if not _has_ml_tables(conn):
        return payload

    seg = conn.execute(
        """
        SELECT segment, rao_segment, alertes_segment,
               activitat_pct, capture_rate_pct, regularitat_pct, tendencia_pct,
               dies_inactiu, spend_12m, spend_total, mesos_actius, mesos_totals,
               pot_anual, predicho_6m, variacio_6m_pct,
               n_prod_critical, n_prod_warning, n_prod_risc_total, fecha_pipeline
        FROM ml_client_segment
        WHERE cliente_id = ?
        """,
        (cliente_id,),
    ).fetchone()
    if seg:
        payload["segmento_ml"] = seg["segment"]
        payload["rao_segment"] = seg["rao_segment"]
        payload["alertes_segment"] = seg["alertes_segment"]
        payload["predicho_6m"] = (
            float(seg["predicho_6m"]) if seg["predicho_6m"] is not None else None
        )
        payload["variacio_6m_pct"] = (
            float(seg["variacio_6m_pct"]) if seg["variacio_6m_pct"] is not None else None
        )
        payload["n_prod_critical"] = int(seg["n_prod_critical"] or 0)
        payload["n_prod_warning"] = int(seg["n_prod_warning"] or 0)
        payload["n_prod_risc_total"] = int(seg["n_prod_risc_total"] or 0)
        payload["fecha_pipeline"] = seg["fecha_pipeline"]
        payload["spend_12m"] = (
            float(seg["spend_12m"]) if seg["spend_12m"] is not None else None
        )
        payload["pot_anual"] = (
            float(seg["pot_anual"]) if seg["pot_anual"] is not None else None
        )
        payload["regularitat_pct"] = (
            float(seg["regularitat_pct"]) if seg["regularitat_pct"] is not None else None
        )
        payload["tendencia_pct"] = (
            float(seg["tendencia_pct"]) if seg["tendencia_pct"] is not None else None
        )
        payload["activitat_pct"] = (
            float(seg["activitat_pct"]) if seg["activitat_pct"] is not None else None
        )
        payload["capture_rate_pct"] = (
            float(seg["capture_rate_pct"]) if seg["capture_rate_pct"] is not None else None
        )

    riesgo_rows = conn.execute(
        """
        SELECT producto_id, familia, tipus, vendes_12m, dies_inactiu, gravetat, motius
        FROM ml_client_product_risk
        WHERE cliente_id = ?
        ORDER BY
            CASE gravetat WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
            COALESCE(vendes_12m, 0) DESC,
            producto_id
        LIMIT 12
        """,
        (cliente_id,),
    ).fetchall()
    payload["productos_riesgo"] = [
        {
            "producto_id": r["producto_id"],
            "familia": r["familia"],
            "tipus": r["tipus"],
            "vendes_12m": float(r["vendes_12m"] or 0),
            "dies_inactiu": int(r["dies_inactiu"] or 0),
            "gravetat": r["gravetat"],
            "motius": r["motius"],
        }
        for r in riesgo_rows
    ]

    fc_rows = conn.execute(
        """
        SELECT ds, predicho, lower, upper, metode
        FROM ml_client_forecast
        WHERE cliente_id = ?
        ORDER BY ds
        """,
        (cliente_id,),
    ).fetchall()
    payload["forecast_6m"] = [
        {
            "ds": r["ds"],
            "predicho": float(r["predicho"] or 0),
            "lower": float(r["lower"]) if r["lower"] is not None else None,
            "upper": float(r["upper"]) if r["upper"] is not None else None,
            "metode": r["metode"],
        }
        for r in fc_rows
    ]
    return payload


@app.get("/clients/{cliente_id}")
def get_client(cliente_id: str):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT
            c.cliente_id,
            COALESCE(c.provincia_nombre, 'Sin provincia') AS provincia_nombre,
            ce.estado_cliente,
            ce.semaforo_riesgo,
            ce.recomendacion_estado,
            ce.compras_total,
            ce.devoluciones_total,
            ce.fecha_ultima_compra,
            COALESCE(p.perfil_cliente, 'SIN_CLASIFICAR') AS perfil_cliente
        FROM dim_cliente c
        LEFT JOIN vw_cliente_semaforo_riesgo ce ON ce.cliente_id = c.cliente_id
        LEFT JOIN vw_perfil_cliente_recurrencia p ON p.cliente_id = c.cliente_id
        WHERE c.cliente_id = ?
        """,
        (cliente_id,),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Client not found")

    monthly = conn.execute(
        """
        SELECT
            year_month,
            SUM(unidades_compra) AS unidades_compra
        FROM vw_cliente_producto_mensual
        WHERE cliente_id = ?
        GROUP BY year_month
        ORDER BY year_month DESC
        LIMIT 12
        """,
        (cliente_id,),
    ).fetchall()

    monthly = list(reversed(monthly))
    values = [float(m["unidades_compra"] or 0) for m in monthly]
    avg_3 = mean(values[-3:]) if values else 0.0
    trend = 0.0
    if len(values) >= 2:
        trend = values[-1] - values[-2]
    pred_next = max(0.0, avg_3 + 0.5 * trend)

    productos_cliente_rows = conn.execute(
        """
        SELECT
            cpm.producto_id,
            MAX(dp.categoria_producto) AS categoria_producto,
            MAX(dp.familia_producto) AS familia_producto,
            SUM(COALESCE(cpm.unidades_compra, 0)) AS unidades_total
        FROM vw_cliente_producto_mensual cpm
        JOIN dim_producto dp ON dp.producto_id = cpm.producto_id
        WHERE cpm.cliente_id = ?
        GROUP BY cpm.producto_id
        HAVING SUM(COALESCE(cpm.unidades_compra, 0)) > 0
        ORDER BY unidades_total DESC, cpm.producto_id
        """,
        (cliente_id,),
    ).fetchall()

    data = dict(row)
    data["productos_cliente"] = [
        {
            "producto_id": r["producto_id"],
            "categoria_producto": r["categoria_producto"],
            "familia_producto": r["familia_producto"],
            "unidades_total": round(float(r["unidades_total"] or 0), 2),
        }
        for r in productos_cliente_rows
    ]
    data["compras_mensuales"] = values
    data["compras_mensuales_detalle"] = [
        {"periodo": m["year_month"], "valor": float(m["unidades_compra"] or 0)} for m in monthly
    ]
    data["prediccion_siguiente_mes"] = round(pred_next, 2)
    data["prediccion_metodo"] = "media_3m_con_tendencia"
    # Enriquecimiento con outputs del pipeline ML (segment LEAL/PROMETEDOR/RISC,
    # productos en riesgo y forecast 6m de gasto). Se sirven planos para que el
    # front los consuma sin lógica adicional.
    data["ml"] = _ml_client_payload(conn, cliente_id)
    return data

@app.get("/alerts/daily")
def daily_alerts(limit: int = Query(default=25, ge=1, le=200)):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT *
        FROM vw_alertas_operativas_final
        ORDER BY
            CASE prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
            COALESCE(valor_actual, 0) DESC,
            alerta_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

@app.get("/alerts/queue")
def alerts_queue(
    limit: int = Query(default=50, ge=1, le=500),
    prioridad: str = Query(default=""),
    semaforo: str = Query(default=""),
):
    conn = get_conn()
    where = []
    params = []
    if prioridad.strip():
        where.append("a.prioridad = ?")
        params.append(prioridad.strip().upper())
    if semaforo.strip():
        where.append("c.semaforo_riesgo = ?")
        params.append(semaforo.strip().upper())
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    rows = conn.execute(
        f"""
        WITH latest_tracking AS (
            SELECT
                s.alerta_id,
                s.estado,
                s.fecha_contacto,
                ROW_NUMBER() OVER (PARTITION BY s.alerta_id ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC) AS rn
            FROM seguimiento_alerta s
            WHERE s.es_demo = 0
        )
        SELECT
            a.alerta_id,
            a.fecha_alerta,
            a.cliente_id,
            a.producto_id,
            a.prioridad,
            a.motivo,
            a.accion_recomendada,
            a.valor_actual,
            a.valor_referencia,
            c.semaforo_riesgo,
            c.dias_desde_ultima_compra,
            c.recomendacion_estado,
            COALESCE(t.estado, 'pendiente') AS ultimo_estado_contacto,
            t.fecha_contacto AS ultima_fecha_contacto
        FROM vw_alertas_operativas_final a
        LEFT JOIN vw_cliente_semaforo_riesgo c ON c.cliente_id = a.cliente_id
        LEFT JOIN latest_tracking t ON t.alerta_id = a.alerta_id AND t.rn = 1
        {where_sql}
        ORDER BY
            CASE a.prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
            COALESCE(c.dias_desde_ultima_compra, 0) DESC,
            a.alerta_id DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [dict(r) for r in rows]

@app.get("/clients/{cliente_id}/profile")
def client_profile(cliente_id: str):
    conn = get_conn()
    base = conn.execute(
        """
        SELECT
            c.cliente_id,
            COALESCE(c.provincia_nombre, 'Sin provincia') AS provincia_nombre,
            COALESCE(p.perfil_cliente, 'SIN_CLASIFICAR') AS perfil_cliente,
            s.estado_cliente,
            s.semaforo_riesgo,
            s.recomendacion_estado,
            s.compras_total,
            s.devoluciones_total,
            s.fecha_ultima_compra,
            s.dias_desde_ultima_compra
        FROM dim_cliente c
        LEFT JOIN vw_perfil_cliente_recurrencia p ON p.cliente_id = c.cliente_id
        LEFT JOIN vw_cliente_semaforo_riesgo s ON s.cliente_id = c.cliente_id
        WHERE c.cliente_id = ?
        """,
        (cliente_id,),
    ).fetchone()
    if not base:
        raise HTTPException(status_code=404, detail="Client not found")

    trend = conn.execute(
        """
        SELECT year_month AS periodo, SUM(unidades_compra) AS unidades
        FROM vw_cliente_producto_mensual
        WHERE cliente_id = ?
        GROUP BY year_month
        ORDER BY year_month DESC
        LIMIT 12
        """,
        (cliente_id,),
    ).fetchall()
    trend = list(reversed(trend))

    top_products = conn.execute(
        """
        SELECT
            producto_id,
            SUM(unidades_compra) AS unidades_compra,
            SUM(unidades_devolucion) AS unidades_devolucion
        FROM vw_cliente_producto_mensual
        WHERE cliente_id = ?
        GROUP BY producto_id
        ORDER BY SUM(unidades_compra) DESC
        LIMIT 5
        """,
        (cliente_id,),
    ).fetchall()

    category_mix = conn.execute(
        """
        SELECT
            dp.categoria_producto,
            SUM(v.unidades_netas) AS unidades
        FROM fact_venta_linea v
        JOIN dim_producto dp ON dp.producto_id = v.producto_id
        WHERE v.cliente_id = ?
          AND v.tipo_movimiento = 'COMPRA'
        GROUP BY dp.categoria_producto
        ORDER BY SUM(v.unidades_netas) DESC
        LIMIT 5
        """,
        (cliente_id,),
    ).fetchall()

    follow_up = conn.execute(
        """
        SELECT
            COUNT(*) AS gestiones_total,
            SUM(CASE WHEN estado = 'recuperado' THEN 1 ELSE 0 END) AS recuperados,
            SUM(CASE WHEN estado = 'contactado' THEN 1 ELSE 0 END) AS contactados,
            SUM(CASE WHEN estado = 'sin_respuesta' THEN 1 ELSE 0 END) AS sin_respuesta,
            SUM(CASE WHEN estado = 'perdido' THEN 1 ELSE 0 END) AS perdidos
        FROM seguimiento_alerta s
        JOIN alerta_cliente_producto a ON a.alerta_id = s.alerta_id
        WHERE a.cliente_id = ?
          AND s.es_demo = 0
        """,
        (cliente_id,),
    ).fetchone()

    ciclo = conn.execute(
        """
        WITH fechas AS (
            SELECT DISTINCT fecha AS d
            FROM fact_venta_linea
            WHERE cliente_id = ? AND tipo_movimiento = 'COMPRA'
            ORDER BY d
        ),
        gaps AS (
            SELECT
                julianday(d) - julianday(LAG(d) OVER (ORDER BY d)) AS dias_entre
            FROM fechas
        )
        SELECT AVG(dias_entre) AS ciclo_dias_medio
        FROM gaps
        WHERE dias_entre IS NOT NULL AND dias_entre > 0 AND dias_entre < 400
        """,
        (cliente_id,),
    ).fetchone()

    dow_rows = conn.execute(
        """
        SELECT
            CAST(strftime('%w', fecha) AS INTEGER) AS dow,
            COUNT(*) AS cnt
        FROM fact_venta_linea
        WHERE cliente_id = ? AND tipo_movimiento = 'COMPRA'
        GROUP BY dow
        ORDER BY cnt DESC
        LIMIT 3
        """,
        (cliente_id,),
    ).fetchall()

    productos_activos_row = conn.execute(
        """
        SELECT COUNT(DISTINCT producto_id) AS n
        FROM vw_cliente_producto_mensual
        WHERE cliente_id = ? AND COALESCE(unidades_compra, 0) > 0
        """,
        (cliente_id,),
    ).fetchone()

    ciclo_dias = float(ciclo["ciclo_dias_medio"] or 0) if ciclo else 0.0
    if ciclo_dias <= 0 or ciclo_dias != ciclo_dias:
        ciclo_dias = 0.0

    return {
        "cliente": dict(base),
        "trend_12m": [{"periodo": r["periodo"], "unidades": float(r["unidades"] or 0)} for r in trend],
        "top_products": [dict(r) for r in top_products],
        "category_mix": [dict(r) for r in category_mix],
        "ciclo_dias_medio": round(ciclo_dias, 1) if ciclo_dias > 0 else None,
        "productos_activos": int(productos_activos_row["n"] or 0),
        "contacto_dow_top": [int(r["dow"]) for r in dow_rows],
        "follow_up": {
            "gestiones_total": int(follow_up["gestiones_total"] or 0),
            "recuperados": int(follow_up["recuperados"] or 0),
            "contactados": int(follow_up["contactados"] or 0),
            "sin_respuesta": int(follow_up["sin_respuesta"] or 0),
            "perdidos": int(follow_up["perdidos"] or 0),
        },
        "ml": _ml_client_payload(conn, cliente_id),
    }

@app.get("/products/xgb-forecast/portfolio-summary")
def product_xgb_portfolio_summary(
    meses: int = Query(default=12, ge=1, le=36),
    max_productos: int = Query(default=500, ge=1, le=5000),
):
    """
    Vista global: compras mensuales totales (Master) + suma de forecasts por mes (todos los productos del catálogo).
    Equivalente a agregar las columnas de ``exportar_forecast_csv`` por fecha.
    """
    try:
        from forecast_por_tipo import get_portfolio_purchases_payload
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Dependencias de forecast no disponibles: {exc}",
        ) from exc
    payload = get_portfolio_purchases_payload(meses_futuros=meses, max_productos=max_productos)
    if not payload.get("available"):
        err = payload.get("error", "Portfolio no disponible")
        infra = any(
            x in err.lower()
            for x in ("libomp", "xgboost", "openmp", "matplotlib", "pandas", "sklearn", "no module named")
        )
        raise HTTPException(status_code=503 if infra else 404, detail=err)
    return payload


@app.get("/products/xgb-forecast/{producto_id}")
def product_xgb_forecast(producto_id: str, meses: int = Query(default=12, ge=1, le=36)):
    """
    Forecast agregado por producto (modelo XGB pooled por tipo T/C).
    Requiere ``backend/Master_Datos_Unificado.csv`` y dependencias en ``requirements-forecast.txt``.
    La primera llamada puede tardar (carga CSV + entrena modelos).
    """
    try:
        from forecast_por_tipo import get_forecast_chart_payload
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Dependencias de forecast no disponibles: {exc}",
        ) from exc
    payload = get_forecast_chart_payload(producto_id.strip(), meses_futuros=meses)
    if not payload.get("available"):
        err = payload.get("error", "Forecast no disponible")
        infra = any(
            x in err.lower()
            for x in ("libomp", "xgboost", "openmp", "matplotlib", "pandas", "sklearn", "no module named")
        )
        raise HTTPException(status_code=503 if infra else 404, detail=err)
    return payload


@app.get("/products/options")
def product_options():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT producto_id, categoria_producto, familia_producto
        FROM dim_producto
        ORDER BY producto_id
        """
    ).fetchall()
    return [dict(r) for r in rows]

@app.get("/products/analysis")
def product_analysis(cliente_id: str, producto_id: str):
    conn = get_conn()
    return build_product_analysis(conn, cliente_id, producto_id)

@app.post("/products/escalate")
def escalate_product(payload: ProductEscalationPayload):
    conn = get_conn()
    analysis = build_product_analysis(conn, payload.cliente_id, payload.producto_id)
    motivo = payload.motivo or "Escalado manual desde vista de productos"
    valor_actual = analysis["prediction"]["dias_retraso"] or analysis["limits"]["current"]
    valor_referencia = analysis["limits"]["average"]
    fecha_alerta = conn.execute("SELECT MAX(fecha) AS max_fecha FROM fact_venta_linea").fetchone()["max_fecha"]

    existing = conn.execute(
        """
        SELECT alerta_id
        FROM alerta_cliente_producto
        WHERE fecha_alerta = ?
          AND cliente_id = ?
          AND producto_id = ?
          AND motivo = ?
        ORDER BY alerta_id DESC
        LIMIT 1
        """,
        (fecha_alerta, payload.cliente_id, payload.producto_id, motivo),
    ).fetchone()

    if existing:
        return {"ok": True, "alerta_id": existing["alerta_id"], "already_exists": True}

    cur = conn.execute(
        """
        INSERT INTO alerta_cliente_producto
            (fecha_alerta, cliente_id, producto_id, prioridad, motivo, valor_actual, valor_referencia, accion_recomendada)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fecha_alerta,
            payload.cliente_id,
            payload.producto_id,
            payload.prioridad,
            motivo,
            valor_actual,
            valor_referencia,
            "Revisar patron de compra en vista de productos y contactar cliente",
        ),
    )
    conn.commit()
    return {"ok": True, "alerta_id": cur.lastrowid, "already_exists": False}

@app.post("/alerts/{alerta_id}/outcome")
def create_alert_outcome(alerta_id: int, payload: OutcomePayload):
    """Registra resultado de seguimiento.

    Si el estado no es «pendiente», aplica el mismo resultado a **todas** las alertas
    del mismo cliente cuyo último seguimiento sigue en pendiente (o sin registro),
    para que un solo gesto cierre la cola operativa del cliente.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT cliente_id FROM alerta_cliente_producto WHERE alerta_id = ?",
        (alerta_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    cliente_id = row["cliente_id"]

    fecha_contacto = payload.fecha_contacto.isoformat() if payload.fecha_contacto else date.today().isoformat()

    if payload.estado == "pendiente":
        conn.execute(
            """
            INSERT INTO seguimiento_alerta (alerta_id, fecha_contacto, estado, es_demo, notas, usuario_operador)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                alerta_id,
                fecha_contacto,
                payload.estado,
                payload.es_demo,
                payload.notas,
                payload.usuario_operador,
            ),
        )
        conn.commit()
        return {"ok": True}

    open_rows = conn.execute(
        """
        WITH ult AS (
            SELECT
                s.alerta_id,
                s.estado,
                ROW_NUMBER() OVER (
                    PARTITION BY s.alerta_id
                    ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC
                ) AS rn
            FROM seguimiento_alerta s
            WHERE s.es_demo = 0
        )
        SELECT a.alerta_id
        FROM alerta_cliente_producto a
        LEFT JOIN ult u ON u.alerta_id = a.alerta_id AND u.rn = 1
        WHERE a.cliente_id = ?
          AND COALESCE(u.estado, 'pendiente') = 'pendiente'
        """,
        (cliente_id,),
    ).fetchall()

    targets = [r["alerta_id"] for r in open_rows]
    if not targets:
        conn.execute(
            """
            INSERT INTO seguimiento_alerta (alerta_id, fecha_contacto, estado, es_demo, notas, usuario_operador)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                alerta_id,
                fecha_contacto,
                payload.estado,
                payload.es_demo,
                payload.notas,
                payload.usuario_operador,
            ),
        )
        conn.commit()
        return {"ok": True, "updated_alertas": 1}

    for aid in targets:
        conn.execute(
            """
            INSERT INTO seguimiento_alerta (alerta_id, fecha_contacto, estado, es_demo, notas, usuario_operador)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                aid,
                fecha_contacto,
                payload.estado,
                payload.es_demo,
                payload.notas,
                payload.usuario_operador,
            ),
        )
    conn.commit()
    return {"ok": True, "updated_alertas": len(targets)}