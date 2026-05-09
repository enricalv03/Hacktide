# ROADMAP

## Requisitos y notas

- **Notificaciones de clientes que reducen la frecuencia**: detectar cuando un cliente deja de seguir su regularidad habitual de compra.
- **Filtrado de productos por grupos**:
  - Desinfección y anestesia → "productos usados diariamente".
  - Técnicos → "producto no común" (no recurrente).
- **Perfilado de usuarios** según la demanda que mantienen a lo largo del tiempo (teniendo en cuenta el riesgo de interpretación).
- **Forma de distribución**: diferenciar entre clientes "reales" y "promiscuos" (mayoristas / intermediarios "prom" que no compran de forma directa).
- **Identificar el momento óptimo de compra** de cada cliente.
- **Productos técnicos**: nunca se consideran promiscuos, ya que no son productos recurrentes (dependen de la intervención).
- **Temporada del año**: tener en cuenta la estacionalidad en el análisis.
- **Alerta útil**: debe permitir identificar `id_cliente`, `id_producto`, `prioridad`, etc.
