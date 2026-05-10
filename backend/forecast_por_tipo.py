"""
Forecast de unidades por producto — Modelo pooled por tipo (T / C)
==================================================================
Entrena UN modelo por tipo (T=Técnicos, C=Commodities) con todos los
productos del tipo. Mucho más datos que el modelo por producto individual.

Mejoras respecto a forecast_por_producto.py:
  - Datos de entrenamiento: ~24 obs  →  ~384 (C) / ~960 (T)
  - Target en log1p: estabiliza varianza entre productos de distinto volumen
  - Features de lags también en log1p: escala coherente entre todos los productos
  - id_prod_enc: el modelo aprende el perfil de cada producto

Uso:
    python forecast_por_tipo.py                  # modo interactivo
    python forecast_por_tipo.py 4912 6           # producto 4912, 6 meses
    python forecast_por_tipo.py 5099 12          # producto T, 12 meses
    python forecast_por_tipo.py --lista          # top productos
"""

import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
import warnings

# xgboost / sklearn se importan dentro de las funciones que los usan para que FastAPI
# pueda arrancar aunque falle libomp en macOS hasta la primera llamada al forecast.
warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent  # carpeta del proyecto (INTERHACK), hermana de backend/
OUT = BASE / "xgb_output_forecast" / "tipos"
OUT.mkdir(parents=True, exist_ok=True)


def _find_master_csv() -> Path | None:
    """Busca Master junto al backend, o en db/raw/ (reorg 2026-05); fallback a db/."""
    for p in (
        BASE / "Master_Datos_Unificado.csv",
        REPO_ROOT / "db" / "raw" / "Master_Datos_Unificado.csv",
        REPO_ROOT / "db" / "Master_Datos_Unificado.csv",  # legacy
    ):
        if p.is_file():
            return p
    return None


def _resolve_dimension_columns(df: pd.DataFrame) -> tuple[str, str, str]:
    """Prefiere columnas Prod_* para no coger Pot_* u otras dimensiones del Master."""
    cols = list(df.columns)

    def bloque_col():
        for c in cols:
            if c.startswith("Prod_") and "Bloque" in c:
                return c
        for c in cols:
            if "Bloque" in c:
                return c
        return None

    def cat_col():
        for c in cols:
            if c.startswith("Prod_") and "Categoria_H" in c:
                return c
        for c in cols:
            if "Categoria_H" in c and not c.startswith("Pot_"):
                return c
        for c in cols:
            if "Categoria_H" in c:
                return c
        return None

    def fam_col():
        for c in cols:
            if c.startswith("Prod_") and "Familia_H" in c:
                return c
        for c in cols:
            if "Familia_H" in c:
                return c
        return None

    bloque, cat, fam = bloque_col(), cat_col(), fam_col()
    if not all((bloque, cat, fam)):
        raise ValueError(
            "No se pudieron detectar columnas Bloque / Categoria_H / Familia_H en el Master CSV."
        )
    return bloque, cat, fam


CORTE = pd.Timestamp("2024-01-01")

TIPO_COLOR = {"C": "#2563EB", "T": "#16A34A"}

plt.rcParams.update({
    "figure.facecolor": "#F8F9FA", "axes.facecolor": "#FFFFFF",
    "axes.grid": True, "grid.alpha": 0.35, "font.size": 11,
    "axes.spines.top": False, "axes.spines.right": False,
})

FEATS = [
    "lag_1_log", "lag_2_log", "lag_3_log", "lag_6_log", "lag_12_log",
    "roll_3_log", "roll_6_log",
    "mes", "trim", "año", "trend",
    "log_vol_base", "id_prod_enc",
]

# ── Estado global (lazy: no entrena al importar; sirve para FastAPI / scripts) ─
_initialized = False
_df = None
_catalogo = None
_all_months = None
_trend_map = None
_max_trend = None
_bloque_col = None
_cat_col = None
_fam_col = None
_models = None
_pooled = None


def _ensure_forecast_ready(verbose: bool = False):
    """Carga Master CSV y entrena modelos pooled T/C una sola vez."""
    global _initialized, _df, _catalogo, _all_months, _trend_map, _max_trend
    global _bloque_col, _cat_col, _fam_col, _models, _pooled
    if _initialized:
        return
    master_path = _find_master_csv()
    if master_path is None:
        raise FileNotFoundError(
            "No se encontró Master_Datos_Unificado.csv. Colócalo en backend/ o en db/ "
            "(por ejemplo db/Master_Datos_Unificado.csv)."
        )
    if verbose:
        print(f"Cargando datos desde {master_path}...")
    _df = pd.read_csv(master_path, sep=";", encoding="utf-8-sig", low_memory=False)
    _df.columns = _df.columns.str.strip()
    _df["Fecha"] = pd.to_datetime(_df["Fecha"])
    _df["Unidades"] = pd.to_numeric(_df["Unidades"], errors="coerce").abs().fillna(0)

    _bloque_col, _cat_col, _fam_col = _resolve_dimension_columns(_df)

    _df["tipo"] = _df[_bloque_col].apply(
        lambda b: "T" if "cni" in str(b).lower() else "C"
    )

    _all_months = pd.period_range(
        _df["Fecha"].dt.to_period("M").min(),
        _df["Fecha"].dt.to_period("M").max(),
        freq="M",
    )
    _trend_map = {p: i for i, p in enumerate(_all_months)}
    _max_trend = max(_trend_map.values())

    _catalogo = (
        _df.groupby("Id.Producto")
        .agg(
            tipo=("tipo", "first"),
            categoria=(_cat_col, "first"),
            familia=(_fam_col, "first"),
            bloque=(_bloque_col, "first"),
            meses_venta=("Fecha", lambda x: x.dt.to_period("M").nunique()),
            unidades_tot=("Unidades", "sum"),
        )
        .reset_index()
        .sort_values("unidades_tot", ascending=False)
    )

    if verbose:
        print("Construyendo datasets pooled y entrenando modelos T y C...")
    _pooled_C, _enc_C = _build_pooled("C")
    _pooled_T, _enc_T = _build_pooled("T")
    _model_C = _train_model(_pooled_C, "C", verbose=verbose)
    _model_T = _train_model(_pooled_T, "T", verbose=verbose)

    _models = {"C": (_model_C, _enc_C), "T": (_model_T, _enc_T)}
    _pooled = {"C": _pooled_C, "T": _pooled_T}
    _initialized = True


