#!/usr/bin/env python3
"""
Carga los CSV producidos por `backend/pipeline.py` en `db/interhack.db`.

Tres tablas materializadas (idempotentes, se vacían y se rellenan en una transacción):
- ml_client_segment        ← clientes_clasificados.csv
- ml_client_product_risk   ← productos_riesgo_cliente.csv
- ml_client_forecast       ← forecast_clientes.csv

Las usa `backend/app.py` para servir segmento (LEAL/PROMETEDOR/RISC), productos en
riesgo y forecast 6m sin reentrenar XGBoost en vivo. El front consume todo via API.

Uso:
    python3 db/load_model_outputs.py [--dataset-dir db/dataset]

Si falta algún CSV se avisa y se continúa con los disponibles.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "interhack.db"
DEFAULT_DATASET_DIR = ROOT / "dataset"

CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"  # Excel/pandas escriben BOM con utf-8-sig


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS ml_client_segment (
    cliente_id          TEXT PRIMARY KEY,
    segment             TEXT NOT NULL,
    rao_segment         TEXT,
    alertes_segment     TEXT,
    activitat_pct       REAL,
    capture_rate_pct    REAL,
    regularitat_pct     REAL,
    tendencia_pct       REAL,
    dies_inactiu        INTEGER,
    spend_12m           REAL,
    spend_total         REAL,
    mesos_actius        INTEGER,
    mesos_totals        INTEGER,
    pot_anual           REAL,
    predicho_6m         REAL,
    variacio_6m_pct     REAL,
    n_prod_critical     INTEGER,
    n_prod_warning      INTEGER,
    n_prod_risc_total   INTEGER,
    fecha_pipeline      DATE
);

CREATE INDEX IF NOT EXISTS idx_ml_segment_segment ON ml_client_segment (segment);

CREATE TABLE IF NOT EXISTS ml_client_product_risk (
    risk_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id      TEXT NOT NULL,
    producto_id     TEXT NOT NULL,
    familia         TEXT,
    tipus           TEXT,
    vendes_12m      REAL,
    dies_inactiu    INTEGER,
    gravetat        TEXT,
    motius          TEXT,
    segment         TEXT,
    fecha_pipeline  DATE
);

CREATE INDEX IF NOT EXISTS idx_ml_cprisk_cliente
    ON ml_client_product_risk (cliente_id, gravetat);
CREATE INDEX IF NOT EXISTS idx_ml_cprisk_producto
    ON ml_client_product_risk (producto_id);

CREATE TABLE IF NOT EXISTS ml_client_forecast (
    cliente_id      TEXT NOT NULL,
    segment         TEXT,
    ds              TEXT NOT NULL,        -- YYYY-MM
    predicho        REAL,
    lower           REAL,
    upper           REAL,
    metode          TEXT,
    fecha_pipeline  DATE,
    PRIMARY KEY (cliente_id, ds)
);

CREATE INDEX IF NOT EXISTS idx_ml_cfore_cli ON ml_client_forecast (cliente_id);
"""


def _to_float(value: str) -> float | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(value: str) -> int | None:
    f = _to_float(value)
    if f is None:
        return None
    try:
        return int(f)
    except (ValueError, OverflowError):
        return None


def _to_text(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def _open_csv(path: Path) -> csv.DictReader | None:
    if not path.is_file():
        return None
    fh = path.open("r", encoding=CSV_ENCODING, newline="")
    return csv.DictReader(fh, delimiter=CSV_DELIMITER)


def _bulk_insert(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple]) -> int:
    cur = conn.cursor()
    n = 0
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= 1000:
            cur.executemany(sql, batch)
            n += len(batch)
            batch.clear()
    if batch:
        cur.executemany(sql, batch)
        n += len(batch)
    return n


def load_segments(conn: sqlite3.Connection, csv_path: Path, fecha: str) -> int:
    reader = _open_csv(csv_path)
    if reader is None:
        print(f"[skip] No existe {csv_path.name}, no se carga ml_client_segment.")
        return 0
    print(f"[load] {csv_path.name} → ml_client_segment")
    conn.execute("DELETE FROM ml_client_segment")
    insert_sql = """
        INSERT INTO ml_client_segment (
            cliente_id, segment, rao_segment, alertes_segment,
            activitat_pct, capture_rate_pct, regularitat_pct, tendencia_pct,
            dies_inactiu, spend_12m, spend_total, mesos_actius, mesos_totals,
            pot_anual, predicho_6m, variacio_6m_pct,
            n_prod_critical, n_prod_warning, n_prod_risc_total,
            fecha_pipeline
        ) VALUES (?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?,  ?)
        ON CONFLICT(cliente_id) DO UPDATE SET
            segment           = excluded.segment,
            rao_segment       = excluded.rao_segment,
            alertes_segment   = excluded.alertes_segment,
            activitat_pct     = excluded.activitat_pct,
            capture_rate_pct  = excluded.capture_rate_pct,
            regularitat_pct   = excluded.regularitat_pct,
            tendencia_pct     = excluded.tendencia_pct,
            dies_inactiu      = excluded.dies_inactiu,
            spend_12m         = excluded.spend_12m,
            spend_total       = excluded.spend_total,
            mesos_actius      = excluded.mesos_actius,
            mesos_totals      = excluded.mesos_totals,
            pot_anual         = excluded.pot_anual,
            predicho_6m       = excluded.predicho_6m,
            variacio_6m_pct   = excluded.variacio_6m_pct,
            n_prod_critical   = excluded.n_prod_critical,
            n_prod_warning    = excluded.n_prod_warning,
            n_prod_risc_total = excluded.n_prod_risc_total,
            fecha_pipeline    = excluded.fecha_pipeline
    """

    def gen():
        for r in reader:
            cid = _to_text(r.get("id_client"))
            seg = _to_text(r.get("segment"))
            if not cid or not seg:
                continue
            yield (
                cid,
                seg,
                _to_text(r.get("rao_segment")),
                _to_text(r.get("alertes_segment")),
                _to_float(r.get("activitat_pct", "")),
                _to_float(r.get("capture_rate_pct", "")),
                _to_float(r.get("regularitat_pct", "")),
                _to_float(r.get("tendencia_pct", "")),
                _to_int(r.get("dies_inactiu", "")),
                _to_float(r.get("spend_12m", "")),
                _to_float(r.get("spend_total", "")),
                _to_int(r.get("mesos_actius", "")),
                _to_int(r.get("mesos_totals", "")),
                _to_float(r.get("pot_anual", "")),
                _to_float(r.get("predicho_6m", "")),
                _to_float(r.get("variacio_6m_pct", "")),
                _to_int(r.get("n_prod_critical", "")),
                _to_int(r.get("n_prod_warning", "")),
                _to_int(r.get("n_prod_risc_total", "")),
                fecha,
            )

    return _bulk_insert(conn, insert_sql, gen())


