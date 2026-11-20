#!/usr/bin/env bash
# 一鍵啟動器：本機、遠端、容器、測試四種模式。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-local}"

case "$MODE" in
  local)
    exec python3 app.py
    ;;
  remote)
    export GRANTFLOW_HOST="${GRANTFLOW_HOST:-0.0.0.0}"
    export GRANTFLOW_ALLOWED_HOSTS="${GRANTFLOW_ALLOWED_HOSTS:-localhost,127.0.0.1}"
    echo "遠端模式：請確認 GRANTFLOW_ALLOWED_HOSTS 為實際域名，並在前端設定 HTTPS 反向代理。"
    exec python3 app.py
    ;;
  docker)
    exec docker compose up --build -d
    ;;
  test)
    exec python3 -m pytest tests -q
    ;;
  *)
    echo "用法：$0 [local|remote|docker|test]" >&2
    exit 2
    ;;
esac
