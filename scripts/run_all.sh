#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_all.sh — Lanza el panel INIBSA (backend + frontend) con un solo comando.
#
# Uso (desde la raíz del repo INTERHACK):
#   bash scripts/run_all.sh
#
# Variables opcionales:
#   API_PORT=8000         puerto del backend (FastAPI/Uvicorn)
#   FRONT_PORT=5173       puerto del frontend (Vite)
#   SKIP_INSTALL=1        no reinstala dependencias Python/npm
#   SKIP_DB_CHECK=1       no comprueba ni regenera la base SQLite
#   NO_OPEN=1             no abre el navegador automáticamente al final
#
# Detiene ambos procesos con Ctrl+C; el trap se encarga de la limpieza.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_PORT="${API_PORT:-8000}"
FRONT_PORT="${FRONT_PORT:-5173}"

LOG_DIR="$ROOT_DIR/.run_all_logs"
mkdir -p "$LOG_DIR"
API_LOG="$LOG_DIR/api.log"
FRONT_LOG="$LOG_DIR/frontend.log"

API_PID=""
FRONT_PID=""

color() { printf "\033[%sm%s\033[0m" "$1" "$2"; }
info()  { echo "$(color "1;36" "[run_all]") $*"; }
warn()  { echo "$(color "1;33" "[run_all]") $*" >&2; }
err()   { echo "$(color "1;31" "[run_all]") $*" >&2; }
ok()    { echo "$(color "1;32" "[run_all]") $*"; }

cleanup() {
  echo ""
  info "Deteniendo procesos…"
  if [ -n "$API_PID" ] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [ -n "$FRONT_PID" ] && kill -0 "$FRONT_PID" 2>/dev/null; then
    kill "$FRONT_PID" 2>/dev/null || true
  fi
  # Segunda pasada por si algo se queda colgado en los puertos.
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill 2>/dev/null || true
    lsof -tiTCP:"$FRONT_PORT" -sTCP:LISTEN 2>/dev/null | xargs -r kill 2>/dev/null || true
  fi
  ok "Adiós."
}
trap cleanup EXIT INT TERM

# ── 0. Comprobación de prerequisitos ───────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { err "Falta python3. Instala Python 3.10+."; exit 1; }
command -v node    >/dev/null 2>&1 || { err "Falta node. Instala Node.js 18+."; exit 1; }
command -v npm     >/dev/null 2>&1 || { err "Falta npm.";   exit 1; }

# ── 1. Liberar puertos por si algo quedó colgado ───────────────────────────
free_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$pids" ]; then
      warn "Puerto $port ocupado, terminando procesos previos…"
      echo "$pids" | xargs kill -9 2>/dev/null || true
      sleep 0.4
    fi
  fi
}
free_port "$API_PORT"
free_port "$FRONT_PORT"

# ── 2. venv de Python + dependencias ────────────────────────────────────────
PY="python3"
if [ -d "$ROOT_DIR/venv" ]; then
  PY="$ROOT_DIR/venv/bin/python"
elif [ -d "$ROOT_DIR/.venv" ]; then
  PY="$ROOT_DIR/.venv/bin/python"
else
  info "Creando entorno virtual en venv/ (primera vez)…"
  python3 -m venv "$ROOT_DIR/venv"
  PY="$ROOT_DIR/venv/bin/python"
fi

if [ "${SKIP_INSTALL:-0}" != "1" ]; then
  info "Instalando dependencias del backend…"
  "$PY" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$PY" -m pip install --quiet --disable-pip-version-check -r "$ROOT_DIR/backend/requirements.txt"
fi

# ── 3. Base SQLite ─────────────────────────────────────────────────────────
DB_FILE="$ROOT_DIR/db/interhack.db"
if [ "${SKIP_DB_CHECK:-0}" != "1" ]; then
  if [ ! -f "$DB_FILE" ]; then
    info "No existe $DB_FILE — generando base SQLite por primera vez…"
    "$PY" "$ROOT_DIR/db/create_database.py"
    if [ -f "$ROOT_DIR/db/run_alerts.py" ]; then
      "$PY" "$ROOT_DIR/db/run_alerts.py" || warn "run_alerts.py falló, continúo igual."
    fi
  else
    ok "Base SQLite encontrada ($DB_FILE)."
  fi
fi

# ── 4. Frontend deps ───────────────────────────────────────────────────────
if [ "${SKIP_INSTALL:-0}" != "1" ]; then
  if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    info "Instalando dependencias del frontend (npm install)…"
    (cd "$ROOT_DIR/frontend" && npm install --silent)
  fi
fi

# ── 5. Arrancar backend en background ─────────────────────────────────────
info "Arrancando API en http://127.0.0.1:$API_PORT (logs → $API_LOG)…"
( "$PY" -m uvicorn backend.app:app --host 127.0.0.1 --port "$API_PORT" >"$API_LOG" 2>&1 ) &
API_PID=$!

# Espera a que /health responda (máx ~25s).
ready=""
for i in $(seq 1 50); do
  if curl -fsS --max-time 1 "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
    ready="yes"
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    err "El backend murió al arrancar. Revisa $API_LOG"
    tail -n 30 "$API_LOG" >&2 || true
    exit 1
  fi
  sleep 0.5
done

if [ -z "$ready" ]; then
  err "El backend no respondió en 25s. Revisa $API_LOG"
  exit 1
fi
ok "API arriba (/health OK)."

# ── 6. Arrancar frontend en background ────────────────────────────────────
info "Arrancando frontend (Vite) en http://127.0.0.1:$FRONT_PORT (logs → $FRONT_LOG)…"
(
  cd "$ROOT_DIR/frontend"
  # `--host 127.0.0.1` evita exponerlo en la LAN; quítalo si quieres acceso desde móvil.
  npm run dev -- --host 127.0.0.1 --port "$FRONT_PORT" >"$FRONT_LOG" 2>&1
) &
FRONT_PID=$!

# Espera al banner "ready in" o hasta que el puerto responda.
for i in $(seq 1 60); do
  if curl -fsS --max-time 1 "http://127.0.0.1:$FRONT_PORT" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$FRONT_PID" 2>/dev/null; then
    err "El frontend murió al arrancar. Revisa $FRONT_LOG"
    tail -n 30 "$FRONT_LOG" >&2 || true
    exit 1
  fi
  sleep 0.5
done

URL="http://127.0.0.1:$FRONT_PORT"
ok "Frontend listo."

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  Panel INIBSA en marcha"
echo "  ────────────────────────────────────────────────────────────────"
echo "  Frontend → $URL"
echo "  Backend  → http://127.0.0.1:$API_PORT  (docs en /docs)"
echo "  Logs     → $LOG_DIR/{api,frontend}.log"
echo ""
echo "  Pulsa Ctrl+C para detener ambos servicios."
echo "════════════════════════════════════════════════════════════════════"
echo ""

if [ "${NO_OPEN:-0}" != "1" ]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi

# Espera mientras los procesos estén vivos. Si cae alguno, paramos los dos.
while true; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    err "El backend ha muerto (revisa $API_LOG). Cerrando frontend."
    exit 1
  fi
  if ! kill -0 "$FRONT_PID" 2>/dev/null; then
    err "El frontend ha muerto (revisa $FRONT_LOG). Cerrando backend."
    exit 1
  fi
  sleep 2
done
