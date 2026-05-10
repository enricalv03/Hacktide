#!/usr/bin/env python3
from pathlib import Path
import csv
import json
import math


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
MODELS_DIR = ROOT / "models"
REPORTS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)
DATASET_PATH = REPORTS_DIR / "ml_training_dataset.csv"
MODEL_PATH = MODELS_DIR / "baseline_model.json"
REPORT_PATH = REPORTS_DIR / "model_vs_rules_report.md"
JSON_PATH = MODELS_DIR / "model_vs_rules.json"


def sigmoid(z: float) -> float:
    if z < -40:
        return 0.0
    if z > 40:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def make_features(row):
    prioridad = row["prioridad"]
    semaforo = row["semaforo_riesgo"]
    return [
        float(row["valor_actual"] or 0.0),
        float(row["valor_referencia"] or 0.0),
        1.0 if prioridad == "ALTA" else 0.0,
        1.0 if prioridad == "MEDIA" else 0.0,
        1.0 if semaforo == "ROJO" else 0.0,
        1.0 if semaforo == "AMARILLO" else 0.0,
    ], int(row["label_recuperado"] or 0)


def eval_preds(preds, ys):
    tp = fp = fn = tn = 0
    for p, y in zip(preds, ys):
        if p == 1 and y == 1:
            tp += 1
        elif p == 1 and y == 0:
            fp += 1
        elif p == 0 and y == 1:
            fn += 1
        else:
            tn += 1
    precision = 0.0 if (tp + fp) == 0 else tp / (tp + fp)
    recall = 0.0 if (tp + fn) == 0 else tp / (tp + fn)
    accuracy = (tp + tn) / max(1, len(ys))
    return {"precision": precision, "recall": recall, "accuracy": accuracy, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def main():
    rows = list(csv.DictReader(DATASET_PATH.open("r", encoding="utf-8", newline="")))
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    w = model["weights"]
    threshold = float(model.get("threshold", 0.5))

    test_rows = [r for idx, r in enumerate(rows) if idx % 5 == 0]
    xs, ys = zip(*(make_features(r) for r in test_rows)) if test_rows else ([], [])

    model_preds = []
    rule_preds = []
    for x, row in zip(xs, test_rows):
        z = w[0] + sum(w[i + 1] * x[i] for i in range(len(x)))
        model_preds.append(1 if sigmoid(z) >= threshold else 0)
        rule_preds.append(1 if row["prioridad"] in {"ALTA", "MEDIA"} else 0)

    model_m = eval_preds(model_preds, ys)
    rule_m = eval_preds(rule_preds, ys)

    report = f"""# Model vs Rules Report

Test rows: **{len(test_rows)}**

## Baseline model

- Precision: **{model_m['precision']:.4f}**
- Recall: **{model_m['recall']:.4f}**
- Accuracy: **{model_m['accuracy']:.4f}**

## Rules baseline (`ALTA|MEDIA` => positive)

- Precision: **{rule_m['precision']:.4f}**
- Recall: **{rule_m['recall']:.4f}**
- Accuracy: **{rule_m['accuracy']:.4f}**

## Decision hint

{"Use model ranking for prioritization." if model_m['accuracy'] >= rule_m['accuracy'] else "Keep rule-based system and gather more outcomes."}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")
    payload = {
        "test_rows": len(test_rows),
        "model": model_m,
        "rules": rule_m,
        "decision_hint": "Use model ranking for prioritization."
        if model_m["accuracy"] >= rule_m["accuracy"]
        else "Keep rule-based system and gather more outcomes.",
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Comparison report written to: {REPORT_PATH}")
    print(f"Comparison JSON written to: {JSON_PATH}")


if __name__ == "__main__":
    main()