# ── CONSTRUCCIÓN DE SERIES ─────────────────────────────────────────────────────
def _build_series(id_producto, id_enc, log_vol_base):
    """Serie mensual de un producto con features log-transformados."""
    df_p = _df[_df["Id.Producto"] == id_producto]
    m = (df_p.groupby(df_p["Fecha"].dt.to_period("M"))["Unidades"]
             .sum()
             .reindex(_all_months, fill_value=0)
             .reset_index())
    m.columns = ["periodo", "unidades"]
    m["ds"]    = m["periodo"].dt.to_timestamp()
    m["trend"] = m["periodo"].map(_trend_map)
    m["mes"]   = m["ds"].dt.month
    m["trim"]  = m["ds"].dt.quarter
    m["año"]   = m["ds"].dt.year

    # Lags en escala natural → log1p
    for lag in [1, 2, 3, 6, 12]:
        m[f"lag_{lag}_log"] = np.log1p(m["unidades"].shift(lag).fillna(0))

    # Rolling means en log1p
    m["roll_3_log"] = np.log1p(
        m["unidades"].shift(1).rolling(3, min_periods=1).mean().fillna(0)
    )
    m["roll_6_log"] = np.log1p(
        m["unidades"].shift(1).rolling(6, min_periods=1).mean().fillna(0)
    )

    m["log_vol_base"] = log_vol_base
    m["id_prod_enc"]  = id_enc

    # Target en log1p
    m["y"] = np.log1p(m["unidades"])

    return m


def _build_pooled(tipo):
    """Apila las series de todos los productos del tipo dado."""
    prods = sorted(
        _catalogo[_catalogo["tipo"] == tipo]["Id.Producto"].tolist()
    )
    enc_map = {pid: i for i, pid in enumerate(prods)}

    frames = []
    for pid in prods:
        info = _catalogo[_catalogo["Id.Producto"] == pid].iloc[0]
        vol_base   = info["unidades_tot"] / max(info["meses_venta"], 1)
        log_vol_b  = float(np.log1p(vol_base))
        s = _build_series(pid, enc_map[pid], log_vol_b)
        s["id_prod"] = pid
        frames.append(s)

    pooled = pd.concat(frames, ignore_index=True)
    return pooled, enc_map


def _train_model(pooled, tipo, verbose: bool = True):
    """Entrena XGBRegressor en el dataset pooled (target en log1p)."""
    from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
    from xgboost import XGBRegressor

    train = pooled[pooled["ds"] < CORTE]
    test = pooled[pooled["ds"] >= CORTE]

    n_prod_tr = train["id_prod"].nunique()
    n_prod_te = test["id_prod"].nunique()
    if verbose:
        print(
            f"\n  [{tipo}] Train: {len(train)} obs / {n_prod_tr} prod  |  "
            f"Test: {len(test)} obs / {n_prod_te} prod"
        )

    X_tr, y_tr = train[FEATS].values, train["y"].values
    X_te, y_te = test[FEATS].values,  test["y"].values

    model = XGBRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.7,
        reg_alpha=0.5, reg_lambda=3.0,
        min_child_weight=3,
        random_state=42, verbosity=0,
    )
    model.fit(X_tr, y_tr)

    # Métricas en escala original
    y_tr_pred = np.expm1(model.predict(X_tr))
    y_te_pred = np.expm1(model.predict(X_te))
    y_tr_real = np.expm1(y_tr)
    y_te_real = np.expm1(y_te)

    r2_tr  = r2_score(y_tr_real, y_tr_pred)
    mae_tr = mean_absolute_error(y_tr_real, y_tr_pred)
    r2_te  = r2_score(y_te_real, y_te_pred)
    mae_te = mean_absolute_error(y_te_real, y_te_pred)
    # MAPE solo sobre meses con ventas > 0 (evita division por cero)
    mask = y_te_real > 0
    mape_te = (mean_absolute_percentage_error(y_te_real[mask], y_te_pred[mask]) * 100
               if mask.sum() > 0 else float("nan"))

    if verbose:
        print(f"  [{tipo}] Train → R²={r2_tr:.3f}  MAE={mae_tr:.0f} ud/mes")
        print(f"  [{tipo}] Test  → R²={r2_te:.3f}  MAE={mae_te:.0f}  MAPE={mape_te:.1f}% (meses activos)")

    return model


