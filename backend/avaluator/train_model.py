import pandas as pd
import numpy as np
import pickle

from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import LabelEncoder


YEAR_WEIGHTS = {
    2021: 0.114,
    2022: 0.147,
    2023: 0.188,
    2024: 0.242,
    2025: 0.310
}


def load_and_prepare_data(csv_path):
    df = pd.read_csv(csv_path)

    df["Fecha"] = pd.to_datetime(
        df["Fecha"],
        format="%d/%m/%Y",
        errors="coerce"
    )

    df = df.dropna(subset=["Fecha"])

    df["year"] = df["Fecha"].dt.year
    df["month"] = df["Fecha"].dt.month

    df["Unidades"] = pd.to_numeric(df["Unidades"], errors="coerce").fillna(0)
    df["Valores_H"] = pd.to_numeric(df["Valores_H"], errors="coerce").fillna(0)

    monthly_data = (
        df
        .groupby(["Id. Producto", "year", "month"], as_index=False)
        .agg({
            "Unidades": "sum",
            "Valores_H": "sum"
        })
    )

    return monthly_data


def train_model(monthly_data):
    product_encoder = LabelEncoder()

    data = monthly_data.copy()
    data["product_encoded"] = product_encoder.fit_transform(data["Id. Producto"])

    X = data[[
        "product_encoded",
        "year",
        "month",
        "Valores_H"
    ]]

    y = data["Unidades"]

    model = RandomForestRegressor(
        n_estimators=200,
        random_state=42,
        min_samples_leaf=2
    )

    model.fit(X, y)

    return model, product_encoder


def save_training_file(model, product_encoder, monthly_data, output_path):
    training_package = {
        "model": model,
        "product_encoder": product_encoder,
        "monthly_data": monthly_data,
        "year_weights": YEAR_WEIGHTS
    }

    with open(output_path, "wb") as f:
        pickle.dump(training_package, f)


if __name__ == "__main__":
    csv_path = "sales.csv"
    output_path = "sales_model.pkl"

    monthly_data = load_and_prepare_data(csv_path)
    model, product_encoder = train_model(monthly_data)

    save_training_file(
        model=model,
        product_encoder=product_encoder,
        monthly_data=monthly_data,
        output_path=output_path
    )

    print("Modelo entrenado correctamente.")
    print(f"Archivo generado: {output_path}")