# ROADMAP

## Objetivo general

Construir una base analitica y operativa para:

- detectar clientes que reducen frecuencia o volumen de compra;
- predecir la proxima fecha esperada de pedido por cliente/producto recurrente;
- generar alertas accionables para operadores (`id_cliente`, `id_producto`, `prioridad`, motivo y recomendacion).

## Reglas de negocio base

- **Productos recurrentes (uso diario)**: Desinfeccion + Anestesia (categorias C1/C2).
- **Productos tecnicos**: no se usan para clasificar "promiscuidad" (compra no recurrente).
- **Perfil de cliente por recurrencia**:
  - `LEAL`: recurrencia >= 70%
  - `PROMISCUO`: recurrencia entre 30% y 70%
  - `MARGINAL`: recurrencia > 0% y < 30%
- **Unidades en ventas**:
  - `unidades > 0`: compra
  - `unidades < 0`: devolucion
- **`Potencial_H`**: es clave; representa el costo anual esperado por cliente en euros (base para gap de compra y riesgo comercial).
- **`Valores_H`**: no se usa (dato no confiable).
- **Estacionalidad y campañas**: deben corregir comparaciones para evitar falsas alertas.

## Fase 0 - Setup y estructura de datos

- [x] Crear base SQLite inicial (`db/interhack.db`) con esquema normalizado.
- [x] Crear tablas staging, dimensiones, hechos y tabla de alertas.
- [x] Cargar CSVs y normalizar llaves (`cliente`, `producto`, fechas, potencial).
- [x] Marcar ventas en ventana de campaña.
- [x] Definir version Postgres del esquema (`schema_postgres.sql`) para produccion.

## Fase 1 - Calidad y consistencia de datos

- [x] Validar duplicados de clientes y politica de consolidacion.
- [x] Documentar clientes presentes en ventas/potencial pero ausentes en maestros.
- [x] Validar rangos de fechas, nulos y tipos.
- [x] Crear reporte de calidad semanal (conteos + anomalias).

## Fase 2 - Capa analitica base (features SQL)

- [x] Vista mensual por `cliente-producto` con compras, devoluciones y movimientos.
- [x] Vista de intervalos entre compras (dias entre pedidos consecutivos).
- [x] Vista de perfil de cliente (`LEAL`, `PROMISCUO`, `MARGINAL`) excluyendo tecnicos.
- [x] Vista de tendencia (`media 3m reciente` vs `media historica`) por cliente/producto.
- [x] Variables de estacionalidad (mes, trimestre, campaña).
- [x] Vista de brecha de potencial: `consumo_anual_real` vs `Potencial_H` por cliente/categoria.

## Fase 3 - Prediccion de proxima compra

- [x] Definir `intervalo_base` por cliente/producto (mediana ultimos N intervalos).
- [x] Calcular `fecha_esperada_compra` y `dias_retraso`.
- [x] Incorporar score de confianza (cantidad de historial + estabilidad de intervalos).
- [x] Excluir o tratar aparte productos tecnicos.

## Fase 4 - Motor de alertas operativas

- [x] Diseñar tabla operativa de alertas (`fecha_alerta`, `cliente`, `producto`, `prioridad`, `motivo`, recomendacion).
- [x] Regla de prioridad por retraso y caida de frecuencia:
  - [x] ALTA: retraso severo o fuerte caida de tendencia.
  - [x] MEDIA: desviacion moderada.
  - [x] BAJA: alerta temprana / seguimiento.
- [x] Regla de prioridad por brecha de potencial:
  - [x] elevar prioridad cuando `consumo_real / Potencial_H` cae por debajo de umbral.
- [x] Generar job/script para poblar alertas periodicamente.
- [x] Evitar duplicados de alerta activa por mismo cliente/producto.

## Fase 5 - Validacion y tuning

- [x] Backtesting historico de alertas (precision, recall, antelacion).
- [x] Analizar falsos positivos por estacionalidad/campañas.
- [x] Ajustar umbrales de prioridad con feedback de operadores.
- [x] Crear set de casos de prueba representativos.

## Fase 6 - Operacion continua

- [x] Ejecutar pipeline semanal: carga -> features -> prediccion -> alertas.
- [x] Exportar alertas para operacion (CSV/dashboard).
- [x] Registrar resultado de llamadas (contactado, recuperado, perdido, pendiente).
- [x] Medir impacto comercial de alertas (clientes recuperados, ventas reactivadas).

## Fase 7 - Evolucion a modelo ML (despues del MVP)

- [x] Definir target (probabilidad de compra en N dias o riesgo de fuga).
- [x] Construir dataset de entrenamiento con outcomes de alertas.
- [x] Entrenar baseline (regresion/logit o gradient boosting).
- [x] Comparar contra reglas SQL y desplegar solo si mejora claramente.

## Entregables inmediatos (siguiente sprint)

- [x] `db/analysis.sql` con vistas de:
  - [x] serie mensual cliente-producto;
  - [x] intervalos de compra;
  - [x] proxima compra esperada;
  - [x] riesgo/caida de frecuencia.
- [x] `db/run_alerts.py` para generar `alerta_cliente_producto`.
- [x] Actualizar `db/README.md` con flujo operativo paso a paso.

## Implementado (MVP actual)

- [x] Deteccion de clientes con descenso de frecuencia y retraso de compra (alertas ROJO/AMARILLO/VERDE).
- [x] Segmentacion cliente real vs no cliente:
  - `CLIENTE` = con compras historicas.
  - `NO_CLIENTE` = sin compras historicas.
- [x] Cola operativa de clientes pendientes para atencion priorizada.
- [x] Frontend conectado a backend y base de datos real (sin mock para cartera principal).
- [x] Vista de productos con filtros por cliente/producto y grafico historico.
- [x] Analisis predictivo inicial por cliente-producto:
  - limites bajo/alto (baseline) por serie historica;
  - fecha esperada de compra y dias de retraso;
  - recomendacion de preocupacion (`should_worry`).

## Siguiente iteracion

- [x] Ajustar umbrales de semaforo para reducir falsos ROJOS.
- [x] Incorporar estacionalidad explicita por mes/campana en el calculo predictivo.
- [x] Añadir formulario frontend para registrar outcomes (`contactado/recuperado/perdido`).


- **Notificaciones de clientes que reducen la frecuencia**: detectar cuando un cliente 
deja de seguir su regularidad habitual de compra.
- **Filtrado de productos por grupos**:
  - Desinfección y anestesia → "productos usados diariamente".
  - Técnicos → "producto no común" (no recurrente).
- **Perfilado de usuarios** según la demanda que mantienen a lo largo del tiempo (teniendo 
en cuenta el riesgo de interpretación).
- **Forma de distribución**: diferenciar entre clientes "reales" y "promiscuos" 
(mayoristas / intermediarios "prom" que no compran de forma directa).
- **Identificar el momento óptimo de compra** de cada cliente.
- **Productos técnicos**: nunca se consideran promiscuos, ya que no son productos 
recurrentes (dependen de la intervención).
- **Temporada del año**: tener en cuenta la estacionalidad en el análisis.
- **Alerta útil**: debe permitir identificar `id_cliente`, `id_producto`, `prioridad`, etc.
