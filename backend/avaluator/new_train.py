import pandas as pd
import numpy as np
import joblib
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_absolute_error, mean_squared_error

try:
    from xgboost import XGBRegressor
    HAS_XGBOOST = True
except ImportError:
    from sklearn.ensemble import HistGradientBoostingRegressor
    HAS_XGBOOST = False

CSV_FILE = "sales.csv"
MODEL_OUTPUT = "sales_model.pkl"

class SalesModelTrainer:
    def __init__(self, csv_file: str):
        self.csv_file = csv_file
        self.product_encoder = LabelEncoder()
        self.model = None

        self.feature_columns = [
            "product_code", "year", "month", "half", "period_in_year", "time_index",
            "lag_1", "lag_2", "lag_3", "lag_4", "lag_6", "lag_12", "lag_24",
            "rolling_2", "rolling_3", "rolling_4", "rolling_6", "rolling_12", "rolling_24",
            "rolling_std_3", "rolling_std_6", "rolling_std_12",
            "product_global_mean", "product_month_mean", "product_half_mean", "product_period_mean",
            "product_global_median", "product_month_median",
            "trend_1", "trend_2", "trend_3",
            "month_sin", "month_cos", "period_sin", "period_cos",
        ]

    def read_csv_safely(self) -> pd.DataFrame:
        for sep in [";", ",", "\t"]:
            try:
                df = pd.read_csv(self.csv_file, sep=sep, encoding="utf-8-sig")
                if len(df.columns) > 1:
                    print(f"CSV 读取成功，使用分隔符: {repr(sep)}")
                    return df
            except Exception:
                pass
        raise ValueError("CSV 读取失败，请检查 sales.csv 的分隔符或编码。")

    def clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.strip() for c in df.columns]
        required_cols = ["Fecha", "Id. Producto", "Unidades"]
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"缺少必要字段: {col}")

        df["Fecha"] = pd.to_datetime(df["Fecha"], format="%d/%m/%Y", errors="coerce")
        df = df.dropna(subset=["Fecha"])
        df["Id. Producto"] = df["Id. Producto"].astype(str).str.strip()

        df["Unidades"] = (
            df["Unidades"]
            .astype(str)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
        )
        df["Unidades"] = pd.to_numeric(df["Unidades"], errors="coerce").fillna(0)
        df["Unidades"] = df["Unidades"].clip(lower=0)

        df["year"] = df["Fecha"].dt.year
        df["month"] = df["Fecha"].dt.month
        df["half"] = np.where(df["Fecha"].dt.day <= 15, 1, 2)
        return df

    def aggregate_sales(self, df: pd.DataFrame) -> pd.DataFrame:
        grouped = df.groupby(["Id. Producto", "year", "month", "half"], as_index=False).agg(
            unidades=("Unidades", "sum"),
            invoice_count=("Unidades", "count")
        )
        return grouped

    def complete_missing_periods(self, grouped: pd.DataFrame) -> pd.DataFrame:
        """从该产品首次出现的年份开始补齐 0，避免远古年份大量 0 污染特征"""
        min_years = grouped.groupby("Id. Producto")["year"].min().to_dict()
        max_year = grouped["year"].max()
        
        records = []
        for prod, min_y in min_years.items():
            for y in range(min_y, max_year + 1):
                for m in range(1, 13):
                    for h in [1, 2]:
                        records.append((prod, y, m, h))
                        
        full_index = pd.MultiIndex.from_tuples(records, names=["Id. Producto", "year", "month", "half"])
        
        full_df = grouped.set_index(["Id. Producto", "year", "month", "half"]).reindex(full_index).reset_index()
        full_df["unidades"] = full_df["unidades"].fillna(0)
        full_df["invoice_count"] = full_df["invoice_count"].fillna(0)
        return full_df

    def build_features(self, df: pd.DataFrame, fit_encoder: bool = True, first_year: int = None) -> pd.DataFrame:
        df = df.copy()
        df["Id. Producto"] = df["Id. Producto"].astype(str)
        df = df.sort_values(["Id. Producto", "year", "month", "half"]).reset_index(drop=True)

        df["period_in_year"] = (df["month"] - 1) * 2 + df["half"]
        
        if first_year is None:
            first_year = df["year"].min()
        df["time_index"] = (df["year"] - first_year) * 24 + df["period_in_year"]

        if fit_encoder:
            df["product_code"] = self.product_encoder.fit_transform(df["Id. Producto"])
        else:
            known = set(self.product_encoder.classes_)
            df["product_code"] = df["Id. Producto"].apply(lambda x: self.product_encoder.transform([x])[0] if x in known else -1)

        # 核心修复：使用 transform 确保特征计算绝对局限在单一分组内
        # Lag
        for lag in [1, 2, 3, 4, 6, 12, 24]:
            df[f"lag_{lag}"] = df.groupby("Id. Producto")["unidades"].shift(lag)

        # Rolling
        for w in [2, 3, 4, 6, 12, 24]:
            df[f"rolling_{w}"] = df.groupby("Id. Producto")["unidades"].transform(lambda x: x.shift(1).rolling(w).mean())
        for w in [3, 6, 12]:
            df[f"rolling_std_{w}"] = df.groupby("Id. Producto")["unidades"].transform(lambda x: x.shift(1).rolling(w).std())

        # Expanding means (Shifted safely)
        df["product_global_mean"] = df.groupby("Id. Producto")["unidades"].transform(lambda x: x.shift(1).expanding().mean())
        df["product_global_median"] = df.groupby("Id. Producto")["unidades"].transform(lambda x: x.shift(1).expanding().median())
        
        df["product_month_mean"] = df.groupby(["Id. Producto", "month"])["unidades"].transform(lambda x: x.shift(1).expanding().mean())
        df["product_half_mean"] = df.groupby(["Id. Producto", "half"])["unidades"].transform(lambda x: x.shift(1).expanding().mean())
        df["product_period_mean"] = df.groupby(["Id. Producto", "period_in_year"])["unidades"].transform(lambda x: x.shift(1).expanding().mean())
        df["product_month_median"] = df.groupby(["Id. Producto", "month"])["unidades"].transform(lambda x: x.shift(1).expanding().median())

        df["trend_1"] = df["lag_1"] - df["lag_2"]
        df["trend_2"] = df["lag_2"] - df["lag_3"]
        df["trend_3"] = df["rolling_3"] - df["rolling_12"]

        df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
        df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
        df["period_sin"] = np.sin(2 * np.pi * df["period_in_year"] / 24)
        df["period_cos"] = np.cos(2 * np.pi * df["period_in_year"] / 24)

        for col in self.feature_columns:
            if col not in df.columns:
                df[col] = 0
            df[col] = df[col].fillna(0)
            
        return df, first_year

    def create_year_weights(self, years: pd.Series) -> np.ndarray:
        # 核心修复：降低权重衰减率，让模型记住更长远的规律
        max_year = years.max()
        distance = max_year - years
        weights = np.exp(-0.1 * distance) # 从 0.45 降到 0.1
        return weights

    def build_model(self):
        if HAS_XGBOOST:
            return XGBRegressor(
                n_estimators=1500, learning_rate=0.02, max_depth=6,
                min_child_weight=3, subsample=0.8, colsample_bytree=0.8,
                reg_alpha=0.1, reg_lambda=1.5, objective="reg:squarederror",
                random_state=42, n_jobs=-1, tree_method="hist"
            )
        return HistGradientBoostingRegressor(
            max_iter=1000, learning_rate=0.03, max_leaf_nodes=31,
            l2_regularization=0.1, random_state=42
        )

    def train(self):
        raw_df = self.read_csv_safely()
        clean_df = self.clean_data(raw_df)
        grouped = self.aggregate_sales(clean_df)
        full_df = self.complete_missing_periods(grouped)

        feature_df, first_year = self.build_features(full_df, fit_encoder=True)
        last_year = feature_df["year"].max()

        print(f"使用全部年份 {first_year} - {last_year} 训练最终模型...")
        X_all = feature_df[self.feature_columns]
        y_all = np.log1p(feature_df["unidades"].clip(lower=0))
        weight_all = self.create_year_weights(feature_df["year"])

        self.model = self.build_model()
        self.model.fit(X_all, y_all, sample_weight=weight_all)

        package = {
            "model": self.model,
            "product_encoder": self.product_encoder,
            "feature_columns": self.feature_columns,
            "history": feature_df,
            "last_year": int(last_year),
            "first_year": int(first_year),
        }
        joblib.dump(package, MODEL_OUTPUT)
        print(f"最终模型已保存: {MODEL_OUTPUT} 🚀")

if __name__ == "__main__":
    trainer = SalesModelTrainer(CSV_FILE)
    trainer.train()