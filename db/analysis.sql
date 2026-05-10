DROP VIEW IF EXISTS vw_cliente_producto_mensual;
CREATE VIEW vw_cliente_producto_mensual AS
SELECT
    cliente_id,
    producto_id,
    substr(fecha, 1, 7) AS year_month,
    SUM(CASE WHEN unidades_netas > 0 THEN unidades_netas ELSE 0 END) AS unidades_compra,
    SUM(CASE WHEN unidades_netas < 0 THEN -unidades_netas ELSE 0 END) AS unidades_devolucion,
    COUNT(*) AS movimientos
FROM fact_venta_linea
GROUP BY cliente_id, producto_id, substr(fecha, 1, 7);

DROP VIEW IF EXISTS vw_perfil_cliente_recurrencia;
CREATE VIEW vw_perfil_cliente_recurrencia AS
WITH compras AS (
    SELECT
        f.cliente_id,
        SUM(CASE WHEN f.unidades_netas > 0 THEN f.unidades_netas ELSE 0 END) AS unidades_recurrentes,
        SUM(CASE WHEN f.unidades_netas < 0 THEN -f.unidades_netas ELSE 0 END) AS unidades_devueltas
    FROM fact_venta_linea f
    JOIN dim_producto p ON p.producto_id = f.producto_id
    WHERE p.es_tecnico = 0
    GROUP BY f.cliente_id
),
calc AS (
    SELECT
        cliente_id,
        unidades_recurrentes,
        unidades_devueltas,
        CASE
            WHEN (unidades_recurrentes + unidades_devueltas) = 0 THEN 0.0
            ELSE (unidades_recurrentes * 1.0) / (unidades_recurrentes + unidades_devueltas)
        END AS ratio_recurrencia
    FROM compras
)
SELECT
    cliente_id,
    unidades_recurrentes,
    unidades_devueltas,
    ratio_recurrencia,
    CASE
        WHEN ratio_recurrencia >= 0.70 THEN 'LEAL'
        WHEN ratio_recurrencia >= 0.30 THEN 'PROMISCUO'
        WHEN ratio_recurrencia > 0 THEN 'MARGINAL'
        ELSE 'SIN_CLASIFICAR'
    END AS perfil_cliente
FROM calc;

DROP VIEW IF EXISTS vw_alerta_caida_frecuencia;
CREATE VIEW vw_alerta_caida_frecuencia AS
WITH stats AS (
    SELECT
        a.cliente_id,
        a.producto_id,
        a.year_month AS periodo_actual,
        a.unidades_compra AS compra_actual,
        AVG(b.unidades_compra) AS media_3m_previos
    FROM vw_cliente_producto_mensual a
    LEFT JOIN vw_cliente_producto_mensual b
        ON b.cliente_id = a.cliente_id
        AND b.producto_id = a.producto_id
        AND b.year_month < a.year_month
        AND b.year_month >= strftime('%Y-%m', date(a.year_month || '-01', '-3 month'))
    GROUP BY a.cliente_id, a.producto_id, a.year_month, a.unidades_compra
)
SELECT
    s.cliente_id,
    s.producto_id,
    s.periodo_actual,
    s.compra_actual,
    COALESCE(s.media_3m_previos, 0) AS media_3m_previos,
    CASE
        WHEN COALESCE(s.media_3m_previos, 0) = 0 THEN NULL
        ELSE (s.compra_actual * 1.0) / s.media_3m_previos
    END AS ratio_vs_media,
    CASE
        WHEN COALESCE(s.media_3m_previos, 0) = 0 THEN NULL
        WHEN (s.compra_actual * 1.0) / s.media_3m_previos < 0.50 THEN 'ALTA'
        WHEN (s.compra_actual * 1.0) / s.media_3m_previos < 0.80 THEN 'MEDIA'
        ELSE 'BAJA'
    END AS prioridad_sugerida
FROM stats s;

DROP VIEW IF EXISTS vw_intervalo_compra_cliente_producto;
CREATE VIEW vw_intervalo_compra_cliente_producto AS
WITH compras AS (
    SELECT
        cliente_id,
        producto_id,
        fecha,
        LAG(fecha) OVER (PARTITION BY cliente_id, producto_id ORDER BY fecha) AS fecha_compra_anterior
    FROM fact_venta_linea
    WHERE tipo_movimiento = 'COMPRA'
),
intervalos AS (
    SELECT
        cliente_id,
        producto_id,
        fecha,
        fecha_compra_anterior,
        CAST(julianday(fecha) - julianday(fecha_compra_anterior) AS INTEGER) AS dias_intervalo
    FROM compras
    WHERE fecha_compra_anterior IS NOT NULL
)
SELECT * FROM intervalos;

