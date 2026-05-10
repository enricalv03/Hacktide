"""
Pipeline de Classificació de Clients
=====================================
Entrena (o carrega) els models XGBoost i classifica tots els clients
d'un dataset amb la mateixa estructura que Master_Datos_Unificado.csv.

Ús:
    python pipeline.py                          # usa Master_Datos_Unificado.csv
    python pipeline.py fitxer_nou.csv           # qualsevol fitxer CSV
    python pipeline.py --retrain                # força re-entrenament
    python pipeline.py fitxer.csv --retrain     # re-entrena + classifica
    python pipeline.py --help

Sortida (mateixa carpeta que el fitxer d'entrada o BASE):
    clientes_clasificados.csv
    productos_riesgo_cliente.csv
    forecast_clientes.csv
    forecast_productos_global.csv

Models desats (reutilitzables):
    modelos_xgb/model_C.joblib
    modelos_xgb/model_T.joblib
    modelos_xgb/meta.joblib
"""

import sys
import warnings
warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from datetime import timedelta
from xgboost import XGBRegressor

BASE      = Path(__file__).parent
MODEL_DIR = BASE / "modelos_xgb"
MODEL_DIR.mkdir(exist_ok=True)

FEATS = [
    "lag_1_log","lag_2_log","lag_3_log","lag_6_log","lag_12_log",
    "roll_3_log","roll_6_log",
    "mes","trim","ano","trend",
    "log_vol_base","id_prod_enc",
]
CORTE = pd.Timestamp("2024-01-01")

THR_LEAL = 0.70
THR_PROM = 0.30


# ══════════════════════════════════════════════════════════════════════════════
# CÀRREGA DE DADES — accepta qualsevol CSV amb estructura similar
# ══════════════════════════════════════════════════════════════════════════════

