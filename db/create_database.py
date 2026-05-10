#!/usr/bin/env python3
"""
Construye interhack.db desde CSV de dimensiones + **ventas del Master unificado**.

Las líneas de venta (`fact_venta_linea`) se cargan preferentemente desde
`Master_Datos_Unificado.csv` (misma referencia temporal que forecast/XGB).
Si no existe el Master, se usa `Datasets.xlsx - Ventas.csv` como respaldo.

Los productos que aparecen solo en el Master se añaden a staging antes de los hechos.
"""

import csv
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
RAW_DIR = ROOT / "raw"
DB_PATH = ROOT / "interhack.db"
SCHEMA_PATH = ROOT / "schema.sql"
ANALYSIS_PATH = ROOT / "analysis.sql"


def resolve_master_csv() -> Path | None:
    """Ruta del Master usada para ventas y productos extra (mismo criterio que forecast)."""
    for p in (
        RAW_DIR / "Master_Datos_Unificado.csv",
        ROOT / "Master_Datos_Unificado.csv",  # legacy: ubicación previa
        REPO_ROOT / "Master_Datos_Unificado.csv",
        REPO_ROOT / "backend" / "Master_Datos_Unificado.csv",
    ):
        if p.is_file():
            return p
    return None

# Los CSV de origen viven en db/raw/ (reorganización 2026-05). Para arranque
# tolerante (instalaciones legacy) caemos a db/ si no existe la copia en raw/.
def _src_csv(name: str) -> Path:
    p = RAW_DIR / name
    return p if p.is_file() else ROOT / name


CSV_FILES = {
    "clientes":  _src_csv("Datasets.xlsx - Clientes.csv"),
    "productos": _src_csv("Datasets.xlsx - Productos.csv"),
    "ventas":    _src_csv("Datasets.xlsx - Ventas.csv"),
    "potencial": _src_csv("Datasets.xlsx - Potencial.csv"),
    "campanas":  _src_csv("Datasets.xlsx - Campañas.csv"),
}


def _norm_field(fieldnames: list[str], *substrings: str) -> str | None:
    """Encuentra columna cuyo nombre contiene las subcadenas típicas del Master."""
    for fn in fieldnames:
        fk = fn.lower().replace(" ", "").replace(".", "")
        for sub in substrings:
            s = sub.lower().replace(" ", "").replace(".", "")
            if s in fk:
                return fn
    return None


def parse_date(value: str) -> str:
    """Normaliza a YYYY-MM-DD (SQLite DATE)."""
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def parse_eur_number(value: str) -> float:
    normalized = value.strip().replace(".", "").replace(",", ".").replace('"', "")
    if normalized == "":
        return 0.0
    return float(normalized)