DROP VIEW IF EXISTS vw_proxima_compra_esperada;
CREATE VIEW vw_proxima_compra_esperada AS
WITH ultimas_compras AS (
    SELECT
        cliente_id,
        producto_id,
        MAX(fecha) AS fecha_ultima_compra,
        COUNT(*) AS total_compras
    FROM fact_venta_linea
    WHERE tipo_movimiento = 'COMPRA'
    GROUP BY cliente_id, producto_id
),
base AS (
    SELECT
        i.cliente_id,
        i.producto_id,
        AVG(i.dias_intervalo) AS intervalo_base_dias,
        COUNT(*) AS num_intervalos
    FROM vw_intervalo_compra_cliente_producto i
    WHERE i.dias_intervalo > 0
    GROUP BY i.cliente_id, i.producto_id
)
SELECT
    u.cliente_id,
    u.producto_id,
    u.fecha_ultima_compra,
    ROUND(COALESCE(b.intervalo_base_dias, 30), 2) AS intervalo_base_dias,
    date(u.fecha_ultima_compra, '+' || CAST(ROUND(COALESCE(b.intervalo_base_dias, 30), 0) AS INTEGER) || ' day') AS fecha_esperada_compra,
    CAST(julianday('now') - julianday(date(u.fecha_ultima_compra, '+' || CAST(ROUND(COALESCE(b.intervalo_base_dias, 30), 0) AS INTEGER) || ' day')) AS INTEGER) AS dias_retraso,
    u.total_compras,
    COALESCE(b.num_intervalos, 0) AS num_intervalos
FROM ultimas_compras u
LEFT JOIN base b
    ON b.cliente_id = u.cliente_id
    AND b.producto_id = u.producto_id;

DROP VIEW IF EXISTS vw_consumo_anual_cliente_categoria;
CREATE VIEW vw_consumo_anual_cliente_categoria AS
WITH base AS (
    SELECT
        f.cliente_id,
        p.categoria_producto,
        SUM(CASE WHEN f.unidades_netas > 0 THEN f.unidades_netas ELSE 0 END) AS unidades_compra_anual
    FROM fact_venta_linea f
    JOIN dim_producto p ON p.producto_id = f.producto_id
    WHERE f.fecha >= date('now', '-12 months')
    GROUP BY f.cliente_id, p.categoria_producto
)
SELECT
    cliente_id,
    categoria_producto,
    unidades_compra_anual
FROM base;

DROP VIEW IF EXISTS vw_gap_potencial_cliente;
CREATE VIEW vw_gap_potencial_cliente AS
WITH potencial AS (
    SELECT
        cliente_id,
        categoria_producto,
        SUM(potencial_valor) AS potencial_anual_eur
    FROM bridge_cliente_potencial
    GROUP BY cliente_id, categoria_producto
),
consumo AS (
    SELECT
        c.cliente_id,
        c.categoria_producto,
        c.unidades_compra_anual * COALESCE(pp.precio_unitario_medio, 0) AS consumo_real_estimado_eur
    FROM vw_consumo_anual_cliente_categoria c
    LEFT JOIN (
        SELECT
            p.categoria_producto,
            1.0 AS precio_unitario_medio
        FROM dim_producto p
        GROUP BY p.categoria_producto
    ) pp
        ON pp.categoria_producto = c.categoria_producto
)
SELECT
    p.cliente_id,
    p.categoria_producto,
    p.potencial_anual_eur,
    COALESCE(c.consumo_real_estimado_eur, 0) AS consumo_real_estimado_eur,
    CASE
        WHEN p.potencial_anual_eur <= 0 THEN NULL
        ELSE COALESCE(c.consumo_real_estimado_eur, 0) / p.potencial_anual_eur
    END AS ratio_consumo_vs_potencial,
    CASE
        WHEN p.potencial_anual_eur <= 0 THEN 'SIN_POTENCIAL'
        WHEN COALESCE(c.consumo_real_estimado_eur, 0) / p.potencial_anual_eur < 0.30 THEN 'ALTA'
        WHEN COALESCE(c.consumo_real_estimado_eur, 0) / p.potencial_anual_eur < 0.60 THEN 'MEDIA'
        ELSE 'BAJA'
    END AS prioridad_gap_potencial
FROM potencial p
LEFT JOIN consumo c
    ON c.cliente_id = p.cliente_id
    AND c.categoria_producto = p.categoria_producto;

