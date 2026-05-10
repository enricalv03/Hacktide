import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Leer el archivo CSV
df = pd.read_csv('sales.csv', low_memory=False)

# Limpiar nombres de columnas (eliminar espacios)
df.columns = df.columns.str.strip()

# Convertir la columna Fecha a datetime
df['Fecha'] = pd.to_datetime(df['Fecha'], format='%m/%d/%Y')

# Convertir Unidades a numerico
df['Unidades'] = pd.to_numeric(df['Unidades'], errors='coerce')

# Agrupar por Id. Producto y Fecha, sumar unidades
sales_by_product_date = df.groupby(['Id. Producto', 'Fecha'])['Unidades'].sum().reset_index()

# Ordenar por producto y fecha
sales_by_product_date = sales_by_product_date.sort_values(['Id. Producto', 'Fecha'])

# Lista de productos
productos = sorted(sales_by_product_date['Id. Producto'].unique())

print("=" * 60)
print("ANALISIS DE EVOLUCION DE COMPRAS POR PRODUCTO")
print("=" * 60)
print(f"\nTotal de productos disponibles: {len(productos)}")
print(f"Productos: {list(productos)}")
print(f"Rango de fechas: {df['Fecha'].min().date()} a {df['Fecha'].max().date()}\n")


def generar_grafica_producto(id_producto):
    if id_producto not in productos:
        print(f"Error: El producto {id_producto} no existe en los datos.")
        print(f"Productos disponibles: {list(productos)}")
        return

    datos_producto = sales_by_product_date[sales_by_product_date['Id. Producto'] == id_producto]
    datos_producto = datos_producto.sort_values('Fecha')
    serie = datos_producto.set_index('Fecha')['Unidades']
    serie_mensual = serie.resample('M').sum()
    serie_suavizada = serie_mensual.rolling(window=3, min_periods=1).mean()

    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(12, 6))

    ax.fill_between(serie_suavizada.index, serie_suavizada.values, color='#BFD7EA', alpha=0.6)
    ax.plot(
        serie_mensual.index,
        serie_mensual.values,
        linewidth=2.0,
        color='#5DA9E9',
        alpha=0.6,
        label='Total mensual'
    )
    ax.plot(
        serie_suavizada.index,
        serie_suavizada.values,
        linewidth=3.0,
        color='#1B4965',
        label='Tendencia (3 meses)'
    )

    ax.set_title(
        f'Evolucion de Compras - Producto ID: {id_producto}',
        fontsize=14,
        fontweight='bold',
        pad=20
    )
    ax.set_xlabel('Fecha', fontsize=12)
    ax.set_ylabel('Cantidad de Unidades', fontsize=12)
    ax.grid(True, alpha=0.25, linestyle='--')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    ax.legend(frameon=False, loc='upper left')

    total_unidades = datos_producto['Unidades'].sum()
    promedio = datos_producto['Unidades'].mean()
    max_venta = datos_producto['Unidades'].max()
    min_venta = datos_producto['Unidades'].min()
    num_transacciones = len(datos_producto)

    stats_text = (
        f"Total: {total_unidades:.0f} | Promedio: {promedio:.1f} | "
        f"Max: {max_venta:.0f} | Min: {min_venta:.0f}"
    )
    ax.text(
        0.5,
        -0.15,
        stats_text,
        transform=ax.transAxes,
        ha='center',
        fontsize=10,
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5)
    )

    plt.tight_layout()

    filename = f'producto_{id_producto}.png'
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"Grafica guardada como: {filename}")

    print(f"\n{'='*50}")
    print(f"PRODUCTO ID: {id_producto}")
    print(f"{'='*50}")
    print(f"Total de unidades compradas: {total_unidades:.0f}")
    print(f"Promedio por transaccion: {promedio:.2f}")
    print(f"Maximo comprado en una transaccion: {max_venta:.0f}")
    print(f"Minimo comprado en una transaccion: {min_venta:.0f}")
    print(f"Numero de transacciones: {num_transacciones}")
    print(f"Fecha primera compra: {datos_producto['Fecha'].min().date()}")
    print(f"Fecha ultima compra: {datos_producto['Fecha'].max().date()}")
    print(f"{'='*50}\n")

    plt.show()


while True:
    try:
        id_input = input("Ingrese el ID del producto (o 'salir' para terminar): ").strip()

        if id_input.lower() == 'salir':
            print("Hasta luego.")
            break

        id_producto = int(id_input)
        generar_grafica_producto(id_producto)

    except ValueError:
        print("Ingrese un numero valido o 'salir' para terminar.\n")
