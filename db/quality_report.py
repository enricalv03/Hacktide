#!/usr/bin/env python3
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
OUT_PATH = REPORTS_DIR / "quality_report.md"


def q1(conn: sqlite3.Connection, query: str):
    return conn.execute(query).fetchone()[0]


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        total_clientes = q1(conn, "SELECT COUNT(*) FROM dim_cliente")
        total_clientes_con_compra = q1(
            conn, "SELECT COUNT(DISTINCT cliente_id) FROM fact_venta_linea WHERE tipo_movimiento = 'COMPRA'"
        )
        total_no_clientes = q1(conn, "SELECT COUNT(*) FROM vw_clientes_estado WHERE estado_cliente = 'NO_CLIENTE'")
        total_alertas = q1(conn, "SELECT COUNT(*) FROM alerta_cliente_producto")
        clientes_sin_provincia = q1(conn, "SELECT COUNT(*) FROM dim_cliente WHERE provincia_nombre IS NULL")
        devoluciones = q1(conn, "SELECT COUNT(*) FROM fact_venta_linea WHERE tipo_movimiento = 'DEVOLUCION'")
        compras = q1(conn, "SELECT COUNT(*) FROM fact_venta_linea WHERE tipo_movimiento = 'COMPRA'")

        missing_master = q1(
            conn,
            """
            SELECT COUNT(*)
            FROM (
                SELECT DISTINCT id_cliente_raw AS id FROM stg_ventas
                UNION
                SELECT DISTINCT id_cliente_raw AS id FROM stg_potencial
            ) s
            LEFT JOIN stg_clientes c ON c.id_cliente_raw = s.id
            WHERE c.id_cliente_raw IS NULL
            """,
        )
    finally:
        conn.close()

    ratio_devol = 0 if compras == 0 else round((devoluciones * 100.0) / compras, 2)

    report = f"""# Quality Report

- Total clientes (`dim_cliente`): **{total_clientes}**
- Clientes con compras: **{total_clientes_con_compra}**
- No clientes (sin compra histórica): **{total_no_clientes}**
- Clientes sin provincia en maestro: **{clientes_sin_provincia}**
- IDs en ventas/potencial ausentes en `Clientes.csv`: **{missing_master}**
- Alertas operativas actuales: **{total_alertas}**
- Ratio devoluciones/compras: **{ratio_devol}%**
"""

    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"Quality report written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