def listar_productos(top=40):
    _ensure_forecast_ready(verbose=True)
    print(f"\n{'ID':>10}  {'Tipo':>5}  {'Categoria':<18}  {'Familia':<14}  "
          f"{'Meses':>6}  {'Unidades':>10}")
    print("-" * 76)
    for _, row in _catalogo.head(top).iterrows():
        print(f"{row['Id.Producto']:>10}  {str(row['tipo']):>5}  "
              f"{str(row['categoria']):<18}  {str(row['familia']):<14}  "
              f"{row['meses_venta']:>6}  {row['unidades_tot']:>10,.0f}")


def _ts_iso(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%d")


def _rolling_future_for_product(pid: int, meses_futuros: int) -> pd.DataFrame | None:
    """
    Genera solo el tramo futuro (ds, predicho) para un producto del catálogo Master.
    None si no hay historia suficiente para el modelo.
    """
    pid = int(pid)
    if _catalogo is None or pid not in _catalogo["Id.Producto"].values:
        return None
    info = _catalogo[_catalogo["Id.Producto"] == pid].iloc[0]
    tipo = info["tipo"]
    model, enc_map = _models[tipo]
    id_enc = enc_map.get(pid, 0)
    vol_base = info["unidades_tot"] / max(info["meses_venta"], 1)
    log_vol_base = float(np.log1p(vol_base))
    serie = _build_series(pid, id_enc, log_vol_base)
    datos = serie.copy().reset_index(drop=True)
    if len(datos) < 6:
        return None
    train = datos[datos["ds"] < CORTE]
    if len(train) < 3:
        return None

    hist_units = list(serie["unidades"].values)
    hist_dates = list(serie["ds"].values)
    future_rows = []
    for _ in range(meses_futuros):
        nxt = pd.Timestamp(hist_dates[-1]) + pd.DateOffset(months=1)
        h = hist_units
        nxt_period = nxt.to_period("M")
        if nxt_period in _trend_map:
            trend_val = _trend_map[nxt_period]
        else:
            months_beyond = nxt_period.ordinal - max(p.ordinal for p in _trend_map)
            trend_val = _max_trend + months_beyond
        r = {
            "lag_1_log": np.log1p(h[-1]),
            "lag_2_log": np.log1p(h[-2]) if len(h) >= 2 else 0.0,
            "lag_3_log": np.log1p(h[-3]) if len(h) >= 3 else 0.0,
            "lag_6_log": np.log1p(h[-6]) if len(h) >= 6 else 0.0,
            "lag_12_log": np.log1p(h[-12]) if len(h) >= 12 else 0.0,
            "roll_3_log": np.log1p(np.mean(h[-3:])) if len(h) >= 3 else 0.0,
            "roll_6_log": np.log1p(np.mean(h[-6:])) if len(h) >= 6 else 0.0,
            "mes": nxt.month,
            "trim": (nxt.month - 1) // 3 + 1,
            "año": nxt.year,
            "trend": trend_val,
            "log_vol_base": log_vol_base,
            "id_prod_enc": id_enc,
        }
        pred_log = float(model.predict(np.array([[r[f] for f in FEATS]]))[0])
        pred = max(0.0, round(float(np.expm1(pred_log)), 1))
        future_rows.append({"ds": nxt, "predicho": pred})
        hist_units.append(pred)
        hist_dates.append(nxt)
    return pd.DataFrame(future_rows)


def get_portfolio_purchases_payload(meses_futuros: int = 12, max_productos: int | None = None):
    """
    Vista agregada: suma mensual de unidades reales (Master) y suma de forecasts por mes
    (misma lógica que ``exportar_forecast_csv`` / un producto, pero acumulado en cartera).
    """
    from collections import defaultdict

    try:
        _ensure_forecast_ready(verbose=False)
    except FileNotFoundError as e:
        return {"available": False, "error": str(e)}
    except (ValueError, OSError) as e:
        return {"available": False, "error": str(e)}
    except Exception as e:
        msg = str(e)
        if "libomp" in msg or "libxgboost" in msg.lower() or "XGBoost Library" in msg:
            msg = (
                "XGBoost no carga (OpenMP). En macOS suele faltar libomp: ejecuta `brew install libomp` "
                "y reinicia el backend. "
                + msg[:300]
            )
        return {"available": False, "error": msg}

    monthly = _df.groupby(_df["Fecha"].dt.to_period("M"))["Unidades"].sum().sort_index()
    historical = [
        {"ds": p.to_timestamp().strftime("%Y-%m-%d"), "unidades": float(v)} for p, v in monthly.items()
    ]

    pids = [int(x) for x in _catalogo["Id.Producto"].tolist()]
    if max_productos is not None:
        pids = pids[: max(0, int(max_productos))]

    fut_acc: dict[str, float] = defaultdict(float)
    skipped = 0
    for pid in pids:
        fdf = _rolling_future_for_product(pid, meses_futuros)
        if fdf is None or len(fdf) == 0:
            skipped += 1
            continue
        for _, row in fdf.iterrows():
            ds = row["ds"]
            key = pd.Timestamp(ds).strftime("%Y-%m-%d")
            fut_acc[key] += float(row["predicho"])

    future_list = [{"ds": k, "predicho_total": round(v, 1)} for k, v in sorted(fut_acc.items())]
    ultimo_real = _df["Fecha"].max()
    ustr = ultimo_real.strftime("%Y-%m-%d") if pd.notna(ultimo_real) else ""

    return {
        "available": True,
        "descripcion": "Histórico = suma mensual de unidades en Master; futuro = suma de forecasts XGB por producto (mismo script que exportar CSV).",
        "fuente_historico": "Master_Datos_Unificado",
        "ultima_fecha_venta_master": ustr,
        "n_productos_catalogo_master": int(len(_catalogo)),
        "n_productos_procesados": len(pids),
        "n_productos_sin_forecast": skipped,
        "meses_futuros": meses_futuros,
        "series": {
            "historical_monthly_total": historical,
            "future_monthly_total": future_list,
        },
        "totales": {
            "unidades_historicas_master": round(float(_df["Unidades"].sum()), 1),
            "suma_forecast_periodo": round(sum(f["predicho_total"] for f in future_list), 1),
        },
    }


def get_forecast_chart_payload(id_producto, meses_futuros: int = 6):
    """
    Misma lógica que ``forecast_producto`` pero devuelve datos JSON para el panel web
    (sin abrir ventanas ni depender de UI). Incluye train/test/futuro y error % en test.
    """
    from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score

    try:
        _ensure_forecast_ready(verbose=False)
    except FileNotFoundError as e:
        return {"available": False, "error": str(e)}
    except (ValueError, OSError) as e:
        return {"available": False, "error": str(e)}
    except Exception as e:
        msg = str(e)
        if "libomp" in msg or "libxgboost" in msg.lower() or "XGBoost Library" in msg:
            msg = (
                "XGBoost no carga (OpenMP). En macOS suele faltar libomp: ejecuta `brew install libomp` "
                "y reinicia el backend. "
                + msg[:300]
            )
        return {"available": False, "error": msg}
    try:
        pid = int(id_producto)
    except (TypeError, ValueError):
        return {"available": False, "error": "ID de producto inválido"}
    if pid not in _catalogo["Id.Producto"].values:
        return {"available": False, "error": "Producto no encontrado en Master_Datos_Unificado"}

    info = _catalogo[_catalogo["Id.Producto"] == pid].iloc[0]
    tipo = info["tipo"]
    model, enc_map = _models[tipo]
    id_enc = enc_map.get(pid, 0)
    n_pool = int(_pooled[tipo]["id_prod"].nunique())

    vol_base = info["unidades_tot"] / max(info["meses_venta"], 1)
    log_vol_base = float(np.log1p(vol_base))

    serie = _build_series(pid, id_enc, log_vol_base)
    datos = serie.copy().reset_index(drop=True)

    if len(datos) < 6:
        return {"available": False, "error": f"Solo {len(datos)} meses de historia; se requieren al menos 6."}
    train = datos[datos["ds"] < CORTE]
    test = datos[datos["ds"] >= CORTE]
    if len(train) < 3:
        return {"available": False, "error": "Menos de 3 meses en periodo de entrenamiento."}

    X_tr = train[FEATS].values
    X_te = test[FEATS].values if len(test) > 0 else None
    y_tr_pred = np.expm1(model.predict(X_tr))
    y_te_pred = np.expm1(model.predict(X_te)) if X_te is not None else np.array([])
    y_tr_real = train["unidades"].values
    y_te_real = test["unidades"].values if len(test) > 0 else np.array([])

    r2_tr = r2_score(y_tr_real, y_tr_pred) if len(y_tr_real) else None
    mae_tr = mean_absolute_error(y_tr_real, y_tr_pred) if len(y_tr_real) else None
    r2_te = mae_te = mape_te = None
    if len(y_te_pred) > 0 and len(y_te_real) > 0:
        r2_te = r2_score(y_te_real, y_te_pred)
        mae_te = mean_absolute_error(y_te_real, y_te_pred)
        mask_te = y_te_real > 0
        mape_te = (
            float(mean_absolute_percentage_error(y_te_real[mask_te], y_te_pred[mask_te]) * 100)
            if mask_te.sum() > 0
            else None
        )

    future_df = _rolling_future_for_product(pid, meses_futuros)
    if future_df is None or len(future_df) == 0:
        return {"available": False, "error": "No se pudo generar el forecast futuro."}
    tipo_hex = TIPO_COLOR.get(str(tipo), "#2563EB")

    real_series = [{"ds": _ts_iso(r["ds"]), "unidades": float(r["unidades"])} for _, r in serie.iterrows()]
    train_pred = [
        {"ds": _ts_iso(row["ds"]), "valor": float(y_tr_pred[i])} for i, (_, row) in enumerate(train.iterrows())
    ]
    test_points = []
    err_pct = []
    if len(test) > 0 and len(y_te_pred) > 0:
        for i in range(len(test)):
            row = test.iloc[i]
            ds = _ts_iso(row["ds"])
            test_points.append(
                {"ds": ds, "real": float(y_te_real[i]), "pred": float(y_te_pred[i])}
            )
            err_pct.append(
                {
                    "ds": ds,
                    "pct": float((y_te_pred[i] - y_te_real[i]) / (y_te_real[i] + 1) * 100),
                }
            )

    future_out = [{"ds": _ts_iso(r["ds"]), "predicho": float(r["predicho"])} for _, r in future_df.iterrows()]

    return {
        "available": True,
        "producto_id": pid,
        "tipo": str(tipo),
        "tipo_color": tipo_hex,
        "categoria": str(info["categoria"]),
        "familia": str(info["familia"]),
        "meses_con_venta": int(info["meses_venta"]),
        "unidades_totales_historicas": float(info["unidades_tot"]),
        "modelo_pooled_productos": n_pool,
        "corte_train_test": _ts_iso(CORTE),
        "metrics": {
            "train_r2": None if r2_tr is None else float(r2_tr),
            "train_mae": None if mae_tr is None else float(mae_tr),
            "test_r2": None if r2_te is None else float(r2_te),
            "test_mae": None if mae_te is None else float(mae_te),
            "test_mape_pct": None if mape_te is None else float(mape_te),
        },
        "series": {
            "real": real_series,
            "train_pred": train_pred,
            "test": test_points,
            "test_error_pct": err_pct,
            "future": future_out,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
def forecast_producto(id_producto, meses_futuros=6, guardar=True, verbose=True):
    """
    Genera forecast de unidades para un producto usando el modelo pooled de
    su tipo (T o C).

    Parámetros
    ----------
    id_producto   : ID del producto (int o str)
    meses_futuros : meses futuros a predecir (default 6)
    guardar       : guarda el gráfico en xgb_output_forecast/tipos/
    verbose       : imprime métricas por consola

    Retorna
    -------
    DataFrame con columnas [ds, predicho] para los meses futuros
    """
    _ensure_forecast_ready(verbose=verbose)
    id_producto = int(id_producto)

    if id_producto not in _catalogo["Id.Producto"].values:
        print(f"[ERROR] Producto {id_producto} no encontrado en el dataset.")
        return None

    info = _catalogo[_catalogo["Id.Producto"] == id_producto].iloc[0]
    tipo = info["tipo"]
    model, enc_map = _models[tipo]
    id_enc         = enc_map.get(id_producto, 0)
    n_pool         = _pooled[tipo]["id_prod"].nunique()

    vol_base     = info["unidades_tot"] / max(info["meses_venta"], 1)
    log_vol_base = float(np.log1p(vol_base))

    if verbose:
        print(f"\nProducto {id_producto}  |  Tipo={tipo}  |  "
              f"{info['categoria']}  |  {info['familia']}")
        print(f"  Meses con ventas : {info['meses_venta']}  |  "
              f"Total unidades: {info['unidades_tot']:,.0f}")
        print(f"  Modelo pooled    : {n_pool} productos tipo {tipo}  "
              f"(~{len(_pooled[tipo])} obs de entrenamiento total)")

    # ── Serie del producto con features ──────────────────────────────────────
    serie = _build_series(id_producto, id_enc, log_vol_base)
    datos = serie.copy().reset_index(drop=True)

    if len(datos) < 6:
        print(f"[AVISO] Producto {id_producto}: solo {len(datos)} obs.")
        return None

    train = datos[datos["ds"] <  CORTE]
    test  = datos[datos["ds"] >= CORTE]

    if len(train) < 3:
        print(f"[AVISO] Producto {id_producto}: menos de 3 meses en train.")
        return None

    X_tr = train[FEATS].values
    X_te = test[FEATS].values if len(test) > 0 else None

    y_tr_pred = np.expm1(model.predict(X_tr))
    y_te_pred = np.expm1(model.predict(X_te)) if X_te is not None else np.array([])
    y_tr_real = train["unidades"].values
    y_te_real = test["unidades"].values if len(test) > 0 else np.array([])

    if verbose:
        from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score

        r2_tr = r2_score(y_tr_real, y_tr_pred)
        mae_tr = mean_absolute_error(y_tr_real, y_tr_pred)
        print(f"  Train (2021-2023) → R²={r2_tr:.3f}  MAE={mae_tr:.1f} ud/mes")
        if len(y_te_pred) > 0:
            r2_te = r2_score(y_te_real, y_te_pred)
            mae_te = mean_absolute_error(y_te_real, y_te_pred)
            mask_te = y_te_real > 0
            mape_te = (
                mean_absolute_percentage_error(y_te_real[mask_te], y_te_pred[mask_te]) * 100
                if mask_te.sum() > 0
                else float("nan")
            )
            print(f"  Test  (2024-2025) → R²={r2_te:.3f}  MAE={mae_te:.1f}  MAPE={mape_te:.1f}%")

    # ── Rolling forecast ──────────────────────────────────────────────────────
    hist_units = list(serie["unidades"].values)
    hist_dates = list(serie["ds"].values)
    future_rows = []

    for step in range(meses_futuros):
        nxt = pd.Timestamp(hist_dates[-1]) + pd.DateOffset(months=1)
        h   = hist_units

        # Trend: extrapola si el mes está más allá del dataset
        nxt_period = nxt.to_period("M")
        if nxt_period in _trend_map:
            trend_val = _trend_map[nxt_period]
        else:
            months_beyond = (nxt_period.ordinal - max(p.ordinal for p in _trend_map))
            trend_val = _max_trend + months_beyond

        r = {
            "lag_1_log":  np.log1p(h[-1]),
            "lag_2_log":  np.log1p(h[-2])  if len(h) >= 2  else 0.0,
            "lag_3_log":  np.log1p(h[-3])  if len(h) >= 3  else 0.0,
            "lag_6_log":  np.log1p(h[-6])  if len(h) >= 6  else 0.0,
            "lag_12_log": np.log1p(h[-12]) if len(h) >= 12 else 0.0,
            "roll_3_log": np.log1p(np.mean(h[-3:])) if len(h) >= 3 else 0.0,
            "roll_6_log": np.log1p(np.mean(h[-6:])) if len(h) >= 6 else 0.0,
            "mes":        nxt.month,
            "trim":       (nxt.month - 1) // 3 + 1,
            "año":        nxt.year,
            "trend":      trend_val,
            "log_vol_base": log_vol_base,
            "id_prod_enc":  id_enc,
        }

        pred_log = float(model.predict(np.array([[r[f] for f in FEATS]]))[0])
        pred     = max(0.0, round(float(np.expm1(pred_log)), 1))

        future_rows.append({"ds": nxt, "predicho": pred})
        hist_units.append(pred)
        hist_dates.append(nxt)

    future_df = pd.DataFrame(future_rows)

    if verbose:
        print(f"\n  Forecast {meses_futuros} meses:")
        print(f"  {'Mes':<12}  {'Predicho':>10}")
        for _, row in future_df.iterrows():
            print(f"  {row['ds'].strftime('%b %Y'):<12}  {row['predicho']:>10,.1f}")

    # ── Gráfico ───────────────────────────────────────────────────────────────
    mensual  = serie[["ds", "unidades"]].copy()
    tipo_col = TIPO_COLOR[tipo]

    fig, axes = plt.subplots(2, 1, figsize=(14, 9),
                              gridspec_kw={"height_ratios": [2.5, 1]})
    titulo = (f"Producto {id_producto}  —  Tipo {tipo}  |  "
              f"{info['categoria']} / {info['familia']}\n"
              f"Modelo pooled ({n_pool} prod. tipo {tipo})  |  "
              f"Train 2021-2023  |  Test 2024-2025  |  Forecast +{meses_futuros} meses")
    fig.suptitle(titulo, fontsize=12, fontweight="bold")

    ax = axes[0]

    if len(train) > 0:
        ax.axvspan(train["ds"].min(), CORTE,
                   alpha=0.05, color="#2563EB", zorder=0)
    if len(test) > 0:
        ax.axvspan(CORTE, test["ds"].max() + pd.DateOffset(months=1),
                   alpha=0.05, color="#DC2626", zorder=0)
    ax.axvline(CORTE, color="#64748B", lw=1.5, linestyle="--",
               label="Corte train/test")

    ax.plot(mensual["ds"], mensual["unidades"],
            color="#1E3A5F", lw=2.2, label="Real", zorder=4)
    ax.plot(train["ds"], y_tr_pred,
            color="#F97316", lw=1.6, linestyle="--", alpha=0.85,
            label="Predicho train", zorder=3)

    if len(y_te_pred) > 0:
        ax.plot(test["ds"], y_te_pred,
                color="#DC2626", lw=2.0, linestyle="--",
                marker="o", markersize=4,
                label="Predicho test 2024-2025", zorder=5)
        ax.fill_between(test["ds"], y_te_real, y_te_pred,
                        alpha=0.12, color="#DC2626")

    ax.axvline(future_df["ds"].iloc[0], color=tipo_col,
               lw=1.2, linestyle=":", alpha=0.6)
    ax.plot(future_df["ds"], future_df["predicho"],
            color=tipo_col, lw=2.2, linestyle=":",
            marker="s", markersize=6,
            label=f"Forecast +{meses_futuros} meses (Tipo {tipo})", zorder=6)

    for _, row in future_df.iterrows():
        ax.annotate(f"{row['predicho']:,.0f}",
                    (row["ds"], row["predicho"]),
                    textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize=7.5, color=tipo_col)

    ax.set_ylabel("Unidades / mes")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=9)
    ax.legend(fontsize=9, loc="upper left", framealpha=0.85)

    ax2 = axes[1]
    if len(y_te_pred) > 0 and len(test) > 0:
        err  = (y_te_pred - y_te_real) / (y_te_real + 1) * 100
        cols = ["#16A34A" if e > 0 else "#DC2626" for e in err]
        ax2.bar(test["ds"], err, color=cols, alpha=0.8, width=20)
        ax2.axhline(0,    color="black",   lw=1)
        ax2.axhline( 10,  color="#94A3B8", lw=0.8, linestyle=":")
        ax2.axhline(-10,  color="#94A3B8", lw=0.8, linestyle=":")
        ax2.set_ylabel("Error relativo (%)")
        ax2.set_title("Error relativo mensual en TEST", fontsize=10, pad=4)
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:+.0f}%"))
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=9)
    else:
        ax2.text(0.5, 0.5, "Sin datos de test para este producto",
                 ha="center", va="center", transform=ax2.transAxes, color="#64748B")

    plt.tight_layout()

    if guardar:
        ruta = OUT / f"forecast_tipo{tipo}_prod_{id_producto}.png"
        plt.savefig(ruta, dpi=150, bbox_inches="tight")
        if verbose:
            print(f"\n  [OK] Grafico guardado: {ruta}")

    plt.show()
    return future_df


