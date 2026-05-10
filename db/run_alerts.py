#!/usr/bin/env python3
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"


def generate_alerts(conn: sqlite3.Connection):
    cur = conn.cursor()

    cur.execute("DELETE FROM alerta_cliente_producto")

    cur.execute(
        """
        WITH clientes_activos AS (
            SELECT
                cliente_id,
                MAX(fecha) AS fecha_ultima_actividad
            FROM fact_venta_linea
            WHERE tipo_movimiento = 'COMPRA'
            GROUP BY cliente_id
            HAVING MAX(fecha) >= date((SELECT MAX(fecha) FROM fact_venta_linea), '-12 months')
        ),
        params AS (
            SELECT MAX(fecha) AS as_of_date
            FROM fact_venta_linea
        ),
        base_overdue_raw AS (
            SELECT
                cliente_id,
                producto_id,
                fecha_ultima_compra,
                intervalo_base_dias,
                CAST(
                    julianday((SELECT as_of_date FROM params))
                    - julianday(fecha_esperada_compra)
                    AS INTEGER
                ) AS dias_retraso_asof
            FROM vw_proxima_compra_esperada
        ),
        base_overdue AS (
            SELECT
                p.cliente_id,
                p.producto_id,
                p.dias_retraso_asof AS dias_retraso,
                p.intervalo_base_dias,
                CASE
                    WHEN p.dias_retraso_asof >= 30 THEN 50
                    WHEN p.dias_retraso_asof >= 14 THEN 35
                    WHEN p.dias_retraso_asof >= 7 THEN 20
                    ELSE 10
                END AS overdue_score
            FROM base_overdue_raw p
            JOIN dim_producto dp ON dp.producto_id = p.producto_id
            JOIN clientes_activos ca ON ca.cliente_id = p.cliente_id
            WHERE dp.es_tecnico = 0
              AND p.dias_retraso_asof > 0
              AND p.fecha_ultima_compra >= date((SELECT as_of_date FROM params), '-12 months')
        ),
        base_freq AS (
            WITH seasonality_product_month AS (
                SELECT
                    producto_id,
                    CAST(substr(year_month, 6, 2) AS INTEGER) AS month_num,
                    AVG(unidades_compra) AS avg_month_unidades,
                    AVG(AVG(unidades_compra)) OVER (PARTITION BY producto_id) AS avg_global_unidades
                FROM vw_cliente_producto_mensual
                GROUP BY producto_id, CAST(substr(year_month, 6, 2) AS INTEGER)
            )
            SELECT
                c.cliente_id,
                c.producto_id,
                c.ratio_vs_media,
                CASE
                    WHEN COALESCE(spm.avg_global_unidades, 0) = 0 THEN c.ratio_vs_media
                    ELSE c.ratio_vs_media / MAX(0.60, MIN(1.40, spm.avg_month_unidades / spm.avg_global_unidades))
                END AS ratio_ajustada_estacional,
                CASE
                    WHEN (
                        CASE
                            WHEN COALESCE(spm.avg_global_unidades, 0) = 0 THEN c.ratio_vs_media
                            ELSE c.ratio_vs_media / MAX(0.60, MIN(1.40, spm.avg_month_unidades / spm.avg_global_unidades))
                        END
                    ) < 0.40 THEN 35
                    WHEN (
                        CASE
                            WHEN COALESCE(spm.avg_global_unidades, 0) = 0 THEN c.ratio_vs_media
                            ELSE c.ratio_vs_media / MAX(0.60, MIN(1.40, spm.avg_month_unidades / spm.avg_global_unidades))
                        END
                    ) < 0.60 THEN 25
                    WHEN (
                        CASE
                            WHEN COALESCE(spm.avg_global_unidades, 0) = 0 THEN c.ratio_vs_media
                            ELSE c.ratio_vs_media / MAX(0.60, MIN(1.40, spm.avg_month_unidades / spm.avg_global_unidades))
                        END
                    ) < 0.80 THEN 15
                    ELSE 0
                END AS freq_score
            FROM vw_alerta_caida_frecuencia c
            JOIN dim_producto dp ON dp.producto_id = c.producto_id
            JOIN clientes_activos ca ON ca.cliente_id = c.cliente_id
            LEFT JOIN seasonality_product_month spm
                ON spm.producto_id = c.producto_id
                AND spm.month_num = CAST(substr(c.periodo_actual, 6, 2) AS INTEGER)
            WHERE c.ratio_vs_media IS NOT NULL
              AND dp.es_tecnico = 0
              AND c.periodo_actual >= strftime('%Y-%m', date((SELECT as_of_date FROM params), '-2 months'))
        ),
        base_potential AS (
            SELECT
                g.cliente_id,
                NULL AS producto_id,
                g.ratio_consumo_vs_potencial,
                CASE
                    WHEN g.ratio_consumo_vs_potencial < 0.15 THEN 30
                    WHEN g.ratio_consumo_vs_potencial < 0.30 THEN 20
                    WHEN g.ratio_consumo_vs_potencial < 0.50 THEN 10
                    ELSE 0
                END AS potential_score
            FROM vw_gap_potencial_cliente g
            JOIN clientes_activos ca ON ca.cliente_id = g.cliente_id
            WHERE g.ratio_consumo_vs_potencial IS NOT NULL
        ),
        merged AS (
            SELECT
                o.cliente_id,
                o.producto_id,
                o.dias_retraso,
                o.intervalo_base_dias,
                COALESCE(f.ratio_vs_media, 1.0) AS ratio_vs_media,
                COALESCE(o.overdue_score, 0) + COALESCE(f.freq_score, 0) AS product_score
            FROM base_overdue o
            LEFT JOIN base_freq f
                ON f.cliente_id = o.cliente_id
                AND f.producto_id = o.producto_id
        ),
        scored AS (
            SELECT
                m.cliente_id,
                m.producto_id,
                m.dias_retraso AS valor_actual,
                m.intervalo_base_dias AS valor_referencia,
                COALESCE(o.overdue_score, 0) AS overdue_score,
                COALESCE(f.freq_score, 0) AS freq_score,
                COALESCE(bp.potential_score, 0) AS potential_score,
                m.product_score + COALESCE(bp.potential_score, 0) AS total_score,
                (CASE WHEN COALESCE(o.overdue_score, 0) > 0 THEN 1 ELSE 0 END) +
                (CASE WHEN COALESCE(f.freq_score, 0) > 0 THEN 1 ELSE 0 END) +
                (CASE WHEN COALESCE(bp.potential_score, 0) > 0 THEN 1 ELSE 0 END) AS evidencias_activas,
                m.ratio_vs_media,
                COALESCE(bp.ratio_consumo_vs_potencial, NULL) AS ratio_potencial
            FROM merged m
            LEFT JOIN base_overdue o
                ON o.cliente_id = m.cliente_id
                AND o.producto_id = m.producto_id
            LEFT JOIN base_freq f
                ON f.cliente_id = m.cliente_id
                AND f.producto_id = m.producto_id
            LEFT JOIN (
                SELECT
                    cliente_id,
                    MAX(potential_score) AS potential_score,
                    MIN(ratio_consumo_vs_potencial) AS ratio_consumo_vs_potencial
                FROM base_potential
                GROUP BY cliente_id
            ) bp
                ON bp.cliente_id = m.cliente_id
        ),
        ranked AS (
            SELECT
                s.*,
                ROW_NUMBER() OVER (
                    PARTITION BY s.cliente_id, s.producto_id
                    ORDER BY s.total_score DESC, s.valor_actual DESC
                ) AS rn
            FROM scored s
            WHERE s.total_score >= 40
        ),
        dedup AS (
            SELECT
                r.*,
                ROW_NUMBER() OVER (
                    ORDER BY r.total_score DESC, r.valor_actual DESC
                ) AS global_rank,
                COUNT(*) OVER () AS total_alertas,
                PERCENT_RANK() OVER (ORDER BY r.total_score DESC, r.valor_actual DESC) AS risk_percentile
            FROM ranked r
            WHERE r.rn = 1
        )
        INSERT INTO alerta_cliente_producto
            (fecha_alerta, cliente_id, producto_id, prioridad, motivo, valor_actual, valor_referencia, accion_recomendada)
        SELECT
            (SELECT as_of_date FROM params) AS fecha_alerta,
            r.cliente_id,
            r.producto_id,
            CASE
                WHEN r.risk_percentile <= 0.20 AND r.valor_actual >= 21 AND r.evidencias_activas >= 2 THEN 'ALTA'
                WHEN r.risk_percentile <= 0.55 AND r.evidencias_activas >= 1 THEN 'MEDIA'
                ELSE 'BAJA'
            END AS prioridad,
            'Riesgo combinado: retraso de compra + tendencia + brecha de potencial' AS motivo,
            r.valor_actual,
            r.valor_referencia,
            CASE
                WHEN r.risk_percentile <= 0.20 AND r.valor_actual >= 21 AND r.evidencias_activas >= 2
                    THEN 'Llamar en menos de 24h y ofrecer plan de recompra'
                WHEN r.risk_percentile <= 0.55 AND r.evidencias_activas >= 1
                    THEN 'Contactar en 3 dias y validar motivo de caida'
                ELSE 'Seguimiento preventivo en proximo corte'
            END AS accion_recomendada
        FROM dedup r
        """
    )

    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        generate_alerts(conn)
    finally:
        conn.close()
    print(f"Alerts generated in: {DB_PATH}")


if __name__ == "__main__":
    main()
