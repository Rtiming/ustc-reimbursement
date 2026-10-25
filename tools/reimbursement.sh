#!/bin/sh
set -eu
RTIME_REIMBURSE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
if [ -x "$RTIME_REIMBURSE_ROOT/.local/mcp-venv/bin/python" ]; then
    RTIME_REIMBURSE_PYTHON="$RTIME_REIMBURSE_ROOT/.local/mcp-venv/bin/python"
elif [ -x /opt/homebrew/bin/python3 ]; then
    RTIME_REIMBURSE_PYTHON=/opt/homebrew/bin/python3
elif [ -x /usr/local/bin/python3 ]; then
    RTIME_REIMBURSE_PYTHON=/usr/local/bin/python3
elif [ "$(uname -s)" = Darwin ]; then
    printf '%s\n' '未找到本项目环境或已安装的Homebrew Python；请先按设备指南配置Python。' >&2
    exit 2
else
    RTIME_REIMBURSE_PYTHON=$(command -v python3)
fi
exec "$RTIME_REIMBURSE_PYTHON" "$RTIME_REIMBURSE_ROOT/assistant.py" "$@"
