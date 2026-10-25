#!/usr/bin/env python3
"""只读定位macOS微信附件；按文件名列出全部候选，不读取聊天库或自动选取原件。"""
import argparse
import hashlib
import json
import os
import stat
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


SECURE_IO_SUPPORTED = (os.name == 'posix'
                       and all(hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK'))
                       and os.open in os.supports_dir_fd and os.scandir in os.supports_fd)
DIRECTORY_FLAGS = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if SECURE_IO_SUPPORTED else None
PRIVATE_DIRS = {'db_storage', 'database', 'databases', 'config', 'configs', 'login'}
PRIVATE_SUFFIXES = ('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3',
                    '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm')


class UnsupportedPlatformError(ValueError):
    code = 'unsupported_platform'


def _require_secure_io():
    if not SECURE_IO_SUPPORTED:
        raise UnsupportedPlatformError(
            'unsupported_platform: 微信附件定位需要POSIX防链接目录句柄；当前平台未提供安全读取适配，未检索文件')


def _absolute(path):
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _error(path, operation, exc):
    return {'path': str(path), 'operation': operation,
            'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None),
            'message': str(exc)}


def _open_directory(path):
    """逐层打开，防止根路径或中间目录借软链接越过声明的范围。"""
    _require_secure_io()
    path = _absolute(path)
    fd = os.open(path.anchor, DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_file(name):
    lower = name.casefold()
    return lower.endswith(PRIVATE_SUFFIXES) or lower in {
        'config.ini', 'config.json', 'login_info.dat', 'logininfo.dat'}


def discover_roots(home=None):
    """只枚举已知布局的有限层级，不递归扫描微信应用数据或读取账户配置。"""
    _require_secure_io()
    home = _absolute(home or Path.home())
    roots, errors, skipped = [], [], []

    def children(path):
        try:
            fd = _open_directory(path)
        except FileNotFoundError:
            return []
        except OSError as exc:
            errors.append(_error(path, 'discover', exc))
            return []
        try:
            with os.scandir(fd) as entries:
                result = []
                for item in entries:
                    if item.is_symlink():
                        skipped.append({'path': str(path / item.name), 'reason': 'symlink'})
                    elif item.name.casefold() in PRIVATE_DIRS:
                        skipped.append({'path': str(path / item.name), 'reason': 'private_storage'})
                    elif item.is_dir(follow_symlinks=False):
                        result.append(path / item.name)
                return sorted(result)
        except OSError as exc:
            errors.append(_error(path, 'discover', exc))
            return []
        finally:
            os.close(fd)

    def add(path, kind):
        try:
            fd = _open_directory(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            errors.append(_error(path, 'discover', exc))
            return
        os.close(fd)
        roots.append({'path': str(path), 'kind': kind})

    for bundle in ('com.tencent.xinWeChat', 'com.tencent.WeChat'):
        data = home / 'Library/Containers' / bundle / 'Data'
        current = data / 'Documents/xwechat_files'
        for account in children(current):
            add(account / 'msg/attach', 'wechat_current')
        old = data / 'Library/Application Support' / bundle
        for level_one in children(old):
            add(level_one / 'Message/MessageTemp', 'wechat_legacy')
            for level_two in children(level_one):
                add(level_two / 'Message/MessageTemp', 'wechat_legacy')
        # 只处理旧版明确的文件存储根，不进入联系人、消息或登录配置目录。
        for base in (data / 'Documents/WeChat Files', home / 'Documents/WeChat Files'):
            for account in children(base):
                add(account / 'FileStorage/File', 'wechat_legacy')
    unique = {r['path']: r for r in roots}
    return {'roots': list(unique.values()), 'errors': errors, 'skipped': skipped}


def _walk_files(fd, directory, errors, skipped):
    try:
        with os.scandir(fd) as entries:
            entries = sorted(entries, key=lambda item: item.name)
    except OSError as exc:
        errors.append(_error(directory, 'list_directory', exc))
        return
    for item in entries:
        path = directory / item.name
        try:
            if item.is_symlink():
                skipped.append({'path': str(path), 'reason': 'symlink'})
            elif item.is_dir(follow_symlinks=False):
                if item.name.casefold() in PRIVATE_DIRS:
                    skipped.append({'path': str(path), 'reason': 'private_storage'})
                    continue
                child_fd = os.open(item.name, DIRECTORY_FLAGS, dir_fd=fd)
                try:
                    yield from _walk_files(child_fd, path, errors, skipped)
                finally:
                    os.close(child_fd)
            elif item.is_file(follow_symlinks=False):
                if _private_file(item.name):
                    skipped.append({'path': str(path), 'reason': 'database_or_login_config'})
                else:
                    yield fd, path
        except OSError as exc:
            errors.append(_error(path, 'inspect_entry', exc))


def _read_candidate(directory_fd, path):
    _require_secure_io()
    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('候选已变为非普通文件，未读取')
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('读取期间源文件改变，SHA256不作为有效证据')
        return {'size': after.st_size, 'mtime_ns': after.st_mtime_ns,
                'mtime_utc': datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat(),
                'sha256': digest.hexdigest(), 'status': 'readable'}
    finally:
        os.close(fd)


def locate(names=(), contains=(), roots=(), include_default_roots=True, home=None):
    queries, seen = [], set()
    for kind, values in (('exact', names), ('contains', contains)):
        for value in values:
            if not isinstance(value, str) or not value.strip() or '\x00' in value:
                raise ValueError('文件名或关键词必须为非空字符串')
            if kind == 'exact' and ('/' in value or '\\' in value):
                raise ValueError('--name只接受文件名，不接受路径')
            normalized = unicodedata.normalize('NFC', value)
            if (kind, normalized) not in seen:
                queries.append({'kind': kind, 'value': value, 'normalized_value': normalized})
                seen.add((kind, normalized))
    if not queries:
        raise ValueError('至少提供一个--name或--contains')
    _require_secure_io()
    discovery = discover_roots(home) if include_default_roots else {
        'roots': [], 'errors': [], 'skipped': []}
    errors, skipped = discovery['errors'], discovery['skipped']
    root_specs = discovery['roots'] + [
        {'path': str(_absolute(root)), 'kind': 'explicit'} for root in roots]
    root_specs = list({r['path']: r for r in root_specs}.values())
    if not root_specs:
        errors.append({'path': None, 'operation': 'discover', 'type': 'NoAttachmentRoots',
                       'errno': None, 'message': '未发现可检索的附件根；请明确--root材料目录'})
    matches, scanned_paths, files_scanned = [], set(), 0
    for root in root_specs:
        directory = Path(root['path'])
        if any(part.casefold() in PRIVATE_DIRS for part in directory.parts):
            errors.append(_error(directory, 'open_root',
                                 ValueError('来源根不得位于已知数据库或登录配置目录')))
            root['status'] = 'unavailable'
            continue
        try:
            fd = _open_directory(directory)
        except OSError as exc:
            errors.append(_error(directory, 'open_root', exc))
            root['status'] = 'unavailable'
            continue
        error_count = len(errors)
        try:
            for directory_fd, path in _walk_files(fd, directory, errors, skipped):
                if str(path) in scanned_paths:
                    continue
                scanned_paths.add(str(path))
                files_scanned += 1
                normalized = unicodedata.normalize('NFC', path.name)
                indices = [i for i, q in enumerate(queries) if (
                    normalized == q['normalized_value'] if q['kind'] == 'exact'
                    else q['normalized_value'] in normalized)]
                if not indices:
                    continue
                match = {'path': str(path), 'name': path.name, 'root': str(directory),
                         'relative_path': str(path.relative_to(directory)),
                         'matched_queries': indices, 'size': None, 'mtime_ns': None,
                         'mtime_utc': None, 'sha256': None}
                try:
                    match.update(_read_candidate(directory_fd, path))
                except (OSError, ValueError) as exc:
                    error = _error(path, 'hash_candidate', exc)
                    errors.append(error)
                    match.update(status='error', error=error)
                matches.append(match)
        finally:
            os.close(fd)
        root['status'] = 'scanned' if len(errors) == error_count else 'incomplete'
    matches.sort(key=lambda m: m['path'])
    complete = not errors
    for index, query in enumerate(queries):
        candidates = [m for m in matches if index in m['matched_queries']]
        query['candidate_paths'] = [m['path'] for m in candidates]
        query['candidate_count'] = len(candidates)
        query['distinct_content_count'] = len({m['sha256'] for m in candidates if m['sha256']})
        query['status'] = ('ambiguous' if len(candidates) > 1 else
                           'incomplete' if not complete else
                           'unique' if candidates else 'missing')
    missing = [q['value'] for q in queries if q['status'] == 'missing']
    ambiguous = [q['value'] for q in queries if q['status'] == 'ambiguous']
    return {'schema_version': 1, 'read_only': True,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'ok': complete and all(q['status'] == 'unique' for q in queries),
            'scan_complete': complete, 'roots': root_specs, 'files_scanned': files_scanned,
            'queries': queries, 'matches': matches, 'missing': missing,
            'ambiguous': ambiguous, 'errors': errors, 'skipped': skipped}


def _write_report(output, report):
    _require_secure_io()
    output = _absolute(output)
    resolved = output.resolve()
    for root in report['roots']:
        try:
            resolved.relative_to(Path(root['path']).resolve())
        except ValueError:
            continue
        raise ValueError('报告不能写入待检索的来源目录')
    # 独占创建，已有回执或来源文件永不覆盖。
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', action='append', nargs='+', default=[], help='精确文件名，可重复或每次列多个')
    parser.add_argument('--contains', action='append', default=[], help='文件名包含关键词，可重复')
    parser.add_argument('--root', action='append', default=[], help='增加明确的材料根目录')
    parser.add_argument('--no-default-roots', action='store_true', help='只查--root，不自动发现微信目录')
    parser.add_argument('--output', type=Path, help='另存JSON回执；只创建新文件')
    args = parser.parse_args(argv)
    try:
        report = locate([name for group in args.name for name in group], args.contains,
                        args.root, not args.no_default_roots)
        if args.output:
            _write_report(args.output, report)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['ok'] else 1
    except (OSError, ValueError) as exc:
        error = {'ok': False, 'read_only': True, 'error': str(exc)}
        if isinstance(exc, UnsupportedPlatformError):
            error.update(error_code=exc.code, files_scanned=0)
        print(json.dumps(error,
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
