#!/usr/bin/env python3
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUT_PATH = REPORTS_DIR / "backtesting_report.md"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            WITH latest_outcome AS (
                SELECT
                    s.alerta_id,
                    s.estado,
                    s.fecha_contacto,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.alerta_id
                        ORDER BY s.fecha_contacto DESC, s.seguimiento_id DESC
                    ) AS rn
                FROM seguimiento_alerta s
                WHERE s.es_demo = 0
            ),
            joined AS (
                SELECT
                    a.alerta_id,
                    a.fecha_alerta,
                    a.prioridad,
                    lo.estado,
                    lo.fecha_contacto,
                    CAST(julianday(lo.fecha_contacto) - julianday(a.fecha_alerta) AS INTEGER) AS lead_days
                FROM alerta_cliente_producto a
                LEFT JOIN latest_outcome lo ON lo.alerta_id = a.alerta_id AND lo.rn = 1
            )
            SELECT
                COUNT(*) AS total_alertas,
                SUM(CASE WHEN estado IS NOT NULL THEN 1 ELSE 0 END) AS evaluadas,
                SUM(CASE WHEN estado = 'recuperado' THEN 1 ELSE 0 END) AS recuperadas,
                SUM(CASE WHEN estado = 'perdido' THEN 1 ELSE 0 END) AS perdidas,
                SUM(CASE WHEN estado = 'sin_respuesta' THEN 1 ELSE 0 END) AS sin_respuesta,
                AVG(CASE WHEN estado = 'recuperado' THEN lead_days END) AS lead_time_recuperadas
            FROM joined
            """
        ).fetchone()

        # precision proxy = recovered / evaluated
        evaluadas = int(row["evaluadas"] or 0)
        recuperadas = int(row["recuperadas"] or 0)
        perdidas = int(row["perdidas"] or 0)
        sin_respuesta = int(row["sin_respuesta"] or 0)
        total = int(row["total_alertas"] or 0)
        precision_proxy = 0.0 if evaluadas == 0 else recuperadas / evaluadas
        recall_proxy = 0.0 if (recuperadas + perdidas) == 0 else recuperadas / (recuperadas + perdidas)
        lead_time = row["lead_time_recuperadas"]
    finally:
        conn.close()

    report = f"""# Backtesting Report

- Alertas emitidas: **{total}**
- Alertas evaluadas (con outcome): **{evaluadas}**
- Recuperadas: **{recuperadas}**
- Perdidas: **{perdidas}**
- Sin respuesta: **{sin_respuesta}**

## Métricas proxy

- Precision proxy (`recuperadas / evaluadas`): **{precision_proxy:.4f}**
- Recall proxy (`recuperadas / (recuperadas + perdidas)`): **{recall_proxy:.4f}**
- Antelación media en recuperadas (días): **{0 if lead_time is None else round(lead_time, 2)}**

> Nota: estas métricas dependen de registrar outcomes reales en `seguimiento_alerta`.
"""
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"Backtesting report written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
