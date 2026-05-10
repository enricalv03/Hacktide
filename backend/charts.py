"""
Generador de gràfiques d'evolució lineal
==========================================
Llegeix els CSVs generats pel pipeline i el dataset principal,
i produeix un informe HTML autònom amb gràfiques lineals consistents.

Ús:
    python charts.py                        # usa la carpeta dades/ per defecte
    python charts.py --dades ruta/carpeta   # carpeta amb els CSVs i el master
    python charts.py --top 20               # top N clients al detall (default 15)
    python charts.py --out informe.html     # nom del fitxer de sortida

Prerequisit:
    Haver executat primer:  python pipeline.py
"""

import sys
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
from datetime import timedelta

# ── Paleta i estil globals ────────────────────────────────────────────────────
PAL = {
    "LEAL":       "#16A34A",
    "PROMETEDOR": "#D97706",
    "RISC":       "#DC2626",
    "forecast":   "#2563EB",
    "real":       "#1E3A5F",
    "grid":       "#E5E7EB",
    "text":       "#111827",
    "subtext":    "#6B7280",
    "bg":         "#FFFFFF",
    "bg_panel":   "#F9FAFB",
}

LAYOUT_BASE = dict(
    font=dict(family="Inter, Arial, sans-serif", size=13, color=PAL["text"]),
    paper_bgcolor=PAL["bg"],
    plot_bgcolor=PAL["bg_panel"],
    margin=dict(t=56, b=40, l=60, r=24),
    hovermode="x unified",
    xaxis=dict(gridcolor=PAL["grid"], linecolor=PAL["grid"], showline=True),
    yaxis=dict(gridcolor=PAL["grid"], linecolor=PAL["grid"], zeroline=False),
    legend=dict(orientation="h", y=1.08, x=0, bgcolor="rgba(0,0,0,0)"),
)


def _layout(**extra):
    d = LAYOUT_BASE.copy()
    d.update(extra)
    return d


def _eur(v):
    if v >= 1_000_000:
        return f"{v/1e6:.1f}M€"
    if v >= 1_000:
        return f"{v/1e3:.0f}k€"
    return f"{v:.0f}€"


# ── Càrrega ───────────────────────────────────────────────────────────────────

