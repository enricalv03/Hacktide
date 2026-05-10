import { useEffect, useState } from "react";

const ESTADO_ETIQUETA = {
  pendiente: "Pendiente",
  contactado: "Contactado",
  sin_respuesta: "Sin respuesta",
  recuperado: "Recuperado",
  perdido: "Perdido",
};

function DeltaHint({ delta }) {
  const value = Number(delta ?? 0);
  if (value > 0) return <span className="delta up">+{value} vs ayer</span>;
  if (value < 0) return <span className="delta down">{value} vs ayer</span>;
  return <span className="delta neutral">0 vs ayer</span>;
}

function Sparkline({ data = [] }) {
  const points = data.map((v) => Number(v || 0));
  const max = Math.max(1, ...points);
  return (
    <div className="sparkline" aria-hidden="true">
      {points.map((v, idx) => (
        <span
          key={`${idx}-${v}`}
          className="sparkline-bar"
          style={{ height: `${Math.max(8, Math.round((v / max) * 100))}%` }}
          title={`Día ${idx + 1}: ${v}`}
        />
      ))}
    </div>
  );
}

const SEGMENT_LABEL = {
  LEAL: "Leal",
  PROMETEDOR: "Prometedor",
  RISC: "Riesgo",
};

function SegmentChip({ value }) {
  if (!value) return <span className="tag segment-na">N/D</span>;
  const cls = `tag segment-${String(value).toLowerCase()}`;
  return <span className={cls}>{SEGMENT_LABEL[value] ?? value}</span>;
}

function fmtEuro(n) {
  const v = Number(n || 0);
  if (!Number.isFinite(v)) return "0 €";
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)} M€`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(1)} k€`;
  return `${Math.round(v)} €`;
}