# ══════════════════════════════════════════════════════════════════════════════
# HISTORIAL COMPLETO
# ══════════════════════════════════════════════════════════════════════════════
def historial_producto(id_producto, guardar=True):
    """Gráfica lineal: real completo + predicción del modelo solo en el período de test."""
    _ensure_forecast_ready(verbose=True)
    id_producto = int(id_producto)

    if id_producto not in _catalogo["Id.Producto"].values:
        print(f"[ERROR] Producto {id_producto} no encontrado.")
        return

    info         = _catalogo[_catalogo["Id.Producto"] == id_producto].iloc[0]
    tipo         = info["tipo"]
    model, enc_map = _models[tipo]
    id_enc       = enc_map.get(id_producto, 0)
    vol_base     = info["unidades_tot"] / max(info["meses_venta"], 1)
    log_vol_base = float(np.log1p(vol_base))

    serie   = _build_series(id_producto, id_enc, log_vol_base)
    y_pred  = np.expm1(model.predict(serie[FEATS].values))

    fig, ax = plt.subplots(figsize=(14, 5))

    # Real completo — línea continua
    ax.plot(serie["ds"], serie["unidades"],
            color="#1E3A5F", lw=2.2, label="Real")

    # Predicción sobre todo el dataset — línea discontinua roja
    ax.plot(serie["ds"], y_pred,
            color="#DC2626", lw=2.0, linestyle="--",
            label="Predicción modelo")

    ax.set_title(
        f"Producto {id_producto}  —  Tipo {tipo}  |  "
        f"{info['categoria']} / {info['familia']}",
        fontsize=12, fontweight="bold",
    )
    ax.set_ylabel("Unidades / mes")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=9)
    ax.legend(fontsize=9, loc="upper left", framealpha=0.85)

    plt.tight_layout()

    if guardar:
        ruta = OUT / f"historial_tipo{tipo}_prod_{id_producto}.png"
        plt.savefig(ruta, dpi=150, bbox_inches="tight")
        print(f"  [OK] Grafico guardado: {ruta}")

    plt.show()