def parse_unidades_raw(value: str) -> str:
    """Unidades para staging (compatible con build_bridge: int desde string)."""
    if value is None:
        return "0"
    s = str(value).strip().replace('"', "").replace(".", "").replace(",", ".")
    if s == "":
        return "0"
    try:
        return str(int(round(float(s))))
    except ValueError:
        return "0"


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def expand_stg_productos_from_master(conn: sqlite3.Connection, master_path: Path) -> None:
    """Productos presentes en el Master que no vienen en el catálogo Datasets."""
    if not master_path.is_file():
        return
    cur = conn.cursor()
    existing = {r[0] for r in cur.execute("SELECT DISTINCT id_producto_raw FROM stg_productos")}
    seen_new: set[str] = set()
    batch: list[tuple[str, str, str, str]] = []

    with master_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        fn = reader.fieldnames or []
        col_prod = _norm_field(fn, "Id.Producto", "Producto")
        col_bloque = _norm_field(fn, "Prod_Bloque", "Bloque analítico")
        col_cat = _norm_field(fn, "Prod_Categoria", "Categoria_H")
        col_fam = _norm_field(fn, "Prod_Familia", "Familia_H")
        if not col_prod:
            return
        for row in reader:
            pid = (row.get(col_prod) or "").strip()
            if not pid or pid in existing or pid in seen_new:
                continue
            bloque = (row.get(col_bloque, "") if col_bloque else "").strip() or "Sin clasificar"
            cat = (row.get(col_cat, "") if col_cat else "").strip() or "Sin clasificar"
            fam = (row.get(col_fam, "") if col_fam else "").strip() or "Sin clasificar"
            seen_new.add(pid)
            batch.append((pid, bloque, cat, fam))

    if batch:
        cur.executemany(
            """
            INSERT INTO stg_productos (id_producto_raw, bloque_analitico, categoria_producto, familia_producto)
            VALUES (?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()
        print(f"Productos extra desde Master: {len(batch)}")


def load_staging_ventas(conn: sqlite3.Connection, master_path: Path | None) -> None:
    cur = conn.cursor()
    if master_path is not None and master_path.is_file():
        batch: list[tuple[str, str, str, str, str, str]] = []
        BS = 8000
        with master_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            fn = reader.fieldnames or []
            col_fact = _norm_field(fn, "Num.Fact", "Fact")
            col_fecha = _norm_field(fn, "Fecha")
            col_cli = _norm_field(fn, "Id.Cliente", "Cliente")
            col_prod = _norm_field(fn, "Id.Producto", "Producto")
            col_uds = _norm_field(fn, "Unidades")
            col_val = _norm_field(fn, "Valores_H", "Valores")
            if not col_fact or not col_fecha or not col_cli or not col_prod:
                raise RuntimeError(
                    "Master_Datos_Unificado.csv: faltan columnas Num.Fact / Fecha / Id.Cliente / Id.Producto."
                )
            for row in reader:
                batch.append(
                    (
                        (row.get(col_fact) or "").strip(),
                        (row.get(col_fecha) or "").strip(),
                        (row.get(col_cli) or "").strip(),
                        (row.get(col_prod) or "").strip(),
                        parse_unidades_raw(row.get(col_uds, "") if col_uds else "0"),
                        (row.get(col_val, "") if col_val else "").strip(),
                    )
                )
                if len(batch) >= BS:
                    cur.executemany(
                        """
                        INSERT INTO stg_ventas (factura_num, fecha_raw, id_cliente_raw, id_producto_raw, unidades_raw, valores_h_raw)
                        VALUES (?,?,?,?,?,?)
                        """,
                        batch,
                    )
                    batch = []
            if batch:
                cur.executemany(
                    """
                    INSERT INTO stg_ventas (factura_num, fecha_raw, id_cliente_raw, id_producto_raw, unidades_raw, valores_h_raw)
                    VALUES (?,?,?,?,?,?)
                    """,
                    batch,
                )
        conn.commit()
        print(f"Ventas cargadas desde {master_path} ({master_path.name}).")
        return

    if CSV_FILES["ventas"].is_file():
        for row in read_csv(CSV_FILES["ventas"]):
            cur.execute(
                """
                INSERT INTO stg_ventas (factura_num, fecha_raw, id_cliente_raw, id_producto_raw, unidades_raw, valores_h_raw)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("Num.Fact", "").strip(),
                    row.get("Fecha", "").strip(),
                    row.get("Id. Cliente", "").strip(),
                    row.get("Id. Producto", "").strip(),
                    row.get("Unidades", "").strip(),
                    row.get("Valores_H", "").strip(),
                ),
            )
        conn.commit()
        print("Ventas cargadas desde Datasets.xlsx - Ventas.csv (respaldo).")
        return

    raise FileNotFoundError(
        "No hay fuente de ventas: coloca db/Master_Datos_Unificado.csv o db/Datasets.xlsx - Ventas.csv"
    )


def load_staging(conn: sqlite3.Connection):
    cur = conn.cursor()

    for row in read_csv(CSV_FILES["clientes"]):
        cur.execute(
            """
            INSERT INTO stg_clientes (id_cliente_raw, provincia_codigo, provincia_nombre)
            VALUES (?, ?, ?)
            """,
            (
                row.get("Id. Cliente", "").strip(),
                row.get("", "").strip(),
                row.get("Provincia", "").strip(),
            ),
        )

    for row in read_csv(CSV_FILES["productos"]):
        cur.execute(
            """
            INSERT INTO stg_productos (id_producto_raw, bloque_analitico, categoria_producto, familia_producto)
            VALUES (?, ?, ?, ?)
            """,
            (
                row.get("Id.Prod", "").strip(),
                row.get("Bloque analítico", "").strip(),
                row.get("Categoria_H", "").strip(),
                row.get("Familia_H", "").strip(),
            ),
        )

    for row in read_csv(CSV_FILES["potencial"]):
        cur.execute(
            """
            INSERT INTO stg_potencial (id_cliente_raw, familia_negocio, categoria_producto, potencial_raw)
            VALUES (?, ?, ?, ?)
            """,
            (
                row.get("Id.Cliente", "").strip(),
                row.get("Familia", "").strip(),
                row.get("Categoria Productos", "").strip(),
                row.get("Potencial_H", "").strip(),
            ),
        )

    for row in read_csv(CSV_FILES["campanas"]):
        cur.execute(
            """
            INSERT INTO stg_campanas (campana_codigo, fecha_inicio_raw, fecha_fin_raw)
            VALUES (?, ?, ?)
            """,
            (
                row.get("Campaña", "").strip(),
                row.get("Fecha inicio", "").strip(),
                row.get("Fecha fin", "").strip(),
            ),
        )

    conn.commit()