DROP VIEW IF EXISTS vw_alertas_operativas_final;
CREATE VIEW vw_alertas_operativas_final AS
WITH ultima_alerta AS (
    SELECT
        a.*,
        ROW_NUMBER() OVER (
            PARTITION BY a.cliente_id, COALESCE(a.producto_id, 'NO_PRODUCTO')
            ORDER BY a.fecha_alerta DESC, a.alerta_id DESC
        ) AS rn
    FROM alerta_cliente_producto a
)
SELECT
    u.alerta_id,
    u.fecha_alerta,
    u.cliente_id,
    u.producto_id,
    u.prioridad,
    u.motivo,
    u.valor_actual,
    u.valor_referencia,
    p.perfil_cliente,
    u.accion_recomendada
FROM ultima_alerta u
LEFT JOIN vw_perfil_cliente_recurrencia p ON p.cliente_id = u.cliente_id
WHERE u.rn = 1;

DROP VIEW IF EXISTS vw_alertas_queue_diaria;
CREATE VIEW vw_alertas_queue_diaria AS
WITH ranked AS (
    SELECT
        a.*,
        ROW_NUMBER() OVER (
            ORDER BY
                CASE a.prioridad
                    WHEN 'ALTA' THEN 1
                    WHEN 'MEDIA' THEN 2
                    ELSE 3
                END,
                COALESCE(a.valor_actual, 0) DESC,
                a.alerta_id DESC
        ) AS orden_llamada
    FROM vw_alertas_operativas_final a
)
SELECT
    alerta_id,
    fecha_alerta,
    cliente_id,
    producto_id,
    prioridad,
    perfil_cliente,
    motivo,
    valor_actual,
    valor_referencia,
    accion_recomendada,
    orden_llamada
FROM ranked
WHERE orden_llamada <= 25;

DROP VIEW IF EXISTS vw_alertas_con_resultado;
CREATE VIEW vw_alertas_con_resultado AS
WITH ultimo_seguimiento AS (
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY s.alerta_id
            ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC
        ) AS rn
    FROM seguimiento_alerta s
    WHERE s.es_demo = 0
)
SELECT
    a.alerta_id,
    a.fecha_alerta,
    a.cliente_id,
    a.producto_id,
    a.prioridad,
    a.motivo,
    a.valor_actual,
    a.valor_referencia,
    a.accion_recomendada,
    COALESCE(u.estado, 'pendiente') AS estado_actual,
    u.fecha_contacto AS fecha_ultimo_contacto,
    u.usuario_operador
FROM alerta_cliente_producto a
LEFT JOIN ultimo_seguimiento u
    ON u.alerta_id = a.alerta_id
    AND u.rn = 1;

DROP VIEW IF EXISTS vw_kpi_semanal_operacion;
CREATE VIEW vw_kpi_semanal_operacion AS
WITH base AS (
    SELECT
        strftime('%Y-%W', a.fecha_alerta) AS semana,
        a.alerta_id,
        a.prioridad,
        r.estado_actual
    FROM vw_alertas_con_resultado r
    JOIN alerta_cliente_producto a ON a.alerta_id = r.alerta_id
),
agg AS (
    SELECT
        semana,
        COUNT(*) AS alertas_emitidas,
        SUM(CASE WHEN estado_actual <> 'pendiente' THEN 1 ELSE 0 END) AS alertas_trabajadas,
        SUM(CASE WHEN estado_actual = 'contactado' THEN 1 ELSE 0 END) AS casos_contactados,
        SUM(CASE WHEN estado_actual = 'sin_respuesta' THEN 1 ELSE 0 END) AS casos_sin_respuesta,
        SUM(CASE WHEN estado_actual = 'recuperado' THEN 1 ELSE 0 END) AS casos_recuperados,
        SUM(CASE WHEN estado_actual = 'perdido' THEN 1 ELSE 0 END) AS casos_perdidos,
        SUM(CASE WHEN prioridad = 'ALTA' THEN 1 ELSE 0 END) AS alertas_alta
    FROM base
    GROUP BY semana
)
SELECT
    semana,
    alertas_emitidas,
    alertas_trabajadas,
    casos_contactados,
    casos_sin_respuesta,
    casos_recuperados,
    casos_perdidos,
    alertas_alta,
    ROUND(
        CASE WHEN alertas_emitidas = 0 THEN 0
             ELSE (alertas_trabajadas * 1.0) / alertas_emitidas
        END, 4
    ) AS tasa_gestion,
    ROUND(
        CASE WHEN alertas_trabajadas = 0 THEN 0
             ELSE (casos_recuperados * 1.0) / alertas_trabajadas
        END, 4
    ) AS tasa_recuperacion
