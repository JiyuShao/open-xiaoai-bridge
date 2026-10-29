#!/bin/bash
# --check 只检查本地文件；不启动服务或操作音箱。
set -euo pipefail
cd "$(dirname "$0")/.."
WAKE_MODE="$(printf '%s' "${WAKE_MODE:-native}" | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
export WAKE_MODE
if [[ "$WAKE_MODE" != "native" ]]; then
    echo "native-wake.sh 仅支持 WAKE_MODE=native" >&2; exit 2
fi
export CONFIG_PATH="${CONFIG_PATH:-config.native-wake.py}"
export XIAOZHI_ENABLE="${XIAOZHI_ENABLE:-0}"
export OPENCLAW_ENABLE="${OPENCLAW_ENABLE:-0}"
export OPENCLAW_ENABLED="${OPENCLAW_ENABLED:-0}"
export OPENAI_ENABLE="${OPENAI_ENABLE:-0}"
export QWENPAW_ENABLE="${QWENPAW_ENABLE:-0}"
export API_SERVER_ENABLE="${API_SERVER_ENABLE:-0}"
export AUDIO_INPUT_ENABLE="${AUDIO_INPUT_ENABLE:-1}"
case "${1:-}" in
    --check) exec bash scripts/start.sh "$@" ;;
    "") exec bash scripts/start.sh ;;
    *) echo "用法: bash scripts/native-wake.sh [--check]" >&2; exit 2 ;;
esac
