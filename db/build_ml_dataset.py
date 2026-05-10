#!/usr/bin/env python3
from pathlib import Path
import csv
import sqlite3


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUT_PATH = REPORTS_DIR / "ml_training_dataset.csv"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            WITH latest_outcome AS (
                SELECT
                    s.alerta_id,
                    s.estado,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.alerta_id
                        ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC
                    ) AS rn
                FROM seguimiento_alerta s
                WHERE s.es_demo = 0
            ),
            sem AS (
                SELECT cliente_id, semaforo_riesgo
                FROM vw_cliente_semaforo_riesgo
            )
            SELECT
                a.alerta_id,
                a.fecha_alerta,
                a.cliente_id,
                a.producto_id,
                a.prioridad,
                a.valor_actual,
                a.valor_referencia,
                sem.semaforo_riesgo,
                COALESCE(o.estado, 'pendiente') AS estado_final,
                CASE WHEN COALESCE(o.estado, '') = 'recuperado' THEN 1 ELSE 0 END AS label_recuperado
            FROM alerta_cliente_producto a
            LEFT JOIN latest_outcome o ON o.alerta_id = a.alerta_id AND o.rn = 1
            LEFT JOIN sem ON sem.cliente_id = a.cliente_id
            """
        ).fetchall()
    finally:
        conn.close()

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "alerta_id",
                "fecha_alerta",
                "cliente_id",
                "producto_id",
                "prioridad",
                "valor_actual",
                "valor_referencia",
                "semaforo_riesgo",
                "estado_final",
                "label_recuperado",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["alerta_id"],
                    r["fecha_alerta"],
                    r["cliente_id"],
                    r["producto_id"],
                    r["prioridad"],
                    r["valor_actual"],
                    r["valor_referencia"],
                    r["semaforo_riesgo"],
                    r["estado_final"],
                    r["label_recuperado"],
                ]
            )

    print(f"ML dataset written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
