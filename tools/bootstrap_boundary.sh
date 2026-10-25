#!/bin/sh
# 无Python时先核状态目录；仅允许运行时自身指向.local内部的正常链接。
set -eu
root=$1
shift
check_link() {
    target=$1
    n=0
    while [ -L "$target" ]; do
        n=$((n + 1))
        [ "$n" -le 40 ] || { printf '%s\n' 'Runtime link cycle / 运行目录链接成环' >&2; return 2; }
        link=$(readlink "$target")
        case "$link" in /*) target=$link ;; *) target=$(dirname -- "$target")/$link ;; esac
    done
    [ -e "$target" ] || { printf '%s\n' 'Broken runtime link / 运行目录链接目标缺失' >&2; return 2; }
    if [ -d "$target" ]; then
        resolved=$(CDPATH= cd -P -- "$target" && pwd -P)
    else
        parent=$(CDPATH= cd -P -- "$(dirname -- "$target")" && pwd -P)
        resolved=$parent/$(basename -- "$target")
    fi
    case "$resolved" in "$root"/.local/*) ;; *) printf '%s\n' 'Runtime link escapes tool directory / 运行链接越出工具目录，保留原状' >&2; return 2 ;; esac
}
if [ "${1:-}" = '--links' ]; then
    shift
    for entry do check_link "$entry"; done
    exit 0
fi
[ ! -L "$root/.local" ] || { printf '%s\n' '.local cannot be a link / .local不能是链接' >&2; exit 2; }
if [ -e "$root/.local" ]; then
    [ -d "$root/.local" ] || { printf '%s\n' '.local must be a directory / .local须为目录' >&2; exit 2; }
    # -exec参数传路径，不把文件名拼入shell程序；不跟随目录链接。
    result=$(mktemp "$root/.bootstrap-boundary.XXXXXX")
    trap 'rm -f "$result"' EXIT HUP INT TERM
    if ! find "$root/.local" -type l -exec sh "$root/tools/bootstrap_boundary.sh" "$root" --links {} + >"$result" 2>&1; then
        cat "$result" >&2
        exit 2
    fi
fi
