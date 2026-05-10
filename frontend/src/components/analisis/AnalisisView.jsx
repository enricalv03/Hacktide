import { useEffect, useMemo, useState } from "react";
import { getProductAnalysis } from "../../api/client";
import { tendenciaUsuario } from "../../utils/tendencia";

function formatPeriodo(periodo) {
  if (!periodo || typeof periodo !== "string") return "";
  const [year, month] = periodo.split("-");
  return `${month}/${year}`;
}

function LineChart({ puntosProducto, puntosGlobal, prediccion }) {
  const width = 920;
  const height = 320;
  const padX = 52;
  const padY = 30;
  const allValues = [
    ...puntosProducto.map((p) => Number(p.valor || 0)),
    ...puntosGlobal.map((p) => Number(p.valor || 0)),
    Number(prediccion || 0),
  ];
  const max = Math.max(1, ...allValues);
  const min = Math.min(0, ...allValues);
  const range = Math.max(1, max - min);
  const totalPoints = Math.max(puntosProducto.length, 1);
  const step = totalPoints > 1 ? (width - padX * 2) / (totalPoints - 1) : 0;
  const y = (v) => height - padY - ((v - min) / range) * (height - padY * 2);
  const x = (idx) => padX + idx * step;
  const yTicks = 5;
  const yValues = Array.from({ length: yTicks + 1 }, (_, i) => min + (range * i) / yTicks);

  const productLine = puntosProducto.map((p, i) => `${x(i)},${y(Number(p.valor || 0))}`).join(" ");
  const globalLine = puntosGlobal.map((p, i) => `${x(i)},${y(Number(p.valor || 0))}`).join(" ");
  const peak = puntosProducto.reduce(
    (acc, p) => (Number(p.valor || 0) > Number(acc.valor || 0) ? p : acc),
    puntosProducto[0],
  );
  const valley = puntosProducto.reduce(
    (acc, p) => (Number(p.valor || 0) < Number(acc.valor || 0) ? p : acc),
    puntosProducto[0],
  );
  const predX = x(Math.max(0, totalPoints - 1)) + (step || 44);
  const predY = y(Number(prediccion || 0));
  const avg = puntosProducto.length
    ? puntosProducto.reduce((a, b) => a + Number(b.valor || 0), 0) / puntosProducto.length
    : 0;
  const avgY = y(avg);
  const peakIdx = puntosProducto.findIndex((p) => p.periodo === peak?.periodo);
  const valleyIdx = puntosProducto.findIndex((p) => p.periodo === valley?.periodo);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="analysis-line-chart" role="img" aria-label="Evolución compras">
      <rect x={padX} y={padY} width={width - padX * 2} height={height - padY * 2} fill="#f8fbff" />
      {yValues.map((tickValue) => (
        <g key={`y-${tickValue}`}>
          <line
            x1={padX}
            y1={y(tickValue)}
            x2={width - padX}
            y2={y(tickValue)}
            stroke="#d9e4f2"
            strokeDasharray="3 3"
          />
          <text x={8} y={y(tickValue) + 4} fontSize="10" fill="#64748b">
            {tickValue.toFixed(0)}
          </text>
        </g>
      ))}
      <line x1={padX} y1={avgY} x2={width - padX} y2={avgY} stroke="#c9ced8" strokeDasharray="4 4" />
      <polyline fill="none" stroke="#0a7f66" strokeWidth="3" points={productLine} />
      <polyline fill="none" stroke="#2563eb" strokeWidth="2" points={globalLine} opacity="0.8" />
      {puntosProducto.map((p, i) => (
        <circle key={p.periodo} cx={x(i)} cy={y(Number(p.valor || 0))} r="3.5" fill="#0a7f66" />
      ))}
      {puntosGlobal.map((p, i) => (
        <circle key={`global-${p.periodo}`} cx={x(i)} cy={y(Number(p.valor || 0))} r="2.8" fill="#2563eb" />
      ))}
      <line
        x1={x(Math.max(0, totalPoints - 1))}
        y1={y(Number(puntosProducto[puntosProducto.length - 1]?.valor || 0))}
        x2={predX}
        y2={predY}
        stroke="#ef4444"
        strokeDasharray="5 4"
      />
      <circle cx={predX} cy={predY} r="4.5" fill="#ef4444" />
      <text x={predX + 6} y={predY - 6} fontSize="11" fill="#ef4444">
        Pred: {Number(prediccion || 0).toFixed(1)}
      </text>
      {peakIdx >= 0 && (
        <>
          <circle cx={x(peakIdx)} cy={y(Number(peak.valor || 0))} r="6" fill="#16a34a" />
          <text x={x(peakIdx) + 8} y={y(Number(peak.valor || 0)) - 8} fontSize="11" fill="#166534">
            Pico
          </text>
        </>
      )}
      {valleyIdx >= 0 && (
        <>
          <circle cx={x(valleyIdx)} cy={y(Number(valley.valor || 0))} r="6" fill="#f97316" />
          <text x={x(valleyIdx) + 8} y={y(Number(valley.valor || 0)) - 8} fontSize="11" fill="#9a3412">
            Valle
          </text>
        </>
      )}
      <text x={padX} y={14} fontSize="11" fill="#334155">
        Pico: {formatPeriodo(peak.periodo)} ({Number(peak.valor || 0).toFixed(1)})
      </text>
      <text x={padX + 230} y={14} fontSize="11" fill="#334155">
        Valle: {formatPeriodo(valley.periodo)} ({Number(valley.valor || 0).toFixed(1)})
      </text>
      <text x={width - 188} y={14} fontSize="11" fill="#475569">
        Media producto: {avg.toFixed(1)}
      </text>
      {puntosProducto.map((p, i) => (
        <g key={`x-${p.periodo}`}>
          <line x1={x(i)} y1={height - padY} x2={x(i)} y2={height - padY + 4} stroke="#94a3b8" />
          {i % 2 === 0 && (
            <text x={x(i) - 16} y={height - 6} fontSize="9" fill="#64748b">
              {formatPeriodo(p.periodo)}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

function optionLabel(p) {
  const u = p.unidades_total != null ? Number(p.unidades_total) : null;
  const suf = u != null && !Number.isNaN(u) ? ` · ${u >= 100 ? Math.round(u) : u.toFixed(1)} uds.` : "";
  return `${p.producto_id}${suf}`;
}

const ML_SEGMENT_LABEL = {
  LEAL: "Leal",
  PROMETEDOR: "Prometedor",
  RISC: "Riesgo",
};

function SegmentChipML({ value }) {
  if (!value) return <span className="tag segment-na">N/D</span>;
  const cls = `tag segment-${String(value).toLowerCase()}`;
  return <span className={cls}>{ML_SEGMENT_LABEL[value] ?? value}</span>;
}

function formatEuro(n) {
  const v = Number(n || 0);
  if (!Number.isFinite(v)) return "0 €";
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(2)} M€`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(1)} k€`;
  return `${v.toFixed(0)} €`;
}

const CLIENT_SELECT_LIMIT = 500;

export function AnalisisView({ usuario, clientesOptions = [], onSelectCliente, onContactarCliente }) {
  const [productoId, setProductoId] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [analysisError, setAnalysisError] = useState("");
  const [clienteBusqueda, setClienteBusqueda] = useState("");

  const productosCliente = useMemo(() => {
    const raw = usuario?.productos_cliente;
    return Array.isArray(raw) ? raw : [];
  }, [usuario?.cliente_id, usuario?.productos_cliente]);

  const clientesFiltrados = useMemo(() => {
    const base = Array.isArray(clientesOptions) ? clientesOptions : [];
    const q = clienteBusqueda.trim().toLowerCase();
    const hit = q ? base.filter((id) => String(id).toLowerCase().includes(q)) : base;
    return hit.slice(0, CLIENT_SELECT_LIMIT);
  }, [clientesOptions, clienteBusqueda]);

  useEffect(() => {
    if (!usuario?.cliente_id) return;
    if (!productosCliente.length) {
      setProductoId("");
      return;
    }
    const ids = new Set(productosCliente.map((p) => p.producto_id));
    if (!productoId || !ids.has(productoId)) {
      setProductoId(productosCliente[0].producto_id);
    }
  }, [usuario?.cliente_id, productosCliente, productoId]);

  useEffect(() => {
    let mounted = true;
    async function loadAnalysis() {
      if (!usuario?.cliente_id || !productoId) return;
      setAnalysisError("");
      try {
        const data = await getProductAnalysis(usuario.cliente_id, productoId);
        if (!mounted) return;
        setAnalysis(data);
      } catch (_err) {
        if (!mounted) return;
        setAnalysis(null);
        setAnalysisError("No se pudo cargar el análisis por producto.");
      }
    }
    loadAnalysis();
    return () => {
      mounted = false;
    };
  }, [usuario?.cliente_id, productoId]);

  const serie = usuario?.compras_mensuales ?? [];
  const serieProducto = analysis?.monthly_product ?? [];
  const serieGlobal = analysis?.monthly_overall ?? [];

  const { puntosProducto, puntosGlobal } = useMemo(() => {
    const periodos = Array.from(
      new Set([...serieProducto.map((p) => p.periodo), ...serieGlobal.map((p) => p.periodo)]),
    ).sort();
    const mapProducto = new Map(serieProducto.map((p) => [p.periodo, Number(p.valor || 0)]));
    const mapGlobal = new Map(serieGlobal.map((p) => [p.periodo, Number(p.valor || 0)]));
    return {
      puntosProducto: periodos.map((periodo) => ({ periodo, valor: mapProducto.get(periodo) ?? 0 })),
      puntosGlobal: periodos.map((periodo) => ({ periodo, valor: mapGlobal.get(periodo) ?? 0 })),
    };
  }, [serieProducto, serieGlobal]);

  const prediccion = useMemo(() => {
    if (!puntosProducto.length) return 0;
    const valores = puntosProducto.map((p) => Number(p.valor || 0));
    const last3 = valores.slice(-3);
    const base = last3.reduce((a, b) => a + b, 0) / Math.max(1, last3.length);
    const trend = valores.length >= 2 ? valores[valores.length - 1] - valores[valores.length - 2] : 0;
    return Math.max(0, base + 0.5 * trend);
  }, [puntosProducto]);
  const sumaProducto = puntosProducto.reduce((acc, p) => acc + Number(p.valor || 0), 0);
  const sumaGlobal = puntosGlobal.reduce((acc, p) => acc + Number(p.valor || 0), 0);
  const pesoProducto = sumaGlobal > 0 ? (sumaProducto / sumaGlobal) * 100 : 0;

  const tendencia = usuario ? tendenciaUsuario(serie) : "";
  const ultima = serie[serie.length - 1] ?? 0;
  const primera = serie[0] ?? 0;

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Análisis por cliente</h3>
      </div>

      <div className="filtro-grid" style={{ marginBottom: 16 }}>
        <label className="field">
          Buscar cliente (ID)
          <input
            type="search"
            value={clienteBusqueda}
            onChange={(e) => setClienteBusqueda(e.target.value)}
            placeholder="Filtra la lista…"
            autoComplete="off"
          />
        </label>
        <label className="field">
          Cliente
          <select
            value={usuario?.cliente_id ?? ""}
            onChange={(e) => onSelectCliente?.(e.target.value)}
          >
            <option value="">— Elige un cliente —</option>
            {clientesFiltrados.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!clienteBusqueda.trim() && clientesOptions.length > CLIENT_SELECT_LIMIT && (
        <p className="muted" style={{ marginTop: 0, marginBottom: 12 }}>
          Mostrando los primeros {CLIENT_SELECT_LIMIT} IDs. Escribe en el buscador para acotar.
        </p>
      )}
      {clienteBusqueda.trim() && clientesFiltrados.length >= CLIENT_SELECT_LIMIT && (
        <p className="muted" style={{ marginTop: 0, marginBottom: 12 }}>
          Mostrando {CLIENT_SELECT_LIMIT} coincidencias como máximo; refina el filtro si no ves el ID.
        </p>
      )}

      {!usuario && (
        <p className="muted">Selecciona un cliente arriba o abre uno desde la pestaña Usuarios o Pulse.</p>
      )}

      {usuario && (
        <>
          <div className="panel-header" style={{ marginTop: 8, alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <div>
              <h3 style={{ margin: 0 }}>Cliente {usuario.cliente_id}</h3>
              <span className="muted small">
                {usuario.provincia_nombre ?? "—"} · {usuario.estado_cliente ?? "—"} · perfil {usuario.perfil_cliente ?? "—"}
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="primary-btn"
                onClick={() => onContactarCliente?.(usuario.cliente_id)}
              >
                Enviar a Alertas y Contacto
              </button>
            </div>
          </div>

          <div className="kpi-grid">
            <article className="kpi-card">
              <p>Compras actuales</p>
              <h4>{ultima} uds.</h4>
            </article>
            <article className="kpi-card">
              <p>Variación 6 meses</p>
              <h4>{ultima - primera} uds.</h4>
            </article>
            <article className="kpi-card">
              <p>Perfil</p>
              <h4>{usuario.perfil_cliente ?? "SIN_CLASIFICAR"}</h4>
            </article>
            <article className="kpi-card">
              <p>Estado</p>
              <h4>{usuario.estado_cliente ?? "N/D"}</h4>
            </article>
            <article className="kpi-card">
              <p>Tendencia</p>
              <h4>{tendencia}</h4>
            </article>
            {usuario.ml?.segmento_ml && (
              <article className="kpi-card">
                <p>
                  Segmento ML <SegmentChipML value={usuario.ml.segmento_ml} />
                </p>
                <h4>
                  {usuario.ml.predicho_6m != null
                    ? `${formatEuro(usuario.ml.predicho_6m)} · 6m`
                    : "—"}
                </h4>
                {usuario.ml.variacio_6m_pct != null && (
                  <span
                    className={`delta ${usuario.ml.variacio_6m_pct >= 0 ? "up" : "down"}`}
                  >
                    {usuario.ml.variacio_6m_pct >= 0 ? "+" : ""}
                    {Number(usuario.ml.variacio_6m_pct).toFixed(1)}% vs 6m anteriores
                  </span>
                )}
                {(usuario.ml.n_prod_critical ?? 0) +
                  (usuario.ml.n_prod_warning ?? 0) >
                  0 && (
                  <span className="muted small">
                    {usuario.ml.n_prod_critical ?? 0} críticos ·{" "}
                    {usuario.ml.n_prod_warning ?? 0} aviso
                  </span>
                )}
              </article>
            )}
          </div>

          {Array.isArray(usuario.ml?.productos_riesgo) &&
            usuario.ml.productos_riesgo.length > 0 && (
              <div className="chart-block" style={{ marginTop: 12 }}>
                <div className="panel-header">
                  <h4>Productos en riesgo (modelo)</h4>
                  <span className="muted small">
                    Top {usuario.ml.productos_riesgo.length} por gravedad · pipeline{" "}
                    {usuario.ml.fecha_pipeline ?? ""}
                  </span>
                </div>
                <table className="tabla">
                  <thead>
                    <tr>
                      <th>Gravedad</th>
                      <th>Producto</th>
                      <th>Familia</th>
                      <th>Tipo</th>
                      <th>Ventas 12m</th>
                      <th>Días inactivo</th>
                      <th>Motivo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usuario.ml.productos_riesgo.map((p) => (
                      <tr key={`${p.producto_id}-${p.gravetat}`}>
                        <td>
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
                          </span>
                        </td>
                        <td>{p.producto_id}</td>
                        <td>{p.familia ?? "—"}</td>
                        <td>{p.tipus ?? "—"}</td>
                        <td>{Number(p.vendes_12m || 0).toFixed(2)}</td>
                        <td>{p.dies_inactiu ?? "—"}</td>
                        <td className="muted small" style={{ maxWidth: 360 }}>
                          {p.motius ?? ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

          <div className="chart-block">
        <div className="panel-header">
          <h4>Evolución temporal (producto vs global)</h4>
          <label className="field">
            Producto (compras de este cliente)
            <select
              value={productoId}
              onChange={(event) => setProductoId(event.target.value)}
              disabled={!productosCliente.length}
            >
              {productosCliente.map((p) => (
                <option key={p.producto_id} value={p.producto_id}>
                  {optionLabel(p)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {analysisError && <p className="muted">{analysisError}</p>}
        {!productosCliente.length && !analysisError && (
          <p className="muted">No hay productos con compras registradas para este cliente.</p>
        )}
        {!analysisError && productosCliente.length > 0 && puntosProducto.length >= 2 && (
          <>
            <div className="chart-legend chart-legend--analisis">
              <span className="chart-legend-item">
                <span className="chart-legend-swatch" style={{ background: "#0a7f66" }} />
                <strong>Compras de este producto</strong> (mensuales, en uds.)
              </span>
              <span className="chart-legend-item">
                <span className="chart-legend-swatch" style={{ background: "#2563eb" }} />
                <strong>Compras totales del cliente</strong> (todos los productos juntos)
              </span>
              <span className="chart-legend-item">
                <span
                  className="chart-legend-swatch"
                  style={{
                    background: "#ef4444",
                    border: "2px dashed #ef4444",
                    height: 0,
                    marginTop: 6,
                    width: 18,
                  }}
                />
                <strong>Previsión del próximo mes</strong> (modelo)
              </span>
            </div>
            <LineChart puntosProducto={puntosProducto} puntosGlobal={puntosGlobal} prediccion={prediccion} />
            <p className="muted small" style={{ marginTop: 10, lineHeight: 1.6 }}>
              En el periodo mostrado, este cliente compró{" "}
              <strong>{sumaProducto.toFixed(0)} uds</strong> de este producto y{" "}
              <strong>{sumaGlobal.toFixed(0)} uds</strong> en total entre todos los productos —
              este producto representa el{" "}
              <strong>{pesoProducto.toFixed(1)}%</strong> de sus compras.
              {Number.isFinite(prediccion) && prediccion > 0 && (
                <>
                  {" "}
                  La proyección para el próximo mes es de{" "}
                  <strong>{Number(prediccion).toFixed(0)} uds</strong>.
                </>
              )}
            </p>
          </>
        )}
        {!analysisError && productosCliente.length > 0 && puntosProducto.length === 1 && (
          <div className="chart-block" style={{ marginTop: 8 }}>
            <p className="muted" style={{ lineHeight: 1.55, marginBottom: 12 }}>
              Solo hay <strong>un mes</strong> distinto en la serie mensual usada para este producto (ventas agregadas por
              mes). Para dibujar una línea temporal comparativa hace falta <strong>al menos dos meses</strong> con datos en
              esa vista.
            </p>
            <p className="muted" style={{ lineHeight: 1.55, marginBottom: 12 }}>
              Eso no contradice la <strong>prioridad ALTA</strong> del cliente: las alertas usan otras señales (retraso
              respecto a la próxima compra esperada, caída de frecuencia, brecha vs potencial, etc.) y no exigen varios
              meses de histórico <em>por producto</em> en este gráfico.
            </p>
            <p className="muted">
              Mes: {formatPeriodo(puntosProducto[0]?.periodo)} · Unidades este producto:{" "}
              {Number(puntosProducto[0]?.valor ?? 0).toFixed(1)} · Mismo mes (total cliente):{" "}
              {Number(puntosGlobal.find((g) => g.periodo === puntosProducto[0]?.periodo)?.valor ?? 0).toFixed(1)}
            </p>
          </div>
        )}
        {!analysisError && productosCliente.length > 0 && puntosProducto.length === 0 && (
          <p className="muted" style={{ lineHeight: 1.55 }}>
            No hay puntos en la serie mensual para este producto frente al total del cliente (datos aún no cargados o sin
            solape de periodos). La prioridad del listado puede seguir siendo alta por las reglas de alertas. Prueba otro
            producto del desplegable o revisa que las ventas estén en la base.
          </p>
        )}
          </div>
        </>
      )}
    </section>
  );
}
