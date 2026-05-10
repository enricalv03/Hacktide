import pandas as pd

def consultar_ventas_anuales_del_mes():
    print("Cargando datos...")
    # 1. 读取并预处理数据
    df = pd.read_csv('sales.csv', low_memory=False)
    df.columns = df.columns.str.strip()
    df['Fecha'] = pd.to_datetime(df['Fecha'], format='%m/%d/%Y')
    df['Unidades'] = pd.to_numeric(df['Unidades'], errors='coerce')
    
    # 提取年份和月份
    df['Año'] = df['Fecha'].dt.year
    df['Mes'] = df['Fecha'].dt.month
    
    print("Datos listos.\n")

    while True:
        # 2. 获取用户输入
        id_input = input("Ingrese el ID del producto (o 'salir' para terminar): ").strip()
        if id_input.lower() == 'salir':
            print("Hasta luego.")
            break
            
        mes_input = input("Ingrese el mes a consultar (1-12): ").strip()
        if mes_input.lower() == 'salir':
            print("Hasta luego.")
            break

        try:
            id_producto = int(id_input)
            mes = int(mes_input)
            
            if not (1 <= mes <= 12):
                print("\n[!] Error: El mes debe estar entre 1 y 12.\n")
                continue
            
            # 3. 核心逻辑：同时过滤产品ID和月份
            df_filtrado = df[(df['Id. Producto'] == id_producto) & (df['Mes'] == mes)]
            
            if df_filtrado.empty:
                print(f"\n[!] No hay registros para el producto {id_producto} en el mes {mes}.\n")
                continue
            
            # 4. 按年份分组并把当月的 'Unidades' 加起来
            ventas_por_ano = df_filtrado.groupby('Año')['Unidades'].sum().reset_index()
            
            # 5. 打印干净的结果
            print(f"\n{'='*45}")
            print(f"Producto ID: {id_producto} | Ventas en el mes: {mes}")
            print(f"{'='*45}")
            
            for index, row in ventas_por_ano.iterrows():
                # 格式化输出，确保年份和数量显示为整数
                print(f"En el año {int(row['Año'])} se vendieron: {int(row['Unidades'])} unidades")
            
            print(f"{'-'*45}")
            # 顺便给一个历年该月的总和
            total_historico = int(ventas_por_ano['Unidades'].sum())
            print(f"Total histórico de todos los años: {total_historico}")
            print(f"{'='*45}\n")
                
        except ValueError:
            print("\n[!] Error: Ingrese números válidos.\n")

if __name__ == "__main__":
    try:
        consultar_ventas_anuales_del_mes()
    except FileNotFoundError:
        print("Error: No se encontró el archivo 'sales.csv'.")