FROM agg;

DROP VIEW IF EXISTS vw_clientes_estado;
CREATE VIEW vw_clientes_estado AS
WITH compras AS (
    SELECT
        cliente_id,
        COUNT(*) AS compras_total,
        MAX(fecha) AS fecha_ultima_compra
    FROM fact_venta_linea
    WHERE tipo_movimiento = 'COMPRA'
    GROUP BY cliente_id
),
devoluciones AS (
    SELECT
        cliente_id,
        COUNT(*) AS devoluciones_total
    FROM fact_venta_linea
    WHERE tipo_movimiento = 'DEVOLUCION'
    GROUP BY cliente_id
)
SELECT
    c.cliente_id,
    COALESCE(cp.compras_total, 0) AS compras_total,
    COALESCE(dv.devoluciones_total, 0) AS devoluciones_total,
    cp.fecha_ultima_compra,
    CAST(
        julianday((SELECT MAX(fecha) FROM fact_venta_linea))
        - julianday(cp.fecha_ultima_compra)
        AS INTEGER
    ) AS dias_desde_ultima_compra,
    CASE
        WHEN COALESCE(cp.compras_total, 0) = 0 THEN 'NO_CLIENTE'
        ELSE 'CLIENTE'
    END AS estado_cliente
FROM dim_cliente c
LEFT JOIN compras cp ON cp.cliente_id = c.cliente_id
LEFT JOIN devoluciones dv ON dv.cliente_id = c.cliente_id;

DROP VIEW IF EXISTS vw_inactivos_sin_compra_potencial;
CREATE VIEW vw_inactivos_sin_compra_potencial AS
WITH potencial AS (
    SELECT
        cliente_id,
        SUM(potencial_valor) AS potencial_total_eur
    FROM bridge_cliente_potencial
    GROUP BY cliente_id
)
SELECT
    e.cliente_id,
    e.estado_cliente,
    p.potencial_total_eur,
    e.compras_total,
    e.devoluciones_total
FROM vw_clientes_estado e
LEFT JOIN potencial p ON p.cliente_id = e.cliente_id
WHERE e.estado_cliente = 'NO_CLIENTE'
ORDER BY COALESCE(p.potencial_total_eur, 0) DESC, e.cliente_id;

DROP VIEW IF EXISTS vw_cliente_semaforo_riesgo;
CREATE VIEW vw_cliente_semaforo_riesgo AS
WITH ultima_tendencia AS (
    SELECT
        c.cliente_id,
        MIN(c.ratio_vs_media) AS peor_ratio_tendencia
    FROM vw_alerta_caida_frecuencia c
    WHERE c.periodo_actual = (
        SELECT MAX(periodo_actual) FROM vw_alerta_caida_frecuencia
    )
    GROUP BY c.cliente_id
)
SELECT
    e.cliente_id,
    e.estado_cliente,
    e.compras_total,
    e.devoluciones_total,
    e.fecha_ultima_compra,
    e.dias_desde_ultima_compra,
    COALESCE(t.peor_ratio_tendencia, 1.0) AS ratio_tendencia,
    CASE
        WHEN e.estado_cliente = 'NO_CLIENTE' THEN 'GRIS'
        WHEN COALESCE(e.dias_desde_ultima_compra, 9999) >= 60 THEN 'ROJO'
        WHEN COALESCE(t.peor_ratio_tendencia, 1.0) < 0.60 THEN 'ROJO'
        WHEN COALESCE(e.dias_desde_ultima_compra, 9999) >= 30 THEN 'AMARILLO'
        WHEN COALESCE(t.peor_ratio_tendencia, 1.0) < 0.85 THEN 'AMARILLO'
        ELSE 'VERDE'
    END AS semaforo_riesgo,
    CASE
        WHEN e.estado_cliente = 'NO_CLIENTE' THEN 'No cliente: fuera de flujo de retencion'
        WHEN COALESCE(e.dias_desde_ultima_compra, 9999) >= 60 OR COALESCE(t.peor_ratio_tendencia, 1.0) < 0.60
            THEN 'Riesgo alto de cambio de proveedor: llamar urgente'
        WHEN COALESCE(e.dias_desde_ultima_compra, 9999) >= 30 OR COALESCE(t.peor_ratio_tendencia, 1.0) < 0.85
            THEN 'Seguimiento preventivo recomendado'
        ELSE 'Cliente estable'
    END AS recomendacion_estado
FROM vw_clientes_estado e
LEFT JOIN ultima_tendencia t ON t.cliente_id = e.cliente_id;
