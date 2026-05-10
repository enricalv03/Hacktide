import pandas as pd
import numpy as np
import joblib

class SalesPredictor:
    def __init__(self, model_path="sales_model.pkl"):
        package = joblib.load(model_path)
        self.model = package["model"]
        self.product_encoder = package["product_encoder"]
        self.feature_columns = package["feature_columns"]
        self.history = package["history"].copy()
        self.last_year = package["last_year"]
        self.first_year = package.get("first_year", self.history["year"].min())

        self.history["Id. Producto"] = self.history["Id. Producto"].astype(str)
        self.history["half"] = self.history["half"].astype(int)
        self.history["month"] = self.history["month"].astype(int)
        self.history["year"] = self.history["year"].astype(int)

    def build_features(self, df):
        """完全同步训练集特征构造逻辑"""
        df = df.copy()
        df["Id. Producto"] = df["Id. Producto"].astype(str)
        df = df.sort_values(["Id. Producto", "year", "month", "half"]).reset_index(drop=True)

        df["period_in_year"] = (df["month"] - 1) * 2 + df["half"]
        # 核心修复：复用训练集的 first_year，确保 time_index 基准统一
        df["time_index"] = (df["year"] - self.first_year) * 24 + df["period_in_year"]

        known = set(self.product_encoder.classes_)
        df["product_code"] = df["Id. Producto"].apply(lambda x: self.product_encoder.transform([x])[0] if x in known else -1)

        for lag in [1, 2, 3, 4, 6, 12, 24]:
            df[f"lag_{lag}"] = df.groupby("Id. Producto")["unidades"].shift(lag)

        for w in [2, 3, 4, 6, 12, 24]:
            df[f"rolling_{w}"] = df.groupby("Id. Producto")["unidades"].transform(lambda x: x.shift(1).rolling(w).mean())
        for w in [3, 6, 12]:
            df[f"rolling_std_{w}"] = df.groupby("Id. Producto")["unidades"].transform(lambda x: x.shift(1).rolling(w).std())

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

        return df

    def predict_one_period(self, product_id, target_year, month, half, working_history):
        new_row = {
            "Id. Producto": str(product_id),
            "year": int(target_year),
            "month": int(month),
            "half": int(half),
            "unidades": np.nan,
            "invoice_count": 0,
        }

        combined = pd.concat([working_history, pd.DataFrame([new_row])], ignore_index=True)
        feature_df = self.build_features(combined)

        target_mask = (
            (feature_df["Id. Producto"] == str(product_id)) &
            (feature_df["year"] == int(target_year)) &
            (feature_df["month"] == int(month)) &
            (feature_df["half"] == int(half))
        )
        current_row = feature_df[target_mask]

        if current_row.empty:
            raise RuntimeError("没有找到当前预测行")

        X = current_row[self.feature_columns]
        
        # 预测并还原对数
        pred_log = float(self.model.predict(X)[0])
        model_pred = float(np.expm1(pred_log))
        
        # 核心修复：移除人为兜底，完全信任模型，仅做非负数限制
        pred_units = max(0, model_pred)

        saved_row = new_row.copy()
        saved_row["unidades"] = pred_units
        working_history = pd.concat([working_history, pd.DataFrame([saved_row])], ignore_index=True)

        return pred_units, working_history

    def predict_product_month(self, product_id, month, target_year=None, debug=False):
        product_id = str(product_id)
        if target_year is None:
            target_year = self.last_year + 1

        if month < 1 or month > 12:
            raise ValueError("month 必须是 1 到 12")

        working_history = self.history[self.history["Id. Producto"] == product_id].copy()

        if working_history.empty:
            print(f"警告: 产品 {product_id} 是新产品，没有历史数据。模型将基于 0 历史特征预测。")

        predictions = {}

        # 步进推演
        for m in range(1, int(month) + 1):
            for half in [1, 2]:
                pred_units, working_history = self.predict_one_period(
                    product_id=product_id, target_year=target_year, month=m, half=half,
                    working_history=working_history
                )
                
                if debug:
                    print(f"DEBUG {product_id} {target_year}-{m} H{half}: {pred_units:.2f} 件")

                if m == int(month):
                    predictions[f"H{half}"] = round(pred_units, 2)

        total = predictions["H1"] + predictions["H2"]

        return {
            "product_id": product_id,
            "year": int(target_year),
            "month": int(month),
            "H1": predictions["H1"],
            "H2": predictions["H2"],
            "total": round(total, 2)
        }

if __name__ == "__main__":
    predictor = SalesPredictor("sales_model.pkl")
    prod_id = input("Enter product ID: ")
    tgt_month = int(input("Enter target month 1-12: "))
    
    res = predictor.predict_product_month(product_id=prod_id, month=tgt_month, debug=True)
    print("\n预测结果:", res)