/**
 * Vista global: serie mensual total de unidades (Master) + suma de forecasts XGB por mes.
 */
function ts(ds) {
  return new Date(`${ds}T12:00:00`).getTime();
}

export function ProductPortfolioChart({ payload }) {
  if (!payload?.series) return null;

  const hist = payload.series.historical_monthly_total ?? [];
  const fut = payload.series.future_monthly_total ?? [];
  const W = 920;
  const H = 340;
  const pad = { l: 56, r: 20, t: 28, b: 52 };

  const allVals = [
    ...hist.map((p) => Number(p.unidades)),
    ...fut.map((p) => Number(p.predicho_total)),
  ];
  const maxY = Math.max(8, ...allVals, 1);

  const allTimes = [...hist.map((p) => ts(p.ds)), ...fut.map((p) => ts(p.ds))];
  let minT = Math.min(...allTimes);
  let maxT = Math.max(...allTimes);
  if (minT === maxT) maxT = minT + 86400000 * 60;

  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const xScale = (t) => pad.l + ((t - minT) / (maxT - minT)) * iw;
  const yScale = (v) => pad.t + ih - (v / maxY) * ih;

  const histPts = hist.map((p) => `${xScale(ts(p.ds))},${yScale(Number(p.unidades))}`).join(" ");
  const futPts = fut.map((p) => `${xScale(ts(p.ds))},${yScale(Number(p.predicho_total))}`).join(" ");

  let dividerX = null;
  if (hist.length && fut.length) {
    const a = ts(hist[hist.length - 1].ds);
    const b = ts(fut[0].ds);
    dividerX = xScale((a + b) / 2);
  }

  const meta = payload.totales ?? {};
  const nProd = payload.n_productos_catalogo_master ?? "—";
  const skipped = payload.n_productos_sin_forecast ?? 0;
  const processed = payload.n_productos_procesados ?? "—";

  return (
    <div className="product-portfolio-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="product-portfolio-svg" role="img" aria-label="Vista global compras y forecast">
        <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="#f8fbff" rx="8" />

        {[0, 0.25, 0.5, 0.75, 1].map((r) => {
          const v = maxY * r;
          const y = yScale(v);
          return (
            <g key={`g-${r}`}>
              <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} stroke="#e2e8f0" strokeDasharray="4 4" />
              <text x={8} y={y + 4} fontSize="9" fill="#64748b">
                {v >= 1000 ? `${Math.round(v / 1000)}k` : Math.round(v)}
              </text>
            </g>
          );
        })}

        {dividerX != null && (
          <line
            x1={dividerX}
            x2={dividerX}
            y1={pad.t}
            y2={pad.t + ih}
            stroke="#94a3b8"
            strokeWidth="1"
            strokeDasharray="6 4"
          />
        )}

        {histPts && (
          <polyline fill="none" stroke="#1e3a5f" strokeWidth="2.5" points={histPts} />
        )}
        {futPts && (
          <polyline fill="none" stroke="#16a34a" strokeWidth="2.5" strokeDasharray="8 5" points={futPts} />
        )}

        <text x={pad.l} y={18} fontSize="11" fill="#334155">
          Azul: unidades vendidas totales por mes (Master) · Verde discontinuo: suma forecast por mes (XGB, todos los productos)
        </text>

        <text x={pad.l} y={H - 12} fontSize="10" fill="#64748b">
          Catálogo Master: {nProd} productos · En esta vista: {processed} procesados · Omitidos sin modelo: {skipped}
          {meta.unidades_historicas_master != null && (
            <> · Histórico total acumulado: {Number(meta.unidades_historicas_master).toLocaleString()} uds.</>
          )}
          {meta.suma_forecast_periodo != null && (
            <> · Suma forecast ({payload.meses_futuros ?? "?"} mes): {Number(meta.suma_forecast_periodo).toLocaleString()} uds.</>
          )}
        </text>
      </svg>
      {payload.descripcion && <p className="muted small">{payload.descripcion}</p>}
    </div>
  );
}