def _to_float(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except Exception:
        return np.nan


def _norm(c):
    return (c.strip()
             .replace("\xf1", "n").replace("\xe1", "a").replace("\xe9", "e")
             .replace("\xed", "i").replace("\xf3", "o").replace("\xfa", "u")
             .replace(" ", "_"))


def load_all(dades: Path):
    # ── CSVs del pipeline ────────────────────────────────────────────────────
    cli  = pd.read_csv(dades / "clientes_clasificados.csv",
                       sep=";", encoding="utf-8-sig")
    cli["id_client"] = pd.to_numeric(cli["id_client"], errors="coerce")
    cli["alertes_segment"] = cli["alertes_segment"].fillna("")

    prod = pd.read_csv(dades / "productos_riesgo_cliente.csv",
                       sep=";", encoding="utf-8-sig")
    prod["id_client"]   = pd.to_numeric(prod["id_client"],   errors="coerce")
    prod["id_producte"] = pd.to_numeric(prod["id_producte"], errors="coerce")

    fore = pd.read_csv(dades / "forecast_clientes.csv",
                       sep=";", encoding="utf-8-sig")
    fore["id_client"] = pd.to_numeric(fore["id_client"], errors="coerce")
    fore["ds"]        = pd.to_datetime(fore["ds"], format="%Y-%m", errors="coerce")
    fore["predicho"]  = pd.to_numeric(fore["predicho"], errors="coerce").fillna(0)
    fore["lower"]     = pd.to_numeric(fore["lower"],    errors="coerce").fillna(0)
    fore["upper"]     = pd.to_numeric(fore["upper"],    errors="coerce").fillna(0)

    # ── Master (transaccions historiques) ────────────────────────────────────
    master_path = dades / "Master_Datos_Unificado.csv"
    if not master_path.exists():
        # Prova un nivell amunt
        master_path = dades.parent / "Master_Datos_Unificado.csv"

    df_raw = None
    if master_path.exists():
        for enc in ("utf-8-sig", "latin-1", "cp1252"):
            try:
                df_raw = pd.read_csv(master_path, sep=";", decimal=",",
                                     thousands=".", encoding=enc, low_memory=False)
                break
            except UnicodeDecodeError:
                continue
        df_raw.columns = [_norm(c) for c in df_raw.columns]
        renames = {}
        for c in df_raw.columns:
            cu = c.upper()
            if   "FECHA"       in cu and "FECHA"    not in renames.values(): renames[c] = "FECHA"
            elif "ID.CLIENTE"  in cu or "ID_CLIENTE"  in cu:                 renames[c] = "ID_CLIENT"
            elif "ID.PRODUCTO" in cu or "ID_PRODUCTO"  in cu:                renames[c] = "ID_PROD"
            elif "VALORES"     in cu:                                         renames[c] = "VALOR"
            elif "BLOQUE"      in cu:                                         renames[c] = "BLOC"
            elif "FAMILIA_H"   in cu and "POT" not in cu:                     renames[c] = "FAMILIA"
        df_raw.rename(columns=renames, inplace=True)
        df_raw["FECHA"] = pd.to_datetime(df_raw["FECHA"], errors="coerce")
        df_raw["valor"] = df_raw.get("VALOR", pd.Series(np.nan)).apply(_to_float).abs()
        df_raw = df_raw.dropna(subset=["FECHA", "ID_CLIENT"])

    # ── Campanyes ────────────────────────────────────────────────────────────
    camp_path = dades / "Datasets.xlsx - Campañas.csv"
    camps = pd.DataFrame()
    if camp_path.exists():
        camps = pd.read_csv(camp_path)
        camps.columns = [c.strip() for c in camps.columns]
        camps["Fecha inicio"] = pd.to_datetime(camps["Fecha inicio"],
                                               format="%m/%d/%Y", errors="coerce")
        camps["Fecha fin"]    = pd.to_datetime(camps["Fecha fin"],
                                               format="%m/%d/%Y", errors="coerce")
        camps = camps.dropna(subset=["Fecha inicio"]).sort_values("Fecha inicio")

    return cli, prod, fore, df_raw, camps


# ── Funcions de sèrie mensual ─────────────────────────────────────────────────

def mensual_global(df_raw: pd.DataFrame) -> pd.Series:
    tmp = df_raw.copy()
    tmp["_m"] = tmp["FECHA"].dt.to_period("M")
    return tmp.groupby("_m")["valor"].sum().rename_axis("mes")


def mensual_per_segment(df_raw: pd.DataFrame, cli: pd.DataFrame) -> pd.DataFrame:
    merged = df_raw.merge(cli[["id_client","segment"]],
                          left_on="ID_CLIENT", right_on="id_client", how="left")
    merged["_m"] = merged["FECHA"].dt.to_period("M")
    return (merged.groupby(["_m","segment"])["valor"].sum()
                  .reset_index().rename(columns={"valor":"spend","_m":"mes"}))


def mensual_client(df_raw: pd.DataFrame, id_cli, data_ref) -> pd.Series:
    grp = df_raw[df_raw["ID_CLIENT"] == id_cli].copy()
    if grp.empty:
        return pd.Series(dtype=float)
    grp["_m"] = grp["FECHA"].dt.to_period("M")
    monthly   = grp.groupby("_m")["valor"].sum()
    full_r    = pd.period_range(grp["FECHA"].min().to_period("M"),
                                data_ref.to_period("M"), freq="M")
    return pd.Series(monthly.reindex(full_r, fill_value=0.0).values,
                     index=full_r.to_timestamp())


# ── Utilitat de campanyes ─────────────────────────────────────────────────────

def _add_camps(fig, camps, data_ini=None, data_fi=None, row=None, col=None):
    if camps.empty:
        return
    kw = dict(row=row, col=col) if row else {}
    for _, c in camps.iterrows():
        if data_ini and c["Fecha fin"] < data_ini:
            continue
        if data_fi and c["Fecha inicio"] > data_fi:
            continue
        fig.add_vrect(
            x0=c["Fecha inicio"], x1=c["Fecha fin"],
            fillcolor="rgba(251,191,36,0.15)", line_width=0,
            annotation_text=c["Campaña"], annotation_position="top left",
            annotation_font_size=8, annotation_font_color="#92400E",
            **kw,
        )


# ══════════════════════════════════════════════════════════════════════════════
# GRÀFIQUES
# ══════════════════════════════════════════════════════════════════════════════

def g_evolucio_global(df_raw, fore, camps):
    """Evolució mensual de vendes globals + forecast agregat."""
    ser = mensual_global(df_raw)
    ts  = ser.index.to_timestamp()
    vs  = ser.values

    fore_grp = (fore.groupby("ds")
                    .agg(predicho=("predicho","sum"),
                         lower=("lower","sum"),
                         upper=("upper","sum"))
                    .reset_index().sort_values("ds"))

    fig = go.Figure()
    _add_camps(fig, camps, data_ini=ts[0], data_fi=fore_grp["ds"].max())

    # Línia real
    fig.add_trace(go.Scatter(
        x=ts, y=vs, name="Vendes reals",
        mode="lines", line=dict(color=PAL["real"], width=2.5),
        hovertemplate="%{x|%b %Y}: <b>%{customdata}</b>",
        customdata=[_eur(v) for v in vs],
    ))

    # Banda d'incertesa del forecast
    fig.add_trace(go.Scatter(
        x=list(fore_grp["ds"]) + list(fore_grp["ds"][::-1]),
        y=list(fore_grp["upper"]) + list(fore_grp["lower"][::-1]),
        fill="toself",
        fillcolor="rgba(37,99,235,0.10)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Interval de confiança",
        showlegend=True,
    ))

    # Línia de forecast
    fig.add_trace(go.Scatter(
        x=fore_grp["ds"], y=fore_grp["predicho"],
        name="Forecast 6m",
        mode="lines+markers",
        line=dict(color=PAL["forecast"], width=2.5, dash="dash"),
        marker=dict(size=7, symbol="circle"),
        hovertemplate="%{x|%b %Y}: <b>%{customdata}</b>",
        customdata=[_eur(v) for v in fore_grp["predicho"]],
    ))

    # Línia vertical de tall
    fig.add_vline(x=ts[-1], line_dash="dot", line_color=PAL["subtext"],
                  annotation_text="avui", annotation_position="top right",
                  annotation_font_size=11)

    fig.update_layout(**_layout(
        title=dict(text="Evolució de vendes globals · Historial + Forecast 6m",
                   font_size=16),
        yaxis_title="EUR / mes",
        height=400,
    ))
    return fig


def g_evolucio_per_segment(df_raw, cli, fore, camps):
    """Evolució mensual de vendes per segment + forecast per segment."""
    seg_hist = mensual_per_segment(df_raw, cli)
    fore_seg = (fore.merge(cli[["id_client","segment"]], on="id_client", how="left")
                    .groupby(["ds","segment"])
                    .agg(predicho=("predicho","sum"))
                    .reset_index())

    fig = go.Figure()
    data_ini = seg_hist["mes"].min().to_timestamp()
    data_fi  = fore_seg["ds"].max() if not fore_seg.empty else None
    _add_camps(fig, camps, data_ini=data_ini, data_fi=data_fi)

    for seg in ["LEAL", "PROMETEDOR", "RISC"]:
        col = PAL[seg]
        # Historial
        h = seg_hist[seg_hist["segment"] == seg].sort_values("mes")
        if not h.empty:
            ts = h["mes"].dt.to_timestamp()
            fig.add_trace(go.Scatter(
                x=ts, y=h["spend"].values,
                name=seg, legendgroup=seg,
                mode="lines", line=dict(color=col, width=2.2),
                hovertemplate=f"[{seg}] %{{x|%b %Y}}: <b>%{{customdata}}</b>",
                customdata=[_eur(v) for v in h["spend"].values],
            ))
        # Forecast
        f = fore_seg[fore_seg["segment"] == seg].sort_values("ds")
        if not f.empty:
            fig.add_trace(go.Scatter(
                x=f["ds"], y=f["predicho"].values,
                name=f"{seg} forecast", legendgroup=seg,
                mode="lines+markers",
                line=dict(color=col, width=2, dash="dash"),
                marker=dict(size=6, symbol="circle-open"),
                showlegend=False,
                hovertemplate=f"[{seg} fc] %{{x|%b %Y}}: <b>%{{customdata}}</b>",
                customdata=[_eur(v) for v in f["predicho"].values],
            ))

    if not seg_hist.empty:
        fig.add_vline(x=seg_hist["mes"].max().to_timestamp(),
                      line_dash="dot", line_color=PAL["subtext"],
                      annotation_text="avui", annotation_font_size=11)

    fig.update_layout(**_layout(
        title=dict(text="Evolució de vendes per segment · Historial + Forecast 6m",
                   font_size=16),
        yaxis_title="EUR / mes",
        height=420,
    ))
    return fig


def g_forecast_global_barres(fore):
    """Barres mensuals del forecast global agregat (properes 6m)."""
    grp = (fore.groupby("ds")
               .agg(predicho=("predicho","sum"),
                    lower=("lower","sum"),
                    upper=("upper","sum"))
               .reset_index().sort_values("ds"))

    fig = go.Figure(go.Bar(
        x=grp["ds"].dt.strftime("%b %Y"),
        y=grp["predicho"],
        marker_color=PAL["forecast"],
        error_y=dict(
            type="data",
            array=(grp["upper"] - grp["predicho"]).tolist(),
            arrayminus=(grp["predicho"] - grp["lower"]).tolist(),
            thickness=1.5, width=6,
        ),
        text=[_eur(v) for v in grp["predicho"]],
        textposition="outside",
        hovertemplate="%{x}: <b>%{text}</b><extra></extra>",
    ))
    fig.update_layout(**_layout(
        title=dict(text="Forecast agregat · Propers 6 mesos per mes",
                   font_size=16),
        yaxis_title="EUR / mes",
        hovermode="x",
        height=360,
    ))
    return fig


def g_distribucio_segments(cli):
    """Evolució acumulada del compte de clients per segment (snapshot actual)."""
    cnt = cli.groupby("segment")["id_client"].count().reindex(
        ["LEAL","PROMETEDOR","RISC"], fill_value=0)
    spend = cli.groupby("segment")["spend_12m"].sum().reindex(
        ["LEAL","PROMETEDOR","RISC"], fill_value=0)

    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=("Nombre de clients per segment",
                                        "Spend 12m per segment (€)"))

    for seg in ["LEAL","PROMETEDOR","RISC"]:
        fig.add_trace(go.Bar(
            x=[seg], y=[cnt[seg]],
            name=seg, marker_color=PAL[seg],
            text=[f"{cnt[seg]:,}"], textposition="auto",
            showlegend=True, legendgroup=seg,
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            x=[seg], y=[spend[seg]],
            name=seg, marker_color=PAL[seg],
            text=[_eur(spend[seg])], textposition="auto",
            showlegend=False, legendgroup=seg,
        ), row=1, col=2)

    fig.update_layout(**_layout(
        title=dict(text="Distribució actual de clients i volum de negoci",
                   font_size=16),
        height=360, barmode="group",
    ))
    return fig


