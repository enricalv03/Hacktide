const MENU = [
  { key: "pulse", label: "Resumen ejecutivo", hint: "Visión general del negocio" },
  { key: "usuarios", label: "Cartera de clientes", hint: "Segmentación y previsión" },
  { key: "productos", label: "Productos", hint: "Tendencia por familia" },
  { key: "analisis", label: "Análisis", hint: "Detalle por cliente" },
  { key: "alertas", label: "Alertas y Contacto", hint: "Tu cola de llamadas" },
];

export function Sidebar({ tabActiva, onSelectTab }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <p className="sidebar-kicker">Inibsa · Comercial</p>
        <h2>Inteligencia comercial</h2>
      </div>

      <nav className="sidebar-nav" aria-label="Navegación principal">
        {MENU.map((item) => (
          <button
            key={item.key}
            type="button"
            className={item.key === tabActiva ? "nav-item active" : "nav-item"}
            onClick={() => onSelectTab(item.key)}
            aria-current={item.key === tabActiva ? "page" : undefined}
          >
            <span style={{ display: "block" }}>{item.label}</span>
            <span
              style={{
                display: "block",
                fontSize: 11,
                fontWeight: 400,
                opacity: 0.65,
                marginTop: 2,
                letterSpacing: 0.02,
              }}
            >
              {item.hint}
            </span>
          </button>
        ))}
      </nav>

      <p className="sidebar-foot">Datos hasta {new Date().toLocaleDateString("es-ES", { month: "short", year: "numeric" })}</p>
    </aside>
  );
}
