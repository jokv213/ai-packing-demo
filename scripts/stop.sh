#!/usr/bin/env bash
set -euo pipefail

PIDS="$(pgrep -f 'uvicorn app.main:app' || true)"
if [ -z "$PIDS" ]; then
  echo "[INFO] 停止対象の uvicorn プロセスはありません"
  exit 0
fi

echo "[INFO] Stopping uvicorn: ${PIDS}"
kill $PIDS || true
sleep 1

REMAINING="$(pgrep -f 'uvicorn app.main:app' || true)"
if [ -n "$REMAINING" ]; then
  echo "[WARN] 強制停止します: ${REMAINING}"
  kill -9 $REMAINING || true
fi

echo "[OK] 停止しました"
