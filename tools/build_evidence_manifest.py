#!/usr/bin/env python3
"""对显式证据目录生成SHA清单；不改源件，不读取浏览器会话或数据库。"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# These are storage names, not material formats; list exclusions without opening them.
PRIVATE_DIRS = {'chrome', 'chromium', 'google chrome', 'microsoft edge', 'firefox', 'safari',
                'user data', 'sessions', 'session storage', 'local storage', 'indexeddb',
                'db_storage', 'databases', 'login'}
PRIVATE_NAMES = {'cookies', 'history', 'login data', 'web data', 'local state', 'preferences',
                 'current session', 'current tabs', 'last session', 'last tabs'}
PRIVATE_SUFFIXES = ('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.sqlite-wal', '.sqlite-shm')
SKIP_DIRS = {'.git', '.venv', '__pycache__', 'node_modules'}


def manifest(root):
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('证据root须为目录')
    if any(part.casefold() in PRIVATE_DIRS for part in root.parts):
        raise ValueError('证据root不能指向浏览器或账户私有存储')
    records, skipped = [], []
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in sorted(dirs + names):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('证据目录包含软链接或越界路径，请单独核对：' + relative)
            lower = name.casefold()
            if lower in PRIVATE_DIRS | PRIVATE_NAMES | SKIP_DIRS or lower.endswith(PRIVATE_SUFFIXES):
                skipped.append({'path': relative, 'reason': 'private_or_runtime_storage_not_read'})
                if name in dirs:
                    dirs.remove(name)
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                skipped.append({'path': relative, 'reason': 'non_regular_file_not_read'})
                continue
            sha = hashlib.sha256()
            with path.open('rb') as source:
                before = os.fstat(source.fileno())
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    sha.update(block)
                after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError('读取时来源发生改变，须重建清单：' + relative)
            records.append({'path': relative, 'sha256': sha.hexdigest(), 'size': after.st_size})
    return {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'source_root': str(root),
            'note': '只对显式来源生成清单；含业务信息，不能因生成SHA而公开分发。',
            'files': sorted(records, key=lambda row: row['path']), 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='明确的证据目录；不默认扫描仓库或案件')
    parser.add_argument('--output', required=True, type=Path, help='新建JSON清单，不覆盖既有文件')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出已存在，请使用新清单路径')
    report = manifest(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'files': len(report['files']), 'skipped': len(report['skipped']),
                      'output': str(args.output.resolve())}, ensure_ascii=False))


if __name__ == '__main__':
    main()
