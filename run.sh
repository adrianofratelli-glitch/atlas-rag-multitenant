#!/usr/bin/env bash
#
# Start/stop the POC locally. Each server runs detached (nohup) so the app
# keeps running after you close the terminal. Re-running start is safe: it
# only launches whichever server is not already up (heals a partial state).
#   - Backend  : FastAPI    at http://localhost:8180
#   - Frontend : React+Vite at http://localhost:5180  (proxies /api -> :8180)
#
# Usage:
#   ./run.sh          start (or heal) both
#   ./run.sh stop     stop both
#   ./run.sh status   show status
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BACKEND_PORT=8180
FRONTEND_PORT=5180
LOG_DIR="$ROOT/.logs"

pid_on_port() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }
cwd_of_pid()  { lsof -p "$1" -a -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'; }
mine()        { case "$(cwd_of_pid "$1")" in "$ROOT"*) return 0 ;; *) return 1 ;; esac; }

# O uvicorn leva alguns segundos importando langchain/voyage antes de servir.
# Sem esta espera o launcher abre o browser cedo demais e a UI cai na casca offline.
wait_for_backend() {
  for _ in $(seq 1 60); do
    if curl -sf -m 2 "http://127.0.0.1:$BACKEND_PORT/api/health" > /dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "backend  did not answer /api/health in 60s — check $LOG_DIR/backend.log"
  return 1
}

status() {
  for pair in "backend $BACKEND_PORT" "frontend $FRONTEND_PORT"; do
    # shellcheck disable=SC2086
    set -- $pair
    pid="$(pid_on_port "$2" || true)"
    if [ -n "$pid" ]; then echo "$1 (:$2): running (pid $pid)"; else echo "$1 (:$2): stopped"; fi
  done
}

stop() {
  for port in "$FRONTEND_PORT" "$BACKEND_PORT"; do
    pid="$(pid_on_port "$port" || true)"
    [ -z "$pid" ] && continue
    if mine "$pid"; then kill "$pid" 2>/dev/null && echo "stopped :$port (pid $pid)"
    else echo "port $port held by another project — left running"; fi
  done
}

start() {
  mkdir -p "$LOG_DIR"
  [ -d frontend/node_modules ] || { echo "Installing frontend dependencies (first run)..."; (cd frontend && npm install); }
  if [ "${POV_DEV:-0}" != "1" ] && {
    [ ! -f frontend/dist/index.html ] ||
    [ -n "$(find frontend/src -type f -newer frontend/dist/index.html -print -quit)" ] ||
    [ frontend/package-lock.json -nt frontend/dist/index.html ] ||
    [ frontend/vite.config.js -nt frontend/dist/index.html ];
  }; then
    echo "Building optimized frontend..."
    (cd frontend && npm run build)
  fi

  # Backend
  pid="$(pid_on_port "$BACKEND_PORT" || true)"
  if [ -z "$pid" ]; then
    nohup .venv/bin/uvicorn backend.api:app --port "$BACKEND_PORT" > "$LOG_DIR/backend.log" 2>&1 < /dev/null &
    echo "backend  started  -> http://localhost:$BACKEND_PORT"
  elif mine "$pid"; then echo "backend  already up (pid $pid)"
  else echo "Port $BACKEND_PORT is used by another project — aborting."; exit 1; fi

  # Preview estático por padrão, sem watcher/HMR. POV_DEV=1 mantém o fluxo de edição.
  pid="$(pid_on_port "$FRONTEND_PORT" || true)"
  if [ -z "$pid" ]; then
    if [ "${POV_DEV:-0}" = "1" ]; then
      FRONTEND_CMD=(node_modules/.bin/vite --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort)
    else
      FRONTEND_CMD=(node_modules/.bin/vite preview --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort)
    fi
    ( cd frontend && nohup "${FRONTEND_CMD[@]}" > "$LOG_DIR/frontend.log" 2>&1 < /dev/null & )
    echo "frontend started  -> http://localhost:$FRONTEND_PORT"
  elif mine "$pid"; then echo "frontend already up (pid $pid)"
  else echo "Port $FRONTEND_PORT is used by another project — aborting."; exit 1; fi

  echo "waiting for backend to answer /api/health..."
  wait_for_backend || exit 1

  echo ""
  echo "Open http://localhost:$FRONTEND_PORT   (logs in .logs/ · stop with ./run.sh stop)"
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *) echo "Usage: ./run.sh [start|stop|status]"; exit 1 ;;
esac
