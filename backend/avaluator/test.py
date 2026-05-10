import pandas as pd
import numpy as np
from pathlib import Path

from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_absolute_error, mean_squared_error

try:
    from xgboost import XGBRegressor
    HAS_XGBOOST = True
except ImportError:
    from sklearn.ensemble import HistGradientBoostingRegressor
    HAS_XGBOOST = False


CSV_FILE = "sales.csv"
OUTPUT_FILE = "last_year_analysis.xlsx"


def read_csv_safely(path: str) -> pd.DataFrame:
    for sep in [";", ",", "\t"]:
        try:
            df = pd.read_csv(path, sep=sep, encoding="utf-8-sig")
            if len(df.columns) > 1:
                return df
        except Exception:
            pass

    raise ValueError("CSV 读取失败，请检查分隔符或编码。")


def clean_sales_data(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]

    required = ["Fecha", "Id. Producto", "Unidades"]

    for col in required:
        if col not in df.columns:
            raise ValueError(f"缺少字段: {col}")

    # Fecha 格式：日/月/年
    df["Fecha"] = pd.to_datetime(
        df["Fecha"],
        format="%d/%m/%Y",
        errors="coerce"
    )

    df = df.dropna(subset=["Fecha"])

    df["Id. Producto"] = df["Id. Producto"].astype(str).str.strip()

    df["Unidades"] = (
        df["Unidades"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.replace(" ", "", regex=False)
    )

    df["Unidades"] = pd.to_numeric(df["Unidades"], errors="coerce")
    df["Unidades"] = df["Unidades"].fillna(0)

    # 如果你不想让退货影响预测，可以打开这一行
    # df["Unidades"] = df["Unidades"].clip(lower=0)

    df["year"] = df["Fecha"].dt.year
    df["month"] = df["Fecha"].dt.month
    df["half"] = np.where(df["Fecha"].dt.day <= 15, 1, 2)

    return df


def aggregate_sales(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["Id. Producto", "year", "month", "half"], as_index=False)
        .agg(
            unidades=("Unidades", "sum"),
            invoice_count=("Unidades", "count"),
        )
    )

    return grouped


def complete_missing_periods(grouped: pd.DataFrame) -> pd.DataFrame:
    products = grouped["Id. Producto"].unique()
    years = range(grouped["year"].min(), grouped["year"].max() + 1)
    months = range(1, 13)
    halves = [1, 2]

    full_index = pd.MultiIndex.from_product(
        [products, years, months, halves],
        names=["Id. Producto", "year", "month", "half"]
    )

    full_df = (
        grouped
        .set_index(["Id. Producto", "year", "month", "half"])
        .reindex(full_index)
        .reset_index()
    )

    full_df["unidades"] = full_df["unidades"].fillna(0)
    full_df["invoice_count"] = full_df["invoice_count"].fillna(0)

    return full_df


def create_features(df: pd.DataFrame, encoder: LabelEncoder, fit_encoder: bool):
    df = df.copy()
    df = df.sort_values(["Id. Producto", "year", "month", "half"])

    df["period_in_year"] = (df["month"] - 1) * 2 + df["half"]
    df["time_index"] = df["year"] * 24 + df["period_in_year"]

    if fit_encoder:
        df["product_code"] = encoder.fit_transform(df["Id. Producto"])
    else:
        df["product_code"] = encoder.transform(df["Id. Producto"])

    # lag 特征：过去销量
    df["lag_1"] = df.groupby("Id. Producto")["unidades"].shift(1)
    df["lag_2"] = df.groupby("Id. Producto")["unidades"].shift(2)
    df["lag_12"] = df.groupby("Id. Producto")["unidades"].shift(12)
    df["lag_24"] = df.groupby("Id. Producto")["unidades"].shift(24)

    # rolling 特征：历史平均
    df["rolling_3"] = (
        df.groupby("Id. Producto")["unidades"]
        .shift(1)
        .rolling(3)
        .mean()
        .reset_index(level=0, drop=True)
    )

    df["rolling_6"] = (
        df.groupby("Id. Producto")["unidades"]
        .shift(1)
        .rolling(6)
        .mean()
        .reset_index(level=0, drop=True)
    )

    df["rolling_12"] = (
        df.groupby("Id. Producto")["unidades"]
        .shift(1)
        .rolling(12)
        .mean()
        .reset_index(level=0, drop=True)
    )

    # 季节性特征
    df["product_month_mean"] = (
        df.groupby(["Id. Producto", "month"])["unidades"]
        .transform("mean")
    )

    df["product_half_mean"] = (
        df.groupby(["Id. Producto", "half"])["unidades"]
        .transform("mean")
    )

    df["product_global_mean"] = (
        df.groupby("Id. Producto")["unidades"]
        .transform("mean")
    )

    fill_cols = [
        "lag_1",
        "lag_2",
        "lag_12",
        "lag_24",
        "rolling_3",
        "rolling_6",
        "rolling_12",
        "product_month_mean",
        "product_half_mean",
        "product_global_mean",
    ]

    for col in fill_cols:
        df[col] = df[col].fillna(0)

    return df


