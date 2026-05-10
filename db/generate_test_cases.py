#!/usr/bin/env python3
from pathlib import Path
import csv
import sqlite3


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUT_PATH = REPORTS_DIR / "test_cases.csv"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            WITH base AS (
                SELECT
                    c.cliente_id,
                    c.estado_cliente,
                    c.semaforo_riesgo,
                    c.dias_desde_ultima_compra,
                    COALESCE(a.prioridad, 'BAJA') AS prioridad_alerta
                FROM vw_cliente_semaforo_riesgo c
                LEFT JOIN vw_alertas_operativas_final a ON a.cliente_id = c.cliente_id
            )
            SELECT *
            FROM base
            WHERE
                (semaforo_riesgo = 'ROJO')
                OR (semaforo_riesgo = 'AMARILLO')
                OR (estado_cliente = 'NO_CLIENTE')
            LIMIT 200
            """
        ).fetchall()
    finally:
        conn.close()

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "cliente_id",
                "estado_cliente",
                "semaforo_riesgo",
                "dias_desde_ultima_compra",
                "prioridad_alerta",
                "expected_behavior_note",
            ]
        )
        for r in rows:
            note = "revisar manualmente"
            if r["estado_cliente"] == "NO_CLIENTE":
                note = "no debe entrar en retencion"
            elif r["semaforo_riesgo"] == "ROJO":
                note = "debe tener accion de llamada urgente"
            elif r["semaforo_riesgo"] == "AMARILLO":
                note = "debe quedar en seguimiento preventivo"
            writer.writerow(
                [
                    r["cliente_id"],
                    r["estado_cliente"],
                    r["semaforo_riesgo"],
                    r["dias_desde_ultima_compra"],
                    r["prioridad_alerta"],
                    note,
                ]
            )

    print(f"Test cases written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
