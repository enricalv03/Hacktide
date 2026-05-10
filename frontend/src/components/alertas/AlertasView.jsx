import { useEffect, useMemo, useRef, useState } from "react";
import {
  getAlertsQueue,
  getClientContact,
  postAlertOutcome,
} from "../../api/client";

/* ─── helpers ──────────────────────────────────────────────────────────────── */

const SEGMENT_LABEL = {
  LEAL: "Leal",
  PROMETEDOR: "Prometedor",
  RISC: "Riesgo",
};

const STATUS_LABEL = {
  pendiente: "Pendiente",
  contactado: "Contactado",
  recuperado: "Recuperado",
  perdido: "Perdido",
};

const STATUS_TONE = {
  pendiente: "media",
  contactado: "baja",
  recuperado: "baja",
  perdido: "alta",
};

function SegmentChipSmall({ value }) {
  if (!value) return <span className="tag segment-na">Sin segmento</span>;
  return (
    <span className={`tag segment-${String(value).toLowerCase()}`}>
      {SEGMENT_LABEL[value] ?? value}
    </span>
  );
}

function fmtEuro(n) {
  const v = Number(n || 0);
  if (!Number.isFinite(v)) return "0 €";
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(2)} M€`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(1)} k€`;
  return `${Math.round(v)} €`;
}

/* ─── tarjeta de contacto por cliente en la cola ───────────────────────────── */

function CallCard({ item, onRemove, onMarkStatus }) {
  const [contact, setContact] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setErr("");
    getClientContact(item.clienteId)
      .then((data) => mounted && setContact(data))
      .catch(() => {
        if (!mounted) return;
        setContact(null);
        setErr("No se pudo cargar la información de contacto.");
      })
      .finally(() => mounted && setLoading(false));
    return () => {
      mounted = false;
    };
  }, [item.clienteId]);

  const copy = (txt) => txt && navigator.clipboard?.writeText(txt);

  const status = item.status ?? "pendiente";
  const statusTone = STATUS_TONE[status] ?? "baja";

  const expanded = status === "pendiente"; // colapsa los ya gestionados para enfocar lo activo

  return (
    <article className={`call-card call-card--${status}`}>
      <header className="call-card-head">
        <div className="call-card-head-main">
          <span className="muted small" style={{ textTransform: "uppercase", letterSpacing: "0.1em" }}>
            {contact?.contacto?.contacto_principal ?? "Cliente"}
          </span>
          <h3 className="call-card-title">
            {item.clienteId}{" "}
            <span className="muted">
              · {contact?.provincia_nombre ?? "—"}
            </span>
          </h3>
          <div className="call-card-pills">
            <span className={`tag ${statusTone}`}>{STATUS_LABEL[status]}</span>
            <SegmentChipSmall value={contact?.ml?.segmento_ml} />
            {contact?.semaforo_riesgo && (
              <span
                className={`tag ${
                  contact.semaforo_riesgo === "ROJO"
                    ? "alta"
                    : contact.semaforo_riesgo === "AMARILLO"
                    ? "media"
                    : "baja"
                }`}
              >
                Semáforo {contact.semaforo_riesgo.toLowerCase()}
              </span>
            )}
            {contact?.dias_desde_ultima_compra != null && (
              <span className="tag">
                {contact.dias_desde_ultima_compra} días sin compra
              </span>
            )}
          </div>
        </div>
        <div className="call-card-head-actions">
          {status === "pendiente" && (
            <>
              <button
                type="button"
                className="primary-btn"
                onClick={() => onMarkStatus(item.clienteId, "contactado")}
                title="Marcar como contactado"
              >
                Marcar contactado
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => onMarkStatus(item.clienteId, "recuperado")}
              >
                Recuperado
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => onMarkStatus(item.clienteId, "perdido")}
              >
                Perdido
              </button>
            </>
          )}
          {status !== "pendiente" && (
            <button
              type="button"
              className="ghost"
              onClick={() => onMarkStatus(item.clienteId, "pendiente")}
            >
              Reabrir
            </button>
          )}
          <button
            type="button"
            className="ghost-as-link"
            onClick={() => onRemove(item.clienteId)}
            style={{ marginLeft: 6 }}
          >
            Quitar
          </button>
        </div>
      </header>

      {err && <p className="muted">{err}</p>}
      {loading && <p className="muted small">Cargando contacto…</p>}

      {expanded && contact && (
        <div className="contact-grid call-card-grid">
          <div className="contact-block">
            <p className="muted small" style={{ margin: 0 }}>Teléfono móvil</p>
            <p className="contact-value">
              {contact.contacto?.telefono_movil ?? "—"}
              <button
                type="button"
                className="ghost-as-link"
                onClick={() => copy(contact.contacto?.telefono_movil)}
              >
                Copiar
              </button>
            </p>
          </div>
          <div className="contact-block">
            <p className="muted small" style={{ margin: 0 }}>Teléfono fijo</p>
            <p className="contact-value">
              {contact.contacto?.telefono_fijo ?? "—"}
              <button
                type="button"
                className="ghost-as-link"
                onClick={() => copy(contact.contacto?.telefono_fijo)}
              >
                Copiar
              </button>
            </p>
          </div>
          <div className="contact-block">
            <p className="muted small" style={{ margin: 0 }}>Email</p>
            <p className="contact-value">
              {contact.contacto?.email ?? "—"}
              <button
                type="button"
                className="ghost-as-link"
                onClick={() => copy(contact.contacto?.email)}
              >
                Copiar
              </button>
            </p>
          </div>
          <div className="contact-block">
            <p className="muted small" style={{ margin: 0 }}>Compra prevista 6 m</p>
            <p className="contact-value">
              {contact.ml?.predicho_6m != null ? fmtEuro(contact.ml.predicho_6m) : "—"}
              {contact.ml?.variacio_6m_pct != null && (
                <span className={`delta ${contact.ml.variacio_6m_pct >= 0 ? "up" : "down"}`}>
                  {contact.ml.variacio_6m_pct >= 0 ? "+" : ""}
                  {Number(contact.ml.variacio_6m_pct).toFixed(1)}% vs 6m anteriores
                </span>
              )}
            </p>
          </div>

          <div className="contact-block contact-block--wide">
            <p className="muted small" style={{ margin: 0 }}>Guion sugerido</p>
            <p className="contact-script">{contact.guion_sugerido ?? "—"}</p>
          </div>

          {contact.productos_riesgo_top?.length > 0 && (
            <div className="contact-block contact-block--wide">
              <p className="muted small" style={{ margin: 0 }}>Productos a tratar</p>
              <ul className="contact-products">
                {contact.productos_riesgo_top.slice(0, 4).map((p) => (
                  <li key={p.producto_id}>
                    <span
                      className={`tag ${
                        p.gravetat === "CRITICAL"
                          ? "alta"
                          : p.gravetat === "WARNING"
                          ? "media"
                          : "baja"
                      }`}
                    >
                      {p.gravetat}
                    </span>{" "}
                    <strong>{p.producto_id}</strong> · {p.familia ?? "—"} ·{" "}
                    {Number(p.vendes_12m || 0).toFixed(0)} uds. 12m · {p.dies_inactiu ?? "—"} días
                    inactivo
                  </li>
                ))}
              </ul>
            </div>
          )}

          {contact.contacto?._demo && (
            <p className="muted small contact-demo-hint">
              Datos de teléfono y email son <strong>placeholder</strong> deterministas (basados en
              el ID de cliente). Sustituir por integración con CRM real cuando esté disponible.
            </p>
          )}
        </div>
      )}

      {!expanded && contact && (
        <p className="muted small" style={{ margin: "8px 0 0" }}>
          Estado <strong>{STATUS_LABEL[status]}</strong> · gestionado el{" "}
          {item.addedAt ? new Date(item.addedAt).toLocaleDateString("es-ES") : "—"}.
          {item.notes && <> Notas: {item.notes}.</>}
        </p>
      )}
    </article>
  );
}

