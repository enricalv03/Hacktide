import pandas as pd
import numpy as np


def build_customer_weight_model(
    ventas,
    productos,
    client_id,
    product_id,
    date_col="Fecha",
    client_col="Id. Cliente",
    product_col="Id. Producto",
    product_master_col="Id.Prod",
    units_col="Unidades",
    value_col="Valores_H",
    category_col="Categoria_H",
    family_col="Familia_H",
    target_year=None
):
    """
    输入:
        ventas: ventas dataframe
        productos: productos dataframe
        client_id: 客户ID
        product_id: 产品ID

    输出:
        serious_low_weights: 未来12个月严重偏低权重
        low_weights: 未来12个月偏低权重
        normal_weights: 未来12个月正常购买权重
    """

    ventas = ventas.copy()
    productos = productos.copy()

    client_id = str(client_id)
    product_id = str(product_id)

    ventas[date_col] = pd.to_datetime(ventas[date_col], errors="coerce")
    ventas[client_col] = ventas[client_col].astype(str)
    ventas[product_col] = ventas[product_col].astype(str)
    productos[product_master_col] = productos[product_master_col].astype(str)

    ventas[units_col] = pd.to_numeric(ventas[units_col], errors="coerce").fillna(0)
    ventas[value_col] = pd.to_numeric(ventas[value_col], errors="coerce").fillna(0)

    df = ventas.merge(
        productos,
        left_on=product_col,
        right_on=product_master_col,
        how="left"
    )

    df = df.dropna(subset=[date_col])

    df["year"] = df[date_col].dt.year
    df["month"] = df[date_col].dt.month
    df["year_month"] = df[date_col].dt.to_period("M").astype(str)

    if target_year is None:
        target_year = int(df["year"].max()) + 1

    # ---------- 小工具 ----------
    def safe_div(a, b, default=0):
        if b is None or b == 0 or pd.isna(b):
            return default
        return a / b

    def clip(x, low=0, high=1):
        if pd.isna(x) or np.isinf(x):
            return 0
        return max(low, min(high, x))

    def get_product_category(product_id):
        rows = df[df[product_col] == product_id]
        if rows.empty:
            raise ValueError(f"找不到产品 {product_id}")

        category = rows[category_col].dropna()
        family = rows[family_col].dropna()

        category = category.iloc[0] if len(category) > 0 else None
        family = family.iloc[0] if len(family) > 0 else None

        return category, family

    category, family = get_product_category(product_id)

    client_df = df[df[client_col] == client_id]

    if client_df.empty:
        # 新客户：没有历史数据，给保守权重
        normal_weights = [0.5] * 12
        low_weights = [0.5 * 0.75] * 12
        serious_low_weights = [0.5 * 0.5] * 12

        return serious_low_weights, low_weights, normal_weights

    # =========================================================
    # 1. 购买力 power_score
    # =========================================================

    client_total = (
        df.groupby(client_col)
        .agg(
            total_units=(units_col, "sum"),
            total_value=(value_col, "sum")
        )
        .reset_index()
    )

    client_total["power_score"] = client_total["total_value"].rank(pct=True)

    power_score = client_total.loc[
        client_total[client_col] == client_id,
        "power_score"
    ]

    power_score = float(power_score.iloc[0]) if len(power_score) > 0 else 0.5
    power_score = clip(power_score)

    # =========================================================
    # 2. 类型偏好 category_preference_score
    # =========================================================

    total_client_units = client_df[units_col].sum()

    category_units = client_df[
        client_df[category_col] == category
    ][units_col].sum()

    category_preference_score = safe_div(
        category_units,
        total_client_units,
        default=0
    )

    category_preference_score = clip(category_preference_score)

    # =========================================================
    # 3. 类别忠诚度 category_loyalty_score
    # =========================================================

    total_active_months = client_df["year_month"].nunique()

    category_active_months = client_df[
        client_df[category_col] == category
    ]["year_month"].nunique()

    category_loyalty_score = safe_div(
        category_active_months,
        total_active_months,
        default=0
    )

    category_loyalty_score = clip(category_loyalty_score)

    # =========================================================
    # 4. 产品忠诚度 product_loyalty_score
    # =========================================================

    product_active_months = client_df[
        client_df[product_col] == product_id
    ]["year_month"].nunique()

    product_loyalty_score = safe_div(
        product_active_months,
        total_active_months,
        default=0
    )

    product_loyalty_score = clip(product_loyalty_score)

    # =========================================================
    # 5. 类别购买频率 category_frequency_score
    # =========================================================

    category_orders = client_df[
        client_df[category_col] == category
    ]["Num.Fact"].nunique()

    category_freq_per_month = safe_div(
        category_orders,
        total_active_months,
        default=0
    )

    # 每月买2次以上算高频
    category_frequency_score = clip(category_freq_per_month / 2)

    # =========================================================
    # 6. 产品购买频率 product_frequency_score
    # =========================================================

    product_orders = client_df[
        client_df[product_col] == product_id
    ]["Num.Fact"].nunique()

    product_freq_per_month = safe_div(
        product_orders,
        total_active_months,
        default=0
    )

    product_frequency_score = clip(product_freq_per_month / 2)

    # =========================================================
    # 7. 近期趋势 recent_trend_score
    # =========================================================

    max_date = client_df[date_col].max()
    recent_start = max_date - pd.DateOffset(months=3)

    monthly_client = (
        client_df.groupby("year_month")[units_col]
        .sum()
        .reset_index()
    )

    monthly_client["date"] = pd.to_datetime(monthly_client["year_month"] + "-01")

    recent = monthly_client[monthly_client["date"] >= recent_start]
    history = monthly_client[monthly_client["date"] < recent_start]

    if recent.empty or history.empty:
        recent_trend_score = 0.5
    else:
        recent_avg = recent[units_col].mean()
        history_avg = history[units_col].mean()

        trend_ratio = safe_div(recent_avg, history_avg, default=1)

        # ratio = 0.5 -> 0
        # ratio = 1.0 -> 0.5
        # ratio = 1.5 -> 1
        recent_trend_score = clip((trend_ratio - 0.5) / 1.0)

    # =========================================================
    # 8. 购买周期函数
    # =========================================================

    def cycle_score(target_df, reference_date):
        """
        返回 0~1
        0：刚买过，还不该买
        1：已经很久没买，应该买
        """

        if target_df.empty:
            return 0.5

        dates = (
            target_df[[date_col]]
            .drop_duplicates()
            .sort_values(date_col)
        )

        if len(dates) < 2:
            return 0.5

        dates["diff_days"] = dates[date_col].diff().dt.days

        avg_cycle = dates["diff_days"].dropna().mean()
        last_purchase = dates[date_col].max()

        if pd.isna(avg_cycle) or avg_cycle <= 0:
            return 0.5

        days_since_last = (reference_date - last_purchase).days

        ratio = days_since_last / avg_cycle

        if ratio < 0.5:
            return 0.25
        elif ratio < 1.0:
            return 0.50
        elif ratio < 1.5:
            return 0.75
        else:
            return 1.00

    # =========================================================
    # 9. 月份季节性
    # =========================================================

    def month_seasonality_score(month):
        """
        客户对这个产品在不同月份的购买习惯。
        """
        cp_df = client_df[client_df[product_col] == product_id]

        if cp_df.empty:
            return 0.5

        monthly_units = cp_df.groupby("month")[units_col].sum()

        avg_month_units = monthly_units.mean()
        target_month_units = monthly_units.get(month, 0)

        if avg_month_units == 0:
            return 0.5

        ratio = target_month_units / avg_month_units

        # ratio = 0.5 -> 0
        # ratio = 1.0 -> 0.5
        # ratio = 1.5 -> 1
        return clip((ratio - 0.5) / 1.0)

    # =========================================================
    # 10. 权重配置
    # =========================================================

    WEIGHTS = {
        "power": 0.20,
        "category_preference": 0.15,
        "category_loyalty": 0.15,
        "product_loyalty": 0.15,
        "category_frequency": 0.10,
        "product_frequency": 0.10,
        "cycle": 0.10,
        "recent_trend": 0.05,
    }

    serious_low_weights = []
    low_weights = []
    normal_weights = []

    category_df = client_df[client_df[category_col] == category]
    product_df = client_df[client_df[product_col] == product_id]

    # =========================================================
    # 11. 生成未来12个月权重
    # =========================================================

    for month in range(1, 13):
        reference_date = pd.Timestamp(
            year=target_year,
            month=month,
            day=1
        )

        category_cycle_score = cycle_score(category_df, reference_date)
        product_cycle_score = cycle_score(product_df, reference_date)

        cycle_score_final = (
            0.5 * category_cycle_score +
            0.5 * product_cycle_score
        )

        seasonality_score = month_seasonality_score(month)

        base_score = (
            WEIGHTS["power"] * power_score +
            WEIGHTS["category_preference"] * category_preference_score +
            WEIGHTS["category_loyalty"] * category_loyalty_score +
            WEIGHTS["product_loyalty"] * product_loyalty_score +
            WEIGHTS["category_frequency"] * category_frequency_score +
            WEIGHTS["product_frequency"] * product_frequency_score +
            WEIGHTS["cycle"] * cycle_score_final +
            WEIGHTS["recent_trend"] * recent_trend_score
        )

        # 加入月份季节性
        final_score = 0.85 * base_score + 0.15 * seasonality_score

        # 关键：把 0~1 的分数变成购买权重
        # 0   -> 0.5
        # 0.5 -> 1.0
        # 1   -> 1.5
        normal_weight = 0.5 + final_score

        serious_low_weight = normal_weight * 0.50
        low_weight = normal_weight * 0.75

        normal_weights.append(float(normal_weight))
        low_weights.append(float(low_weight))
        serious_low_weights.append(float(serious_low_weight))

    return serious_low_weights, low_weights, normal_weights



ventas = pd.read_csv("Ventas.csv")
productos = pd.read_csv("Productos.csv")

serious_low, low, normal = build_customer_weight_model(
    ventas=ventas,
    productos=productos,
    client_id=123,
    product_id=4565
)

print("Molt Baix:", serious_low)
print("Baix:", low)
print("Normal:", normal)