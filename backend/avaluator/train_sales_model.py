import pandas as pd
import numpy as np
import joblib
from pathlib import Path

from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


class ProductSalesTrainer:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.model = None
        self.product_encoder = LabelEncoder()
        self.feature_columns = [
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
            "yearly_mean",
            "month_mean",
            "half_mean",
        ]

    def read_csv_safely(self) -> pd.DataFrame:
        """
        自动尝试常见 CSV 分隔符。
        """
        for sep in [";", ",", "\t"]:
            try:
                df = pd.read_csv(self.csv_path, sep=sep, encoding="utf-8-sig")
                if len(df.columns) > 1:
                    return df
            except Exception:
                pass

        raise ValueError("CSV 读取失败，请检查文件编码或分隔符。")

    def clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        清洗字段名、日期、销量、产品 ID。
        """

        df.columns = [c.strip() for c in df.columns]

        required_columns = [
            "Fecha",
            "Id. Producto",
            "Unidades",
        ]

        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"缺少必要字段: {col}")

        # 日期格式：日/月/年
        df["Fecha"] = pd.to_datetime(
            df["Fecha"],
            format="%d/%m/%Y",
            errors="coerce"
        )

        df = df.dropna(subset=["Fecha"])

        # 产品 ID 当成字符串处理，避免 001 变成 1
        df["Id. Producto"] = df["Id. Producto"].astype(str).str.strip()

        # Unidades 可能有逗号、小数、空值
        df["Unidades"] = (
            df["Unidades"]
            .astype(str)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
        )

        df["Unidades"] = pd.to_numeric(df["Unidades"], errors="coerce")
        df["Unidades"] = df["Unidades"].fillna(0)

        # 如果有退货或负数，可以保留，也可以改成 0
        # 这里保留负数，因为可能代表真实退货
        # 如果你不想保留负数，用下面这一行：
        # df["Unidades"] = df["Unidades"].clip(lower=0)

        df["year"] = df["Fecha"].dt.year
        df["month"] = df["Fecha"].dt.month

        # 上半月 / 下半月
        df["half"] = np.where(df["Fecha"].dt.day <= 15, 1, 2)

        return df

    def aggregate_by_product_period(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        按产品 + 年 + 月 + 上/下半月聚合销量。
        """
        grouped = (
            df.groupby(["Id. Producto", "year", "month", "half"], as_index=False)
            .agg(
                unidades=("Unidades", "sum"),
                invoice_count=("Unidades", "count")
            )
        )

        return grouped

    def complete_missing_periods(self, grouped: pd.DataFrame) -> pd.DataFrame:
        """
        给每个产品补齐缺失的年月半月。
        没有销售的时间段，销量补 0。
        """
        min_year = grouped["year"].min()
        max_year = grouped["year"].max()

        products = grouped["Id. Producto"].unique()
        months = list(range(1, 13))
        halves = [1, 2]
        years = list(range(min_year, max_year + 1))

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

    def create_features(self, df: pd.DataFrame, fit_encoder: bool = True) -> pd.DataFrame:
        """
        创建机器学习特征。
        """
        df = df.copy()

        df = df.sort_values(["Id. Producto", "year", "month", "half"])

        # 时间序号：一年 24 个 period
        df["period_in_year"] = (df["month"] - 1) * 2 + df["half"]
        df["time_index"] = df["year"] * 24 + df["period_in_year"]

        if fit_encoder:
            df["product_code"] = self.product_encoder.fit_transform(df["Id. Producto"])
        else:
            known_products = set(self.product_encoder.classes_)

            df["Id. Producto"] = df["Id. Producto"].astype(str)

            # 如果预测时遇到没见过的新产品，会报错
            unknown_products = set(df["Id. Producto"]) - known_products
            if unknown_products:
                raise ValueError(f"发现训练时没见过的新产品 ID: {unknown_products}")

            df["product_code"] = self.product_encoder.transform(df["Id. Producto"])

        # lag 特征
        df["lag_1"] = df.groupby("Id. Producto")["unidades"].shift(1)
        df["lag_2"] = df.groupby("Id. Producto")["unidades"].shift(2)
        df["lag_12"] = df.groupby("Id. Producto")["unidades"].shift(12)
        df["lag_24"] = df.groupby("Id. Producto")["unidades"].shift(24)

        # rolling 特征
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

        # 历史均值特征
        df["yearly_mean"] = (
            df.groupby(["Id. Producto", "year"])["unidades"]
            .transform("mean")
        )

        df["month_mean"] = (
            df.groupby(["Id. Producto", "month"])["unidades"]
            .transform("mean")
        )

        df["half_mean"] = (
            df.groupby(["Id. Producto", "half"])["unidades"]
            .transform("mean")
        )

        # 缺失值处理
        fill_cols = [
            "lag_1",
            "lag_2",
            "lag_12",
            "lag_24",
            "rolling_3",
            "rolling_6",
            "rolling_12",
            "yearly_mean",
            "month_mean",
            "half_mean",
        ]

        for col in fill_cols:
            df[col] = df[col].fillna(0)

        return df

    def build_model(self):
        """
        优先使用 XGBoost。
        如果没有安装 xgboost，则使用 sklearn 的 HistGradientBoostingRegressor。
        """
        try:
            from xgboost import XGBRegressor

            print("使用模型: XGBoost")

            model = XGBRegressor(
                n_estimators=500,
                learning_rate=0.03,
                max_depth=6,
                subsample=0.9,
                colsample_bytree=0.9,
                objective="reg:squarederror",
                random_state=42,
                n_jobs=-1
            )

            return model

        except ImportError:
            print("没有安装 xgboost，使用 sklearn HistGradientBoostingRegressor")

            model = HistGradientBoostingRegressor(
                max_iter=500,
                learning_rate=0.03,
                max_leaf_nodes=31,
                random_state=42
            )

            return model

    def create_year_weights(self, years: pd.Series) -> np.ndarray:
        """
        年份越新，权重越大。
        使用指数增长权重，不再手写 2021:0.5 这种。
        """
        max_year = years.max()

        # 距离最新年份越远，权重越低
        distance = max_year - years

        # alpha 越大，越重视最近年份
        alpha = 0.35

        weights = np.exp(-alpha * distance)

        return weights

    def train(self, model_output_path: str = "sales_model.pkl"):
        raw_df = self.read_csv_safely()
        clean_df = self.clean_data(raw_df)
        grouped = self.aggregate_by_product_period(clean_df)
        full_df = self.complete_missing_periods(grouped)
        feature_df = self.create_features(full_df, fit_encoder=True)

        # 目标值使用 log1p，让模型对异常大订单更稳定
        X = feature_df[self.feature_columns]
        y = np.log1p(feature_df["unidades"].clip(lower=0))

        sample_weight = self.create_year_weights(feature_df["year"])

        # 简单时间切分：最后一年作为验证
        max_year = feature_df["year"].max()

        train_mask = feature_df["year"] < max_year
        valid_mask = feature_df["year"] == max_year

        X_train = X[train_mask]
        y_train = y[train_mask]
        w_train = sample_weight[train_mask]

        X_valid = X[valid_mask]
        y_valid = y[valid_mask]

        self.model = self.build_model()
        self.model.fit(X_train, y_train, sample_weight=w_train)

        if len(X_valid) > 0:
            pred_log = self.model.predict(X_valid)
            pred = np.expm1(pred_log).clip(min=0)
            real = np.expm1(y_valid)

            mae = mean_absolute_error(real, pred)
            rmse = mean_squared_error(real, pred) ** 0.5

            print(f"验证年份: {max_year}")
            print(f"MAE: {mae:.2f}")
            print(f"RMSE: {rmse:.2f}")

        package = {
            "model": self.model,
            "product_encoder": self.product_encoder,
            "feature_columns": self.feature_columns,
            "history": feature_df,
            "last_year": int(feature_df["year"].max())
        }

        joblib.dump(package, model_output_path)

        print(f"模型已保存到: {model_output_path}")


if __name__ == "__main__":
    trainer = ProductSalesTrainer("sales.csv")
    trainer.train("sales_model.pkl")