import pandas as pd
import numpy as np
import re

df = pd.read_csv("sales.csv", sep=None, engine="python", encoding="utf-8-sig")

df.columns = df.columns.astype(str).str.strip().str.replace("\ufeff", "", regex=False)

normalized = {
    re.sub(r"[^a-z0-9]", "", c.lower()): c
    for c in df.columns
}
fecha_col = normalized.get("fecha")
if not fecha_col:
    raise KeyError("No se encontro una columna de fecha. Columnas: " + ", ".join(df.columns))

df[fecha_col] = pd.to_datetime(df[fecha_col], errors="coerce", dayfirst=False)
df["Unidades"] = (
    df["Unidades"]
    .astype(str)
    .str.replace(",", ".", regex=False)
    .str.replace(" ", "", regex=False)
)
df["Unidades"] = pd.to_numeric(df["Unidades"], errors="coerce").fillna(0)

df["year"] = df[fecha_col].dt.year
df["month"] = df[fecha_col].dt.month
df["half"] = np.where(df[fecha_col].dt.day <= 15, "H1", "H2")

last_year = df["year"].max()

check = (
    df[df["year"] == last_year]
    .groupby(["month", "half"], as_index=False)
    .agg(
        rows=("Unidades", "count"),
        total_unidades=("Unidades", "sum")
    )
    .sort_values(["month", "half"])
)

print("最后一年:", last_year)
print(check)