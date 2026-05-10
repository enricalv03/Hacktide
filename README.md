# INIBSA · Panel comercial inteligente

> Plataforma analítica y operativa que detecta clientes en riesgo, predice la
> próxima compra por cliente/producto y genera alertas accionables para que el
> equipo comercial sepa **a quién llamar, qué decir y por qué**.

Construido durante el **INTERHACK** sobre datos reales (anonimizados) de
ventas, productos, potencial y campañas.

---

## Tabla de contenidos

1. [¿Qué hace el panel?](#qué-hace-el-panel)
2. [Arranque rápido (un solo comando)](#arranque-rápido-un-solo-comando)
3. [Arranque manual paso a paso](#arranque-manual-paso-a-paso)
4. [Arquitectura del proyecto](#arquitectura-del-proyecto)
5. [Pipeline de datos y modelo](#pipeline-de-datos-y-modelo)
6. [Endpoints del API](#endpoints-del-api)
7. [Ciclo operativo recomendado](#ciclo-operativo-recomendado)
8. [Troubleshooting](#troubleshooting)
9. [Variables de entorno y opciones](#variables-de-entorno-y-opciones)
10. [Roadmap y estado actual](#roadmap-y-estado-actual)

---

## ¿Qué hace el panel?

El panel se organiza en **5 pestañas**, pensadas tanto para el comercial de
campo como para perfiles directivos sin background técnico:

| Pestaña | Para qué sirve |
| ------- | -------------- |
| **Resumen ejecutivo** | KPIs de cartera + matriz Segmento × Semáforo + listas «llamar primero» y «recuperación rentable». Un vistazo de un minuto al estado del negocio. |
| **Cartera de clientes** | Tabla priorizada de la cartera con **segmento ML** (Leal/Prometedor/Riesgo) y **compra prevista a 6 meses** vs los 6 anteriores. |
| **Productos** | Tendencia y forecast por familia/producto, con foco en uso diario (anestesia + desinfección) y técnicos por separado. |
| **Análisis** | Vista por cliente: histórico de compras, productos en riesgo, gráfico predictivo con leyenda y proyección al mes siguiente, y botón **Enviar a Alertas y Contacto**. |
| **Alertas y Contacto** | Tu **cola personal de llamadas**. Empieza vacía: añades clientes desde Resumen ejecutivo o Análisis y aquí encuentras teléfono, email y guion sugerido. Marca cada caso como *Contactado · Recuperado · Perdido*. |

Bajo el capó hay un pipeline ETL completo (CSV → SQLite con vistas analíticas
+ tablas ML), un motor de alertas por reglas, un baseline ML calibrado contra
las reglas, y un pipeline XGBoost por cliente/producto que genera el segmento y
la previsión a 6 meses.

---

## Arranque rápido (un solo comando)

> Requiere **Python 3.10+** y **Node.js 18+**.

```bash
git clone <repo-url>
cd INTERHACK
bash scripts/run_all.sh
```

Eso es todo. El script:

1. Crea (o reutiliza) un entorno virtual `venv/` e instala las dependencias del
   backend desde `backend/requirements.txt`.
2. Si no existe `db/interhack.db`, lo genera con `db/create_database.py` y
   ejecuta `db/run_alerts.py` para tener alertas iniciales.
3. Instala las dependencias del frontend (`npm install`) si no estaban.
4. Libera los puertos `8000` (API) y `5173` (Vite) si quedaron ocupados.
5. Lanza **Uvicorn** en background y espera a que `/health` responda OK.
6. Lanza **Vite** y, cuando está listo, abre el navegador automáticamente en
   <http://127.0.0.1:5173>.
7. Mantiene los dos procesos vivos hasta que pulsas **Ctrl+C** (entonces los
   detiene de forma limpia).

Logs en `.run_all_logs/api.log` y `.run_all_logs/frontend.log`.

### Opciones útiles

```bash
# Otros puertos
API_PORT=8001 FRONT_PORT=5174 bash scripts/run_all.sh

# Salto rápido (segundo arranque del día, no quiero reinstalar)
SKIP_INSTALL=1 SKIP_DB_CHECK=1 bash scripts/run_all.sh

# No abrir navegador
NO_OPEN=1 bash scripts/run_all.sh
```

---

## Arranque manual paso a paso

Si prefieres tener cada cosa en su propia terminal:

### 1) Generar la base SQLite (solo la primera vez o tras tocar los CSV)

Coloca los CSV de origen en `db/raw/`:

- `Datasets.xlsx - Clientes.csv`
- `Datasets.xlsx - Productos.csv`
- `Datasets.xlsx - Ventas.csv`
- `Datasets.xlsx - Potencial.csv`
- `Datasets.xlsx - Campañas.csv`
- `Master_Datos_Unificado.csv` *(opcional, pero recomendado para el ML)*

```bash
python3 db/create_database.py     # rebuild interhack.db desde db/raw/
python3 db/run_alerts.py          # genera alerta_cliente_producto
```

### 2) Pipeline operativo completo (alertas + reports + ML)

```bash
bash scripts/run_weekly_pipeline.sh
```

### 3) Backend (FastAPI + Uvicorn)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
bash scripts/start_api.sh         # → http://127.0.0.1:8000
```

### 4) Frontend (Vite + React)

```bash
cd frontend
npm install
npm run dev                       # → http://127.0.0.1:5173
```

---

## Arquitectura del proyecto

```
INTERHACK/
├── README.md                  ← este archivo
├── ROADMAP.md                 ← fases del proyecto y reglas de negocio
├── scripts/
│   ├── run_all.sh             ← lanza backend + frontend con un solo comando
│   ├── start_api.sh           ← arranca solo el backend
│   └── run_weekly_pipeline.sh ← pipeline ETL + ML semanal
│
├── backend/                   ← FastAPI + Uvicorn
│   ├── app.py                 ← endpoints REST que consume el frontend
│   ├── pipeline.py            ← XGBoost por cliente: segmento + forecast 6m
│   ├── charts.py              ← informe HTML con Plotly (offline)
│   ├── forecast_por_tipo.py   ← XGBoost pooled por producto técnico/uso diario
│   ├── check_forecast_setup.py
│   ├── exportar_forecast_csv.py
│   ├── master_pulse.py
│   ├── requirements.txt
│   └── requirements-forecast.txt
│
├── db/                        ← capa de datos (SQLite + scripts ETL/ML)
│   ├── README.md              ← detalle por archivo y vista
│   ├── schema.sql             ← tablas (staging + dim + facts + alertas)
│   ├── analysis.sql           ← vistas analíticas (perfil, riesgo, KPI…)
│   ├── interhack.db           ← SQLite generada (no comitear)
│   ├── create_database.py     ← rebuild db a partir de raw/
│   ├── run_alerts.py          ← motor de alertas (reglas calibradas)
│   ├── load_model_outputs.py  ← carga dataset/*.csv → tablas ml_*
│   ├── raw/                   ← CSV de origen (entrada)
│   ├── reports/               ← informes generados (.md/.csv) [gitignored]
│   ├── models/                ← modelos entrenados (.json)    [gitignored]
│   └── dataset/               ← outputs del pipeline ML por cliente
│
└── frontend/                  ← React + Vite
    ├── src/
    │   ├── App.jsx
    │   ├── api/client.js      ← capa fetch con timeouts y reintentos
    │   ├── hooks/useCallQueue.js  ← cola opt-in (localStorage)
    │   ├── components/
    │   │   ├── pulse/PulseView.jsx       ← Resumen ejecutivo
    │   │   ├── usuarios/UsuariosView.jsx ← Cartera
    │   │   ├── productos/ProductosView.jsx
    │   │   ├── analisis/AnalisisView.jsx
    │   │   ├── alertas/AlertasView.jsx   ← cola opt-in
    │   │   └── layout/Sidebar.jsx
    │   └── styles/theme.css
    ├── package.json
    └── vite.config.js
```

---

## Pipeline de datos y modelo

```
db/raw/*.csv  ─┐
               ├─► db/create_database.py ─► db/interhack.db
               │                            (tablas + analysis.sql vistas)
               │
               ├─► db/run_alerts.py ─────► tabla alerta_cliente_producto
               │                            (semáforo ROJO/AMARILLO/VERDE,
               │                             prioridad ALTA/MEDIA/BAJA)
               │
Master_Unif    ├─► backend/pipeline.py ──► db/dataset/*.csv
.csv           │                            (segmento + forecast 6m + riesgo
               │                             producto vía XGBoost por cliente)
               │
               └─► db/load_model_outputs.py ─► tablas ml_client_segment,
                                                ml_client_product_risk,
                                                ml_client_forecast en SQLite
```

### Conceptos clave

- **Productos uso diario** (C1/C2 = anestesia + desinfección) vs **técnicos**:
  los técnicos no se usan para clasificar recurrencia.
- **Perfil cliente por recurrencia**: `LEAL` (≥70 %), `PROMISCUO` (30–70 %),
  `MARGINAL` (<30 %). Excluye técnicos.
- **Semáforo de riesgo**: combina retraso vs intervalo esperado, caída de
  frecuencia (3M vs histórico) y gap de potencial.
- **Segmento ML** (XGBoost): `LEAL` / `PROMETEDOR` / `RISC`, basado en
  patrones de comportamiento más sutiles que las reglas SQL.
- **Forecast 6m**: predicción de gasto a 6 meses por cliente, con intervalo de
  confianza inferior/superior.

Detalle completo en [`db/README.md`](db/README.md) y [`ROADMAP.md`](ROADMAP.md).

---

## Endpoints del API

Documentación interactiva auto-generada en <http://127.0.0.1:8000/docs>.

Resumen de los endpoints más usados:

| Método | Ruta | Devuelve |
| ------ | ---- | -------- |
| `GET` | `/health` | Health check (`{"ok": true}`) |
| `GET` | `/clients` | Cartera priorizada con segmento ML y forecast 6m |
| `GET` | `/clients/options` | Lista ligera para autocompletados |
| `GET` | `/clients/{id}` | Ficha del cliente con bloque ML |
| `GET` | `/clients/{id}/profile` | Ficha extendida + tendencia 12 meses |
| `GET` | `/clients/{id}/contact` | Datos de contacto, guion y top productos en riesgo |
| `GET` | `/dashboard/pulse-board` | KPIs + matriz Segmento × Semáforo + top urgentes + recuperación |
| `GET` | `/dashboard/pulse-board/cell-clients` | Lista real de clientes en una celda de la matriz |
| `GET` | `/dashboard/segments-summary` | Resumen agregado por segmento ML |
| `GET` | `/dashboard/pending-clients` | Cola operativa de alertas pendientes |
| `GET` | `/dashboard/kpi-today` | KPIs del día (alertas, semáforos, etc.) |
| `GET` | `/dashboard/model-vs-rules` | Comparativa modelo vs reglas |
| `GET` | `/alerts/queue` | Cola completa de alertas filtrable |
| `GET` | `/alerts/daily` | Top alertas del día |
| `POST` | `/alerts/{id}/outcome` | Registra resultado (`contactado/recuperado/perdido`) |
| `GET` | `/products/analysis` | Serie producto vs total cliente para gráfico |
| `GET` | `/products/options` | Lista de productos para selectores |
| `GET` | `/products/xgb-forecast/portfolio-summary` | Tendencia agregada del portfolio |
| `GET` | `/products/xgb-forecast/{producto_id}` | Forecast por producto (XGB pooled) |
| `POST` | `/products/escalate` | Escala una alerta a producción |

---

## Ciclo operativo recomendado

```
Lunes        ┌──────────────────────────────────────────────┐
             │ bash scripts/run_weekly_pipeline.sh          │ ← ETL + ML semanal
             └──────────────────────────────────────────────┘
             │
Lunes-Vier.  ▼
             ┌──────────────────────────────────────────────┐
             │ bash scripts/run_all.sh                      │ ← arranca panel
             └──────────────────────────────────────────────┘
             │
             ▼
   1. Resumen ejecutivo: leer KPIs + matriz, identificar el cuadrante caliente.
   2. Cartera de clientes: revisar Top urgentes; añadir a cola con un clic.
   3. Análisis: abrir clientes con dudas; ver su gráfico y proyección.
   4. Alertas y Contacto: trabajar la cola del día (Contactado/Recuperado/Perdido).
   5. Cerrar Ctrl+C cuando acabes.
```

---

## Troubleshooting

### «Sin conexión al API» en la UI

- Comprueba que `bash scripts/run_all.sh` sigue corriendo en otra terminal.
- Pulsa el botón **«Reintentar»** del propio panel (el frontend reintenta
  cada pocos segundos automáticamente con backoff exponencial).
- Mira los logs:
  ```bash
  tail -n 30 .run_all_logs/api.log
  ```

### El backend dice `Address already in use`

```bash
# macOS / Linux
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill -9
```

O simplemente vuelve a lanzar `run_all.sh` — el script libera los puertos por
ti antes de arrancar.

### El frontend tarda mucho en mostrar la cartera

La primera carga calienta una vista materializada (`mv_cartera_clientes`) en
SQLite. Una vez caliente, las siguientes peticiones bajan a ~200 ms. Si ves la
tabla cargando varias veces seguidas, el cache se está reconstruyendo porque
la base ha cambiado.

### El pipeline ML se cae

`backend/pipeline.py` necesita `xgboost` y `pandas`. Si quieres usarlo:

```bash
pip install -r backend/requirements-forecast.txt
```

Si no lo instalas, la app sigue funcionando con las reglas y vistas SQL — solo
no aparecerá el bloque «Segmento ML» en la cartera.

### Quiero re-generar la base limpia

```bash
rm -f db/interhack.db db/interhack.db-shm db/interhack.db-wal
python3 db/create_database.py
python3 db/run_alerts.py
```

---

## Variables de entorno y opciones

### `scripts/run_all.sh`

| Variable | Default | Descripción |
| -------- | ------- | ----------- |
| `API_PORT` | `8000` | Puerto del backend |
| `FRONT_PORT` | `5173` | Puerto del frontend (Vite) |
| `SKIP_INSTALL` | `0` | `1` para no instalar dependencias (segundas ejecuciones) |
| `SKIP_DB_CHECK` | `0` | `1` para no comprobar/regenerar la base |
| `NO_OPEN` | `0` | `1` para no abrir el navegador automáticamente |

### `scripts/start_api.sh`

| Variable | Default | Descripción |
| -------- | ------- | ----------- |
| `PORT` | `8000` | Puerto del backend |

---

## Roadmap y estado actual

El proyecto cubre las fases 0–7 del [`ROADMAP.md`](ROADMAP.md):

- ✅ ETL completo y vistas analíticas
- ✅ Motor de alertas con prioridades calibradas
- ✅ Frontend operativo con cartera, análisis, productos y cola de llamadas
- ✅ Baseline ML comparado contra reglas
- ✅ XGBoost por cliente: segmento + forecast a 6 meses
- ✅ Cola de llamadas opt-in con persistencia local
- ✅ Botón «Enviar a Alertas y Contacto» end-to-end
- 🔜 Sincronizar la cola personal con `seguimiento_alerta` en SQLite (hoy es
  solo `localStorage`)
- 🔜 Integración con CRM real para sustituir los datos de contacto sintéticos

Consulta [`ROADMAP.md`](ROADMAP.md) para el detalle de cada fase y las reglas
de negocio originales.

---

## Licencia

Ver [`LICENSE`](LICENSE).
