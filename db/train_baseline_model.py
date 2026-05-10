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
REPORT_PATH = REPORTS_DIR / "baseline_model_report.md"


def sigmoid(z: float) -> float:
    if z < -40:
        return 0.0
    if z > 40:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def parse_row(row):
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


def split(rows):
    train_x, train_y, test_x, test_y = [], [], [], []
    for idx, row in enumerate(rows):
        x, y = parse_row(row)
        if idx % 5 == 0:
            test_x.append(x)
            test_y.append(y)
        else:
            train_x.append(x)
            train_y.append(y)
    return train_x, train_y, test_x, test_y


def train_logreg(x_train, y_train, epochs=300, lr=0.001):
    if not x_train:
        return [0.0] * 7
    n_features = len(x_train[0])
    w = [0.0] * (n_features + 1)  # bias + features

    for _ in range(epochs):
        grad = [0.0] * (n_features + 1)
        for x, y in zip(x_train, y_train):
            z = w[0] + sum(w[i + 1] * x[i] for i in range(n_features))
            p = sigmoid(z)
            err = p - y
            grad[0] += err
            for i in range(n_features):
                grad[i + 1] += err * x[i]
        m = max(1, len(x_train))
        for i in range(len(w)):
            w[i] -= lr * (grad[i] / m)
    return w


def predict_prob(weights, x):
    z = weights[0] + sum(weights[i + 1] * x[i] for i in range(len(x)))
    return sigmoid(z)


def evaluate(weights, xs, ys, threshold=0.5):
    if not xs:
        return {"precision": 0.0, "recall": 0.0, "accuracy": 0.0, "tp": 0, "fp": 0, "fn": 0, "tn": 0}
    tp = fp = fn = tn = 0
    for x, y in zip(xs, ys):
        p = 1 if predict_prob(weights, x) >= threshold else 0
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
    accuracy = (tp + tn) / max(1, len(xs))
    return {
        "precision": precision,
        "recall": recall,
        "accuracy": accuracy,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }


def main():
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")

    with DATASET_PATH.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    x_train, y_train, x_test, y_test = split(rows)
    weights = train_logreg(x_train, y_train)
    metrics = evaluate(weights, x_test, y_test, threshold=0.5)

    model = {
        "feature_order": [
            "valor_actual",
            "valor_referencia",
            "prioridad_alta",
            "prioridad_media",
            "semaforo_rojo",
            "semaforo_amarillo",
        ],
        "weights": weights,
        "threshold": 0.5,
        "metrics_test": metrics,
        "train_size": len(x_train),
        "test_size": len(x_test),
    }
    MODEL_PATH.write_text(json.dumps(model, indent=2), encoding="utf-8")

    report = f"""# Baseline Model Report

- Train rows: **{len(x_train)}**
- Test rows: **{len(x_test)}**
- Precision (test): **{metrics['precision']:.4f}**
- Recall (test): **{metrics['recall']:.4f}**
- Accuracy (test): **{metrics['accuracy']:.4f}**

Confusion matrix (test):

- TP: {metrics['tp']}
- FP: {metrics['fp']}
- FN: {metrics['fn']}
- TN: {metrics['tn']}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Model written to: {MODEL_PATH}")
    print(f"Report written to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
