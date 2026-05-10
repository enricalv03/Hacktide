#!/usr/bin/env bash
# Arranca el backend como antes: libera el puerto si hace falta y lanza uvicorn.
# Uso (desde la raíz del repo INTERHACK):
#   bash scripts/start_api.sh
# Otro puerto:
#   PORT=8001 bash scripts/start_api.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"

if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "${PIDS}" ]; then
    echo "[start_api] Puerto ${PORT} en uso; terminando proceso(s)..."
    echo "${PIDS}" | xargs kill -9 2>/dev/null || true
    sleep 0.5
  fi
else
  echo "[start_api] Aviso: sin 'lsof'; si sale 'Address already in use', libera el puerto a mano."
fi

PYTHON="python3"
if [ -x "${ROOT}/venv/bin/python" ]; then
  PYTHON="${ROOT}/venv/bin/python"
elif [ -x "${ROOT}/.venv/bin/python" ]; then
  PYTHON="${ROOT}/.venv/bin/python"
fi

echo "[start_api] API → http://127.0.0.1:${PORT}  (Ctrl+C para parar)"
echo "[start_api] DEJA ESTA VENTANA ABIERTA mientras uses el panel; si la cierras, el API muere."

# Aviso visible si uvicorn termina (caída inesperada o error de import).
trap 'echo "[start_api] El API se ha detenido (ya no hay backend escuchando en :${PORT})."' EXIT

exec "${PYTHON}" -m uvicorn backend.app:app --reload --host 127.0.0.1 --port "${PORT}"
