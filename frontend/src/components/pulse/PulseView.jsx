import { useEffect, useMemo, useState } from "react";
import {
  getClientProfile,
  getPulseBoard,
  getPulseBoardCellClients,
} from "../../api/client";

/* ─── helpers ──────────────────────────────────────────────────────────────── */

const SEGMENT_LABEL = {
  LEAL: "Leal",
  PROMETEDOR: "Prometedor",
  RISC: "Riesgo",
  SIN_SEGMENTO: "Sin segmento",
};

const SEGMENT_HINT = {
  LEAL: "Cliente fiel y constante",
  PROMETEDOR: "Cliente con potencial de crecimiento — todavía recuperable",
  RISC: "Caída fuerte de actividad — riesgo de fuga",
  SIN_SEGMENTO: "Cliente sin clasificar (poca historia)",
};

const SEMAFORO_LABEL = {
  ROJO: "Rojo",
  AMARILLO: "Amarillo",
  VERDE: "Verde",
  OTRO: "—",
};

const SEMAFORO_HINT = {
  ROJO: "Acción urgente",
  AMARILLO: "Vigilar",
  VERDE: "Sano",
  OTRO: "—",
};

const SEGMENT_ORDER = ["RISC", "PROMETEDOR", "LEAL", "SIN_SEGMENTO"];
const SEMAFORO_ORDER = ["ROJO", "AMARILLO", "VERDE"];

function fmtEuro(n) {
  const v = Number(n || 0);
  if (!Number.isFinite(v)) return "0 €";
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)} M€`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(0)} k€`;
  return `${Math.round(v)} €`;
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString("es-ES");
}

function SegmentChip({ value }) {
  const v = String(value || "SIN_SEGMENTO").toUpperCase();
  return (
    <span
      className={`tag segment-${v.toLowerCase()}`}
      title={SEGMENT_HINT[v] ?? ""}
    >
      {SEGMENT_LABEL[v] ?? v}
    </span>
  );
}

function SemaforoDot({ value }) {
  const v = String(value || "OTRO").toUpperCase();
  const cls =
    v === "ROJO"
      ? "sem-dot sem-rojo"
      : v === "AMARILLO"
      ? "sem-dot sem-amar"
      : v === "VERDE"
      ? "sem-dot sem-verde"
      : "sem-dot sem-otro";
  return <span className={cls} title={SEMAFORO_LABEL[v]} aria-label={SEMAFORO_LABEL[v]} />;
}

