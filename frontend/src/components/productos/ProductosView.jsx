import { useEffect, useMemo, useState } from "react";
import {
  escalateProductCase,
  getProductAnalysis,
  getProductPortfolioSummary,
  getProductXgbForecast,
} from "../../api/client";
import { ProductPortfolioChart } from "./ProductPortfolioChart";
import { ProductXgbForecastChart } from "./ProductXgbForecastChart";

function TrendChart({ title, data = [], limits }) {
  const maxValue = useMemo(() => {
    const points = data.map((d) => d.valor);
    if (limits) points.push(limits.high, limits.low, limits.average);
    return Math.max(1, ...points);
  }, [data, limits]);

  const bars = data.slice(-12);

  return (
    <article className="chart-block">
      <h4>{title}</h4>
      {limits && (
        <p className="muted">
          Actual: {limits.current} | Media: {limits.average} | Límite bajo: {limits.low} | Límite alto: {limits.high}
        </p>
      )}
      <div className="mini-chart">
        {bars.map((p) => (
          <div
            key={p.periodo}
            className="bar"
            style={{ height: `${Math.round((p.valor / maxValue) * 100)}%` }}
            title={`${p.periodo}: ${p.valor}`}
          />
        ))}
      </div>
    </article>
  );
}

export function ProductosView({ clientesOptions, productosOptions, onEscalationDone }) {
  const [clienteId, setClienteId] = useState("");
  const [productoId, setProductoId] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [escalationMsg, setEscalationMsg] = useState("");
  const [xgbForecast, setXgbForecast] = useState(null);
  const [xgbLoading, setXgbLoading] = useState(false);
  const [xgbError, setXgbError] = useState("");
  const [portfolio, setPortfolio] = useState(null);
  const [portfolioLoading, setPortfolioLoading] = useState(true);
  const [portfolioError, setPortfolioError] = useState("");

  useEffect(() => {
    let mounted = true;
    async function loadPortfolio() {
      setPortfolioLoading(true);
      setPortfolioError("");
      try {
        const data = await getProductPortfolioSummary(12, 500);
        if (!mounted) return;
        setPortfolio(data);
      } catch (err) {
        if (!mounted) return;
        setPortfolio(null);
        const raw = err instanceof Error ? err.message : String(err);
        const hints =
          "Master en backend/ o db/ · pip install -r backend/requirements-forecast.txt · macOS: brew install libomp.";
        if (raw.includes("aborted") || raw.includes("AbortError")) {
          setPortfolioError("Timeout al cargar la vista global (muchas referencias). Reintenta en unos segundos.");
        } else if (raw.trim()) {
          setPortfolioError(`${raw}\n\n${hints}`);
        } else {
          setPortfolioError(`Vista global no disponible. ${hints}`);
        }
      } finally {
        if (mounted) setPortfolioLoading(false);
      }
    }
    loadPortfolio();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!clienteId || !productoId) {
      setAnalysis(null);
      return;
    }
    let mounted = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const data = await getProductAnalysis(clienteId, productoId);
        if (!mounted) return;
        setAnalysis(data);
      } catch (_err) {
        if (!mounted) return;
        setError("No se pudo calcular el análisis del cliente/producto.");
      } finally {
        if (mounted) setLoading(false);
      }
    }
    load();
    return () => {
      mounted = false;
    };
  }, [clienteId, productoId]);

  useEffect(() => {
    if (!productoId) {
      setXgbForecast(null);
      setXgbError("");
      return;
    }
    let mounted = true;
    async function loadXgb() {
      setXgbLoading(true);
      setXgbError("");
      setXgbForecast(null);
      try {
        const data = await getProductXgbForecast(productoId, 12);
        if (!mounted) return;
        setXgbForecast(data);
      } catch (err) {
        if (!mounted) return;
        setXgbForecast(null);
        const raw = err instanceof Error ? err.message : String(err);
        const hints =
          "Comprueba: Master en backend/ o db/ · pip install -r backend/requirements-forecast.txt · macOS: brew install libomp · mismo Python que el venv para uvicorn.";
        if (raw.includes("aborted") || raw.includes("AbortError") || raw.includes("signal")) {
          setXgbError(
            `Tiempo de espera agotado (el primer arranque del modelo puede tardar >2 min). Reintenta o ejecuta: ./venv/bin/python backend/check_forecast_setup.py`,
          );
        } else if (raw === "Failed to fetch" || raw.includes("NetworkError")) {
          setXgbError(`No hay respuesta del API (${raw}). ¿Está uvicorn en http://127.0.0.1:8000? ${hints}`);
        } else if (raw.trim()) {
          setXgbError(`${raw}\n\n${hints}`);
        } else {
          setXgbError(`Forecast XGB no disponible. ${hints}`);
        }
      } finally {
        if (mounted) setXgbLoading(false);
      }
    }
    loadXgb();
    return () => {
      mounted = false;
    };
  }, [productoId]);

  const escalarCaso = async () => {
    if (!analysis) return;
    setEscalationMsg("");
    try {
      const payload = {
        cliente_id: analysis.cliente_id,
        producto_id: analysis.producto_id,
        prioridad: analysis.prediction.risk_state === "ROJO" ? "ALTA" : "MEDIA",
        motivo: "Escalado manual desde vista de productos",
      };
      const data = await escalateProductCase(payload);
      if (onEscalationDone) await onEscalationDone();
      setEscalationMsg(
        data.already_exists
          ? "Este caso ya estaba escalado para hoy."
          : `Caso escalado correctamente (alerta #${data.alerta_id}).`
      );
    } catch (_err) {
      setEscalationMsg("No se pudo escalar el caso.");
    }
  };

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Productos</h3>
      </div>

      <div className="chart-block product-portfolio-block">
        <div className="panel-header">
          <h4>Vista global de compras y forecast</h4>
          <span className="muted small">
            Suma mensual de unidades del Master (histórico) y suma de predicciones XGB por mes para todos los productos del
            catálogo — misma base que el script exportar_forecast_csv.py (CSV batch).
          </span>
        </div>
        {portfolioLoading && (
          <p className="muted">Generando vista de cartera (primera vez: entrena modelos y recorre productos; puede tardar varios minutos)…</p>
        )}
        {portfolioError && <p className="muted pre-wrap">{portfolioError}</p>}
        {portfolio?.available && (
          <>
            <p className="muted small">
              Última fecha en Master: {portfolio.ultima_fecha_venta_master ?? "—"} · Forecast acumulado en horizonte:{" "}
              {portfolio.meses_futuros ?? 12} meses.
            </p>
            <ProductPortfolioChart payload={portfolio} />
          </>
        )}
      </div>

      <div className="filtro-grid">
        <label className="field">
          Seleccionar cliente
          <select value={clienteId} onChange={(event) => setClienteId(event.target.value)}>
            <option value="">Seleccione un cliente</option>
            {clientesOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          Seleccionar producto
          <select value={productoId} onChange={(event) => setProductoId(event.target.value)}>
            <option value="">Seleccione un producto</option>
            {productosOptions.map((p) => (
              <option key={p.producto_id} value={p.producto_id}>
                {p.producto_id} · {p.categoria_producto}
              </option>
            ))}
          </select>
        </label>
      </div>

      {productoId && (
        <div className="chart-block product-xgb-block">
          <div className="panel-header">
            <h4>Forecast agregado del producto (XGB pooled por tipo)</h4>
            <span className="muted small">
              Misma lógica que los scripts Python: train hasta 2023, test 2024+, forecast mensual.
            </span>
          </div>
          {xgbLoading && <p className="muted">Cargando modelo y serie histórica (la primera vez puede tardar un minuto)…</p>}
          {xgbError && <p className="muted pre-wrap">{xgbError}</p>}
          {xgbForecast?.available && (
            <>
              <p className="muted small">
                Producto {xgbForecast.producto_id} · Tipo {xgbForecast.tipo} · {xgbForecast.categoria} / {xgbForecast.familia}{" "}
                · Modelo pooled {xgbForecast.modelo_pooled_productos} productos · Histórico {xgbForecast.unidades_totales_historicas?.toLocaleString?.() ?? "—"} uds.
              </p>
              <ProductXgbForecastChart payload={xgbForecast} />
            </>
          )}
        </div>
      )}

      {loading && <p className="muted">Calculando análisis predictivo...</p>}
      {error && <p className="muted">{error}</p>}

      {analysis && (
        <>
          {(analysis.prediction.should_worry || analysis.prediction.risk_state === "AMARILLO") && (
            <div className="alert-banner">
              Cliente en riesgo para este producto ({analysis.prediction.risk_state}). Recomendado: escalar y llamar.
            </div>
          )}

          <div className="kpi-grid">
            <article className="kpi-card">
              <p>Semáforo producto</p>
              <h4>{analysis.prediction.risk_state}</h4>
            </article>
            <article className="kpi-card">
              <p>Fecha esperada próxima compra</p>
              <h4>{analysis.prediction.fecha_esperada_compra ?? "N/D"}</h4>
            </article>
            <article className="kpi-card">
              <p>Días retraso</p>
              <h4>{analysis.prediction.dias_retraso ?? 0}</h4>
            </article>
          </div>

          <TrendChart
            title="Histórico del producto seleccionado (12 meses)"
            data={analysis.monthly_product}
            limits={analysis.limits}
          />

          <TrendChart title="Histórico global del cliente (todos los productos)" data={analysis.monthly_overall} />

          <div className="actions-row">
            <button type="button" className="ghost" onClick={escalarCaso}>
              Escalar a pendientes
            </button>
            {escalationMsg && <span className="muted">{escalationMsg}</span>}
          </div>
        </>
      )}
    </section>
  );
}
