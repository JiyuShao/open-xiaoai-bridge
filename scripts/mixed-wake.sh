#!/bin/bash
# --check 只验证本地配置和资源，不启动服务或播放音频。
set -euo pipefail
cd "$(dirname "$0")/.."
WAKE_MODE="$(printf '%s' "${WAKE_MODE:-mixed}" | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
export WAKE_MODE
if [[ "$WAKE_MODE" != "mixed" ]]; then
    echo "mixed-wake.sh 仅支持 WAKE_MODE=mixed" >&2; exit 2
fi
export CONFIG_PATH="${CONFIG_PATH:-config.mixed-wake.py}"
export OPENAI_ENABLE="${OPENAI_ENABLE:-1}"
export AUDIO_INPUT_ENABLE="${AUDIO_INPUT_ENABLE:-1}"
case "${1:-}" in
    --check) exec bash scripts/start.sh "$@" ;;
    "") exec bash scripts/start.sh ;;
    *) echo "用法: bash scripts/mixed-wake.sh [--check]" >&2; exit 2 ;;
esac
