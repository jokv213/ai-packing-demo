#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run"
LAST_URL_FILE="${RUN_DIR}/last_url"
LAST_PORT_FILE="${RUN_DIR}/last_port"

ok() { echo "[OK] $1"; }
warn() { echo "[WARN] $1"; }
info() { echo "[INFO] $1"; }

if [ ! -f "$LAST_URL_FILE" ] || [ ! -f "$LAST_PORT_FILE" ]; then
  warn "起動情報がありません。稼働中ポートを自動検出します。"
  DETECTED=""
  for candidate in 8000 8001 8002 8003 8004 8005; do
    if curl -fsS "http://127.0.0.1:${candidate}/" >/dev/null 2>&1; then
      DETECTED="$candidate"
      break
    fi
  done

  if [ -z "$DETECTED" ]; then
    warn "稼働中のローカルサーバーを検出できませんでした"
    warn "先に ./scripts/dev.sh を実行してください"
    exit 1
  fi

  URL="http://127.0.0.1:${DETECTED}"
  PORT="$DETECTED"
  info "自動検出URL: ${URL}"
else
  URL="$(cat "$LAST_URL_FILE")"
  PORT="$(cat "$LAST_PORT_FILE")"
fi

if command -v lsof >/dev/null 2>&1; then
  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ok "PORT=${PORT} は LISTEN 中です"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  else
    warn "PORT=${PORT} は LISTEN していません"
  fi
else
  warn "lsof が無いため LISTEN 状態チェックをスキップしました"
fi

if curl -fsS "${URL}/" >/dev/null 2>&1; then
  ok "${URL}/ へ接続できます"
else
  warn "${URL}/ へ接続できません"
fi

if curl -fsS "${URL}/orders" | grep -q "CSVインポート（受注）"; then
  ok "Orders画面にCSVインポートUIがあります"
else
  warn "Orders画面にCSVインポートUIが見つかりません"
fi

info "Open: ${URL}"