def build_model():
    if HAS_XGBOOST:
        print("使用模型: XGBoost")

        return XGBRegressor(
            n_estimators=500,
            learning_rate=0.03,
            max_depth=6,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="reg:squarederror",
            random_state=42,
            n_jobs=-1
        )

    print("没有安装 xgboost，使用 HistGradientBoostingRegressor")

    return HistGradientBoostingRegressor(
        max_iter=500,
        learning_rate=0.03,
        max_leaf_nodes=31,
        random_state=42
    )


def create_year_weights(years: pd.Series) -> np.ndarray:
    """
    越新的年份权重越大。
    """
    max_year = years.max()
    distance = max_year - years

    alpha = 0.35
    weights = np.exp(-alpha * distance)

    return weights


def add_analysis_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["error"] = df["pred_unidades"] - df["real_unidades"]
    df["abs_error"] = df["error"].abs()

    df["error_percent"] = np.where(
        df["real_unidades"] > 0,
        df["error"] / df["real_unidades"] * 100,
        np.nan
    )

    df["abs_error_percent"] = df["error_percent"].abs()

    def classify(row):
        real = row["real_unidades"]
        pred = row["pred_unidades"]

        if real == 0 and pred == 0:
            return "OK - 无销量"

        if real == 0 and pred > 0:
            return "预测过高 - 实际为0"

        if pred > real * 1.3:
            return "预测偏高"

        if pred < real * 0.7:
            return "预测偏低"

        return "正常"

    df["status"] = df.apply(classify, axis=1)

    return df