def g_top_clients(df_raw, cli, fore, camps, top_n=15):
    """Evolució lineal dels top N clients per spend_12m."""
    top = cli.nlargest(top_n, "spend_12m")
    data_ref = df_raw["FECHA"].max()

    fig = go.Figure()
    _add_camps(fig, camps, data_ini=df_raw["FECHA"].min())

    for _, row in top.iterrows():
        id_cli = row["id_client"]
        seg    = row["segment"]
        col    = PAL[seg]
        hist   = mensual_client(df_raw, id_cli, data_ref)
        if hist.empty:
            continue

        # Historial
        fig.add_trace(go.Scatter(
            x=hist.index, y=hist.values,
            name=f"{id_cli} ({seg})", legendgroup=str(id_cli),
            mode="lines", line=dict(color=col, width=1.6),
            opacity=0.85,
            hovertemplate=f"Client {id_cli} · %{{x|%b %Y}}: <b>%{{customdata}}</b>",
            customdata=[_eur(v) for v in hist.values],
        ))

        # Forecast
        fc = fore[fore["id_client"] == id_cli].sort_values("ds")
        if not fc.empty:
            fig.add_trace(go.Scatter(
                x=fc["ds"], y=fc["predicho"],
                name=f"{id_cli} fc", legendgroup=str(id_cli),
                mode="lines", line=dict(color=col, width=1.4, dash="dot"),
                showlegend=False,
                hovertemplate=f"Client {id_cli} fc · %{{x|%b %Y}}: <b>%{{customdata}}</b>",
                customdata=[_eur(v) for v in fc["predicho"].values],
            ))

    fig.add_vline(x=data_ref, line_dash="dot", line_color=PAL["subtext"],
                  annotation_text="avui", annotation_font_size=11)
    fig.update_layout(**_layout(
        title=dict(text=f"Evolució lineal · Top {top_n} clients per spend 12m",
                   font_size=16),
        yaxis_title="EUR / mes",
        height=500,
    ))
    return fig


