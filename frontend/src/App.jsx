import { useEffect, useMemo, useState } from "react";
import { Sidebar } from "./components/layout/Sidebar";
import { UsuariosView } from "./components/usuarios/UsuariosView";
import { ProductosView } from "./components/productos/ProductosView";
import { AnalisisView } from "./components/analisis/AnalisisView";
import { AlertasView } from "./components/alertas/AlertasView";
import { PulseView } from "./components/pulse/PulseView";
import {
  getClientById,
  getClientOptions,
  getClients,
  getModelVsRules,
  getPendingClients,
  postAlertOutcome,
  getSegmentsSummary,
  getTodayKpi,
  getProductOptions,
} from "./api/client";
import { useCallQueue } from "./hooks/useCallQueue";
import "./styles/theme.css";

const TABS = {
  pulse: "pulse",
  usuarios: "usuarios",
  productos: "productos",
  analisis: "analisis",
  alertas: "alertas",
};

export default function App() {
  const [tabActiva, setTabActiva] = useState(TABS.usuarios);
  const [usuarios, setUsuarios] = useState([]);
  const [clientesOptions, setClientesOptions] = useState([]);
  const [productosOptions, setProductosOptions] = useState([]);
  const [pendientes, setPendientes] = useState([]);
  const [kpiHoy, setKpiHoy] = useState(null);
  const [modelVsRules, setModelVsRules] = useState(null);
  const [segmentsSummary, setSegmentsSummary] = useState(null);
  const [usuarioSeleccionado, setUsuarioSeleccionado] = useState(null);
  const [loading, setLoading] = useState(true);
  /** Solo fallos de GET /clients (cartera). No mezclar con KPI/pendientes. */
  const [errorCartera, setErrorCartera] = useState("");
  /** Fallos al cargar pendientes/KPI/modelo (no bloquea la tabla de clientes). */
  const [panelBootWarning, setPanelBootWarning] = useState("");
  const [pendingError, setPendingError] = useState("");
  const [filtros, setFiltros] = useState({
    search: "",
    estado: "CLIENTE",
    prioridad: "",
    orden: "prioridad_desc",
    page: 0,
    pageSize: 25,
  });
  const [clientsTotal, setClientsTotal] = useState(0);

  /** Reset página al cambiar criterios de cartera (no al cambiar solo `page`). */
  const setFiltrosUsuarios = (patch) => {
    setFiltros((prev) => {
      const next = typeof patch === "function" ? patch(prev) : { ...prev, ...patch };
      const resetPageKeys = ["search", "estado", "prioridad", "orden", "pageSize"];
      if (resetPageKeys.some((k) => next[k] !== prev[k])) {
        return { ...next, page: 0 };
      }
      return next;
    });
  };

  const tituloSeccion = useMemo(() => {
    if (tabActiva === TABS.pulse) return "Visión ejecutiva";
    if (tabActiva === TABS.productos) return "Pulso por producto";
    if (tabActiva === TABS.analisis) return "Análisis por cliente";
    if (tabActiva === TABS.alertas) return "Alertas y Contacto";
    return "Cartera de clientes";
  }, [tabActiva]);

  const subtituloSeccion = useMemo(() => {
    if (tabActiva === TABS.pulse) return "Resumen del riesgo y la oportunidad: matriz de cartera, alertas y candidatos a recuperación.";
    if (tabActiva === TABS.productos) return "Tendencia y forecast por familia y producto técnico/uso diario.";
    if (tabActiva === TABS.analisis) return "Compras, productos y predicciones por cliente; envíalo a la cola de llamadas con un clic.";
    if (tabActiva === TABS.alertas) return "Cliente activo, datos de contacto y cola de llamadas priorizada.";
    return "Lista priorizada de clientes con segmento ML, forecast 6m y semáforo de riesgo.";
  }, [tabActiva]);

  const refreshPending = async () => {
    const [pRes, kRes] = await Promise.allSettled([getPendingClients(8), getTodayKpi()]);
    if (pRes.status === "fulfilled") setPendientes(Array.isArray(pRes.value) ? pRes.value : []);
    else setPendientes([]);
    if (kRes.status === "fulfilled") setKpiHoy(kRes.value ?? null);
    else setKpiHoy(null);
  };

  /** nonce: incrementar para forzar un reintento manual de la cartera y del bootstrap. */
  const [retryNonce, setRetryNonce] = useState(0);
  const [bootRetrying, setBootRetrying] = useState(false);

  const handleRetryBoot = () => {
    setRetryNonce((n) => n + 1);
  };

  /** Cartera (`/clients`). Reintenta automáticamente con backoff (3 intentos) si la API
   *  no responde — recupera al levantar la API sin pedir F5.
   *
   *  UX: solo muestra el spinner cuando la tabla está vacía (primer arranque).
   *  En cambios de filtro/paginación mantenemos las filas previas para evitar el
   *  flash «Cargando clientes…» que daba sensación de lentitud. Search se
   *  debouncéa 300ms para no machacar la API mientras el usuario teclea. */
  useEffect(() => {
    let mounted = true;
    let timer = null;
    let retryTimer = null;
    const isFirstLoad = usuarios.length === 0;
    const debounceMs = filtros.search ? 300 : 0;

    async function load(triesLeft) {
      if (!mounted) return;
      if (isFirstLoad) setLoading(true);
      setErrorCartera("");
      try {
        const data = await getClients({
          search: filtros.search,
          estado: filtros.estado,
          prioridad: filtros.prioridad,
          sort: filtros.orden,
          limit: filtros.pageSize,
          offset: filtros.page * filtros.pageSize,
        });
        if (!mounted) return;
        setUsuarios(data.items ?? []);
        setClientsTotal(Number(data.total) || 0);
        setLoading(false);
      } catch (err) {
        if (!mounted) return;
        if (triesLeft > 0) {
          if (isFirstLoad) setLoading(true);
          retryTimer = setTimeout(() => load(triesLeft - 1), 2000);
          return;
        }
        setErrorCartera("No se pudo cargar la lista de clientes desde backend.");
        setLoading(false);
      }
    }

    timer = setTimeout(() => load(2), debounceMs);
    return () => {
      mounted = false;
      if (timer) clearTimeout(timer);
      if (retryTimer) clearTimeout(retryTimer);
    };
    // `usuarios.length` no entra como dep: solo lo leemos en mount para decidir
    // si pintamos spinner; no queremos disparar refetch al cambiar el array.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtros, retryNonce]);

  /** KPI + cola pendiente + JSON modelo en serie (evita ráfaga de 6 XHR en paralelo
   *  que satura conexiones del navegador y deja varias colgadas). Reintenta hasta 3
   *  veces con backoff de 2s para recuperar al levantar la API. */
  useEffect(() => {
    let mounted = true;
    let timer = null;

    async function attempt(triesLeft) {
      if (!mounted) return;
      setBootRetrying(true);

      let pendientesData = null;
      let kpiData = null;
      let pErr = false;
      let kErr = false;

      try {
        pendientesData = await getPendingClients(8);
      } catch {
        pErr = true;
      }
      if (!mounted) return;

      try {
        kpiData = await getTodayKpi();
      } catch {
        kErr = true;
      }
      if (!mounted) return;

      if (!pErr) setPendientes(Array.isArray(pendientesData) ? pendientesData : []);
      if (!kErr) setKpiHoy(kpiData ?? null);

      if (!pErr && !kErr) {
        setPanelBootWarning("");
        try {
          const mvr = await getModelVsRules();
          if (mounted) setModelVsRules(mvr ?? null);
        } catch {
          if (mounted) setModelVsRules(null);
        }
        try {
          /* /dashboard/segments-summary devuelve {available:false} si todavía no
             se ha lanzado pipeline.py + db/load_model_outputs.py. La UI lo trata. */
          const seg = await getSegmentsSummary();
          if (mounted) setSegmentsSummary(seg ?? null);
        } catch {
          if (mounted) setSegmentsSummary(null);
        }
        if (mounted) setBootRetrying(false);
        return;
      }

      if (triesLeft > 0) {
        const parts = [];
        if (pErr) parts.push("Cola de pendientes no disponible.");
        if (kErr) parts.push("KPI del día no disponibles.");
        parts.push(`Reintentando… (${triesLeft} restante${triesLeft === 1 ? "" : "s"})`);
        setPanelBootWarning(parts.join(" "));
        timer = setTimeout(() => attempt(triesLeft - 1), 2000);
        return;
      }

      const parts = [];
      if (pErr) parts.push("Cola de pendientes no disponible (¿API en marcha en el puerto correcto?).");
      if (kErr) parts.push("KPI del día no disponibles.");
      if (mounted) {
        setPanelBootWarning(parts.join(" "));
        if (pErr) setPendientes([]);
        if (kErr) setKpiHoy(null);
        setBootRetrying(false);
      }
    }

    attempt(3);

    return () => {
      mounted = false;
      if (timer) clearTimeout(timer);
    };
  }, [retryNonce]);

  /** Catálogo de clientes (pesado): solo Análisis / Productos. */
  useEffect(() => {
    const needs = tabActiva === TABS.analisis || tabActiva === TABS.productos;
    if (!needs || clientesOptions.length > 0) return;
    let cancelled = false;
    getClientOptions()
      .then((data) => {
        if (!cancelled) setClientesOptions(data ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tabActiva, clientesOptions.length]);

  /** Catálogo de productos: solo pestaña Productos. */
  useEffect(() => {
    if (tabActiva !== TABS.productos || productosOptions.length > 0) return;
    let cancelled = false;
    getProductOptions()
      .then((data) => {
        if (!cancelled) setProductosOptions(data ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tabActiva, productosOptions.length]);

  const registrarResultadoPendiente = async (alertaId, estado, clienteId) => {
    setPendingError("");
    try {
      await postAlertOutcome(alertaId, { estado, es_demo: 0, usuario_operador: "frontend_operator" });
      /* Quita el cliente de la vista al momento (backend también cierra todas las alertas abiertas del cliente). */
      if (clienteId && estado !== "pendiente") {
        setPendientes((prev) => prev.filter((p) => p.cliente_id !== clienteId));
      }
      await refreshPending();
    } catch (_err) {
      setPendingError("No se pudo guardar el resultado de la gestión.");
      await refreshPending();
    }
  };

  const abrirAnalisisUsuario = async (usuario) => {
    try {
      if (clientesOptions.length === 0) {
        getClientOptions()
          .then((data) => setClientesOptions(data ?? []))
          .catch(() => {});
      }
      const detalle = await getClientById(usuario.cliente_id);
      setUsuarioSeleccionado(detalle);
      setTabActiva(TABS.analisis);
    } catch (_err) {
      setErrorCartera("No se pudo cargar el detalle del cliente.");
      setTabActiva(TABS.usuarios);
    }
  };

  const seleccionarClienteAnalisis = async (clienteId) => {
    if (!clienteId) {
      setUsuarioSeleccionado(null);
      return;
    }
    try {
      const detalle = await getClientById(clienteId);
      setUsuarioSeleccionado(detalle);
    } catch (_err) {
      setErrorCartera("No se pudo cargar el detalle del cliente.");
    }
  };

  /** Cola de llamadas curada por el usuario (persiste en localStorage).
   *  Antes Alertas mostraba toda la cola del sistema; ahora solo aparecen los
   *  clientes que el usuario añade manualmente desde Análisis o Resumen
   *  ejecutivo, para que la pestaña funcione como un to-do operativo. */
  const callQueue = useCallQueue();
  const [clienteContactarId, setClienteContactarId] = useState(null);
  const enviarAContacto = (clienteId) => {
    if (!clienteId) return;
    callQueue.add(clienteId);
    setClienteContactarId(String(clienteId));
    setTabActiva(TABS.alertas);
  };

  return (
    <div className="app-shell">
      <Sidebar tabActiva={tabActiva} onSelectTab={setTabActiva} />
      <main className="app-main">
        <header className="app-header">
          <div>
            <p className="brand">INIBSA · Panel comercial</p>
            <h1>{tituloSeccion}</h1>
            <p
              style={{
                margin: "6px 0 0",
                color: "var(--ink-3)",
                fontSize: 14,
                maxWidth: "62ch",
                lineHeight: 1.5,
              }}
            >
              {subtituloSeccion}
            </p>
          </div>
        </header>

        {tabActiva === TABS.pulse && (
          <PulseView
            onNavigateTab={setTabActiva}
            onOpenAnalisisCliente={(clienteId) => abrirAnalisisUsuario({ cliente_id: clienteId })}
            onContactarCliente={enviarAContacto}
          />
        )}
        {tabActiva === TABS.usuarios && (
          <UsuariosView
            usuarios={usuarios}
            pendientes={pendientes}
            kpiHoy={kpiHoy}
            modelVsRules={modelVsRules}
            segmentsSummary={segmentsSummary}
            loading={loading}
            error={errorCartera}
            bootWarning={panelBootWarning}
            bootRetrying={bootRetrying}
            pendingError={pendingError}
            filtros={filtros}
            clientsTotal={clientsTotal}
            onChangeFiltros={setFiltrosUsuarios}
            onAbrirUsuario={abrirAnalisisUsuario}
            onRegistrarResultado={registrarResultadoPendiente}
            onRetry={handleRetryBoot}
          />
        )}
        {tabActiva === TABS.productos && (
          <ProductosView
            clientesOptions={clientesOptions}
            productosOptions={productosOptions}
            onEscalationDone={refreshPending}
          />
        )}
        {tabActiva === TABS.analisis && (
          <AnalisisView
            usuario={usuarioSeleccionado}
            clientesOptions={clientesOptions}
            onSelectCliente={seleccionarClienteAnalisis}
            onContactarCliente={enviarAContacto}
          />
        )}
        {tabActiva === TABS.alertas && (
          <AlertasView
            preselectClienteId={clienteContactarId}
            onClearPreselect={() => setClienteContactarId(null)}
            queue={callQueue}
          />
        )}
      </main>
    </div>
  );
}