def load_product_risk(conn: sqlite3.Connection, csv_path: Path, fecha: str) -> int:
    reader = _open_csv(csv_path)
    if reader is None:
        print(f"[skip] No existe {csv_path.name}, no se carga ml_client_product_risk.")
        return 0
    print(f"[load] {csv_path.name} → ml_client_product_risk")
    conn.execute("DELETE FROM ml_client_product_risk")
    insert_sql = """
        INSERT INTO ml_client_product_risk (
            cliente_id, producto_id, familia, tipus,
            vendes_12m, dies_inactiu, gravetat, motius, segment,
            fecha_pipeline
        ) VALUES (?, ?, ?, ?,  ?, ?, ?, ?, ?,  ?)
    """

    def gen():
        for r in reader:
            cid = _to_text(r.get("id_client"))
            pid = _to_text(r.get("id_producte"))
            if not cid or not pid:
                continue
            yield (
                cid,
                pid,
                _to_text(r.get("familia")),
                _to_text(r.get("tipus")),
                _to_float(r.get("vendes_12m", "")),
                _to_int(r.get("dies_inactiu", "")),
                _to_text(r.get("gravetat")),
                _to_text(r.get("motius")),
                _to_text(r.get("segment")),
                fecha,
            )

    return _bulk_insert(conn, insert_sql, gen())


def load_forecast(conn: sqlite3.Connection, csv_path: Path, fecha: str) -> int:
    reader = _open_csv(csv_path)
    if reader is None:
        print(f"[skip] No existe {csv_path.name}, no se carga ml_client_forecast.")
        return 0
    print(f"[load] {csv_path.name} → ml_client_forecast")
    conn.execute("DELETE FROM ml_client_forecast")
    insert_sql = """
        INSERT OR REPLACE INTO ml_client_forecast (
            cliente_id, segment, ds, predicho, lower, upper, metode,
            fecha_pipeline
        ) VALUES (?, ?, ?, ?, ?, ?, ?,  ?)
    """

    def gen():
        for r in reader:
            cid = _to_text(r.get("id_client"))
            ds = _to_text(r.get("ds"))
            if not cid or not ds:
                continue
            yield (
                cid,
                _to_text(r.get("segment")),
                ds,
                _to_float(r.get("predicho", "")),
                _to_float(r.get("lower", "")),
                _to_float(r.get("upper", "")),
                _to_text(r.get("metode")),
                fecha,
            )

    return _bulk_insert(conn, insert_sql, gen())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Carpeta con los CSV de pipeline.py (default: db/dataset, fallback db/).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Ruta a la base SQLite (default: db/interhack.db).",
    )
    args = parser.parse_args()

    if not args.db_path.exists():
        print(f"[ERROR] No se encuentra la base SQLite: {args.db_path}")
        print("        Lánzala antes con `python3 db/create_database.py`.")
        return 1

    # Auto-detección: pipeline.py escribe junto al Master que recibe; aceptamos
    # tanto db/dataset/ como db/ como fallback para que el script "haga lo correcto".
    if args.dataset_dir is None:
        candidates = [DEFAULT_DATASET_DIR, ROOT]
        for cand in candidates:
            if (cand / "clientes_clasificados.csv").is_file():
                args.dataset_dir = cand
                break
        if args.dataset_dir is None:
            args.dataset_dir = DEFAULT_DATASET_DIR

    if not args.dataset_dir.exists():
        print(f"[ERROR] No existe la carpeta de dataset: {args.dataset_dir}")
        return 1
    print(f"[load] dataset-dir: {args.dataset_dir}")

    fecha = date.today().isoformat()

    conn = sqlite3.connect(str(args.db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

        conn.executescript(SCHEMA_DDL)

        with conn:  # transacción única para los 3 loads
            n_seg = load_segments(
                conn, args.dataset_dir / "clientes_clasificados.csv", fecha
            )
            n_risk = load_product_risk(
                conn, args.dataset_dir / "productos_riesgo_cliente.csv", fecha
            )
            n_fore = load_forecast(
                conn, args.dataset_dir / "forecast_clientes.csv", fecha
            )

        print()
        print("=" * 56)
        print(f"  ml_client_segment        : {n_seg:>7,} filas")
        print(f"  ml_client_product_risk   : {n_risk:>7,} filas")
        print(f"  ml_client_forecast       : {n_fore:>7,} filas")
        print("=" * 56)
        print(f"  fecha_pipeline = {fecha}")
        print("[OK] Carga ML completada.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