def g_evolucio_alertes(cli):
    """Barres horitzontals del recompte d'alertes (distribució actual)."""
    alertes = [a for s in cli["alertes_segment"] for a in s.split("|") if a]
    if not alertes:
        return None
    cnt = pd.Series(alertes).value_counts().reset_index()
    cnt.columns = ["alerta","n"]

    COLORS_AL = {
        "ABANDONO_TOTAL":          "#DC2626",
        "CAIDA_CRITICA":           "#DC2626",
        "ACTIVITAT_BAIXA":         "#D97706",
        "SILENCI_PROLONGAT":       "#D97706",
        "RISC_CAIGUDA_PROMETEDOR": "#D97706",
        "EROSION_DESDE_LEAL":      "#D97706",
        "CREIXEMENT_CAP_A_LEAL":   "#16A34A",
    }
    colors = [COLORS_AL.get(a, PAL["subtext"]) for a in cnt["alerta"]]

    fig = go.Figure(go.Bar(
        x=cnt["n"], y=cnt["alerta"],
        orientation="h",
        marker_color=colors,
        text=cnt["n"].astype(str),
        textposition="outside",
        hovertemplate="%{y}: <b>%{x} clients</b><extra></extra>",
    ))
    fig.update_layout(**_layout(
        title=dict(text="Distribució d'alertes de segment · Nombre de clients afectats",
                   font_size=16),
        xaxis_title="Nombre de clients",
        yaxis=dict(autorange="reversed", gridcolor=PAL["grid"]),
        hovermode="y unified",
        height=340,
    ))
    return fig


