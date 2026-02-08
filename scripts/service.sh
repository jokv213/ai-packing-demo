#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LABEL="${LABEL:-com.ai_packing_demo.local}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
UID_CURRENT="${UID:-$(id -u)}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
RUN_DIR="${ROOT_DIR}/.run"
OUT_LOG="${RUN_DIR}/launchd.out.log"
ERR_LOG="${RUN_DIR}/launchd.err.log"

pick_python() {
  for p in python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1; then
      echo "$p"
      return
    fi
  done
  echo ""
}

ensure_runtime() {
  local py
  py="$(pick_python)"
  if [ -z "$py" ]; then
    echo "[ERROR] Python 3 が見つかりません"
    exit 1
  fi

  mkdir -p "$RUN_DIR"

  if [ ! -d ".venv" ]; then
    echo "[INFO] .venv を作成します (${py})"
    "$py" -m venv .venv
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate

  if ! python -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
    echo "[WARN] .venv が古いため再作成します"
    rm -rf .venv
    "$py" -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi

  if ! python -c "import fastapi,uvicorn,sqlalchemy,jinja2,python_multipart" >/dev/null 2>&1; then
    echo "[INFO] 依存パッケージをインストールします"
    python -m pip install -r requirements.txt
  fi
}

write_plist() {
  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
      <string>${ROOT_DIR}/.venv/bin/python</string>
      <string>-m</string>
      <string>uvicorn</string>
      <string>app.main:app</string>
      <string>--host</string>
      <string>${HOST}</string>
      <string>--port</string>
      <string>${PORT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${ROOT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${OUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${ERR_LOG}</string>

    <key>EnvironmentVariables</key>
    <dict>
      <key>PYTHONUNBUFFERED</key>
      <string>1</string>
    </dict>
  </dict>
</plist>
EOF
}

check_port_conflict() {
  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    local pid cmd
    pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN | head -n 1)"
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if echo "$cmd" | grep -q "uvicorn app.main:app"; then
      return 0
    fi
    echo "[ERROR] PORT=${PORT} は他プロセスが使用中です"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
    exit 1
  fi
}

start_service() {
  ensure_runtime
  check_port_conflict
  write_plist

  launchctl bootout "gui/${UID_CURRENT}/${LABEL}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${UID_CURRENT}" "$PLIST_PATH"
  launchctl enable "gui/${UID_CURRENT}/${LABEL}" || true
  launchctl kickstart -k "gui/${UID_CURRENT}/${LABEL}"

  for _ in $(seq 1 30); do
    if curl -fsS "http://${HOST}:${PORT}/" >/dev/null 2>&1; then
      echo "[OK] Started: http://${HOST}:${PORT}"
      echo "http://${HOST}:${PORT}" > "${RUN_DIR}/last_url"
      echo "${PORT}" > "${RUN_DIR}/last_port"
      return
    fi
    sleep 1
  done

  echo "[ERROR] 起動確認に失敗しました"
  echo "[HINT] logs を確認: ${OUT_LOG} / ${ERR_LOG}"
  exit 1
}

stop_service() {
  launchctl bootout "gui/${UID_CURRENT}/${LABEL}" >/dev/null 2>&1 || true
  echo "[OK] Stopped: ${LABEL}"
}

status_service() {
  echo "[INFO] Label: ${LABEL}"
  launchctl print "gui/${UID_CURRENT}/${LABEL}" | sed -n '1,80p' || true
  if curl -fsS "http://${HOST}:${PORT}/" >/dev/null 2>&1; then
    echo "[OK] HTTP: http://${HOST}:${PORT}/"
    if curl -fsS "http://${HOST}:${PORT}/orders" | grep -q "CSVインポート（受注）"; then
      echo "[OK] Orders画面にCSVインポートUIあり"
    else
      echo "[WARN] Orders画面UIが期待と異なる可能性があります"
    fi
  else
    echo "[WARN] HTTPに接続できません: http://${HOST}:${PORT}/"
  fi
}

logs_service() {
  echo "[INFO] stdout: ${OUT_LOG}"
  echo "[INFO] stderr: ${ERR_LOG}"
  tail -n 120 "$OUT_LOG" "$ERR_LOG" 2>/dev/null || true
}

usage() {
  cat <<EOF
Usage: ./scripts/service.sh {start|stop|restart|status|logs}

Examples:
  ./scripts/service.sh start
  ./scripts/service.sh status
  ./scripts/service.sh logs
  ./scripts/service.sh stop
EOF
}

cmd="${1:-start}"
case "$cmd" in
  start)
    start_service
    ;;
  stop)
    stop_service
    ;;
  restart)
    stop_service
    start_service
    ;;
  status)
    status_service
    ;;
  logs)
    logs_service
    ;;
  *)
    usage
    exit 1
    ;;
esac
