/**
 * Gráficos equivalentes a forecast_por_tipo.py (matplotlib): serie real,
 * predicho train/test y forecast futuro + error % en periodo test.
 */
function parseTs(ds) {
  return new Date(`${ds}T12:00:00`).getTime();
}

export function ProductXgbForecastChart({ payload }) {
  if (!payload?.series) return null;

  const W = 920;
  const H1 = 320;
  const H2 = 120;
  const gap = 8;
  const pad = { l: 54, r: 20, t: 24, b: 46 };

  const { series, metrics, corte_train_test, tipo_color: tipoCol } = payload;
  const corteTs = corte_train_test ? parseTs(corte_train_test.slice(0, 10)) : null;

  const real = series.real ?? [];
  const trainPred = series.train_pred ?? [];
  const testRows = series.test ?? [];
  const future = series.future ?? [];
  const errPct = series.test_error_pct ?? [];

  const allVals = [];
  real.forEach((p) => allVals.push(Number(p.unidades)));
  trainPred.forEach((p) => allVals.push(Number(p.valor)));
  testRows.forEach((p) => {
    allVals.push(Number(p.real));
    allVals.push(Number(p.pred));
  });
  future.forEach((p) => allVals.push(Number(p.predicho)));

  const maxY = Math.max(8, ...allVals, 1);
  const minY = 0;

  const allTs = [];
  real.forEach((p) => allTs.push(parseTs(p.ds)));
  trainPred.forEach((p) => allTs.push(parseTs(p.ds)));
  testRows.forEach((p) => allTs.push(parseTs(p.ds)));
  future.forEach((p) => allTs.push(parseTs(p.ds)));

  let minT = Math.min(...allTs);
  let maxT = Math.max(...allTs);
  if (minT === maxT) {
    maxT = minT + 86400000 * 30;
  }

  const iw = W - pad.l - pad.r;
  const ih = H1 - pad.t - pad.b;

  const xScale = (ts) => pad.l + ((ts - minT) / (maxT - minT)) * iw;
  const yScale = (v) => pad.t + ih - ((v - minY) / (maxY - minY)) * ih;

  const linePts = (rows, getY, keyDs) =>
    rows
      .map((p) => {
        const t = parseTs(p[keyDs]);
        return `${xScale(t)},${yScale(getY(p))}`;
      })
      .join(" ");

  let errMax = 10;
  errPct.forEach((e) => {
    const a = Math.abs(Number(e.pct));
    if (a > errMax) errMax = Math.ceil(a / 10) * 10;
  });

  const ih2 = H2 - pad.t - 28;
  const yErr = (pct) => pad.t + ih2 / 2 - (pct / errMax) * (ih2 / 2);

  return (
    <div className="product-xgb-wrap">
      <div className="product-xgb-metrics muted small">
        <span>
          Train R² {metrics?.train_r2 != null ? metrics.train_r2.toFixed(3) : "—"} · MAE{" "}
          {metrics?.train_mae != null ? metrics.train_mae.toFixed(1) : "—"}
        </span>
        <span>
          Test R² {metrics?.test_r2 != null ? metrics.test_r2.toFixed(3) : "—"} · MAE{" "}
          {metrics?.test_mae != null ? metrics.test_mae.toFixed(1) : "—"}
          {metrics?.test_mape_pct != null ? ` · MAPE ${metrics.test_mape_pct.toFixed(1)}%` : ""}
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H1}`} className="product-xgb-svg" role="img" aria-label="Forecast unidades mensual">
        <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="#f8fbff" rx="6" />

        {corteTs != null && (
          <line
            x1={xScale(corteTs)}
            x2={xScale(corteTs)}
            y1={pad.t}
            y2={pad.t + ih}
            stroke="#64748b"
            strokeWidth="1.5"
            strokeDasharray="5 4"
          />
        )}

        <polyline
          fill="none"
          stroke="#1e3a5f"
          strokeWidth="2.2"
          points={linePts(real, (p) => Number(p.unidades), "ds")}
        />
        <polyline
          fill="none"
          stroke="#f97316"
          strokeWidth="1.6"
          strokeDasharray="6 4"
          points={linePts(trainPred, (p) => Number(p.valor), "ds")}
        />
        <polyline
          fill="none"
          stroke="#dc2626"
          strokeWidth="2"
          strokeDasharray="5 3"
          points={linePts(testRows, (p) => Number(p.pred), "ds")}
        />
        {testRows.map((p) => (
          <circle
            key={`te-${p.ds}`}
            cx={xScale(parseTs(p.ds))}
            cy={yScale(Number(p.pred))}
            r="3.5"
            fill="#dc2626"
          />
        ))}

        <polyline
          fill="none"
          stroke={tipoCol || "#2563eb"}
          strokeWidth="2.2"
          strokeDasharray="4 4"
          points={linePts(future, (p) => Number(p.predicho), "ds")}
        />
        {future.map((p) => {
          const cx = xScale(parseTs(p.ds));
          const cy = yScale(Number(p.predicho));
          return (
            <rect key={`fu-${p.ds}`} x={cx - 4} y={cy - 4} width="8" height="8" fill={tipoCol || "#2563eb"} rx="1" />
          );
        })}

        <text x={pad.l} y={16} fontSize="11" fill="#475569">
          Azul oscuro: real · Naranja: pred. train · Rojo: pred. test · Cuadrados: forecast ({future.length} meses)
        </text>

        {[0, 0.25, 0.5, 0.75, 1].map((r) => {
          const v = minY + (maxY - minY) * r;
          const y = yScale(v);
          return (
            <g key={`g-${r}`}>
              <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} stroke="#e2e8f0" strokeDasharray="3 3" />
              <text x={6} y={y + 4} fontSize="9" fill="#64748b">
                {v >= 100 ? Math.round(v) : v.toFixed(0)}
              </text>
            </g>
          );
        })}

        <text x={pad.l} y={H1 - 10} fontSize="10" fill="#64748b">
          Corte train/test: {corte_train_test ?? "—"}
        </text>
      </svg>

      {errPct.length > 0 && (
        <svg viewBox={`0 0 ${W} ${H2}`} className="product-xgb-svg product-xgb-svg--err" role="img" aria-label="Error relativo test">
          <text x={pad.l} y={14} fontSize="11" fill="#334155">
            Error relativo en test (% vs real+1)
          </text>
          <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih2 / 2} y2={pad.t + ih2 / 2} stroke="#000" strokeWidth="1" />
          <line
            x1={pad.l}
            x2={W - pad.r}
            y1={yErr(10)}
            y2={yErr(10)}
            stroke="#94a3b8"
            strokeDasharray="3 3"
          />
          <line
            x1={pad.l}
            x2={W - pad.r}
            y1={yErr(-10)}
            y2={yErr(-10)}
            stroke="#94a3b8"
            strokeDasharray="3 3"
          />
          {errPct.map((e) => {
            const cx = xScale(parseTs(e.ds));
            const pct = Number(e.pct);
            const y0 = pad.t + ih2 / 2;
            const y1 = yErr(pct);
            const col = pct > 0 ? "#16a34a" : "#dc2626";
            return (
              <line key={e.ds} x1={cx} y1={y0} x2={cx} y2={y1} stroke={col} strokeWidth="6" strokeLinecap="round" />
            );
          })}
        </svg>
      )}
    </div>
  );
}
