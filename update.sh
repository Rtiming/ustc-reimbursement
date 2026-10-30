#!/bin/sh
set -eu
REIMBURSE_UPDATE_ROOT=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd -P)
sh "$REIMBURSE_UPDATE_ROOT/tools/bootstrap_boundary.sh" "$REIMBURSE_UPDATE_ROOT"
REIMBURSE_UPDATE_PYTHON="$REIMBURSE_UPDATE_ROOT/.local/mcp-venv/bin/python"
if [ ! -x "$REIMBURSE_UPDATE_PYTHON" ]; then
    printf '%s\n' '请先运行sh install.sh完成本机安装。' >&2
    exit 2
fi
exec "$REIMBURSE_UPDATE_PYTHON" -B -X utf8 -I "$REIMBURSE_UPDATE_ROOT/tools/release_update.py" "$@"