# ══════════════════════════════════════════════════════════════════════════════
# PREDICCIÓN DIARIA DE UN MES
# ══════════════════════════════════════════════════════════════════════════════
def historial_diario(id_producto, año, mes, guardar=True):
    """
    Gráfica de predicción diaria para un mes concreto.
    La predicción mensual del modelo se distribuye a días según el patrón
    histórico de ventas diarias de ese mismo mes.
    Para meses históricos muestra también las ventas reales por día.
    """
    import calendar

    _ensure_forecast_ready(verbose=True)
    id_producto = int(id_producto)

    if id_producto not in _catalogo["Id.Producto"].values:
        print(f"[ERROR] Producto {id_producto} no encontrado.")
        return

    info  = _catalogo[_catalogo["Id.Producto"] == id_producto].iloc[0]
    tipo  = info["tipo"]
    MESES_ES = ["Ene","Feb","Mar","Abr","May","Jun",
                "Jul","Ago","Sep","Oct","Nov","Dic"]
    mes_nombre = MESES_ES[mes - 1]

    # ── Datos diarios del producto ────────────────────────────────────────────
    df_p = (_df[_df["Id.Producto"] == id_producto][["Fecha", "Unidades"]]
            .groupby("Fecha")["Unidades"].sum().reset_index()
            .sort_values("Fecha"))

    # ── Patrón histórico: fracción de ventas por día-del-mes en ese mes ───────
    df_patron = df_p[df_p["Fecha"].dt.month == mes].copy()
    df_patron["dia"] = df_patron["Fecha"].dt.day
    patron = df_patron.groupby("dia")["Unidades"].mean().reset_index()
    total_patron = patron["Unidades"].sum()
    patron["fraccion"] = (patron["Unidades"] / total_patron
                          if total_patron > 0
                          else 1 / len(patron))

    # ── Predicción mensual del modelo ─────────────────────────────────────────
    target_ds   = pd.Timestamp(f"{año}-{mes:02d}-01")
    ultimo_real = _df["Fecha"].max()

    if target_ds > ultimo_real:
        meses_futuros = ((target_ds.year  - ultimo_real.year) * 12
                         + (target_ds.month - ultimo_real.month))
        future_df = forecast_producto(id_producto, meses_futuros=meses_futuros,
                                      guardar=False, verbose=False)
        if future_df is None:
            print("[ERROR] No se pudo generar forecast mensual.")
            return
        row_mes = future_df[future_df["ds"].dt.to_period("M") == target_ds.to_period("M")]
        if len(row_mes) == 0:
            print(f"[ERROR] No se encontró predicción para {mes_nombre} {año}.")
            return
        pred_mes = float(row_mes["predicho"].iloc[0])
    else:
        model, enc_map = _models[tipo]
        id_enc       = enc_map.get(id_producto, 0)
        vol_base     = info["unidades_tot"] / max(info["meses_venta"], 1)
        log_vol_base = float(np.log1p(vol_base))
        serie = _build_series(id_producto, id_enc, log_vol_base)
        fila  = serie[serie["ds"].dt.to_period("M") == target_ds.to_period("M")]
        if len(fila) == 0:
            print(f"[ERROR] No hay datos del modelo para {mes_nombre} {año}.")
            return
        pred_mes = float(np.expm1(model.predict(fila[FEATS].values)[0]))

    # ── Distribuir predicción mensual a días ──────────────────────────────────
    dias_en_mes = calendar.monthrange(año, mes)[1]
    fechas_mes  = pd.date_range(f"{año}-{mes:02d}-01", periods=dias_en_mes, freq="D")

    pred_diaria = []
    for fecha in fechas_mes:
        frac_row = patron[patron["dia"] == fecha.day]
        frac = (float(frac_row["fraccion"].iloc[0])
                if len(frac_row) > 0 else 1 / dias_en_mes)
        pred_diaria.append({"fecha": fecha, "predicho": round(pred_mes * frac, 1)})
    pred_df = pd.DataFrame(pred_diaria)

    # ── Datos reales del mes (si existen) ─────────────────────────────────────
    mask_mes = (df_p["Fecha"].dt.year == año) & (df_p["Fecha"].dt.month == mes)
    df_mes   = df_p[mask_mes].copy()

    # ── Gráfico ───────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 5))

    if len(df_mes) > 0:
        ax.plot(df_mes["Fecha"], df_mes["Unidades"],
                color="#1E3A5F", lw=2.2, marker="o", markersize=5, label="Real")

    ax.plot(pred_df["fecha"], pred_df["predicho"],
            color="#DC2626", lw=2.0, linestyle="--", marker="s", markersize=4,
            label=f"Predicción diaria  (total mes: {pred_mes:,.0f})")

    ax.set_title(
        f"Producto {id_producto}  —  {mes_nombre} {año}  |  Tipo {tipo}  |  "
        f"{info['categoria']} / {info['familia']}",
        fontsize=12, fontweight="bold",
    )
    ax.set_ylabel("Unidades / día")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=9)
    ax.legend(fontsize=9, loc="upper left", framealpha=0.85)

    plt.tight_layout()

    if guardar:
        ruta = OUT / f"diario_tipo{tipo}_prod_{id_producto}_{año}_{mes:02d}.png"
        plt.savefig(ruta, dpi=150, bbox_inches="tight")
        print(f"  [OK] Grafico guardado: {ruta}")

    plt.show()
    return pred_df


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════
# Uso:
#   python forecast_por_tipo.py                             → menú interactivo
#   python forecast_por_tipo.py 4912                        → forecast 6 meses
#   python forecast_por_tipo.py 4912 12                     → forecast 12 meses
#   python forecast_por_tipo.py 4912 --historial            → historial + predicción
#   python forecast_por_tipo.py 4912 --diario 2024 3        → predicción diaria mar 2024
#   python forecast_por_tipo.py --lista                     → tabla de productos
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--lista" in args or "-l" in args:
        listar_productos(top=40)
        args = [a for a in args if a not in ("--lista", "-l")]

    modo_historial = "--historial" in args or "-h" in args
    args = [a for a in args if a not in ("--historial", "-h")]

    modo_diario = "--diario" in args
    diario_año, diario_mes = None, None
    if modo_diario:
        idx = args.index("--diario")
        try:
            diario_año = int(args[idx + 1])
            diario_mes = int(args[idx + 2])
            args = args[:idx] + args[idx + 3:]
        except (IndexError, ValueError):
            args = args[:idx] + args[idx + 1:]

    id_prod = None
    meses   = 6

    if len(args) >= 1:
        try:
            id_prod = int(args[0])
        except ValueError:
            pass
    if len(args) >= 2:
        try:
            meses = int(args[1])
        except ValueError:
            pass

    if id_prod is None:
        print("\n=== Forecast pooled por tipo de producto (T / C) ===")
        listar_productos(top=20)
        print()
        id_prod = int(input("ID del producto: ").strip())
        print("  [1] Forecast (prediccion futura)")
        print("  [2] Historial (real + prediccion historica)")
        print("  [3] Diario    (prediccion dia a dia de un mes)")
        opcion = input("Opcion [1/2/3, default 1]: ").strip()
        if opcion == "2":
            modo_historial = True
        elif opcion == "3":
            modo_diario = True
            diario_año = int(input("Año  (ej. 2024): ").strip())
            diario_mes = int(input("Mes  (1-12): ").strip())
        else:
            meses_input = input("Meses a predecir [default 6]: ").strip()
            meses = int(meses_input) if meses_input else 6

    if modo_diario:
        if diario_año is None or diario_mes is None:
            diario_año = int(input("Año  (ej. 2024): ").strip())
            diario_mes = int(input("Mes  (1-12): ").strip())
        historial_diario(id_prod, diario_año, diario_mes)
    elif modo_historial:
        historial_producto(id_prod)
    else:
        forecast_producto(id_prod, meses_futuros=meses)
