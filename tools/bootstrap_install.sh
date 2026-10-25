#!/bin/sh
# 仅使用系统下载/解包工具；运行时和缓存全部落在本工具目录。
set -eu
REIMBURSE_INSTALL_ROOT=$(CDPATH= cd -P -- "$(dirname -- "$0")/.." && pwd -P)
cd "$REIMBURSE_INSTALL_ROOT"
sh tools/bootstrap_boundary.sh "$REIMBURSE_INSTALL_ROOT"
if [ "${1:-}" = "--check" ]; then
    if [ ! -x .local/mcp-venv/bin/python ]; then
        printf '%s\n' '尚未安装；先运行 sh install.sh。自检不会联网安装。' >&2
        exit 2
    fi
    exec .local/mcp-venv/bin/python -X utf8 -I tools/portable_install.py "$@"
fi
case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) REIMBURSE_INSTALL_PLATFORM=darwin-arm64 ;;
    Darwin-x86_64) REIMBURSE_INSTALL_PLATFORM=darwin-x86_64 ;;
    Linux-x86_64) REIMBURSE_INSTALL_PLATFORM=linux-x86_64 ;;
    Linux-aarch64) REIMBURSE_INSTALL_PLATFORM=linux-aarch64 ;;
    *) printf '%s\n' '此系统尚无冻结运行时，请查看用户指南。' >&2; exit 2 ;;
esac
for REIMBURSE_REQUIRED in curl unzip awk; do
    command -v "$REIMBURSE_REQUIRED" >/dev/null 2>&1 || { printf '%s\n' "缺少系统工具：$REIMBURSE_REQUIRED" >&2; exit 2; }
done
REIMBURSE_BOOTSTRAP_ROW=$(awk -F '\t' -v key="$REIMBURSE_INSTALL_PLATFORM" '$1==key {print; found++} END {if(found!=1)exit 2}' distribution/bootstrap.tsv)
IFS="$(printf '\t')" read -r REIMBURSE_PLATFORM REIMBURSE_URL REIMBURSE_SHA REIMBURSE_MEMBER REIMBURSE_PYTHON_VERSION REIMBURSE_UV_VERSION <<EOF
$REIMBURSE_BOOTSTRAP_ROW
EOF
case "$REIMBURSE_URL" in https://files.pythonhosted.org/packages/*) ;; *) printf '%s\n' '启动器下载地址不属于已核定发布源。' >&2; exit 2 ;; esac
REIMBURSE_BOOTSTRAP_DIR="$REIMBURSE_INSTALL_ROOT/.local/bootstrap/$REIMBURSE_INSTALL_PLATFORM"
mkdir -p "$REIMBURSE_BOOTSTRAP_DIR"
REIMBURSE_UV_ARCHIVE="$REIMBURSE_BOOTSTRAP_DIR/uv-$REIMBURSE_UV_VERSION.whl"
reimburse_sha() {
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
    elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
    else printf '%s\n' '缺少SHA256校验工具。' >&2; return 2; fi
}
if [ ! -f "$REIMBURSE_UV_ARCHIVE" ] || [ "$(reimburse_sha "$REIMBURSE_UV_ARCHIVE")" != "$REIMBURSE_SHA" ]; then
    printf '%s\n' '下载冻结版本的安装工具，校验后才执行。'
    curl -q --fail --location --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 180 --retry 2 \
        "$REIMBURSE_URL" --output "$REIMBURSE_UV_ARCHIVE.part"
    [ "$(reimburse_sha "$REIMBURSE_UV_ARCHIVE.part")" = "$REIMBURSE_SHA" ] || { printf '%s\n' '下载SHA不符，已停止。' >&2; exit 2; }
    mv "$REIMBURSE_UV_ARCHIVE.part" "$REIMBURSE_UV_ARCHIVE"
fi
# 每次从已验原包提取唯一固定成员，不信任遗留的可执行副本。
unzip -p "$REIMBURSE_UV_ARCHIVE" "$REIMBURSE_MEMBER" > "$REIMBURSE_BOOTSTRAP_DIR/uv.new"
chmod 700 "$REIMBURSE_BOOTSTRAP_DIR/uv.new"
mv "$REIMBURSE_BOOTSTRAP_DIR/uv.new" "$REIMBURSE_BOOTSTRAP_DIR/uv"
reimburse_uv() {
    env -i PATH="${PATH:-/usr/bin:/bin}" LANG=C.UTF-8 \
        UV_CACHE_DIR="$REIMBURSE_INSTALL_ROOT/.local/cache/uv" \
        UV_PYTHON_INSTALL_DIR="$REIMBURSE_INSTALL_ROOT/.local/python" \
        UV_PYTHON_BIN_DIR="$REIMBURSE_INSTALL_ROOT/.local/python-bin" \
        UV_NO_CONFIG=1 "$REIMBURSE_BOOTSTRAP_DIR/uv" "$@"
}
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV UV_CONFIG_FILE UV_PYTHON
reimburse_uv python install "$REIMBURSE_PYTHON_VERSION" --no-bin --no-registry --no-config
REIMBURSE_MANAGED_PYTHON=$(reimburse_uv python find "$REIMBURSE_PYTHON_VERSION" --managed-python --no-project --no-config)
case "$REIMBURSE_MANAGED_PYTHON" in "$REIMBURSE_INSTALL_ROOT"/.local/python/*) ;; *) printf '%s\n' '运行时不属于本工具目录，已停止。' >&2; exit 2 ;; esac
exec "$REIMBURSE_MANAGED_PYTHON" -X utf8 -I tools/portable_install.py "$@"
