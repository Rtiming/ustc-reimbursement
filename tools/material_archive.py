#!/usr/bin/env python3
"""归档统一入口：复用冻结归档核心，Windows读取复用既有原生句柄与稳定性检查。"""

from __future__ import annotations

import hashlib
import types
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
VENDOR_SHA256 = '675f6c1a1734847b4c430b0cde4f03bde6f6e85f4efa3fc26906075715a29f3c'


def archive_core():
    path = ROOT / 'distribution/vendor/archive_file.py'
    raw = path.read_bytes()
    if path.is_symlink() or hashlib.sha256(raw).hexdigest() != VENDOR_SHA256:
        raise ValueError('归档核心与冻结版本不符，停止执行')
    module = types.ModuleType('reimbursement_archive_core')
    module.__file__ = str(path)
    # 不再二次读取源码或采用旁边的pyc；执行刚刚验SHA的同一字节。
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    if os.name == 'nt':
        module.stable_copy = windows_stable_copy
    return module


def windows_stable_copy(source, target):
    """同一原生句柄核对128位身份及写入时间，避免混用Python路径stat与fd stat。"""
    from windows_source_io import open_source

    source, target = Path(source), Path(target)
    with open_source(source, [source.parent]) as fd:
        with os.fdopen(os.dup(fd), 'rb') as src:
            with target.open('xb') as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            src.seek(0)
            original_sha = hashlib.file_digest(src, 'sha256').hexdigest()
        with target.open('rb') as copied:
            if hashlib.file_digest(copied, 'sha256').hexdigest() != original_sha:
                raise ValueError('归档字节与锁定原件不符')
        # open_source退出时继续核原生文件身份、时间及所有祖先路径，原件仍被保护。
    return original_sha


def archive(*args, **kwargs):
    return archive_core().archive(*args, **kwargs)


def verify_receipt(path):
    return archive_core().verify_receipt(path)


if __name__ == '__main__':
    raise SystemExit(archive_core().main())
