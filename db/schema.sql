PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS stg_clientes;
DROP TABLE IF EXISTS stg_productos;
DROP TABLE IF EXISTS stg_ventas;
DROP TABLE IF EXISTS stg_potencial;
DROP TABLE IF EXISTS stg_campanas;

DROP TABLE IF EXISTS dim_cliente;
DROP TABLE IF EXISTS dim_producto;
DROP TABLE IF EXISTS dim_campana;
DROP TABLE IF EXISTS bridge_cliente_potencial;
DROP TABLE IF EXISTS fact_venta_linea;
DROP TABLE IF EXISTS alerta_cliente_producto;
DROP TABLE IF EXISTS seguimiento_alerta;

CREATE TABLE stg_clientes (
    id_cliente_raw TEXT,
    provincia_codigo TEXT,
    provincia_nombre TEXT
);

CREATE TABLE stg_productos (
    id_producto_raw TEXT,
    bloque_analitico TEXT,
    categoria_producto TEXT,
    familia_producto TEXT
);

CREATE TABLE stg_ventas (
    factura_num TEXT,
    fecha_raw TEXT,
    id_cliente_raw TEXT,
    id_producto_raw TEXT,
    unidades_raw TEXT,
    valores_h_raw TEXT
);

CREATE TABLE stg_potencial (
    id_cliente_raw TEXT,
    familia_negocio TEXT,
    categoria_producto TEXT,
    potencial_raw TEXT
);

CREATE TABLE stg_campanas (
    campana_codigo TEXT,
    fecha_inicio_raw TEXT,
    fecha_fin_raw TEXT
);

CREATE TABLE dim_cliente (
    cliente_id TEXT PRIMARY KEY,
    provincia_codigo TEXT,
    provincia_nombre TEXT
);

CREATE TABLE dim_producto (
    producto_id TEXT PRIMARY KEY,
    bloque_analitico TEXT NOT NULL,
    categoria_producto TEXT NOT NULL,
    familia_producto TEXT NOT NULL,
    es_tecnico INTEGER NOT NULL CHECK (es_tecnico IN (0, 1)),
    es_uso_diario INTEGER NOT NULL CHECK (es_uso_diario IN (0, 1))
);

CREATE TABLE dim_campana (
    campana_id INTEGER PRIMARY KEY AUTOINCREMENT,
    campana_codigo TEXT NOT NULL UNIQUE,
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NOT NULL
);

CREATE TABLE bridge_cliente_potencial (
    cliente_id TEXT NOT NULL,
    familia_negocio TEXT NOT NULL,
    categoria_producto TEXT NOT NULL,
    potencial_valor REAL NOT NULL,
    PRIMARY KEY (cliente_id, familia_negocio, categoria_producto),
    FOREIGN KEY (cliente_id) REFERENCES dim_cliente(cliente_id)
);

CREATE TABLE fact_venta_linea (
    venta_linea_id INTEGER PRIMARY KEY AUTOINCREMENT,
    factura_num TEXT NOT NULL,
    fecha DATE NOT NULL,
    cliente_id TEXT NOT NULL,
    producto_id TEXT NOT NULL,
    unidades_netas INTEGER NOT NULL,
    tipo_movimiento TEXT NOT NULL CHECK (tipo_movimiento IN ('COMPRA', 'DEVOLUCION', 'SIN_MOVIMIENTO')),
    es_campana INTEGER NOT NULL CHECK (es_campana IN (0, 1)),
    campana_id INTEGER,
    FOREIGN KEY (cliente_id) REFERENCES dim_cliente(cliente_id),
    FOREIGN KEY (producto_id) REFERENCES dim_producto(producto_id),
    FOREIGN KEY (campana_id) REFERENCES dim_campana(campana_id)
);

CREATE TABLE alerta_cliente_producto (
    alerta_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_alerta DATE NOT NULL,
    cliente_id TEXT NOT NULL,
    producto_id TEXT,
    prioridad TEXT NOT NULL CHECK (prioridad IN ('ALTA', 'MEDIA', 'BAJA')),
    motivo TEXT NOT NULL,
    valor_actual REAL,
    valor_referencia REAL,
    accion_recomendada TEXT,
    FOREIGN KEY (cliente_id) REFERENCES dim_cliente(cliente_id),
    FOREIGN KEY (producto_id) REFERENCES dim_producto(producto_id)
);

CREATE TABLE seguimiento_alerta (
    seguimiento_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alerta_id INTEGER NOT NULL,
    fecha_contacto DATE NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('contactado', 'sin_respuesta', 'recuperado', 'perdido', 'pendiente')),
    es_demo INTEGER NOT NULL DEFAULT 0 CHECK (es_demo IN (0, 1)),
    notas TEXT,
    usuario_operador TEXT,
    FOREIGN KEY (alerta_id) REFERENCES alerta_cliente_producto(alerta_id)
);

CREATE INDEX idx_fact_venta_cliente_fecha ON fact_venta_linea (cliente_id, fecha);
CREATE INDEX idx_fact_venta_producto_fecha ON fact_venta_linea (producto_id, fecha);
CREATE INDEX idx_fact_venta_campana ON fact_venta_linea (campana_id);
CREATE INDEX idx_alerta_fecha ON alerta_cliente_producto (fecha_alerta, prioridad);
CREATE INDEX idx_seguimiento_alerta ON seguimiento_alerta (alerta_id, fecha_contacto, estado);
CREATE INDEX idx_alerta_cliente_id ON alerta_cliente_producto (cliente_id);
CREATE INDEX idx_seguimiento_fecha_demo ON seguimiento_alerta (fecha_contacto) WHERE es_demo = 0;