/* ─── tabla auxiliar (cola completa del sistema, plegada) ──────────────────── */

function SystemQueueTable({ onAdd }) {
  const [prioridad, setPrioridad] = useState("");
  const [semaforo, setSemaforo] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getAlertsQueue({ limit: 80, prioridad, semaforo });
      setRows(data ?? []);
    } catch (_err) {
      setError("No se pudo cargar la cola del sistema.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prioridad, semaforo]);

  return (
    <>
      <div className="filtro-grid" style={{ marginTop: 8 }}>
        <label className="field">
          Prioridad
          <select value={prioridad} onChange={(e) => setPrioridad(e.target.value)}>
            <option value="">Todas</option>
            <option value="ALTA">Alta</option>
            <option value="MEDIA">Media</option>
            <option value="BAJA">Baja</option>
          </select>
        </label>
        <label className="field">
          Semáforo
          <select value={semaforo} onChange={(e) => setSemaforo(e.target.value)}>
            <option value="">Todos</option>
            <option value="ROJO">Rojo</option>
            <option value="AMARILLO">Amarillo</option>
            <option value="VERDE">Verde</option>
          </select>
        </label>
      </div>
      {error && <p className="muted">{error}</p>}
      <table className="tabla">
        <thead>
          <tr>
            <th>Prioridad</th>
            <th>Cliente</th>
            <th>Producto</th>
            <th>Riesgo</th>
            <th>Motivo</th>
            <th>Acción</th>
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr>
              <td colSpan={6}>Cargando cola del sistema…</td>
            </tr>
          )}
          {!loading &&
            rows.map((r) => (
              <tr key={r.alerta_id}>
                <td>
                  <span className={`tag ${r.prioridad.toLowerCase()}`}>{r.prioridad}</span>
                </td>
                <td>{r.cliente_id}</td>
                <td>{r.producto_id}</td>
                <td>{r.semaforo_riesgo ?? "N/D"}</td>
                <td>{r.motivo}</td>
                <td>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => onAdd(r.cliente_id)}
                  >
                    Añadir a cola
                  </button>
                </td>
              </tr>
            ))}
          {!loading && rows.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                Sin alertas con estos filtros.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

/* ─── componente principal ─────────────────────────────────────────────────── */

export function AlertasView({ preselectClienteId, onClearPreselect, queue }) {
  const { items, add, remove, markStatus, clearAll } = queue ?? {
    items: [],
    add: () => {},
    remove: () => {},
    markStatus: () => {},
    clearAll: () => {},
  };

  const [showSystemQueue, setShowSystemQueue] = useState(false);
  const focusRef = useRef(null);

  /* Si hay cliente preseleccionado y no está aún en la cola, lo añadimos.
     Lo gestionamos aquí (no en App) para evitar effects redundantes. */
  useEffect(() => {
    if (!preselectClienteId) return;
    const cid = String(preselectClienteId);
    const exists = items.some((it) => it.clienteId === cid);
    if (!exists) add(cid);
    if (focusRef.current) {
      focusRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    onClearPreselect?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselectClienteId]);

  /* También intentamos persistir el resultado en `seguimiento_alerta` cuando
     el usuario marca contactado/recuperado/perdido. Si no hay alerta_id (la
     cola es local), simplemente actualizamos localStorage. */
  const handleMarkStatus = async (clienteId, status) => {
    markStatus(clienteId, status);
    // Si quisiéramos persistir contra el sistema necesitaríamos el alerta_id.
    // Lo dejamos como mejora futura: la cola personal no se mezcla con la del
    // sistema todavía.
    void postAlertOutcome; // suppress unused import warning
  };

  const stats = useMemo(() => {
    const acc = { pendiente: 0, contactado: 0, recuperado: 0, perdido: 0 };
    items.forEach((it) => {
      acc[it.status] = (acc[it.status] ?? 0) + 1;
    });
    return acc;
  }, [items]);

  /* Pendientes primero, luego gestionados. */
  const ordered = useMemo(() => {
    const order = { pendiente: 0, contactado: 1, recuperado: 2, perdido: 3 };
    return [...items].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
  }, [items]);

  return (
    <div className="alerts-layout-v2" ref={focusRef}>
      <section className="panel">
        <header className="panel-header" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h3 style={{ margin: 0 }}>Mi cola de llamadas</h3>
            <p className="muted small" style={{ margin: "4px 0 0", maxWidth: "60ch" }}>
              Aquí aparecen los clientes que tú añades manualmente desde Análisis o Resumen
              ejecutivo (botón <strong>«Enviar a Alertas y Contacto»</strong>). Cada tarjeta
              incluye el contacto sugerido y un guion para la llamada.
            </p>
          </div>
          <div className="alerts-stats">
            <span className="alerts-stat">
              <strong>{stats.pendiente}</strong>
              <span className="muted small">Pendientes</span>
            </span>
            <span className="alerts-stat">
              <strong>{stats.contactado}</strong>
              <span className="muted small">Contactados</span>
            </span>
            <span className="alerts-stat">
              <strong>{stats.recuperado}</strong>
              <span className="muted small">Recuperados</span>
            </span>
            <span className="alerts-stat">
              <strong>{stats.perdido}</strong>
              <span className="muted small">Perdidos</span>
            </span>
            {items.length > 0 && (
              <button
                type="button"
                className="ghost-as-link"
                onClick={() => {
                  if (window.confirm("¿Vaciar toda la cola de llamadas?")) clearAll();
                }}
              >
                Vaciar cola
              </button>
            )}
          </div>
        </header>

        {items.length === 0 && (
          <div className="alerts-empty">
            <p className="alerts-empty-title">Tu cola está vacía</p>
            <p className="muted">
              Para empezar, añade clientes desde:
            </p>
            <ul className="alerts-empty-list">
              <li>
                <strong>Resumen ejecutivo</strong> — pulsa «Añadir a mi cola de llamadas» en
                cualquier cliente.
              </li>
              <li>
                <strong>Análisis</strong> — abre la ficha de un cliente y pulsa «Enviar a Alertas y
                Contacto».
              </li>
              <li>
                Más abajo, puedes desplegar la <strong>cola completa del sistema</strong> y añadir
                desde allí.
              </li>
            </ul>
          </div>
        )}

        {ordered.length > 0 && (
          <div className="call-list">
            {ordered.map((it) => (
              <CallCard
                key={it.clienteId}
                item={it}
                onRemove={remove}
                onMarkStatus={handleMarkStatus}
              />
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <header
          className="panel-header"
          style={{ alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}
        >
          <div>
            <h3 style={{ margin: 0 }}>Cola completa del sistema</h3>
            <p className="muted small" style={{ margin: "4px 0 0", maxWidth: "60ch" }}>
              Listado automático de alertas pendientes calculadas por el modelo. Útil si quieres
              llenar tu cola eligiendo los casos más urgentes.
            </p>
          </div>
          <button
            type="button"
            className="ghost"
            onClick={() => setShowSystemQueue((v) => !v)}
            aria-expanded={showSystemQueue}
          >
            {showSystemQueue ? "Ocultar" : "Mostrar cola del sistema"}
          </button>
        </header>
        {showSystemQueue && <SystemQueueTable onAdd={add} />}
      </section>
    </div>
  );
}
