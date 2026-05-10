export function tendenciaUsuario(serie) {
  if (!serie || serie.length < 2) return "Sin datos";
  const inicio = serie[0];
  const fin = serie[serie.length - 1];
  if (fin < inicio - 5) return "Demanda a la baja";
  if (fin > inicio + 5) return "Demanda al alza";
  return "Demanda estable";
}
