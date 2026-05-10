#!/usr/bin/env python3
"""
Diagnóstico del forecast XGB en el panel (Master CSV, SQLite, dependencias).
Ejecutar con el MISMO intérprete que uvicorn, p. ej.:
  ./venv/bin/python backend/check_forecast_setup.py
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def find_master() -> Path | None:
    for p in (
        REPO / "backend" / "Master_Datos_Unificado.csv",
        REPO / "db" / "raw" / "Master_Datos_Unificado.csv",
        REPO / "db" / "Master_Datos_Unificado.csv",  # legacy
    ):
        if p.is_file():
            return p
    return None


def main() -> int:
    print("=== INTERHACK — diagnóstico forecast XGB ===\n")
    print("Python:", sys.executable, "\n")

    master = find_master()
    print("1. Master_Datos_Unificado.csv")
    if not master:
        print("   ERROR: no encontrado en backend/ ni db/")
        print("   → Coloca el archivo o enlázalo en una de esas rutas.")
    else:
        print("   OK:", master)
        with master.open(encoding="utf-8-sig", newline="") as f:
            row = csv.reader(f, delimiter=";").__next__()
        print("   Columnas:", len(row))
        print('   Tiene "Id.Producto":', "Id.Producto" in row)

    db = REPO / "db" / "interhack.db"
    print("\n2. SQLite dim_producto vs Master")
    if not db.is_file():
        print("   ", db, "no existe")
    elif not master:
        print("   (omitido: sin Master)")
    else:
        conn = sqlite3.connect(db)
        db_ids = {str(r[0]).strip() for r in conn.execute("SELECT producto_id FROM dim_producto")}
        conn.close()
        master_ids: set[str] = set()
        with master.open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=";")
            for i, row in enumerate(r):
                if i > 200000:
                    break
                pid = str(row.get("Id.Producto", "")).strip()
                if pid:
                    master_ids.add(pid)
        both = db_ids & master_ids
        print(f"   IDs en dim_producto: {len(db_ids)}")
        print(f"   IDs distintos en Master (muestra): {len(master_ids)}")
        print(f"   Intersección: {len(both)}")
        if len(both) < len(db_ids):
            solo_db = db_ids - master_ids
            print(f"   Aviso: {len(solo_db)} productos del panel no están en el Master:", sorted(list(solo_db))[:15])

    print("\n3. Dependencias Python")
    for mod in ("pandas", "numpy", "sklearn", "matplotlib"):
        try:
            m = __import__(mod if mod != "sklearn" else "sklearn")
            ver = getattr(m, "__version__", "?")
            print(f"   {mod}: OK ({ver})")
        except ImportError as e:
            print(f"   {mod}: FALTA ({e})")

    print("\n4. XGBoost (librería nativa)")
    try:
        import xgboost as xgb

        print("   xgboost:", xgb.__version__, "— OK")
    except Exception as e:
        print("   xgboost: ERROR")
        print("  ", str(e)[:500])
        if "libomp" in str(e).lower() or "OpenMP" in str(e):
            print("\n   En macOS instala OpenMP y reinicia el servidor:")
            print("      brew install libomp")

    print("\n5. Prueba rápida forecast_por_tipo (carga + modelos; puede tardar ~1 min)")
    try:
        import forecast_por_tipo as fpt

        fpt._ensure_forecast_ready(verbose=False)
        print("   _ensure_forecast_ready: OK")
    except Exception as e:
        print("   ERROR:", str(e)[:600])

    print("\n--- Fin ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
