# `db/` — Capa de datos del proyecto

Modelo analítico SQLite + scripts del pipeline semanal. Tras la reorg de mayo 2026 los archivos están agrupados por su rol para que el árbol sea legible.

## Estructura

```
db/
├── README.md                   ← este fichero
├── .gitignore                  ← ignora artefactos regenerables
├── schema.sql                  ← tablas (staging + dim + facts + alertas + seguimiento)
├── analysis.sql                ← vistas analíticas (perfil, riesgo, KPI...)
├── interhack.db (+ -wal/-shm)  ← SQLite generado, no comitear
│
├── create_database.py          ← rebuild: lee raw/ → escribe interhack.db
├── run_alerts.py               ← genera alerta_cliente_producto
├── load_model_outputs.py       ← carga dataset/*.csv → tablas ml_*
│
├── export_alerts.py            ← reports/alerts_export.csv
├── quality_report.py           ← reports/quality_report.md
├── build_ml_dataset.py         ← reports/ml_training_dataset.csv
├── train_baseline_model.py     ← models/baseline_model.json + reports/...md
├── compare_model_vs_rules.py   ← models/model_vs_rules.json + reports/...md
├── backtesting.py              ← reports/backtesting_report.md
├── generate_test_cases.py      ← reports/test_cases.csv
│
├── raw/                        ← CSV de origen (ENTRADA del pipeline)
│   ├── Datasets.xlsx - Clientes.csv
│   ├── Datasets.xlsx - Productos.csv
│   ├── Datasets.xlsx - Ventas.csv
│   ├── Datasets.xlsx - Potencial.csv
│   ├── Datasets.xlsx - Campañas.csv
│   └── Master_Datos_Unificado.csv          ← prioritario para ventas
│
├── reports/                    ← .md/.csv regenerables (gitignored)
├── models/                     ← .json de modelos entrenados (gitignored)
└── dataset/                    ← outputs de backend/pipeline.py (XGBoost por cliente)
    ├── clientes_clasificados.csv
    ├── productos_riesgo_cliente.csv
    └── forecast_clientes.csv
```

Cualquier fichero "huérfano" en la raíz tras un pipeline antiguo se puede borrar — los scripts ahora siempre escriben en su carpeta.

## Quick start

```bash
# 1) Rebuild de la base SQLite desde raw/
python3 db/create_database.py

# 2) Pipeline operativo (alertas + reports + modelos + ML por cliente)
bash scripts/run_weekly_pipeline.sh
```

`run_weekly_pipeline.sh` corre create_database, run_alerts, los scripts de informes (escriben en `reports/`), entrena el modelo baseline (escribe en `models/`), y ejecuta `backend/pipeline.py` + `load_model_outputs.py` para tener el segmento ML (LEAL/PROMETEDOR/RISC) disponible al panel.

## Conceptos modelados

- **Ventas con devoluciones**: `unidades_netas > 0` → `COMPRA`, `< 0` → `DEVOLUCION`.
- **Agrupación de productos**: C1/C2 = uso diario, técnicos marcados con `es_tecnico = 1`.
- **Estacionalidad de campañas**: `is_campana` por solape de fechas con `dim_campana`.
- **Potencial cliente**: `bridge_cliente_potencial.potencial_valor` (€ anuales por familia/categoría) para gap analysis.
- **Perfilado**: `vw_perfil_cliente_recurrencia` clasifica `LEAL` / `PROMISCUO` / `MARGINAL`.
- **Vistas operativas**:
  - `vw_alerta_caida_frecuencia`: actual vs media móvil 3M.
  - `vw_proxima_compra_esperada`: fecha esperada y `dias_retraso`.
  - `vw_gap_potencial_cliente`: consumo real vs potencial anual.
  - `vw_alertas_queue_diaria`: top 25 prioritarias.
  - `vw_alertas_operativas_final`: cola completa con `prioridad`.
  - `vw_clientes_estado`: split `CLIENTE` vs `NO_CLIENTE`.
  - `vw_cliente_semaforo_riesgo`: semáforo `ROJO/AMARILLO/VERDE`.
  - `vw_kpi_semanal_operacion`: KPIs por semana.
- **Seguimiento**: `seguimiento_alerta` registra resultado por `alerta_id` (con `es_demo` para tests).

## Tablas ML (poblan via `load_model_outputs.py`)

- `ml_client_segment`: 1 fila/cliente con segment LEAL/PROMETEDOR/RISC, spend 12m, predicho 6m, n_prod_critical/warning.
- `ml_client_product_risk`: productos en riesgo por cliente (gravedad CRITICAL/WARNING/INFO).
- `ml_client_forecast`: forecast 6 meses por cliente (predicho + IC inferior/superior).

Estas tablas son las que sirven `/clients`, `/clients/{id}`, `/dashboard/segments-summary` en `backend/app.py`.

## Notas operativas

- `run_alerts.py` usa la **fecha máxima del Master** como referencia (`as_of_date`) — no la fecha del sistema — para que los retrasos sean coherentes con datasets históricos.
- Calibración de prioridades:
  - score ponderado = retraso + caída-frecuencia + gap potencial
  - `risk_percentile` y gates de evidencia para evitar `ALTA` débiles
  - `ALTA` ≈ top 20% con ≥ 21 días retraso y ≥ 2 señales activas
  - `MEDIA` ≈ top 55% con ≥ 1 señal activa
  - `BAJA` el resto accionable
- IDs presentes en ventas/potencial pero ausentes en `Clientes` se añaden a `dim_cliente` con metadata nula.

## Cargar resultados de un operador (manual)

```sql
INSERT INTO seguimiento_alerta (alerta_id, fecha_contacto, estado, es_demo, notas, usuario_operador)
VALUES (17, '2026-05-09', 'recuperado', 0, 'Se confirma nueva compra', 'op_01');
```

Los KPI semanales ignoran filas con `es_demo = 1`.
