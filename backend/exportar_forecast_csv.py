"""
Exportar predicciones de todos los productos a CSV
===================================================
Genera un CSV con la predicción mensual de unidades a partir de 2026.

En el panel web, la vista agregada equivalente (sumas por mes) está en
GET /products/xgb-forecast/portfolio-summary (pestaña Productos).

Uso:
    python exportar_forecast_csv.py                       # todos, 2026
    python exportar_forecast_csv.py 2027                  # todos, 2027
    python exportar_forecast_csv.py 2026 2028             # todos, 2026-2028
    python exportar_forecast_csv.py --prod 4912           # prod 4912, 2026
    python exportar_forecast_csv.py --prod 4912 2027      # prod 4912, 2027
    python exportar_forecast_csv.py --prod 4912 2026 2028 # prod 4912, 2026-2028
"""

import sys
import matplotlib

matplotlib.use("Agg")  # sin ventanas

import pandas as pd
from pathlib import Path

import forecast_por_tipo as fpt

print("Cargando modelos...")
fpt._ensure_forecast_ready(verbose=False)
print("Modelos listos.\n")

OUT_CSV = Path(__file__).parent / "xgb_output_forecast" / "tipos"
OUT_CSV.mkdir(parents=True, exist_ok=True)

MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def exportar_forecast(año_inicio=2026, año_fin=None, id_producto=None):
    if año_fin is None:
        año_fin = año_inicio

    _catalogo = fpt._catalogo
    if id_producto is not None:
        if id_producto not in _catalogo["Id.Producto"].values:
            print(f"[ERROR] Producto {id_producto} no encontrado.")
            return None
        productos = [id_producto]
    else:
        productos = _catalogo["Id.Producto"].tolist()

    ultimo_real = fpt._df["Fecha"].max()
    total = len(productos)
    filas = []

    for i, id_prod in enumerate(productos, 1):
        info = _catalogo[_catalogo["Id.Producto"] == id_prod].iloc[0]
        tipo = str(info["tipo"])

        print(f"  [{i:>3}/{total}] Producto {id_prod}  " f"({info['categoria']} / {info['familia']})")

        meses_necesarios = (año_fin - ultimo_real.year) * 12 + (12 - ultimo_real.month)
        future_df = fpt.forecast_producto(id_prod, meses_futuros=meses_necesarios, guardar=False, verbose=False)
        if future_df is None:
            print(f"    [AVISO] Sin datos suficientes, omitido.")
            continue

        for _, row in future_df.iterrows():
            ds = row["ds"]
            if año_inicio <= ds.year <= año_fin:
                filas.append(
                    {
                        "id_producto": int(id_prod),
                        "tipo": tipo,
                        "categoria": str(info["categoria"]),
                        "familia": str(info["familia"]),
                        "año": ds.year,
                        "mes_num": ds.month,
                        "mes_label": MESES_ES[ds.month - 1],
                        "fecha": ds.strftime("%Y-%m-%d"),
                        "predicho_uds": round(float(row["predicho"]), 1),
                    }
                )

    if not filas:
        print("\n[ERROR] No se generaron predicciones.")
        return None

    df_out = pd.DataFrame(filas).sort_values(["año", "mes_num", "tipo", "id_producto"]).reset_index(drop=True)

    sufijo = f"{año_inicio}" if año_inicio == año_fin else f"{año_inicio}_{año_fin}"
    prefijo = f"forecast_prod_{id_producto}" if id_producto else "forecast_todos_productos"
    ruta = OUT_CSV / f"{prefijo}_{sufijo}.csv"

    df_out.to_csv(ruta, index=False, encoding="utf-8-sig", sep=";", float_format="%.1f")

    print(f"\n[OK] CSV guardado: {ruta}")
    print(
        f"     {len(df_out)} filas  |  {df_out['id_producto'].nunique()} productos  "
        f"|  años {año_inicio}–{año_fin}"
    )

    resumen = (
        df_out.groupby(["año", "mes_num", "mes_label"])["predicho_uds"].sum().reset_index().sort_values(["año", "mes_num"])
    )
    print(f"\n{'Mes':<10}  {'Año':>6}  {'Total predicho':>16}")
    print("-" * 36)
    for _, r in resumen.iterrows():
        print(f"{r['mes_label']:<10}  {r['año']:>6}  {r['predicho_uds']:>16,.0f}")
    print(f"\n  Total global: {df_out['predicho_uds'].sum():,.0f} unidades")

    return df_out


if __name__ == "__main__":
    args = sys.argv[1:]

    id_producto = None
    año_inicio = 2026
    año_fin = 2026

    if "--prod" in args:
        idx = args.index("--prod")
        try:
            id_producto = int(args[idx + 1])
            args = args[:idx] + args[idx + 2 :]
        except (IndexError, ValueError):
            print("[ERROR] --prod requiere un ID de producto. Ej: --prod 4912")
            sys.exit(1)

    años = []
    for a in args:
        try:
            años.append(int(a))
        except ValueError:
            pass

    if len(años) >= 1:
        año_inicio = años[0]
        año_fin = años[0]
    if len(años) >= 2:
        año_fin = años[1]

    if año_inicio < 2026:
        print("[AVISO] El año mínimo es 2026. Ajustando a 2026.")
        año_inicio = 2026
    if año_fin < año_inicio:
        año_fin = año_inicio

    desc = f"producto {id_producto}" if id_producto else "todos los productos"
    print(f"Generando predicciones para {desc}  |  años {año_inicio}–{año_fin}...\n")
    exportar_forecast(año_inicio, año_fin, id_producto=id_producto)
