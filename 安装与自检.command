#!/bin/sh
REIMBURSE_INSTALL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$REIMBURSE_INSTALL_ROOT/install.sh" "$@"
REIMBURSE_INSTALL_STATUS=$?
if [ -t 0 ]; then printf '\n%s\n' '按回车关闭窗口。'; read -r REIMBURSE_CLOSE_REPLY; fi
exit "$REIMBURSE_INSTALL_STATUS"
