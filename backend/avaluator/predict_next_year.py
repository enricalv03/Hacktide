import pandas as pd
import numpy as np
import joblib


class ProductSalesPredictor:
    def __init__(self, model_path: str):
        package = joblib.load(model_path)

        self.model = package["model"]
        self.product_encoder = package["product_encoder"]
        self.feature_columns = package["feature_columns"]
        self.history = package["history"].copy()
        self.last_year = package["last_year"]

    def make_future_rows(self, target_year: int, product_id: str | None = None):
        """
        生成未来一年 12个月 x 上下半月 的预测行。
        """
        if product_id is None:
            products = self.history["Id. Producto"].unique()
        else:
            products = [str(product_id)]

        rows = []

        for pid in products:
            for month in range(1, 13):
                for half in [1, 2]:
                    rows.append({
                        "Id. Producto": str(pid),
                        "year": target_year,
                        "month": month,
                        "half": half,
                        "unidades": np.nan,
                        "invoice_count": 0
                    })

        return pd.DataFrame(rows)

    def build_features_for_all(self, df: pd.DataFrame):
        """
        给历史 + 未来数据重新创建特征。
        """
        df = df.copy()
        df = df.sort_values(["Id. Producto", "year", "month", "half"])

        df["period_in_year"] = (df["month"] - 1) * 2 + df["half"]
        df["time_index"] = df["year"] * 24 + df["period_in_year"]

        df["product_code"] = self.product_encoder.transform(df["Id. Producto"].astype(str))

        df["lag_1"] = df.groupby("Id. Producto")["unidades"].shift(1)
        df["lag_2"] = df.groupby("Id. Producto")["unidades"].shift(2)
        df["lag_12"] = df.groupby("Id. Producto")["unidades"].shift(12)
        df["lag_24"] = df.groupby("Id. Producto")["unidades"].shift(24)

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

    def predict_next_year(self, target_year: int | None = None, product_id: str | None = None):
        """
        递归预测下一年。

        例如预测 2026 年：
        先预测 1月上半月；
        然后把这个预测值当成历史；
        再预测 1月下半月；
        再预测 2月上半月...
        """
        if target_year is None:
            target_year = self.last_year + 1

        if product_id is not None:
            product_id = str(product_id)

            if product_id not in set(self.product_encoder.classes_):
                raise ValueError(f"产品 {product_id} 不在训练数据中。")

        future = self.make_future_rows(target_year, product_id)

        working_history = self.history.copy()

        predictions = []

        for _, row in future.iterrows():
            current_row = pd.DataFrame([row])

            combined = pd.concat([working_history, current_row], ignore_index=True)
            combined_features = self.build_features_for_all(combined)

            current_features = combined_features.iloc[[-1]]
            X = current_features[self.feature_columns]

            pred_log = self.model.predict(X)[0]
            pred_units = float(np.expm1(pred_log))
            pred_units = max(0, pred_units)

            pred_units_rounded = round(pred_units, 2)

            result = {
                "Id. Producto": row["Id. Producto"],
                "Año": target_year,
                "Mes": int(row["month"]),
                "Mitad": "H1" if int(row["half"]) == 1 else "H2",
                "Pred_Unidades": pred_units_rounded
            }

            predictions.append(result)

            # 把预测结果加入历史，方便后面的 lag 和 rolling 使用
            new_history_row = row.copy()
            new_history_row["unidades"] = pred_units
            working_history = pd.concat(
                [working_history, pd.DataFrame([new_history_row])],
                ignore_index=True
            )

        return pd.DataFrame(predictions)

    def save_predictions(self, output_path: str, target_year: int | None = None, product_id: str | None = None):
        pred_df = self.predict_next_year(target_year, product_id)
        pred_df.to_excel(output_path, index=False)
        print(f"预测结果已保存到: {output_path}")
        return pred_df


if __name__ == "__main__":
    predictor = ProductSalesPredictor("sales_model.pkl")

    # 预测所有产品下一年
    result = predictor.save_predictions(
        output_path="prediction_next_year.xlsx",
        target_year=None,
        product_id=None
    )

    print(result.head(30))