def _to_float(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except Exception:
        return np.nan


def _norm(c):
    return (c.strip()
             .replace("\xf1","n").replace("\xe1","a").replace("\xe9","e")
             .replace("\xed","i").replace("\xf3","o").replace("\xfa","u")
             .replace(" ","_"))


def cargar_dataset(ruta: Path) -> pd.DataFrame:
    """
    Carrega el CSV detectant automàticament l'encoding i les columnes.
    Columnes requerides (noms aproximats, detectats per paraula clau):
        Fecha, Id.Cliente, Id.Producto, Unidades, Valores_H,
        Bloque_analítico, Categoria_H, Familia_H
    Columnes opcionals:
        Pot_*_Potencial_H  (per al potencial anual del client)
    """
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(ruta, sep=";", decimal=",", thousands=".",
                             encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue

    df.columns = [_norm(c) for c in df.columns]
    renames = {}
    for c in df.columns:
        cu = c.upper()
        if   "FECHA"       in cu and "FECHA"    not in renames.values(): renames[c] = "FECHA"
        elif "ID.CLIENTE"  in cu or  "ID_CLIENTE"  in cu:                renames[c] = "ID_CLIENT"
        elif "ID.PRODUCTO" in cu or  "ID_PRODUCTO"  in cu:               renames[c] = "ID_PROD"
        elif "UNIDADES"    in cu and "RAW" not in cu:                     renames[c] = "UNIDADES"
        elif "VALORES"     in cu:                                         renames[c] = "VALOR"
        elif "BLOQUE"      in cu:                                         renames[c] = "BLOC"
        elif "FAMILIA_H"   in cu and "POT" not in cu:                     renames[c] = "FAMILIA"
        elif "CATEGORIA_H" in cu and "POT" not in cu:                     renames[c] = "CATEGORIA"
    df.rename(columns=renames, inplace=True)

    df["FECHA"]   = pd.to_datetime(df["FECHA"], errors="coerce")
    df["unitats"] = df.get("UNIDADES", pd.Series(np.nan, index=df.index)).apply(_to_float).abs()
    df["valor"]   = df.get("VALOR",    pd.Series(np.nan, index=df.index)).apply(_to_float).abs()
    df["es_commodity"] = df.get("BLOC", pd.Series("", index=df.index)).str.contains(
        "Commod", case=False, na=False)

    # Potencial anual del client (suma de totes les columnes Pot_*_Potencial_H)
    pot_cols = [c for c in df.columns
                if c.startswith("Pot_") and "Potencial_H" in c and "Cat" not in c]
    if pot_cols:
        for c in pot_cols:
            df[c] = df[c].apply(_to_float).fillna(0)
        df["pot_anual_client"] = df[pot_cols].sum(axis=1)
    else:
        df["pot_anual_client"] = 0.0

    df = df.dropna(subset=["FECHA", "ID_CLIENT"])
    print(f"  Dataset: {len(df):,} transaccions  |  "
          f"{df['ID_CLIENT'].nunique():,} clients  |  "
          f"{df['ID_PROD'].nunique():,} productes")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# MODEL KIT — conté i persisteix tot l'estat dels models XGBoost
# ══════════════════════════════════════════════════════════════════════════════

class ModelKit:
    """Encapsula els dos models XGBoost (T i C) i tot el metadatat necessari."""

    def __init__(self):
        self.model_C    = None
        self.model_T    = None
        self.enc_C      = {}     # {id_prod: int_enc}
        self.enc_T      = {}
        self.catalogo   = None   # DataFrame amb info de cada producte
        self.trend_map  = {}     # {Period: int}
        self.max_trend  = 0
        self.all_months = None
        self.global_units = {}   # {id_prod: total unitats historiques}

    # ── Construcció de series ─────────────────────────────────────────────────

    def _build_series(self, df: pd.DataFrame, id_prod, id_enc, log_vol_base):
        df_p = df[df["ID_PROD"] == id_prod]
        m = (df_p.groupby(df_p["FECHA"].dt.to_period("M"))["unitats"]
                 .sum()
                 .reindex(self.all_months, fill_value=0)
                 .reset_index())
        m.columns = ["periodo", "unitats"]
        m["ds"]    = m["periodo"].dt.to_timestamp()
        m["trend"] = m["periodo"].map(self.trend_map)
        m["mes"]   = m["ds"].dt.month
        m["trim"]  = m["ds"].dt.quarter
        m["ano"]   = m["ds"].dt.year

        for lag in [1, 2, 3, 6, 12]:
            m[f"lag_{lag}_log"] = np.log1p(m["unitats"].shift(lag).fillna(0))

        m["roll_3_log"] = np.log1p(
            m["unitats"].shift(1).rolling(3, min_periods=1).mean().fillna(0))
        m["roll_6_log"] = np.log1p(
            m["unitats"].shift(1).rolling(6, min_periods=1).mean().fillna(0))
        m["log_vol_base"] = log_vol_base
        m["id_prod_enc"]  = id_enc
        m["y"] = np.log1p(m["unitats"])
        return m

    def _build_pooled(self, df: pd.DataFrame, tipo: str):
        prods   = sorted(self.catalogo[self.catalogo["tipo"] == tipo]["Id.Producto"].tolist())
        enc_map = {pid: i for i, pid in enumerate(prods)}
        frames  = []
        for pid in prods:
            info      = self.catalogo[self.catalogo["Id.Producto"] == pid].iloc[0]
            vol_base  = info["unitats_tot"] / max(info["mesos_venda"], 1)
            s = self._build_series(df, pid, enc_map[pid], float(np.log1p(vol_base)))
            s["id_prod"] = pid
            frames.append(s)
        return pd.concat(frames, ignore_index=True), enc_map

    # ── Entrenament ───────────────────────────────────────────────────────────

    def train(self, df: pd.DataFrame):
        print("\nPreparant dades d'entrenament...")

        # Determina rang global de mesos
        self.all_months = pd.period_range(
            df["FECHA"].dt.to_period("M").min(),
            df["FECHA"].dt.to_period("M").max(), freq="M")
        self.trend_map  = {p: i for i, p in enumerate(self.all_months)}
        self.max_trend  = max(self.trend_map.values())

        # Catàleg de productes
        bloque_col = "BLOC"
        cat_col    = "CATEGORIA" if "CATEGORIA" in df.columns else bloque_col
        fam_col    = "FAMILIA"   if "FAMILIA"   in df.columns else bloque_col

        self.catalogo = (
            df.groupby("ID_PROD")
              .agg(
                  tipo        = ("es_commodity", lambda x: "C" if x.any() else "T"),
                  categoria   = (cat_col,        "first"),
                  familia     = (fam_col,        "first"),
                  mesos_venda = ("FECHA",        lambda x: x.dt.to_period("M").nunique()),
                  unitats_tot = ("unitats",      "sum"),
              )
              .reset_index()
              .rename(columns={"ID_PROD": "Id.Producto"})
        )

        self.global_units = {
            int(k): float(v) for k, v in
            df.groupby("ID_PROD")["unitats"].sum().items()
        }

        def _train_one(tipo):
            pooled, enc_map = self._build_pooled(df, tipo)
            train  = pooled[pooled["ds"] <  CORTE]
            test   = pooled[pooled["ds"] >= CORTE]
            n_tr   = train["id_prod"].nunique()
            n_te   = test["id_prod"].nunique()
            print(f"  [{tipo}] Train {len(train):,} obs/{n_tr} prod  |  "
                  f"Test {len(test):,} obs/{n_te} prod")
            model = XGBRegressor(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.7,
                reg_alpha=0.5, reg_lambda=3.0, min_child_weight=3,
                random_state=42, verbosity=0,
            )
            model.fit(train[FEATS].values, train["y"].values)
            if len(test) > 0:
                from sklearn.metrics import r2_score, mean_absolute_error
                y_pred = np.expm1(model.predict(test[FEATS].values))
                y_real = np.expm1(test["y"].values)
                r2  = r2_score(y_real, y_pred)
                mae = mean_absolute_error(y_real, y_pred)
                mask = y_real > 0
                mape = (np.mean(np.abs((y_real[mask]-y_pred[mask])/y_real[mask]))*100
                        if mask.sum() > 0 else float("nan"))
                print(f"  [{tipo}] Test  R²={r2:.3f}  MAE={mae:.0f}  MAPE={mape:.1f}%")
            return model, enc_map

        self.model_C, self.enc_C = _train_one("C")
        self.model_T, self.enc_T = _train_one("T")
        print("  Models entrenats correctament.")

    # ── Persistència ──────────────────────────────────────────────────────────

    def save(self, model_dir: Path = MODEL_DIR):
        model_dir.mkdir(exist_ok=True)
        joblib.dump(self.model_C, model_dir / "model_C.joblib")
        joblib.dump(self.model_T, model_dir / "model_T.joblib")
        meta = {
            "enc_C":       self.enc_C,
            "enc_T":       self.enc_T,
            "trend_map":   {str(k): v for k, v in self.trend_map.items()},
            "max_trend":   self.max_trend,
            "global_units":self.global_units,
            "all_months_start": str(self.all_months[0]),
            "all_months_end":   str(self.all_months[-1]),
        }
        joblib.dump(meta, model_dir / "meta.joblib")
        self.catalogo.to_csv(model_dir / "catalogo.csv", index=False,
                             sep=";", encoding="utf-8-sig")
        print(f"  [OK] Models desats a: {model_dir}/")

    def load(self, model_dir: Path = MODEL_DIR) -> bool:
        paths = [model_dir/"model_C.joblib", model_dir/"model_T.joblib",
                 model_dir/"meta.joblib",    model_dir/"catalogo.csv"]
        if not all(p.exists() for p in paths):
            return False
        self.model_C  = joblib.load(model_dir / "model_C.joblib")
        self.model_T  = joblib.load(model_dir / "model_T.joblib")
        meta          = joblib.load(model_dir / "meta.joblib")
        self.enc_C        = meta["enc_C"]
        self.enc_T        = meta["enc_T"]
        self.trend_map    = {pd.Period(k): v for k, v in meta["trend_map"].items()}
        self.max_trend    = meta["max_trend"]
        self.global_units = meta["global_units"]
        self.all_months   = pd.period_range(
            meta["all_months_start"], meta["all_months_end"], freq="M")
        self.catalogo = pd.read_csv(model_dir / "catalogo.csv",
                                    sep=";", encoding="utf-8-sig")
        print(f"  [OK] Models carregats des de: {model_dir}/")
        return True

    # ── Forecast per producte ─────────────────────────────────────────────────

    def forecast_producte(self, df: pd.DataFrame, id_prod, n_mesos=6):
        """Genera forecast d'unitats per a un producte (rolling, n_mesos)."""
        id_prod = int(id_prod)
        cat_row = self.catalogo[self.catalogo["Id.Producto"] == id_prod]
        if cat_row.empty:
            return None

        info   = cat_row.iloc[0]
        tipo   = info["tipo"]
        model  = self.model_C if tipo == "C" else self.model_T
        enc    = self.enc_C   if tipo == "C" else self.enc_T
        id_enc = enc.get(id_prod, 0)

        vol_base     = info["unitats_tot"] / max(info["mesos_venda"], 1)
        log_vol_base = float(np.log1p(vol_base))

        serie      = self._build_series(df, id_prod, id_enc, log_vol_base)
        hist_units = list(serie["unitats"].values)
        hist_dates = list(serie["ds"].values)

        rows = []
        for step in range(n_mesos):
            nxt = pd.Timestamp(hist_dates[-1]) + pd.DateOffset(months=1)
            nxt_period = nxt.to_period("M")
            trend_val  = (self.trend_map.get(nxt_period) or
                          self.max_trend + (nxt_period.ordinal -
                                            max(p.ordinal for p in self.trend_map)))
            h = hist_units
            r = {
                "lag_1_log":  np.log1p(h[-1]),
                "lag_2_log":  np.log1p(h[-2])  if len(h) >= 2  else 0.0,
                "lag_3_log":  np.log1p(h[-3])  if len(h) >= 3  else 0.0,
                "lag_6_log":  np.log1p(h[-6])  if len(h) >= 6  else 0.0,
                "lag_12_log": np.log1p(h[-12]) if len(h) >= 12 else 0.0,
                "roll_3_log": np.log1p(np.mean(h[-3:])) if len(h) >= 3 else 0.0,
                "roll_6_log": np.log1p(np.mean(h[-6:])) if len(h) >= 6 else 0.0,
                "mes":   nxt.month,
                "trim":  (nxt.month - 1) // 3 + 1,
                "ano":   nxt.year,
                "trend": trend_val,
                "log_vol_base": log_vol_base,
                "id_prod_enc":  id_enc,
            }
            pred = float(np.expm1(model.predict(
                np.array([[r[f] for f in FEATS]]))[0]))
            pred = max(0.0, pred)
            rows.append({"ds": nxt, "predicho": round(pred, 1)})
            hist_units.append(pred)
            hist_dates.append(nxt)

        return pd.DataFrame(rows)

    # ── Forecast per client (share del forecast global del producte) ──────────

    def forecast_client(self, df: pd.DataFrame, id_cli, fc_cache: dict, n_mesos=6):
        """
        Forecast en EUR per al client 'id_cli', basat en la seva participació
        sobre el total d'unitats de cada producte.
        fc_cache: {id_prod: DataFrame[ds, predicho]}  (pre-calculat)
        """
        grp = df[df["ID_CLIENT"] == id_cli]
        if grp.empty:
            return None

        monthly = {}
        for id_prod, grp_p in grp.groupby("ID_PROD"):
            id_prod_int = int(id_prod)
            if id_prod_int not in fc_cache:
                continue
            fc_prod   = fc_cache[id_prod_int]
            client_u  = float(grp_p["unitats"].sum())
            total_u   = float(self.global_units.get(id_prod_int, 0))
            if total_u < 1:
                continue
            share     = min(client_u / total_u, 1.0)
            price_eur = float(grp_p["valor"].sum()) / max(client_u, 1.0)
            for _, row in fc_prod.iterrows():
                ds_str = pd.Timestamp(row["ds"]).strftime("%Y-%m")
                monthly[ds_str] = (monthly.get(ds_str, 0.0) +
                                   float(row["predicho"]) * share * price_eur)

        if not monthly:
            return None

        rows = []
        for k, v in sorted(monthly.items()):
            v = max(0.0, v)
            rows.append({
                "ds":      pd.Timestamp(k),
                "predicho": round(v, 2),
                "lower":   round(v * 0.75, 2),
                "upper":   round(v * 1.30, 2),
                "metode":  "xgboost_share",
            })
        return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# MÈTRIQUES PER CLIENT
# ══════════════════════════════════════════════════════════════════════════════

def _metriques_client(grp_cli, data_ref, pot_anual):
    grp          = grp_cli.sort_values("FECHA")
    data_ult     = grp["FECHA"].max()
    dies_inactiu = (data_ref - data_ult).days

    tmp       = grp.copy()
    tmp["_m"] = tmp["FECHA"].dt.to_period("M")
    monthly   = tmp.groupby("_m")["valor"].sum()
    full_r    = pd.period_range(grp["FECHA"].min().to_period("M"),
                                data_ref.to_period("M"), freq="M")
    hist_m    = pd.Series(
        monthly.reindex(full_r, fill_value=0.0).values,
        index=full_r.to_timestamp()
    )

    mesos_totals = max(len(hist_m), 1)
    mesos_actius = int((hist_m > 0).sum())
    regularitat  = mesos_actius / mesos_totals

    vals_nz = hist_m[hist_m > 0]
    if len(vals_nz) >= 6:
        rec = float(vals_nz.iloc[-3:].mean())
        ant = float(vals_nz.iloc[-6:-3].mean())
        tendencia = (rec - ant) / max(ant, 0.01)
    elif len(vals_nz) >= 3:
        rec = float(vals_nz.iloc[-2:].mean())
        ant = float(vals_nz.iloc[:-2].mean()) if len(vals_nz) > 2 else rec
        tendencia = (rec - ant) / max(ant, 0.01)
    else:
        tendencia = 0.0
    tendencia = float(np.clip(tendencia, -1.0, 2.0))

    spend_12m   = float(grp[grp["FECHA"] >= data_ref - timedelta(days=365)]["valor"].sum())
    spend_total = float(grp["valor"].sum())

    capture_rate = min(spend_12m / pot_anual, 1.5) if pot_anual > 0 else None
    activitat    = capture_rate if capture_rate is not None else min(regularitat * max(1 + tendencia * 0.5, 0.1), 1.0)

    return {
        "activitat":    round(float(activitat),    4),
        "capture_rate": round(float(capture_rate), 4) if capture_rate is not None else None,
        "regularitat":  round(regularitat, 4),
        "tendencia":    round(tendencia,   4),
        "dies_inactiu": dies_inactiu,
        "spend_12m":    round(spend_12m,   2),
        "spend_total":  round(spend_total, 2),
        "mesos_actius": mesos_actius,
        "mesos_totals": int(mesos_totals),
        "hist_m":       hist_m,
    }


def _classificar(activitat, tendencia, dies_inactiu):
    alertes = []
    if dies_inactiu > 270:
        return "RISC", "Inactiu >270 dies", ["ABANDONO_TOTAL"]
    if tendencia < -0.50:
        alertes.append("CAIDA_CRITICA")
    if activitat >= THR_LEAL:
        if tendencia < -0.30:
            alertes.append("EROSION_DESDE_LEAL")
            return "PROMETEDOR", f"Activitat {activitat*100:.0f}% baixant", alertes
        return "LEAL", f"Activitat {activitat*100:.0f}%", alertes
    if activitat >= THR_PROM:
        if tendencia < -0.30:
            alertes.append("RISC_CAIGUDA_PROMETEDOR")
        elif tendencia > 0.30:
            alertes.append("CREIXEMENT_CAP_A_LEAL")
        return "PROMETEDOR", f"Activitat {activitat*100:.0f}%", alertes
    if dies_inactiu > 90:
        alertes.append("SILENCI_PROLONGAT")
    alertes.append("ACTIVITAT_BAIXA")
    return "RISC", f"Activitat {activitat*100:.0f}%", alertes


def _productes_en_risc(grp_cli, data_ref):
    riscos = []
    for id_prod, grp_p in grp_cli.groupby("ID_PROD"):
        grp_p        = grp_p.sort_values("FECHA")
        dies_in_p    = (data_ref - grp_p["FECHA"].max()).days
        tmp          = grp_p.copy()
        tmp["_m"]    = tmp["FECHA"].dt.to_period("M")
        m_sums       = tmp.groupby("_m")["valor"].sum()
        full_r       = pd.period_range(grp_p["FECHA"].min().to_period("M"),
                                       data_ref.to_period("M"), freq="M")
        hist_mp      = m_sums.reindex(full_r, fill_value=0.0)
        vendes_12m   = float(grp_p[grp_p["FECHA"] >= data_ref - timedelta(days=365)]["valor"].sum())
        familia      = grp_p["FAMILIA"].iloc[0] if "FAMILIA" in grp_p.columns else ""
        es_comm      = bool(grp_p["es_commodity"].iloc[0])

        motius = []
        grav   = 0

        if len(hist_mp) >= 6:
            rec_p = float(hist_mp.iloc[-3:].mean())
            ant_p = float(hist_mp.iloc[-6:-3].mean())
            caig  = (ant_p - rec_p) / max(ant_p, 0.01) if ant_p > 0 else 0
            if caig > 0.60:
                motius.append(f"Caiguda -{caig*100:.0f}% (3m vs 3m anteriors)")
                grav = max(grav, 2)
            elif caig > 0.30:
                motius.append(f"Reduccio -{caig*100:.0f}%")
                grav = max(grav, 1)

        if len(grp_p) >= 3:
            intervals = grp_p["FECHA"].sort_values().diff().dt.days.dropna()
            freq_tip  = float(intervals.median())
            if dies_in_p > freq_tip * 2.0 and dies_in_p < 365:
                motius.append(f"Sense compra {dies_in_p}d (freq tipica {freq_tip:.0f}d)")
                grav = max(grav, 2)

        if dies_in_p >= 180:
            motius.append(f"Inactiu {dies_in_p} dies en aquest producte")
            grav = max(grav, 2)

        if len(hist_mp) >= 3:
            base_p  = float(hist_mp.iloc[:-1].mean())
            ultim_p = float(hist_mp.iloc[-1])
            if base_p > 0 and ultim_p > base_p * 2.5:
                motius.append(f"Pujada x{ultim_p/base_p:.1f} vs baseline")
                grav = max(grav, 1)

        if motius:
            riscos.append({
                "id_producte":  id_prod,
                "familia":      familia,
                "tipus":        "COMMODITY" if es_comm else "TECNIC",
                "vendes_12m":   round(vendes_12m, 2),
                "dies_inactiu": dies_in_p,
                "gravetat":     ["INFO","WARNING","CRITICAL"][grav],
                "motius":       " | ".join(motius),
            })

    return sorted(riscos, key=lambda r: (
        {"CRITICAL":0,"WARNING":1,"INFO":2}[r["gravetat"]], -r["vendes_12m"]))


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(dataset_path: Path, retrain: bool = False, n_mesos: int = 6):
    print("=" * 62)
    print("  PIPELINE DE CLASSIFICACIÓ DE CLIENTS")
    print("=" * 62)

    # ── 1. Càrrega del dataset ─────────────────────────────────────────────
    print(f"\n[1/5] Carregant dataset: {dataset_path.name}")
    df       = cargar_dataset(dataset_path)
    data_ref = df["FECHA"].max()
    print(f"  Data referència: {data_ref.date()}")

    # Potencial anual per client
    pot_client = df.groupby("ID_CLIENT")["pot_anual_client"].first().fillna(0)

    # ── 2. Càrrega o entrenament dels models ──────────────────────────────
    kit = ModelKit()
    if not retrain and kit.load(MODEL_DIR):
        print(f"[2/5] Models XGBoost carregats (sense re-entrenament)")
    else:
        print(f"[2/5] Entrenant models XGBoost...")
        kit.train(df)
        kit.save(MODEL_DIR)

    # ── 3. Pre-calcul del forecast per producte ───────────────────────────
    print(f"[3/5] Pre-calculant forecast XGB per {len(kit.catalogo)} productes...")
    fc_cache = {}
    prods    = kit.catalogo["Id.Producto"].tolist()
    for i, id_prod in enumerate(prods, 1):
        fc = kit.forecast_producte(df, id_prod, n_mesos=n_mesos)
        if fc is not None:
            fc_cache[int(id_prod)] = fc
        if i % 5 == 0 or i == len(prods):
            print(f"  {i}/{len(prods)} productes")
    print(f"  {len(fc_cache)} productes amb forecast")

    # ── 4. Loop per client ────────────────────────────────────────────────
    print(f"[4/5] Classificant clients...")
    clients       = df["ID_CLIENT"].unique()
    n_cli         = len(clients)
    resultats     = []
    prod_tots     = []
    forecast_tots = []

    for i, id_cli in enumerate(clients, 1):
        if i % 500 == 0 or i == n_cli:
            print(f"  {i:>5}/{n_cli}  ({i/n_cli*100:.0f}%)")

        grp       = df[df["ID_CLIENT"] == id_cli]
        pot_anual = float(pot_client.get(id_cli, 0.0))

        metriques = _metriques_client(grp, data_ref, pot_anual)
        segment, rao, alertes_seg = _classificar(
            metriques["activitat"], metriques["tendencia"], metriques["dies_inactiu"])

        fc_cli = kit.forecast_client(df, id_cli, fc_cache, n_mesos=n_mesos)
        if fc_cli is None:
            # Fallback lineal si no hi ha forecast XGB
            hist   = metriques["hist_m"]
            avg    = float(hist.iloc[-6:].mean()) if len(hist) >= 6 else float(hist.mean())
            future = pd.date_range(data_ref + timedelta(days=1), periods=n_mesos, freq="MS")
            fc_cli = pd.DataFrame([{
                "ds": d, "predicho": round(avg, 2),
                "lower": round(avg*0.75, 2), "upper": round(avg*1.30, 2),
                "metode": "lineal_fallback",
            } for d in future])

        spend_next_6m = float(fc_cli["predicho"].sum())
        spend_last_6m = float(metriques["hist_m"].iloc[-6:].sum()) if len(metriques["hist_m"]) >= 6 else 0.0
        variacio_pct  = ((spend_next_6m - spend_last_6m) / max(spend_last_6m, 0.01) * 100
                         if spend_last_6m > 0 else 0.0)

        for _, fr in fc_cli.iterrows():
            forecast_tots.append({
                "id_client": id_cli, "segment": segment,
                "ds": fr["ds"].strftime("%Y-%m"),
                "predicho": fr["predicho"], "lower": fr["lower"],
                "upper": fr["upper"], "metode": fr["metode"],
            })

        prod_risc = _productes_en_risc(grp, data_ref)
        n_crit = sum(1 for r in prod_risc if r["gravetat"] == "CRITICAL")
        n_warn = sum(1 for r in prod_risc if r["gravetat"] == "WARNING")
        for r in prod_risc:
            r["id_client"] = id_cli
            r["segment"]   = segment
            prod_tots.append(r)

        resultats.append({
            "id_client":         id_cli,
            "segment":           segment,
            "rao_segment":       rao,
            "alertes_segment":   "|".join(alertes_seg) if alertes_seg else "",
            "activitat_pct":     round(metriques["activitat"]    * 100, 1),
            "capture_rate_pct":  round(metriques["capture_rate"] * 100, 1)
                                 if metriques["capture_rate"] is not None else "",
            "regularitat_pct":   round(metriques["regularitat"]  * 100, 1),
            "tendencia_pct":     round(metriques["tendencia"]     * 100, 1),
            "dies_inactiu":      metriques["dies_inactiu"],
            "spend_12m":         metriques["spend_12m"],
            "spend_total":       metriques["spend_total"],
            "mesos_actius":      metriques["mesos_actius"],
            "mesos_totals":      metriques["mesos_totals"],
            "pot_anual":         round(pot_anual, 2),
            "predicho_6m":       round(spend_next_6m, 2),
            "variacio_6m_pct":   round(variacio_pct, 1),
            "n_prod_critical":   n_crit,
            "n_prod_warning":    n_warn,
            "n_prod_risc_total": len(prod_risc),
        })

    # ── 5. Guardar resultats ──────────────────────────────────────────────
    print(f"[5/5] Guardant resultats...")
    out_dir = dataset_path.parent

    df_res  = pd.DataFrame(resultats)
    df_prds = pd.DataFrame(prod_tots)
    df_fore = pd.DataFrame(forecast_tots)

    df_res.sort_values(
        ["segment","n_prod_critical","spend_12m"],
        ascending=[True, False, False],
        key=lambda x: x.map({"RISC":0,"PROMETEDOR":1,"LEAL":2}) if x.name=="segment" else x,
    ).to_csv(out_dir/"clientes_clasificados.csv", index=False, sep=";",
             encoding="utf-8-sig", float_format="%.2f")

    if len(df_prds) > 0:
        df_prds.to_csv(out_dir/"productos_riesgo_cliente.csv", index=False, sep=";",
                       encoding="utf-8-sig", float_format="%.2f")

    if len(df_fore) > 0:
        df_fore.to_csv(out_dir/"forecast_clientes.csv", index=False, sep=";",
                       encoding="utf-8-sig", float_format="%.2f")

    # Forecast global per producte
    rows_fp = []
    for id_prod, fc_df in fc_cache.items():
        cat_row = kit.catalogo[kit.catalogo["Id.Producto"] == id_prod]
        familia = cat_row["familia"].iloc[0] if len(cat_row) > 0 else ""
        for _, row in fc_df.iterrows():
            rows_fp.append({
                "id_producte": id_prod, "familia": familia,
                "ds": pd.Timestamp(row["ds"]).strftime("%Y-%m"),
                "predicho": round(float(row["predicho"]), 2),
            })
    pd.DataFrame(rows_fp).to_csv(out_dir/"forecast_productos_global.csv", index=False,
                                  sep=";", encoding="utf-8-sig", float_format="%.2f")

    # ── Resum ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  RESULTAT")
    print("=" * 62)
    for seg in ["LEAL","PROMETEDOR","RISC"]:
        sub = df_res[df_res["segment"] == seg]
        pct = len(sub)/len(df_res)*100 if len(df_res) > 0 else 0
        print(f"  {seg:<12} {len(sub):>5} clients ({pct:4.1f}%)  "
              f"| Spend 12m: {sub['spend_12m'].sum():>12,.0f}€  "
              f"| CRITICAL: {int(sub['n_prod_critical'].sum()):>5}")
    print("=" * 62)
    alertes_all = [a for r in resultats for a in r["alertes_segment"].split("|") if a]
    if alertes_all:
        print("\nAlertes:")
        for a, cnt in pd.Series(alertes_all).value_counts().items():
            print(f"  {a:<35} {cnt:>5}")
    print(f"\n[OK] Fitxers guardats a: {out_dir}/")
    print(f"     clientes_clasificados.csv")
    print(f"     productos_riesgo_cliente.csv")
    print(f"     forecast_clientes.csv")
    print(f"     forecast_productos_global.csv")
    print(f"\nPer veure el dashboard:")
    print(f"     streamlit run dashboard_clients.py")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args    = sys.argv[1:]
    retrain = "--retrain" in args or "-r" in args
    args    = [a for a in args if a not in ("--retrain","-r")]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if args:
        dataset_path = Path(args[0])
        if not dataset_path.is_absolute():
            dataset_path = BASE / dataset_path
        if not dataset_path.exists():
            print(f"[ERROR] No s'ha trobat el fitxer: {dataset_path}")
            sys.exit(1)
    else:
        dataset_path = BASE / "Master_Datos_Unificado.csv"
        if not dataset_path.exists():
            print("[ERROR] No s'ha trobat Master_Datos_Unificado.csv al directori actual.")
            print("Ús: python pipeline.py <fitxer.csv>")
            sys.exit(1)

    run_pipeline(dataset_path, retrain=retrain)