def g_risc_productes(prod, cli):
    """Evolució del risc per família de producte (barres apilades per gravetat)."""
    if prod.empty:
        return None
    merged = prod.merge(cli[["id_client","spend_12m"]], on="id_client", how="left")
    agg = (merged.groupby(["familia","gravetat"])["vendes_12m"]
                 .sum().reset_index())

    GRAV_COL = {"CRITICAL": "#DC2626", "WARNING": "#D97706", "INFO": "#2563EB"}
    fig = go.Figure()
    for grav in ["CRITICAL","WARNING","INFO"]:
        sub = agg[agg["gravetat"] == grav]
        if sub.empty:
            continue
        fig.add_trace(go.Bar(
            x=sub["familia"], y=sub["vendes_12m"],
            name=grav, marker_color=GRAV_COL[grav],
            text=[_eur(v) for v in sub["vendes_12m"]],
            textposition="inside",
            hovertemplate="%{x} · " + grav + ": <b>%{customdata}</b><extra></extra>",
            customdata=[_eur(v) for v in sub["vendes_12m"]],
        ))
    fig.update_layout(**_layout(
        title=dict(text="Vendes en risc per família de producte · Distribució per gravetat",
                   font_size=16),
        yaxis_title="EUR vendes 12m en risc",
        barmode="stack",
        height=400,
    ))
    return fig


def g_scatter_spend_tendencia(cli):
    """Dispersió spend vs tendència, colorejat per segment."""
    fig = go.Figure()
    for seg in ["LEAL","PROMETEDOR","RISC"]:
        sub = cli[cli["segment"] == seg]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["spend_12m"], y=sub["tendencia_pct"],
            mode="markers", name=seg,
            marker=dict(color=PAL[seg], size=7, opacity=0.65,
                        line=dict(width=0.5, color="white")),
            hovertemplate=(f"[{seg}] Client %{{customdata}}<br>"
                           f"Spend: %{{x:,.0f}}€<br>"
                           f"Tendència: %{{y:+.1f}}%<extra></extra>"),
            customdata=sub["id_client"].astype(str),
        ))
    fig.add_hline(y=0, line_dash="dot", line_color=PAL["subtext"],
                  annotation_text="tendència neutra",
                  annotation_position="bottom right",
                  annotation_font_size=11)
    fig.update_layout(**_layout(
        title=dict(text="Spend 12m vs Tendència · Tots els clients per segment",
                   font_size=16),
        xaxis_title="Spend 12m (€)",
        yaxis_title="Tendència (%)",
        hovermode="closest",
        height=420,
    ))
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLAR L'HTML
# ══════════════════════════════════════════════════════════════════════════════

