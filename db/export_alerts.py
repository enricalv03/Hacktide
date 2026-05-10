#!/usr/bin/env python3
from pathlib import Path
import csv
import sqlite3


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUT_PATH = REPORTS_DIR / "alerts_export.csv"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                alerta_id,
                fecha_alerta,
                cliente_id,
                producto_id,
                prioridad,
                motivo,
                valor_actual,
                valor_referencia,
                accion_recomendada
            FROM vw_alertas_operativas_final
            ORDER BY
                CASE prioridad WHEN 'ALTA' THEN 1 WHEN 'MEDIA' THEN 2 ELSE 3 END,
                COALESCE(valor_actual, 0) DESC,
                alerta_id DESC
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
                "motivo",
                "valor_actual",
                "valor_referencia",
                "accion_recomendada",
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
                    r["motivo"],
                    r["valor_actual"],
                    r["valor_referencia"],
                    r["accion_recomendada"],
                ]
            )

    print(f"Alerts export written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
