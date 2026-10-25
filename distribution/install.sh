#!/bin/sh
set -eu
REIMBURSE_INSTALL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$REIMBURSE_INSTALL_ROOT/tools/bootstrap_install.sh" "$@"