def _fig_to_div(fig, fig_id):
    return fig.to_html(
        full_html=False, include_plotlyjs=False,
        div_id=fig_id, config={"displayModeBar": False},
    )


CARD_CSS = """
body { font-family: Inter, Arial, sans-serif; background: #F3F4F6; margin: 0; padding: 0; }
.header { background: #1E3A5F; color: white; padding: 28px 40px 20px; }
.header h1 { margin: 0 0 4px; font-size: 1.6rem; font-weight: 700; }
.header p  { margin: 0; color: #93C5FD; font-size: 0.9rem; }
.kpis { display: flex; gap: 16px; padding: 20px 40px 0; flex-wrap: wrap; }
.kpi  { background: white; border-radius: 8px; padding: 16px 20px;
        flex: 1; min-width: 140px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.kpi .label { font-size: 0.78rem; color: #6B7280; font-weight: 600;
              text-transform: uppercase; letter-spacing: .04em; }
.kpi .value { font-size: 1.5rem; font-weight: 800; color: #111827; margin-top: 4px; }
.kpi .delta { font-size: 0.82rem; margin-top: 2px; }
.section { background: white; border-radius: 8px; margin: 16px 40px;
           padding: 4px 16px 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.section h2 { font-size: 0.82rem; color: #6B7280; font-weight: 700;
              text-transform: uppercase; letter-spacing: .06em;
              margin: 16px 0 2px; border-bottom: 1px solid #E5E7EB;
              padding-bottom: 6px; }
.note { font-size: 0.78rem; color: #9CA3AF; padding: 0 0 8px 4px; }
footer { text-align: center; padding: 24px; font-size: 0.78rem; color: #9CA3AF; }
"""


