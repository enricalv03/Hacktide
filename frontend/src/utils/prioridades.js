export const PRIORIDADES = ["Alta", "Media", "Baja"];

const MAPA_PRIORIDAD = {
  Alta: 3,
  Media: 2,
  Baja: 1,
};

export function valorPrioridad(prioridad) {
  return MAPA_PRIORIDAD[prioridad] ?? 0;
}
