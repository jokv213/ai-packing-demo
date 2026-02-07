#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pick_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    echo "python3.12"
    return
  fi
  if command -v python3.11 >/dev/null 2>&1; then
    echo "python3.11"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  echo ""
}

PYTHON_BIN="$(pick_python)"
if [ -z "$PYTHON_BIN" ]; then
  echo "[ERROR] Python 3 is not installed."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[INFO] Creating virtualenv with ${PYTHON_BIN}"
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
  echo "[WARN] Existing .venv is not Python 3.11+ compatible. Recreating..."
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if ! python -c "import fastapi, uvicorn, sqlalchemy, jinja2" >/dev/null 2>&1; then
  echo "[INFO] Installing dependencies"
  python -m pip install -r requirements.txt
fi

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
RELOAD="${RELOAD:-0}"

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  if [ "${PORT:-}" = "8000" ]; then
    for candidate in 8001 8002 8003 8004 8005; do
      if ! lsof -tiTCP:"$candidate" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[WARN] Port 8000 is in use. Switching to ${candidate}."
        PORT="$candidate"
        break
      fi
    done
  fi

  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[ERROR] Port ${PORT} is already in use."
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
    echo "[HINT] Stop the process above, or start on another port:"
    echo "       PORT=8001 ./scripts/dev.sh"
    exit 1
  fi
fi

if [ "$RELOAD" = "1" ]; then
  # Polling mode avoids file-watch permission issues in some environments.
  export WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-1}"
  echo "[INFO] Starting app at http://${HOST}:${PORT} (reload=on)"
  exec uvicorn app.main:app --reload --host "$HOST" --port "$PORT"
fi

echo "[INFO] Starting app at http://${HOST}:${PORT} (reload=off)"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
