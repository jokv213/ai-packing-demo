#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ok() { echo "[OK] $1"; }
warn() { echo "[WARN] $1"; }
fail() { echo "[FAIL] $1"; }
info() { echo "[INFO] $1"; }

pick_python() {
  for p in python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1; then
      echo "$p"
      return
    fi
  done
  echo ""
}

pick_free_port() {
  for candidate in 8000 8001 8002 8003 8004 8005; do
    if ! lsof -tiTCP:"$candidate" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "$candidate"
      return
    fi
  done
  echo ""
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  fail "Python 3 が見つかりません"
  exit 1
fi
ok "Python: $($PY --version 2>&1)"

if [ ! -d .venv ]; then
  warn ".venv がないため作成します"
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if python -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
  ok ".venv Python は 3.11+"
else
  fail ".venv Python が古いです。rm -rf .venv && $PY -m venv .venv を実行してください"
fi

if python -c "import fastapi,uvicorn,sqlalchemy,jinja2,python_multipart" >/dev/null 2>&1; then
  ok "必要パッケージはインストール済み"
else
  fail "必要パッケージ不足。pip install -r requirements.txt を実行してください"
fi

if ! command -v lsof >/dev/null 2>&1; then
  warn "lsof が見つからないためポート確認をスキップ"
  info "起動コマンド: ./scripts/dev.sh"
  exit 0
fi

APP_ON_8000=0
if lsof -tiTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  PID_8000="$(lsof -tiTCP:8000 -sTCP:LISTEN | head -n 1)"
  CMD_8000="$(ps -p "$PID_8000" -o command= 2>/dev/null || true)"
  if echo "$CMD_8000" | grep -q "uvicorn app.main:app"; then
    ok "PORT=8000 はこのアプリが使用中です"
    APP_ON_8000=1
  else
    warn "PORT=8000 は他プロセスが使用中です"
  fi
  lsof -nP -iTCP:8000 -sTCP:LISTEN || true
  if lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | grep -q "com.docke"; then
    warn "Docker が 8000 を占有している可能性が高いです"
  fi
else
  ok "PORT=8000 は空いています"
fi

if [ "$APP_ON_8000" = "1" ]; then
  FREE_PORT="8000"
else
  FREE_PORT="$(pick_free_port)"
fi
if [ -n "$FREE_PORT" ]; then
  ok "利用可能ポート: $FREE_PORT"
  info "推奨URL: http://127.0.0.1:${FREE_PORT}"
else
  fail "8000-8005 の空きポートがありません"
fi

RUNNING_PORTS="$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -E 'uvicorn|Python' | awk '{print $9}' | sed -n 's/.*:\([0-9][0-9]*\)$/\1/p' | sort -u)"
if [ -n "${RUNNING_PORTS}" ]; then
  info "現在待受中のPython系ポート: ${RUNNING_PORTS}"
fi

if curl -fsS "http://127.0.0.1:8000/orders" >/dev/null 2>&1; then
  if curl -fsS "http://127.0.0.1:8000/orders" | grep -q "CSVインポート（受注）"; then
    ok "http://127.0.0.1:8000/orders は最新UI（CSVインポート表示）です"
  else
    warn "http://127.0.0.1:8000/orders は応答するが、期待UIと異なる可能性があります"
  fi
fi

info "起動コマンド: ./scripts/dev.sh"
info "表示URLは、起動ログの 'Starting app at ...' のURLを必ず使用してください"
