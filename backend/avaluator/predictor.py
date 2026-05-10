import pickle
import pandas as pd


class SalesPredictor:
    def __init__(self, model_path="sales_model.pkl"):
        with open(model_path, "rb") as f:
            package = pickle.load(f)

        self.model = package["model"]
        self.product_encoder = package["product_encoder"]
        self.monthly_data = package["monthly_data"]
        self.year_weights = package["year_weights"]

    def weighted_history_prediction(self, product_id, month):
        product_month_data = self.monthly_data[
            (self.monthly_data["Id. Producto"] == product_id) &
            (self.monthly_data["month"] == month)
        ]

        prediction = 0
        used_weight = 0

        for year, weight in self.year_weights.items():
            row = product_month_data[product_month_data["year"] == year]

            if not row.empty:
                units = row["Unidades"].values[0]
                prediction += units * weight
                used_weight += weight

        if used_weight > 0:
            prediction = prediction / used_weight
        else:
            prediction = 0

        return prediction

    def ml_prediction(self, product_id, month, target_year=2026):
        if product_id not in self.product_encoder.classes_:
            return 0

        product_encoded = self.product_encoder.transform([product_id])[0]

        similar_data = self.monthly_data[
            (self.monthly_data["Id. Producto"] == product_id) &
            (self.monthly_data["month"] == month)
        ]

        if not similar_data.empty:
            estimated_value = similar_data["Valores_H"].mean()
        else:
            estimated_value = self.monthly_data["Valores_H"].mean()

        X_pred = pd.DataFrame([{
            "product_encoded": product_encoded,
            "year": target_year,
            "month": month,
            "Valores_H": estimated_value
        }])

        prediction = self.model.predict(X_pred)[0]

        return prediction

    def predict(self, product_id, month, target_year=2026):
        history_pred = self.weighted_history_prediction(product_id, month)
        ml_pred = self.ml_prediction(product_id, month, target_year)

        final_prediction = history_pred * 0.60 + ml_pred * 0.40

        return {
            "product_id": product_id,
            "month": month,
            "target_year": target_year,
            "history_prediction": round(float(history_pred), 2),
            "ml_prediction": round(float(ml_pred), 2),
            "final_prediction": round(float(final_prediction), 2),
            "final_prediction_units": int(round(final_prediction))
        }


if __name__ == "__main__":
    predictor = SalesPredictor("sales_model.pkl")

    product_id =int(input("Enter product ID: "))

    for i in range(1, 13):
        result = predictor.predict(
            product_id=product_id,
            month=i,
            target_year="2026"
        )
        print(f"Month: {i}, Prediction: {result['final_prediction_units']} units")