def build_dimensions(conn: sqlite3.Connection):
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO dim_cliente (cliente_id, provincia_codigo, provincia_nombre)
        SELECT
            id_cliente_raw,
            MIN(provincia_codigo),
            MIN(provincia_nombre)
        FROM stg_clientes
        WHERE id_cliente_raw <> ''
        GROUP BY id_cliente_raw
        """
    )

    cur.execute(
        """
        INSERT INTO dim_cliente (cliente_id, provincia_codigo, provincia_nombre)
        SELECT
            s.id_cliente_raw,
            NULL,
            NULL
        FROM (
            SELECT DISTINCT id_cliente_raw
            FROM stg_ventas
            WHERE id_cliente_raw <> ''
            UNION
            SELECT DISTINCT id_cliente_raw
            FROM stg_potencial
            WHERE id_cliente_raw <> ''
        ) s
        LEFT JOIN dim_cliente d ON d.cliente_id = s.id_cliente_raw
        WHERE d.cliente_id IS NULL
        """
    )

    cur.execute(
        """
        INSERT INTO dim_producto (producto_id, bloque_analitico, categoria_producto, familia_producto, es_tecnico, es_uso_diario)
        SELECT
            id_producto_raw,
            bloque_analitico,
            categoria_producto,
            familia_producto,
            CASE WHEN bloque_analitico = 'Productos Técnicos' THEN 1 ELSE 0 END AS es_tecnico,
            CASE
                WHEN categoria_producto IN ('Categoria C1', 'Categoria C2') THEN 1
                ELSE 0
            END AS es_uso_diario
        FROM stg_productos
        WHERE id_producto_raw <> ''
        GROUP BY id_producto_raw, bloque_analitico, categoria_producto, familia_producto
        """
    )

    for row in conn.execute("SELECT campana_codigo, fecha_inicio_raw, fecha_fin_raw FROM stg_campanas"):
        conn.execute(
            """
            INSERT INTO dim_campana (campana_codigo, fecha_inicio, fecha_fin)
            VALUES (?, ?, ?)
            """,
            (row[0], parse_date(row[1]), parse_date(row[2])),
        )

    conn.commit()


def build_bridge_and_facts(conn: sqlite3.Connection):
    cur = conn.cursor()

    for row in conn.execute(
        "SELECT id_cliente_raw, familia_negocio, categoria_producto, potencial_raw FROM stg_potencial WHERE id_cliente_raw <> ''"
    ):
        cur.execute(
            """
            INSERT OR REPLACE INTO bridge_cliente_potencial
                (cliente_id, familia_negocio, categoria_producto, potencial_valor)
            VALUES (?, ?, ?, ?)
            """,
            (
                row[0],
                row[1],
                row[2],
                parse_eur_number(row[3]),
            ),
        )

    campanas = list(conn.execute("SELECT campana_id, fecha_inicio, fecha_fin FROM dim_campana"))

    skipped = 0
    for row in conn.execute(
        "SELECT factura_num, fecha_raw, id_cliente_raw, id_producto_raw, unidades_raw FROM stg_ventas WHERE id_cliente_raw <> '' AND id_producto_raw <> ''"
    ):
        fecha = parse_date(row[1])
        if not fecha:
            skipped += 1
            continue
        unidades = int(float(row[4])) if row[4] else 0
        if unidades > 0:
            tipo_mov = "COMPRA"
        elif unidades < 0:
            tipo_mov = "DEVOLUCION"
        else:
            tipo_mov = "SIN_MOVIMIENTO"

        campana_id = None
        es_campana = 0
        for c_id, inicio, fin in campanas:
            if inicio <= fecha <= fin:
                campana_id = c_id
                es_campana = 1
                break

        cur.execute(
            """
            INSERT INTO fact_venta_linea
                (factura_num, fecha, cliente_id, producto_id, unidades_netas, tipo_movimiento, es_campana, campana_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row[0], fecha, row[2], row[3], unidades, tipo_mov, es_campana, campana_id),
        )

    conn.commit()
    if skipped:
        print(f"Aviso: {skipped} líneas de venta sin fecha válida omitidas.")


def create_analysis_views(conn: sqlite3.Connection):
    conn.executescript(ANALYSIS_PATH.read_text(encoding="utf-8"))
    conn.commit()


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()

    master = resolve_master_csv()
    if master:
        print(f"Master CSV: {master}")
    else:
        print("Aviso: no se encontró Master_Datos_Unificado.csv (db/, raíz repo o backend/); ventas desde Datasets si existe.")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        load_staging(conn)
        if master:
            expand_stg_productos_from_master(conn, master)
        load_staging_ventas(conn, master)
        build_dimensions(conn)
        build_bridge_and_facts(conn)
        create_analysis_views(conn)
    finally:
        conn.close()

    print(f"Database created at: {DB_PATH}")


if __name__ == "__main__":
    main()