def build_html(figs: list, cli: pd.DataFrame, data_ref: pd.Timestamp,
               out_path: Path):
    n_leal = (cli["segment"] == "LEAL").sum()
    n_prom = (cli["segment"] == "PROMETEDOR").sum()
    n_risc = (cli["segment"] == "RISC").sum()
    n_tot  = len(cli)
    spend  = cli["spend_12m"].sum()
    crit   = int(cli["n_prod_critical"].sum())

    kpis_html = f"""
    <div class="kpis">
      <div class="kpi">
        <div class="label">Clients totals</div>
        <div class="value">{n_tot:,}</div>
      </div>
      <div class="kpi">
        <div class="label">🟢 Leals</div>
        <div class="value" style="color:#16A34A">{n_leal:,}</div>
        <div class="delta" style="color:#16A34A">{n_leal/n_tot*100:.1f}%</div>
      </div>
      <div class="kpi">
        <div class="label">🟡 Prometedors</div>
        <div class="value" style="color:#D97706">{n_prom:,}</div>
        <div class="delta" style="color:#D97706">{n_prom/n_tot*100:.1f}%</div>
      </div>
      <div class="kpi">
        <div class="label">🔴 En risc</div>
        <div class="value" style="color:#DC2626">{n_risc:,}</div>
        <div class="delta" style="color:#DC2626">{n_risc/n_tot*100:.1f}%</div>
      </div>
      <div class="kpi">
        <div class="label">Spend 12m</div>
        <div class="value">{_eur(spend)}</div>
      </div>
      <div class="kpi">
        <div class="label">Prod. crítics</div>
        <div class="value" style="color:#DC2626">{crit:,}</div>
      </div>
    </div>"""

    sections_html = ""
    for title, note, fig_html in figs:
        sections_html += f"""
    <div class="section">
      <h2>{title}</h2>
      {"<p class='note'>"+note+"</p>" if note else ""}
      {fig_html}
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="ca">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Anàlisi de Clients · {data_ref.strftime('%d/%m/%Y')}</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
  <style>{CARD_CSS}</style>
</head>
<body>
  <div class="header">
    <h1>Anàlisi i Classificació de Clients</h1>
    <p>Data de referència: {data_ref.strftime('%d de %B de %Y')} · Generat automàticament per pipeline.py</p>
  </div>
  {kpis_html}
  {sections_html}
  <footer>Generat amb pipeline.py · Dades fins a {data_ref.strftime('%d/%m/%Y')}</footer>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Informe guardat: {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    args  = sys.argv[1:]
    dades = Path("dades")
    top_n = 15
    out   = Path("informe_clients.html")

    i = 0
    while i < len(args):
        if args[i] in ("--dades","-d") and i + 1 < len(args):
            dades = Path(args[i+1]); i += 2
        elif args[i] in ("--top","-t") and i + 1 < len(args):
            top_n = int(args[i+1]); i += 2
        elif args[i] in ("--out","-o") and i + 1 < len(args):
            out = Path(args[i+1]); i += 2
        elif args[i] in ("--help","-h"):
            print(__doc__); return
        else:
            i += 1

    print("=" * 56)
    print("  GENERADOR DE GRÀFIQUES D'EVOLUCIÓ LINEAL")
    print("=" * 56)
    print(f"Carpeta de dades: {dades.resolve()}")

    required = ["clientes_clasificados.csv","productos_riesgo_cliente.csv",
                "forecast_clientes.csv"]
    missing  = [f for f in required if not (dades/f).exists()]
    if missing:
        print(f"[ERROR] Falten fitxers a '{dades}/':")
        for f in missing:
            print(f"  · {f}")
        print("Executa primer: python pipeline.py")
        sys.exit(1)

    print("\nCarregant dades...", end=" ", flush=True)
    cli, prod, fore, df_raw, camps = load_all(dades)
    data_ref = df_raw["FECHA"].max() if df_raw is not None else pd.Timestamp.now()
    print("OK")

    print("Generant gràfiques...", end=" ", flush=True)
    figs = []

    # 1. Evolució global
    if df_raw is not None:
        figs.append((
            "Evolució global de vendes",
            "Línia blava = vendes reals mensuals · Línia discontinua = forecast XGBoost · Zones grogues = campanyes comercials",
            _fig_to_div(g_evolucio_global(df_raw, fore, camps), "g1"),
        ))

    # 2. Evolució per segment
    if df_raw is not None:
        figs.append((
            "Evolució per segment",
            "Cada color representa un segment · Línia contínua = historial · Discontinua = forecast",
            _fig_to_div(g_evolucio_per_segment(df_raw, cli, fore, camps), "g2"),
        ))

    # 3. Distribució de segments (snapshot actual)
    figs.append((
        "Distribució actual de clients",
        "Snapshot de la data de referència · Esquerra: nombre de clients · Dreta: volum de negoci",
        _fig_to_div(g_distribucio_segments(cli), "g3"),
    ))

    # 4. Forecast agregat barres
    figs.append((
        "Forecast mensual agregat · Propers 6 mesos",
        "Barres de previsió amb interval de confiança (±25%/+30%)",
        _fig_to_div(g_forecast_global_barres(fore), "g4"),
    ))

    # 5. Spend vs Tendència (scatter)
    figs.append((
        "Spend 12m vs Tendència · Per segment",
        "Cada punt és un client · Eix X = volum de negoci 12m · Eix Y = variació % en les últimes compres",
        _fig_to_div(g_scatter_spend_tendencia(cli), "g5"),
    ))

    # 6. Alertes
    fig_al = g_evolucio_alertes(cli)
    if fig_al:
        figs.append((
            "Distribució d'alertes de segment",
            "Vermell = risc crític · Ambre = seguiment necessari · Verd = tendència positiva",
            _fig_to_div(fig_al, "g6"),
        ))

    # 7. Risc per producte
    fig_rp = g_risc_productes(prod, cli)
    if fig_rp:
        figs.append((
            "Vendes en risc per família de producte",
            "Apilat per gravetat de l'alerta · Representa el volum en euros dels productes amb anomalies",
            _fig_to_div(fig_rp, "g7"),
        ))

    # 8. Top clients evolució lineal
    if df_raw is not None:
        figs.append((
            f"Evolució lineal · Top {top_n} clients per spend 12m",
            "Cada línia = un client · Color = segment · Discontinua = forecast dels propers 6 mesos",
            _fig_to_div(g_top_clients(df_raw, cli, fore, camps, top_n=top_n), "g8"),
        ))

    print("OK")
    print(f"Assemblant HTML...", end=" ", flush=True)
    build_html(figs, cli, data_ref, out)
    print("")
    print(f"\n  Obre al navegador: {out.resolve()}")


if __name__ == "__main__":
    main()
