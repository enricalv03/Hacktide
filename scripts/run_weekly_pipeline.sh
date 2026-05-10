#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/11] Rebuild database..."
python3 "$ROOT_DIR/db/create_database.py"

echo "[2/11] Generate alerts..."
python3 "$ROOT_DIR/db/run_alerts.py"

echo "[3/11] Export alerts CSV..."
python3 "$ROOT_DIR/db/export_alerts.py"

echo "[4/11] Generate quality report..."
python3 "$ROOT_DIR/db/quality_report.py"

echo "[5/11] Build ML training dataset..."
python3 "$ROOT_DIR/db/build_ml_dataset.py"

echo "[6/11] Backtesting report..."
python3 "$ROOT_DIR/db/backtesting.py"

echo "[7/11] Generate representative test cases..."
python3 "$ROOT_DIR/db/generate_test_cases.py"

echo "[8/11] Train baseline ML model..."
python3 "$ROOT_DIR/db/train_baseline_model.py"

echo "[9/11] Compare model vs rules..."
python3 "$ROOT_DIR/db/compare_model_vs_rules.py"

# Pipeline XGBoost por cliente/producto (segment LEAL/PROMETEDOR/RISC + forecast 6m).
# `backend/pipeline.py` escribe los CSV junto al Master que recibe; los volcamos en
# `db/dataset/` para que `db/load_model_outputs.py` los importe a SQLite y la API
# los exponga a la pestaña Usuarios y Análisis.
DATASET_DIR="$ROOT_DIR/db/dataset"
MASTER_DST="$DATASET_DIR/Master_Datos_Unificado.csv"
# Reorg 2026-05: Master vive en db/raw/. Fallback a db/ si proyecto antiguo.
if [ -f "$ROOT_DIR/db/raw/Master_Datos_Unificado.csv" ]; then
    MASTER_SRC="$ROOT_DIR/db/raw/Master_Datos_Unificado.csv"
else
    MASTER_SRC="$ROOT_DIR/db/Master_Datos_Unificado.csv"
fi

mkdir -p "$DATASET_DIR"
if [ ! -f "$MASTER_DST" ] && [ -f "$MASTER_SRC" ]; then
    echo "[10/11] Copiando Master_Datos_Unificado.csv a db/dataset/..."
    cp "$MASTER_SRC" "$MASTER_DST"
fi

if [ -f "$MASTER_DST" ]; then
    echo "[10/11] Run XGBoost client/product pipeline (segment + forecast 6m)..."
    if python3 "$ROOT_DIR/backend/pipeline.py" "$MASTER_DST"; then
        echo "[11/11] Load ML outputs (segment, productos en riesgo, forecast 6m) to SQLite..."
        python3 "$ROOT_DIR/db/load_model_outputs.py" --dataset-dir "$DATASET_DIR"
    else
        echo "[10/11] backend/pipeline.py falló — revisa requirements-forecast.txt (xgboost/pandas)."
        echo "        Saltando paso [11/11] (load_model_outputs.py)."
    fi
else
    echo "[10/11] Sin Master_Datos_Unificado.csv en db/ ni db/dataset/ — saltando pipeline ML."
    echo "[11/11] Saltando load_model_outputs.py."
fi

echo "Weekly pipeline completed."