def main():
    raw_df = read_csv_safely(CSV_FILE)
    clean_df = clean_sales_data(raw_df)
    grouped = aggregate_sales(clean_df)
    full_df = complete_missing_periods(grouped)

    last_year = full_df["year"].max()
    first_year = full_df["year"].min()

    print(f"数据年份范围: {first_year} - {last_year}")
    print(f"将使用 {first_year} - {last_year - 1} 预测 {last_year}")

    encoder = LabelEncoder()

    feature_df = create_features(full_df, encoder, fit_encoder=True)

    feature_columns = [
        "product_code",
        "year",
        "month",
        "half",
        "time_index",
        "lag_1",
        "lag_2",
        "lag_12",
        "lag_24",
        "rolling_3",
        "rolling_6",
        "rolling_12",
        "product_month_mean",
        "product_half_mean",
        "product_global_mean",
    ]

    train_df = feature_df[feature_df["year"] < last_year].copy()
    test_df = feature_df[feature_df["year"] == last_year].copy()

    X_train = train_df[feature_columns]
    y_train = np.log1p(train_df["unidades"].clip(lower=0))

    X_test = test_df[feature_columns]
    y_test_real = test_df["unidades"].clip(lower=0)

    sample_weight = create_year_weights(train_df["year"])

    model = build_model()
    model.fit(X_train, y_train, sample_weight=sample_weight)

    pred_log = model.predict(X_test)
    pred_units = np.expm1(pred_log)
    pred_units = np.clip(pred_units, 0, None)

    pred_df = test_df[["Id. Producto", "year", "month", "half"]].copy()
    pred_df["pred_unidades"] = np.round(pred_units, 2)

    real_df = test_df[[
        "Id. Producto",
        "year",
        "month",
        "half",
        "unidades"
    ]].copy()
    real_df = real_df.rename(columns={
        "unidades": "real_unidades"
    })

    result = pred_df.merge(
        real_df,
        on=["Id. Producto", "year", "month", "half"],
        how="left"
    )
    result["real_unidades"] = result["real_unidades"].fillna(0)
    result["half_name"] = result["half"].map({1: "H1", 2: "H2"})

    result = add_analysis_columns(result)

    mae = mean_absolute_error(result["real_unidades"], result["pred_unidades"])
    rmse = mean_squared_error(result["real_unidades"], result["pred_unidades"]) ** 0.5

    total_real = result["real_unidades"].sum()
    total_pred = result["pred_unidades"].sum()
    total_error = total_pred - total_real

    if total_real > 0:
        total_error_percent = total_error / total_real * 100
    else:
        total_error_percent = np.nan

    summary = pd.DataFrame([
        {
            "metric": "last_year",
            "value": last_year
        },
        {
            "metric": "MAE",
            "value": round(mae, 2)
        },
        {
            "metric": "RMSE",
            "value": round(rmse, 2)
        },
        {
            "metric": "total_real_unidades",
            "value": round(total_real, 2)
        },
        {
            "metric": "total_pred_unidades",
            "value": round(total_pred, 2)
        },
        {
            "metric": "total_error",
            "value": round(total_error, 2)
        },
        {
            "metric": "total_error_percent",
            "value": round(total_error_percent, 2)
        },
    ])

    product_summary = (
        result
        .groupby("Id. Producto", as_index=False)
        .agg(
            real_total=("real_unidades", "sum"),
            pred_total=("pred_unidades", "sum"),
            mae=("abs_error", "mean"),
            max_error=("abs_error", "max"),
        )
    )

    product_summary["total_error"] = (
        product_summary["pred_total"] - product_summary["real_total"]
    )

    product_summary["total_error_percent"] = np.where(
        product_summary["real_total"] > 0,
        product_summary["total_error"] / product_summary["real_total"] * 100,
        np.nan
    )

    product_summary = product_summary.sort_values(
        "mae",
        ascending=False
    )

    month_summary = (
        result
        .groupby(["year", "month", "half_name"], as_index=False)
        .agg(
            real_total=("real_unidades", "sum"),
            pred_total=("pred_unidades", "sum"),
            mae=("abs_error", "mean"),
        )
    )

    month_summary["total_error"] = (
        month_summary["pred_total"] - month_summary["real_total"]
    )

    month_summary["total_error_percent"] = np.where(
        month_summary["real_total"] > 0,
        month_summary["total_error"] / month_summary["real_total"] * 100,
        np.nan
    )

    # 找出问题最大的预测
    worst_predictions = result.sort_values(
        "abs_error",
        ascending=False
    ).head(100)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        result.to_excel(writer, sheet_name="detail_by_product_period", index=False)
        product_summary.to_excel(writer, sheet_name="summary_by_product", index=False)
        month_summary.to_excel(writer, sheet_name="summary_by_month_half", index=False)
        worst_predictions.to_excel(writer, sheet_name="worst_predictions", index=False)

    print()
    print("分析完成")
    print(f"最后一年: {last_year}")
    print(f"MAE: {mae:.2f}")
    print(f"RMSE: {rmse:.2f}")
    print(f"真实总销量: {total_real:.2f}")
    print(f"预测总销量: {total_pred:.2f}")
    print(f"总误差: {total_error:.2f}")
    print(f"总误差百分比: {total_error_percent:.2f}%")
    print(f"已导出: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()