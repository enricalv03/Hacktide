/** Por defecto mismo origen que siempre; opcional: VITE_API_BASE en frontend/.env */
const API_BASE =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE?.trim()) ||
  "http://127.0.0.1:8000";

async function fetchJson(path, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const response = await fetch(`${API_BASE}${path}`, {
    signal: controller.signal,
    cache: "no-store",
  }).finally(() => clearTimeout(timer));
  if (!response.ok) {
    let detail = `API error ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail !== undefined) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) {
      /* ignore */
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function getClients({
  search = "",
  estado = "",
  semaforo = "",
  prioridad = "",
  sort = "prioridad_desc",
  limit = 25,
  offset = 0,
} = {}) {
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (estado) params.set("estado", estado);
  if (semaforo) params.set("semaforo", semaforo);
  if (prioridad) params.set("prioridad", prioridad);
  params.set("sort", sort);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return fetchJson(`/clients?${params.toString()}`, 30000);
}

export async function getClientById(clienteId) {
  return fetchJson(`/clients/${clienteId}`);
}

export async function getClientsPulse(limit = 3000) {
  return fetchJson(`/clients/pulse?limit=${limit}`);
}

export async function getClientProfile(clienteId) {
  return fetchJson(`/clients/${clienteId}/profile`);
}

export async function getPendingClients(limit = 8) {
  /* En cold-start con SQLite + StrictMode firing 2x, 15s puede ser justo. */
  return fetchJson(`/dashboard/pending-clients?limit=${limit}`, 30000);
}

export async function pingHealth(timeoutMs = 4000) {
  return fetchJson("/health", timeoutMs);
}

export async function getAlertsQueue({ limit = 50, prioridad = "", semaforo = "" } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (prioridad) params.set("prioridad", prioridad);
  if (semaforo) params.set("semaforo", semaforo);
  return fetchJson(`/alerts/queue?${params.toString()}`);
}

export async function getTodayKpi() {
  return fetchJson("/dashboard/kpi-today", 30000);
}

export async function getPulseStats() {
  return fetchJson("/dashboard/pulse-stats");
}

export async function getModelVsRules() {
  return fetchJson("/dashboard/model-vs-rules");
}

/** Reparto LEAL/PROMETEDOR/RISC + spend 12m / forecast 6m por segmento.
 *  Devuelve { available:false, ... } si todavía no se ha ejecutado pipeline.py. */
export async function getSegmentsSummary() {
  return fetchJson("/dashboard/segments-summary", 30000);
}

/** Datos consolidados para la pantalla Pulse (KPIs + matriz segmento×semáforo
 *  + top urgentes + candidatos de recuperación). Una sola petición → render. */
export async function getPulseBoard(topN = 12) {
  return fetchJson(`/dashboard/pulse-board?top_n=${topN}`, 30000);
}

/** Lista de clientes en una celda concreta de la matriz Segmento×Semáforo.
 *  La usa PulseView cuando el usuario clickea una celda — antes la celda
 *  filtraba sobre las listas del top N y daba 0 resultados en celdas como
 *  LEAL+VERDE; ahora consulta directamente la cartera completa. */
export async function getPulseBoardCellClients({ segment, semaforo, limit = 30 }) {
  const params = new URLSearchParams({
    segment: String(segment ?? ""),
    semaforo: String(semaforo ?? ""),
    limit: String(limit),
  });
  return fetchJson(`/dashboard/pulse-board/cell-clients?${params}`, 30000);
}

/** Información de contacto + guión sugerido para la pestaña Alertas y Contacto.
 *  El bloque `contacto` viene marcado `_demo: true` (datos placeholder hasta CRM). */
export async function getClientContact(clienteId) {
  const id = encodeURIComponent(String(clienteId));
  return fetchJson(`/clients/${id}/contact`);
}

export async function getClientOptions() {
  return fetchJson("/clients/options?limit=12000");
}

export async function getProductOptions() {
  return fetchJson("/products/options");
}

export async function getProductAnalysis(clienteId, productoId) {
  const params = new URLSearchParams({ cliente_id: clienteId, producto_id: productoId });
  return fetchJson(`/products/analysis?${params.toString()}`);
}

/** Modelo XGB pooled (Master CSV). Primera llamada puede tardar ~30–90s. */
export async function getProductXgbForecast(productoId, meses = 12) {
  const q = new URLSearchParams({ meses: String(meses) });
  const id = encodeURIComponent(String(productoId).trim());
  /* Primer arranque: carga CSV + entrena XGB puede superar 2 min */
  return fetchJson(`/products/xgb-forecast/${id}?${q.toString()}`, 300000);
}

/** Suma mensual histórica (Master) + forecast agregado por mes (todos los productos del Master). Puede tardar varios minutos la primera vez. */
export async function getProductPortfolioSummary(meses = 12, maxProductos = 500) {
  const q = new URLSearchParams({
    meses: String(meses),
    max_productos: String(maxProductos),
  });
  return fetchJson(`/products/xgb-forecast/portfolio-summary?${q.toString()}`, 600000);
}

export async function postAlertOutcome(alertaId, payload) {
  const id = encodeURIComponent(String(alertaId));
  const response = await fetch(`${API_BASE}/alerts/${id}/outcome`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`API error ${response.status}`);
  }
  return response.json();
}

export async function escalateProductCase(payload) {
  const response = await fetch(`${API_BASE}/products/escalate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`API error ${response.status}`);
  }
  return response.json();
}