export function UsuariosView({
  usuarios,
  pendientes,
  kpiHoy,
  modelVsRules,
  segmentsSummary,
  clientsTotal = 0,
  onAbrirUsuario,
  onRegistrarResultado,
  loading,
  error,
  bootWarning = "",
  bootRetrying = false,
  pendingError,
  filtros,
  onChangeFiltros,
  onRetry,
}) {
  const [savingId, setSavingId] = useState(null);
  /** valor del `<select>` por alerta (evita defaultValue + API desincronizada) */
  const [estadoSelect, setEstadoSelect] = useState({});

  useEffect(() => {
    const activos = new Set(pendientes.map((p) => String(p.alerta_id)));
    setEstadoSelect((prev) => {
      const next = { ...prev };
      let tocado = false;
      for (const k of Object.keys(next)) {
        if (!activos.has(k)) {
          delete next[k];
          tocado = true;
        }
      }
      return tocado ? next : prev;
    });
  }, [pendientes]);

  const pageSize = filtros.pageSize ?? 25;
  const page = filtros.page ?? 0;
  const totalPages = Math.max(1, Math.ceil((clientsTotal || 0) / pageSize));
  const rangeStart = clientsTotal === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(clientsTotal, (page + 1) * pageSize);

  const handleOutcomeChange = async (alertaId, clienteId, estado) => {
    if (estado === "pendiente") return;
    setSavingId(alertaId);
    try {
      await onRegistrarResultado(alertaId, estado, clienteId);
    } finally {
      setSavingId(null);
    }
  };

  return (
    <>
      <section className="panel usuarios-hero">
        <p className="muted usuarios-lede" style={{ marginTop: 0, maxWidth: "52rem", lineHeight: 1.55 }}>
          Prioriza llamadas a clientes con alerta activa, registra el resultado y revisa la cartera paginada por
          prioridad operativa (ALTA primero).
        </p>

        {bootWarning && (
          <p className="usuarios-boot-warning" role="alert">
            <strong>{bootRetrying ? "Conectando con el API…" : "Sin conexión al API."}</strong> {bootWarning}{" "}
            <span className="muted">
              Desde la raíz del repo ejecuta: <code className="inline-code">bash scripts/start_api.sh</code>
            </span>{" "}
            {onRetry && (
              <button
                type="button"
                className="ghost"
                onClick={onRetry}
                disabled={bootRetrying}
                style={{ marginLeft: "0.5rem" }}
              >
                {bootRetrying ? "Reintentando…" : "Reintentar"}
              </button>
            )}
          </p>
        )}

        {modelVsRules && (
          <details className="usuarios-details-muted">
            <summary>Model vs Rules (referencia interna)</summary>
            <div className="chart-block" style={{ marginTop: "0.75rem" }}>
              <p className="muted">
                Modelo acc: {Number(modelVsRules.model?.accuracy ?? 0).toFixed(3)} | Reglas acc:{" "}
                {Number(modelVsRules.rules?.accuracy ?? 0).toFixed(3)}
              </p>
              <p className="muted">{modelVsRules.decision_hint}</p>
            </div>
          </details>
        )}

        {segmentsSummary?.available && segmentsSummary.segments?.length > 0 && (
          <div className="kpi-grid">
            {segmentsSummary.segments.map((s) => {
              const pct = segmentsSummary.total_clientes
                ? (s.n / segmentsSummary.total_clientes) * 100
                : 0;
              return (
                <article key={s.segment} className="kpi-card">
                  <p>
                    Segmento <SegmentChip value={s.segment} />
                  </p>
                  <h4>{s.n.toLocaleString("es-ES")} clientes</h4>
                  <p className="muted small" style={{ marginTop: 4 }}>
                    {pct.toFixed(1)}% de la cartera
                  </p>
                  <p className="muted small" style={{ marginTop: 2 }}>
                    Compras 12m: <strong>{fmtEuro(s.spend_12m)}</strong> · Previsión 6m:{" "}
                    <strong>{fmtEuro(s.predicho_6m)}</strong>
                  </p>
                  <p className="muted small" style={{ marginTop: 2 }}>
                    {s.n_prod_critical.toLocaleString("es-ES")} productos críticos
                  </p>
                </article>
              );
            })}
          </div>
        )}

        {kpiHoy && (
          <div className="kpi-grid">
            <article className="kpi-card">
              <p>Alertas pendientes de gestión</p>
              <h4>{kpiHoy.pendientes_totales}</h4>
              <span className="muted small">Casos activos en cola</span>
            </article>
            <article className="kpi-card">
              <p>Gestionados hoy</p>
              <h4>{kpiHoy.gestionadas_hoy}</h4>
              <DeltaHint delta={kpiHoy.delta_gestionadas} />
              <Sparkline data={(kpiHoy.serie_7d ?? []).map((d) => d.gestionadas)} />
            </article>
            <article className="kpi-card">
              <p>Recuperados hoy</p>
              <h4>{kpiHoy.recuperados_hoy}</h4>
              <DeltaHint delta={kpiHoy.delta_recuperados} />
              <Sparkline data={(kpiHoy.serie_7d ?? []).map((d) => d.recuperados)} />
            </article>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>Clientes pendientes de atención</h3>
          <span>{pendientes.length} en vista · prioridad ALTA primero</span>
        </div>
        <p className="muted small" style={{ marginBottom: "1rem" }}>
          Al guardar un resultado distinto de «Pendiente», el caso sale de esta cola y cuenta en «Gestionados hoy».
        </p>
        {pendingError && <p className="muted">{pendingError}</p>}
        <div className="pending-strip" role="list">
          {pendientes.length === 0 && !pendingError && !bootWarning && (
            <p className="muted">No hay casos pendientes con los criterios actuales.</p>
          )}
          {pendientes.length === 0 && !pendingError && bootWarning && (
            <p className="muted">Cola de pendientes no cargada (revisa el aviso arriba o el estado del API).</p>
          )}
          {pendientes.map((p) => (
            <article key={p.alerta_id} className="pending-card" role="listitem">
              <div className="pending-card-top">
                <span
                  className={`pending-status-pill ${
                    (estadoSelect[String(p.alerta_id)] ?? "pendiente") !== "pendiente" ? "pending-status-done" : ""
                  }`}
                >
                  {ESTADO_ETIQUETA[estadoSelect[String(p.alerta_id)] ?? "pendiente"] ?? "Pendiente"}
                </span>
                <strong className="pending-cliente-id">{p.cliente_id}</strong>
                <span className={`pending-tag prioridad-${(p.prioridad || "").toLowerCase()}`}>{p.prioridad}</span>
              </div>
              <p className="muted pending-retraso">Retraso: {p.dias_desde_ultima_compra ?? "N/D"} días</p>
              <div className="pending-card-actions">
                <label className="field pending-field">
                  Resultado llamada
                  <select
                    value={estadoSelect[String(p.alerta_id)] ?? "pendiente"}
                    disabled={savingId === p.alerta_id}
                    onChange={(event) => {
                      const v = event.target.value;
                      setEstadoSelect((prev) => ({ ...prev, [String(p.alerta_id)]: v }));
                      if (v !== "pendiente") {
                        void handleOutcomeChange(p.alerta_id, p.cliente_id, v);
                      }
                    }}
                  >
                    <option value="pendiente">Pendiente</option>
                    <option value="contactado">Contactado</option>
                    <option value="sin_respuesta">Sin respuesta</option>
                    <option value="recuperado">Recuperado</option>
                    <option value="perdido">Perdido</option>
                  </select>
                </label>
                <div className="pending-btn-row">
                  <button type="button" className="ghost" onClick={() => onAbrirUsuario({ cliente_id: p.cliente_id })}>
                    Revisar
                  </button>
                  {savingId === p.alerta_id && <span className="muted small">Guardando…</span>}
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>Cartera de clientes</h3>
          <span>
            {clientsTotal} clientes · página {page + 1} de {totalPages}
          </span>
        </div>
        <p className="muted small" style={{ marginBottom: "1rem" }}>
          Lista paginada ordenada con prioridad ALTA primero (ajustable abajo).
        </p>

        <div className="filtro-grid">
          <label className="field">
            Buscar por ID o provincia
            <input
              value={filtros.search}
              onChange={(event) => onChangeFiltros({ ...filtros, search: event.target.value })}
              placeholder="Ejemplo: 1000078043 o Madrid"
            />
          </label>

          <label className="field">
            Ordenar
            <select
              value={filtros.orden}
              onChange={(event) => onChangeFiltros({ ...filtros, orden: event.target.value })}
            >
              <option value="prioridad_desc">Prioridad: alta → baja</option>
              <option value="prioridad_asc">Prioridad: baja → alta</option>
              <option value="id_asc">Cliente ID (A–Z)</option>
              <option value="semaforo">Semáforo riesgo (rojo primero)</option>
            </select>
          </label>

          <label className="field">
            Tipo
            <select
              value={filtros.estado}
              onChange={(event) => onChangeFiltros({ ...filtros, estado: event.target.value })}
            >
              <option value="">Todos</option>
              <option value="CLIENTE">Cliente</option>
              <option value="NO_CLIENTE">No cliente (sin compra)</option>
            </select>
          </label>
          <p className="muted small" style={{ gridColumn: "1 / -1", margin: "-4px 0 0", lineHeight: 1.5 }}>
            La lista sale de <strong>SQLite</strong> (no lee el Master en vivo). «Cliente» = al menos una línea de{" "}
            <strong>compra</strong> en hechos; con «Todos» ves también maestro/cartera sin compras cargadas. Tras
            actualizar el CSV: <code className="inline-code">python3 db/create_database.py</code>.
          </p>

          <label className="field">
            Prioridad alerta
            <select
              value={filtros.prioridad}
              onChange={(event) => onChangeFiltros({ ...filtros, prioridad: event.target.value })}
            >
              <option value="">Todas</option>
              <option value="ALTA">Alta</option>
              <option value="MEDIA">Media</option>
              <option value="BAJA">Baja</option>
            </select>
          </label>

          <label className="field">
            Por página
            <select
              value={String(pageSize)}
              onChange={(event) =>
                onChangeFiltros({ ...filtros, pageSize: Number(event.target.value), page: 0 })
              }
            >
              <option value="10">10</option>
              <option value="25">25</option>
              <option value="50">50</option>
            </select>
          </label>
        </div>

        <table className="tabla">
          <thead>
            <tr>
              <th>Prioridad</th>
              <th>Cliente ID</th>
              <th>Tipo</th>
              <th>Provincia</th>
              <th title="Cómo se comporta el cliente: Leal (compra constante), Prometedor (potencial de crecer) o Riesgo (caída fuerte)">
                Segmento
              </th>
              <th title="Compra esperada en los próximos 6 meses según el modelo, comparada con los 6 meses anteriores">
                Compra prevista 6 m
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={7}>Cargando clientes…</td>
              </tr>
            )}
            {error && !loading && (
              <tr>
                <td colSpan={7}>
                  {error}{" "}
                  {onRetry && (
                    <button
                      type="button"
                      className="ghost"
                      onClick={onRetry}
                      style={{ marginLeft: "0.5rem" }}
                    >
                      Reintentar
                    </button>
                  )}
                </td>
              </tr>
            )}
            {!loading &&
              !error &&
              usuarios.map((usuario) => (
                <tr key={usuario.cliente_id}>
                  <td>
                    <span className={`tag ${usuario.prioridad.toLowerCase()}`}>
                      {usuario.prioridad[0] + usuario.prioridad.slice(1).toLowerCase()}
                    </span>
                  </td>
                  <td>{usuario.cliente_id}</td>
                  <td>{usuario.estado_cliente}</td>
                  <td>{usuario.provincia_nombre}</td>
                  <td>
                    <SegmentChip value={usuario.segmento_ml} />
                    {usuario.n_prod_critical > 0 && (
                      <span className="muted small" style={{ marginLeft: "0.4rem" }}>
                        {usuario.n_prod_critical} crít.
                      </span>
                    )}
                  </td>
                  <td>
                    {usuario.predicho_6m != null ? (
                      <>
                        {fmtEuro(usuario.predicho_6m)}{" "}
                        {usuario.variacio_6m_pct != null && (
                          <span
                            className={`delta ${usuario.variacio_6m_pct >= 0 ? "up" : "down"}`}
                            title="Variación frente a los 6 meses anteriores"
                          >
                            {usuario.variacio_6m_pct >= 0 ? "+" : ""}
                            {Number(usuario.variacio_6m_pct).toFixed(1)}% vs 6m anteriores
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="muted small">—</span>
                    )}
                  </td>
                  <td>
                    <button type="button" className="ghost" onClick={() => onAbrirUsuario(usuario)}>
                      Abrir
                    </button>
                  </td>
                </tr>
              ))}
            {!loading && !error && usuarios.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  No hay resultados con estos filtros.
                </td>
              </tr>
            )}
          </tbody>
        </table>

        <div className="pagination-bar">
          <button
            type="button"
            className="ghost"
            disabled={page <= 0 || loading}
            onClick={() => onChangeFiltros({ ...filtros, page: page - 1 })}
          >
            Anterior
          </button>
          <span className="muted small">
            {clientsTotal === 0 ? "0" : `${rangeStart}–${rangeEnd}`} de {clientsTotal}
          </span>
          <button
            type="button"
            className="ghost"
            disabled={page >= totalPages - 1 || loading || clientsTotal === 0}
            onClick={() => onChangeFiltros({ ...filtros, page: page + 1 })}
          >
            Siguiente
          </button>
        </div>
      </section>
    </>
  );
}
