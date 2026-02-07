#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ok() { echo "[OK] $1"; }
warn() { echo "[WARN] $1"; }
fail() { echo "[FAIL] $1"; }

PY=""
for p in python3.12 python3.11 python3; do
  if command -v "$p" >/dev/null 2>&1; then
    PY="$p"
    break
  fi
done

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

if python -c "import fastapi,uvicorn,sqlalchemy,jinja2" >/dev/null 2>&1; then
  ok "必要パッケージはインストール済み"
else
  fail "必要パッケージ不足。pip install -r requirements.txt を実行してください"
fi

PORT="${PORT:-8000}"
if command -v lsof >/dev/null 2>&1; then
  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    warn "PORT=$PORT は使用中です"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  else
    ok "PORT=$PORT は空いています"
  fi
else
  warn "lsof が見つからないためポート確認をスキップ"
fi

echo "[INFO] /packing 画面は実装済みです。"
echo "[INFO] 一覧画面: http://127.0.0.1:${PORT}/packing"
echo "[INFO] 注文詳細: http://127.0.0.1:${PORT}/packing/ORD-0001"
echo "[INFO] 起動コマンド: ./scripts/dev.sh"
