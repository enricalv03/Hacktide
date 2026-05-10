import pandas as pd
import numpy as np


VENTAS_FILE = "Ventas.csv"
PRODUCTOS_FILE = "Productos.csv"
OUTPUT_FILE = "product_stability.csv"


def read_csv_smart(path):
    """
    自动尝试常见分隔符。
    西班牙 Excel 导出的 CSV 经常是 ; 分隔。
    """
    for sep in [",", ";", "\t"]:
        try:
            df = pd.read_csv(path, sep=sep, encoding="utf-8")
            if len(df.columns) > 1:
                return df
        except Exception:
            pass

    for sep in [",", ";", "\t"]:
        try:
            df = pd.read_csv(path, sep=sep, encoding="latin1")
            if len(df.columns) > 1:
                return df
        except Exception:
            pass

    raise ValueError(f"No se pudo leer el archivo: {path}")


def classify_stability(fluctuation_percent, months_count, avg_units):
    """
    根据 CV 浮动百分比分类。
    """

    # 数据太少，不建议直接判断
    if months_count < 6:
        return "数据不足"

    # 平均销量太低的产品，容易出现假波动
    if avg_units < 3:
        return "低销量产品，参考价值低"

    if fluctuation_percent <= 25:
        return "稳定"
    elif fluctuation_percent <= 50:
        return "中等波动"
    elif fluctuation_percent <= 80:
        return "不稳定"
    else:
        return "极不稳定"


def main():
    ventas = read_csv_smart(VENTAS_FILE)
    productos = read_csv_smart(PRODUCTOS_FILE)

    ventas.columns = ventas.columns.str.strip()
    productos.columns = productos.columns.str.strip()

    # 日期处理
    ventas["Fecha"] = pd.to_datetime(
        ventas["Fecha"],
        errors="coerce",
        dayfirst=True
    )

    # 数量处理
    ventas["Unidades"] = pd.to_numeric(
        ventas["Unidades"],
        errors="coerce"
    ).fillna(0)

    # 去掉没有日期的数据
    ventas = ventas.dropna(subset=["Fecha"])

    # 生成年月
    ventas["year_month"] = ventas["Fecha"].dt.to_period("M").astype(str)

    # 合并产品信息
    df = ventas.merge(
        productos,
        left_on="Id. Producto",
        right_on="Id.Prod",
        how="left"
    )

    df["Categoria_H"] = df["Categoria_H"].fillna("SIN_CATEGORIA")
    df["Familia_H"] = df["Familia_H"].fillna("SIN_FAMILIA")
    df["Bloque analítico"] = df["Bloque analítico"].fillna("SIN_BLOQUE")

    # ==========================
    # 1. 每个产品每个月销量
    # ==========================
    monthly = (
        df.groupby(
            [
                "Id. Producto",
                "Categoria_H",
                "Familia_H",
                "Bloque analítico",
                "year_month"
            ],
            as_index=False
        )["Unidades"]
        .sum()
    )

    # ==========================
    # 2. 每个产品统计波动
    # ==========================
    result = (
        monthly.groupby(
            [
                "Id. Producto",
                "Categoria_H",
                "Familia_H",
                "Bloque analítico"
            ]
        )
        .agg(
            months_count=("year_month", "count"),
            avg_monthly_units=("Unidades", "mean"),
            median_monthly_units=("Unidades", "median"),
            p25_units=("Unidades", lambda x: x.quantile(0.25)),
            p75_units=("Unidades", lambda x: x.quantile(0.75)),
            min_monthly_units=("Unidades", "min"),
            max_monthly_units=("Unidades", "max"),
            total_units=("Unidades", "sum")
        )
        .reset_index()
    )

    # ==========================
    # 3. 计算总浮动百分比 CV
    # ==========================
    result["fluctuation_percent"] = np.where(
        result["median_monthly_units"] > 0,
        (result["p75_units"] - result["p25_units"]) / result["median_monthly_units"] * 100,
        0
    )

    # ==========================
    # 4. 计算上下浮动范围
    # ==========================
    result["normal_low_units"] = result["p25_units"]
    result["normal_high_units"] = result["p75_units"]

    result["down_percent"] = np.where(
        result["median_monthly_units"] > 0,
        (result["p25_units"] - result["median_monthly_units"])
        / result["median_monthly_units"] * 100,
        0
    )

    result["up_percent"] = np.where(
        result["median_monthly_units"] > 0,
        (result["p75_units"] - result["median_monthly_units"])
        / result["median_monthly_units"] * 100,
        0
    )

    # ==========================
    # 5. 稳定 / 不稳定 分类
    # ==========================
    result["stability"] = result.apply(
        lambda row: classify_stability(
            row["fluctuation_percent"],
            row["months_count"],
            row["median_monthly_units"]
        ),
        axis=1
    )

    # 排序：最稳定的在前面
    result = result.sort_values(
        by=["stability", "fluctuation_percent"],
        ascending=[True, True]
    )

    # 小数保留两位
    numeric_cols = [
        "avg_monthly_units",
        "median_monthly_units",
        "p25_units",
        "p75_units",
        "min_monthly_units",
        "max_monthly_units",
        "fluctuation_percent",
        "normal_low_units",
        "normal_high_units",
        "down_percent",
        "up_percent"
    ]

    result[numeric_cols] = result[numeric_cols].round(2)

    result.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print("完成，已生成：", OUTPUT_FILE)
    print(result.head(20))


if __name__ == "__main__":
    main()