function MiniSparkline({ values = [], color = "#2563eb" }) {
  const w = 220;
  const h = 56;
  const pad = 6;
  if (!values.length) return <svg viewBox={`0 0 ${w} ${h}`} className="pulse-spark" />;
  const max = Math.max(1, ...values);
  const min = Math.min(0, ...values);
  const rng = Math.max(1, max - min);
  const step = values.length > 1 ? (w - pad * 2) / (values.length - 1) : 0;
  const x = (i) => pad + i * step;
  const y = (v) => h - pad - ((v - min) / rng) * (h - pad * 2);
  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${pad},${h - pad} ${line} ${(w - pad).toFixed(1)},${h - pad}`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="pulse-spark" aria-hidden="true">
      <polygon points={area} fill={color} opacity="0.12" />
      <polyline fill="none" stroke={color} strokeWidth="2" points={line} />
    </svg>
  );
}

/* ─── componente ───────────────────────────────────────────────────────────── */

export function PulseView({ onNavigateTab, onOpenAnalisisCliente, onContactarCliente }) {
  const [board, setBoard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showGlossary, setShowGlossary] = useState(false);

  /** Filtro de matriz: cuando hay celda activa cargamos del backend la lista
   *  real de clientes en esa intersección Segmento×Semáforo. */
  const [matrixFilter, setMatrixFilter] = useState(null); // {segment, semaforo}
  const [cellClients, setCellClients] = useState(null);
  const [cellLoading, setCellLoading] = useState(false);
  const [cellError, setCellError] = useState("");

  const [selectedId, setSelectedId] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError("");
    getPulseBoard(15)
      .then((data) => {
        if (!mounted) return;
        setBoard(data);
        if (data?.top_critical?.length && !selectedId) {
          setSelectedId(data.top_critical[0].cliente_id);
        }
      })
      .catch(() => mounted && setError("No se pudo cargar el resumen ejecutivo."))
      .finally(() => mounted && setLoading(false));
    return () => {
      mounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Carga la lista real cuando el usuario clickea una celda de la matriz. */
  useEffect(() => {
    if (!matrixFilter) {
      setCellClients(null);
      setCellError("");
      return;
    }
    let mounted = true;
    setCellLoading(true);
    setCellError("");
    getPulseBoardCellClients({
      segment: matrixFilter.segment,
      semaforo: matrixFilter.semaforo,
      limit: 60,
    })
      .then((data) => mounted && setCellClients(data))
      .catch(() => {
        if (!mounted) return;
        setCellClients(null);
        setCellError("No se pudo cargar la lista de clientes de esa celda.");
      })
      .finally(() => mounted && setCellLoading(false));
    return () => {
      mounted = false;
    };
  }, [matrixFilter]);

  useEffect(() => {
    if (!selectedId) {
      setProfile(null);
      return;
    }
    let mounted = true;
    setProfileLoading(true);
    getClientProfile(selectedId)
      .then((p) => mounted && setProfile(p))
      .catch(() => mounted && setProfile(null))
      .finally(() => mounted && setProfileLoading(false));
    return () => {
      mounted = false;
    };
  }, [selectedId]);

  /* Matriz Segmento×Semáforo. Calculamos totales por segmento para mostrar
     etiquetas más útiles ("Leal · 772 clientes") en la columna izquierda. */
  const matrix = useMemo(() => {
    const m = new Map();
    const segTotals = new Map();
    (board?.matrix ?? []).forEach((c) => {
      const seg = (c.segment || "SIN_SEGMENTO").toUpperCase();
      const sem = (c.semaforo || "OTRO").toUpperCase();
      const n = c.n ?? 0;
      m.set(`${seg}|${sem}`, (m.get(`${seg}|${sem}`) ?? 0) + n);
      segTotals.set(seg, (segTotals.get(seg) ?? 0) + n);
    });
    const segs = SEGMENT_ORDER.filter((s) =>
      (board?.matrix ?? []).some((c) => (c.segment || "SIN_SEGMENTO").toUpperCase() === s),
    );
    const sems = SEMAFORO_ORDER;
    const total = (board?.matrix ?? []).reduce((acc, c) => acc + (c.n ?? 0), 0);
    return {
      segs,
      sems,
      get: (seg, sem) => m.get(`${seg}|${sem}`) ?? 0,
      segTotal: (seg) => segTotals.get(seg) ?? 0,
      total,
    };
  }, [board?.matrix]);

  const selected = useMemo(() => {
    const all = [
      ...(board?.top_critical ?? []),
      ...(board?.recovery_candidates ?? []),
      ...(cellClients?.items ?? []),
    ];
    return all.find((r) => r.cliente_id === selectedId) ?? null;
  }, [board, cellClients, selectedId]);

  const trendValues = useMemo(() => {
    return (profile?.trend_12m ?? []).map((p) => Number(p.unidades || 0));
  }, [profile]);

  const kpis = board?.kpis ?? {};

  /* ─── render ──────────────────────────────────────────────────────────────── */

  return (
    <div className="pulse-board">
      {/* KPI tiles */}
      <section className="pulse-kpi-row">
        <article className="pulse-kpi pulse-kpi--accent">
          <span className="pulse-kpi-label">Cartera activa</span>
          <strong className="pulse-kpi-value">{fmtInt(kpis.clientes_activos)}</strong>
          <span className="pulse-kpi-sub">
            {fmtInt(kpis.clientes_rojo)} en semáforo rojo
          </span>
        </article>
        <article className="pulse-kpi pulse-kpi--warn">
          <span className="pulse-kpi-label">Alertas activas</span>
          <strong className="pulse-kpi-value">{fmtInt(kpis.alertas_total)}</strong>
          <span className="pulse-kpi-sub">{fmtInt(kpis.alertas_alta)} prioridad alta</span>
        </article>
        <article className="pulse-kpi">
          <span className="pulse-kpi-label">Compras 12 meses</span>
          <strong className="pulse-kpi-value">{fmtEuro(kpis.spend_12m)}</strong>
          <span className="pulse-kpi-sub">Histórico facturado</span>
        </article>
        <article className="pulse-kpi pulse-kpi--positive">
          <span className="pulse-kpi-label">Compra prevista 6 m</span>
          <strong className="pulse-kpi-value">{fmtEuro(kpis.predicho_6m)}</strong>
          <span className="pulse-kpi-sub">
            {kpis.fecha_referencia ? `Datos hasta ${kpis.fecha_referencia}` : "Predicción del modelo"}
          </span>
        </article>
      </section>

      {/* Glosario plegable: definiciones para usuarios no técnicos */}
      <section className="panel pulse-glossary">
        <button
          type="button"
          className="pulse-glossary-toggle"
          onClick={() => setShowGlossary((v) => !v)}
          aria-expanded={showGlossary}
        >
          <span>¿Qué significan los segmentos y los colores?</span>
          <span className="muted small">{showGlossary ? "Ocultar" : "Mostrar"}</span>
        </button>
        {showGlossary && (
          <div className="pulse-glossary-grid">
            <div>
              <p className="muted small" style={{ margin: 0, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600 }}>
                Segmento (cómo se comporta)
              </p>
              <ul className="pulse-glossary-list">
                <li>
                  <SegmentChip value="LEAL" /> Cliente fiel y constante: compra regularmente.
                </li>
                <li>
                  <SegmentChip value="PROMETEDOR" /> Cliente con buen potencial de crecimiento; todavía recuperable si baja la actividad.
                </li>
                <li>
                  <SegmentChip value="RISC" /> Cliente con caída fuerte de actividad — riesgo de fuga.
                </li>
              </ul>
            </div>
            <div>
              <p className="muted small" style={{ margin: 0, textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 600 }}>
                Semáforo (qué urgencia tiene)
              </p>
              <ul className="pulse-glossary-list">
                <li>
                  <SemaforoDot value="ROJO" /> <strong>Rojo:</strong> contactar lo antes posible.
                </li>
                <li>
                  <SemaforoDot value="AMARILLO" /> <strong>Amarillo:</strong> vigilar, puede empeorar.
                </li>
                <li>
                  <SemaforoDot value="VERDE" /> <strong>Verde:</strong> sano, sin acción urgente.
                </li>
              </ul>
            </div>
          </div>
        )}
      </section>

      {error && (
        <p className="muted" role="alert" style={{ margin: "8px 0 0" }}>
          {error}
        </p>
      )}

      <div className="pulse-board-grid">
        {/* Matriz segmento × semáforo */}
        <section className="panel pulse-matrix-panel">
          <header className="panel-header" style={{ alignItems: "flex-start" }}>
            <div>
              <h3 style={{ margin: 0 }}>Cartera por segmento × semáforo</h3>
              <p className="muted small" style={{ margin: "4px 0 0", maxWidth: "44rem" }}>
                Cada celda agrupa los clientes por <strong>cómo se comportan</strong> (filas)
                y <strong>su nivel de urgencia</strong> (columnas). Toca una celda para ver la
                lista real de clientes que hay en ella. Incluye técnicos y de uso diario.
              </p>
            </div>
            {matrixFilter && (
              <button type="button" className="ghost" onClick={() => setMatrixFilter(null)}>
                Quitar filtro
              </button>
            )}
          </header>

          {loading && <p className="muted">Calculando matriz…</p>}

          {!loading && matrix.segs.length > 0 && (
            <div className="pulse-matrix">
              <div className="pulse-matrix-corner" aria-hidden="true" />
              {matrix.sems.map((sem) => (
                <div key={`h-${sem}`} className="pulse-matrix-col-head">
                  <SemaforoDot value={sem} />
                  {SEMAFORO_LABEL[sem]}
                  <span className="muted small" style={{ display: "block", fontWeight: 400, letterSpacing: 0, textTransform: "none" }}>
                    {SEMAFORO_HINT[sem]}
                  </span>
                </div>
              ))}
              {matrix.segs.map((seg) => (
                <div key={`row-${seg}`} className="pulse-matrix-row" role="row">
                  <div className="pulse-matrix-row-head">
                    <div>
                      <SegmentChip value={seg} />
                      <p className="muted small" style={{ margin: "4px 0 0" }}>
                        {fmtInt(matrix.segTotal(seg))} clientes
                      </p>
                    </div>
                  </div>
                  {matrix.sems.map((sem) => {
                    const n = matrix.get(seg, sem);
                    const pct = matrix.total > 0 ? (n / matrix.total) * 100 : 0;
                    const intensity = Math.min(1, pct / 14);
                    const active =
                      matrixFilter?.segment === seg && matrixFilter?.semaforo === sem;
                    const tone =
                      seg === "RISC" && sem === "ROJO"
                        ? "danger"
                        : seg === "RISC"
                        ? "warn"
                        : sem === "ROJO"
                        ? "warn"
                        : sem === "AMARILLO"
                        ? "soft"
                        : "ok";
                    return (
                      <button
                        key={`${seg}-${sem}`}
                        type="button"
                        className={`pulse-matrix-cell tone-${tone} ${active ? "active" : ""}`}
                        style={{ "--cell-intensity": intensity }}
                        onClick={() =>
                          setMatrixFilter(active ? null : { segment: seg, semaforo: sem })
                        }
                        title={`${SEGMENT_LABEL[seg] ?? seg} · ${SEMAFORO_LABEL[sem]}: ${fmtInt(n)} clientes`}
                      >
                        <span className="pulse-cell-n">{fmtInt(n)}</span>
                        <span className="pulse-cell-pct">{pct.toFixed(1)}%</span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}

          {!board?.ml_available && !loading && (
            <p className="muted small" style={{ marginTop: 12 }}>
              Pipeline ML no cargado todavía. Ejecuta{" "}
              <code className="inline-code">bash scripts/run_weekly_pipeline.sh</code> para ver
              segmentos LEAL/PROMETEDOR/RISC.
            </p>
          )}
        </section>

        {/* Drawer detalle del cliente */}
        <aside className="panel pulse-detail-panel">
          {!selected && (
            <>
              <h3 style={{ marginTop: 0 }}>Detalle del cliente</h3>
              <p className="muted">Selecciona un cliente de cualquier lista para ver el resumen ejecutivo.</p>
            </>
          )}

          {selected && (
            <>
              <header className="pulse-detail-head">
                <div>
                  <span className="pulse-detail-eyebrow">{selected.provincia_nombre ?? "—"}</span>
                  <h3 className="pulse-detail-title">{selected.cliente_id}</h3>
                </div>
                <div className="pulse-detail-pills">
                  <SegmentChip value={selected.segmento_ml} />
                  <span className={`tag ${(selected.semaforo_riesgo || "").toLowerCase() === "rojo" ? "alta" : (selected.semaforo_riesgo || "").toLowerCase() === "amarillo" ? "media" : "baja"}`}>
                    {SEMAFORO_LABEL[selected.semaforo_riesgo] ?? "—"}
                  </span>
                </div>
              </header>

              <div className="pulse-detail-grid">
                <div>
                  <p className="muted small">Compra prevista 6 m</p>
                  <strong>
                    {selected.predicho_6m != null ? fmtEuro(selected.predicho_6m) : "—"}
                  </strong>
                  {selected.variacio_6m_pct != null && (
                    <span className={`delta ${selected.variacio_6m_pct >= 0 ? "up" : "down"}`}>
                      {selected.variacio_6m_pct >= 0 ? "+" : ""}
                      {Number(selected.variacio_6m_pct).toFixed(1)}%
                    </span>
                  )}
                </div>
                <div>
                  <p className="muted small">Días sin compra</p>
                  <strong>
                    {selected.dias_desde_ultima_compra != null
                      ? `${selected.dias_desde_ultima_compra} d`
                      : "—"}
                  </strong>
                </div>
                <div>
                  <p className="muted small">Productos críticos</p>
                  <strong>{selected.n_prod_critical ?? 0}</strong>
                </div>
                <div>
                  <p className="muted small">Última compra</p>
                  <strong>{selected.fecha_ultima_compra ?? "—"}</strong>
                </div>
              </div>

              <div className="pulse-detail-block">
                <p className="muted small" style={{ margin: "0 0 4px" }}>
                  Tendencia 12 meses (uds.)
                </p>
                {profileLoading ? (
                  <p className="muted small">Cargando…</p>
                ) : (
                  <MiniSparkline values={trendValues} color="#2563eb" />
                )}
              </div>

              <div className="pulse-actions">
                <button
                  type="button"
                  className="primary-btn"
                  onClick={() => onContactarCliente?.(selected.cliente_id)}
                >
                  Añadir a mi cola de llamadas
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => onOpenAnalisisCliente?.(selected.cliente_id)}
                >
                  Ver análisis completo
                </button>
              </div>
            </>
          )}
        </aside>
      </div>

      {/* Cuando hay celda activa: una única lista filtrada con explicación. */}
      {matrixFilter ? (
        <section className="panel">
          <header className="panel-header" style={{ alignItems: "flex-start" }}>
            <div>
              <h3 style={{ margin: 0 }}>
                Clientes en {SEGMENT_LABEL[matrixFilter.segment] ?? matrixFilter.segment}{" "}
                <SemaforoDot value={matrixFilter.semaforo} /> {SEMAFORO_LABEL[matrixFilter.semaforo]}
              </h3>
              <p className="muted small" style={{ margin: "4px 0 0" }}>
                Mostrando los <strong>{cellClients?.shown ?? 0}</strong> más relevantes
                {cellClients?.total != null && (
                  <> de un total de <strong>{fmtInt(cellClients.total)}</strong> en esta celda</>
                )}
                .{" "}
                {matrixFilter.semaforo === "VERDE"
                  ? "Ordenados por valor previsto (los más rentables primero)."
                  : matrixFilter.semaforo === "AMARILLO"
                  ? "Ordenados por nº de productos en alerta y valor previsto."
                  : "Ordenados por nº de productos críticos y antigüedad sin compra."}
              </p>
            </div>
            <button type="button" className="ghost" onClick={() => setMatrixFilter(null)}>
              Quitar filtro
            </button>
          </header>
          {cellLoading && <p className="muted small">Cargando lista…</p>}
          {cellError && <p className="muted">{cellError}</p>}
          {!cellLoading && !cellError && cellClients?.items?.length === 0 && (
            <p className="muted">
              No hay clientes en esta celda. Esto suele ocurrir cuando la categoría es
              minoritaria en la cartera.
            </p>
          )}
          {!cellLoading && cellClients?.items?.length > 0 && (
            <ClientList
              rows={cellClients.items}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onContact={onContactarCliente}
            />
          )}
        </section>
      ) : (
        <div className="pulse-lists-grid">
          <section className="panel">
            <header className="panel-header" style={{ alignItems: "flex-start" }}>
              <div>
                <h3 style={{ margin: 0 }}>1. Llamar primero · riesgo crítico</h3>
                <p className="muted small" style={{ margin: "4px 0 0" }}>
                  Clientes en <strong>caída fuerte</strong> o semáforo rojo. Estos pueden
                  cambiar de proveedor si no los contactas pronto.
                </p>
              </div>
              <span className="muted small">
                Top {board?.top_critical?.length ?? 0}
                {kpis.clientes_rojo != null && (
                  <> · {fmtInt(kpis.clientes_rojo)} rojos en cartera</>
                )}
              </span>
            </header>
            <ClientList
              rows={board?.top_critical ?? []}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onContact={onContactarCliente}
            />
            {board?.top_critical?.length > 0 && (
              <p className="muted small" style={{ marginTop: 8 }}>
                ¿Quieres ver más? Pulsa una celda <SegmentChip value="RISC" /> en la matriz para
                explorar la lista completa.
              </p>
            )}
          </section>

          <section className="panel">
            <header className="panel-header" style={{ alignItems: "flex-start" }}>
              <div>
                <h3 style={{ margin: 0 }}>2. Recuperación rentable · prometedores</h3>
                <p className="muted small" style={{ margin: "4px 0 0" }}>
                  Clientes <SegmentChip value="PROMETEDOR" /> en semáforo rojo o amarillo:
                  alto valor potencial y todavía a tiempo de revertir la caída con un contacto
                  comercial.
                </p>
              </div>
              <span className="muted small">
                Top {board?.recovery_candidates?.length ?? 0}
              </span>
            </header>
            <ClientList
              rows={board?.recovery_candidates ?? []}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onContact={onContactarCliente}
            />
            {board?.recovery_candidates?.length > 0 && (
              <p className="muted small" style={{ marginTop: 8 }}>
                Para la lista completa, pulsa la celda <SegmentChip value="PROMETEDOR" /> + rojo
                o amarillo en la matriz.
              </p>
            )}
          </section>
        </div>
      )}

      {!loading && (board?.kpis?.clientes_activos ?? 0) === 0 && (
        <p className="muted" style={{ marginTop: 16 }}>
          No hay clientes activos en la base. Ejecuta{" "}
          <code className="inline-code">python3 db/create_database.py</code> para cargar ventas.
        </p>
      )}
    </div>
  );
}

/* ─── lista compacta de clientes ──────────────────────────────────────────── */

function ClientList({ rows, selectedId, onSelect, onContact }) {
  if (!rows.length) {
    return (
      <p className="muted small" style={{ marginTop: 8 }}>
        No hay clientes con los filtros actuales.
      </p>
    );
  }
  return (
    <div className="pulse-client-list">
      {rows.map((r) => {
        const active = r.cliente_id === selectedId;
        return (
          <button
            key={r.cliente_id}
            type="button"
            className={`pulse-client-card ${active ? "active" : ""}`}
            onClick={() => onSelect?.(r.cliente_id)}
          >
            <div className="pulse-client-card-top">
              <SemaforoDot value={r.semaforo_riesgo} />
              <strong>{r.cliente_id}</strong>
              <SegmentChip value={r.segmento_ml} />
            </div>
            <div className="pulse-client-card-mid">
              <span className="muted small">{r.provincia_nombre ?? "—"}</span>
              <span className="muted small">
                {r.dias_desde_ultima_compra ?? "—"} días sin compra
              </span>
            </div>
            <div className="pulse-client-card-bot">
              <span className="muted small">
                Compra prevista 6m:{" "}
                <strong>{r.predicho_6m != null ? fmtEuro(r.predicho_6m) : "—"}</strong>
                {r.variacio_6m_pct != null && (
                  <span className={`delta ${r.variacio_6m_pct >= 0 ? "up" : "down"}`}>
                    {r.variacio_6m_pct >= 0 ? "+" : ""}
                    {Number(r.variacio_6m_pct).toFixed(1)}%
                  </span>
                )}
              </span>
              {(r.n_prod_critical ?? 0) > 0 && (
                <span className="pulse-client-criticos">{r.n_prod_critical} críticos</span>
              )}
            </div>
            <div className="pulse-client-card-actions">
              <span
                className="ghost ghost-as-link"
                onClick={(e) => {
                  e.stopPropagation();
                  onContact?.(r.cliente_id);
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    onContact?.(r.cliente_id);
                  }
                }}
              >
                Añadir a cola
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
