#!/usr/bin/env python3
"""从本案候选生成来源清单并审核原生文字PDF；不搜索、解压、OCR或选票。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager, ExitStack, nullcontext
from pathlib import Path


SCHEMA = 'ustc-case-invoice-sources-v1'
AUDIT_SCHEMA = 'ustc-case-invoice-audit-v1'
CANDIDATE_STATUS = 'candidate_invoice_source_not_selected'
# 未适配的系统仍可导入纯参数合同；实际来源读写不降低防链接要求。
POSIX_SECURE_IO_SUPPORTED = (os.name == 'posix'
                            and all(hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK'))
                            and os.open in os.supports_dir_fd)
WINDOWS_SECURE_IO_SUPPORTED = os.name == 'nt' and sys.platform == 'win32'
SECURE_IO_SUPPORTED = POSIX_SECURE_IO_SUPPORTED or WINDOWS_SECURE_IO_SUPPORTED
DIRECTORY_FLAGS = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if POSIX_SECURE_IO_SUPPORTED else None
FILE_FLAGS = (os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK) if POSIX_SECURE_IO_SUPPORTED else None


class UnsupportedPlatformError(ValueError):
    code = 'unsupported_platform'


def _require_secure_io():
    if not SECURE_IO_SUPPORTED:
        raise UnsupportedPlatformError(
            'unsupported_platform: 本案票源核验需要POSIX防链接目录句柄或Windows原生安全句柄；未读取原件')


def _path(value, label):
    if not isinstance(value, (str, Path)) or not str(value) or '\x00' in str(value):
        raise ValueError(f'{label}须为绝对文件路径')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'{label}须为无上级跳转的绝对路径')
    return path


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', value):
        raise ValueError(f'{label}须为64位SHA256文本')
    return value.lower()


def _number(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{20}', value):
        raise ValueError('发票号码须为20位ASCII数字字符串')
    return value


def _numbers(values):
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 20:
        raise ValueError('invoice_numbers须含1至20个不同的完整票号')
    numbers = [_number(value) for value in values]
    if len(set(numbers)) != len(numbers):
        raise ValueError('invoice_numbers不能有重复票号')
    return numbers


def _open_directory(path):
    """逐层拒绝软链接，避免声明根及其中目录在读取时跳到别处。"""
    _require_secure_io()
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


@contextmanager
def _open_file(path, roots):
    matches = [root for root in roots if path.is_relative_to(root) and path != root]
    if not matches:
        raise ValueError(f'材料不在本案批准来源根内：{path}')
    _require_secure_io()
    if WINDOWS_SECURE_IO_SUPPORTED:
        from windows_source_io import open_source
        with open_source(path, roots) as fd:
            yield fd
        return
    root = max(matches, key=lambda item: len(item.parts))
    directory_fd = None
    file_fd = None
    try:
        directory_fd = _open_directory(root)
        relative = path.relative_to(root)
        for part in relative.parts[:-1]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        file_fd = os.open(relative.name, FILE_FLAGS, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError(f'来源必须为普通文件：{path}')
        yield file_fd
    except OSError as exc:
        raise ValueError(f'来源读取失败（不接受软链接或越界目录）：{path}: {exc.strerror}') from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _verified_sha(path, expected, roots):
    with _open_file(path, roots) as fd:
        before = os.fstat(fd)
        digest = hashlib.sha256()
        while block := os.read(fd, 1024 * 1024):
            digest.update(block)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if tuple(getattr(before, field) for field in fields) != tuple(
                getattr(after, field) for field in fields):
            raise ValueError(f'读取期间来源改变：{path}')
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError(f'来源SHA256与本案登记不符：{path}')
        return actual


def _records(material):
    records = material.get('invoice_records')
    if not isinstance(records, list) or not records:
        raise ValueError(f'候选缺少invoice_records票号及页码：{material.get("path")}')
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('invoice_records每项须为number/page对象')
        number = _number(record.get('number'))
        page = record.get('page')
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValueError('invoice_records.page须为从1开始的整数页码')
        item = {'number': number, 'page': page}
        if item not in result:
            result.append(item)
    return result


def _source(material):
    path = _path(material.get('path'), 'materials.path')
    digest = _sha(material.get('sha256'), 'materials.sha256')
    records = _records(material)
    archive = material.get('source_archive')
    archive_sha = material.get('source_archive_sha256')
    chain = material.get('source_chain')
    if chain is not None:
        if isinstance(chain, str):
            chain_text = chain
        elif isinstance(chain, list) and chain and all(isinstance(value, str) and value for value in chain):
            chain_text = '!/'.join(chain)
        else:
            raise ValueError('source_chain须为非空文本或非空文本列表')
        if not chain_text or '\x00' in chain_text:
            raise ValueError('source_chain须为非空来源链')
    else:
        chain_text = None
    if archive is not None:
        archive = _path(archive, 'source_archive')
        if archive.suffix.lower() != '.zip':
            raise ValueError('source_archive须为已登记ZIP原件')
        archive_sha = _sha(archive_sha, 'source_archive_sha256')
        prefix = str(archive) + '!/'
        if not chain_text or not chain_text.startswith(prefix) or len(chain_text) == len(prefix):
            raise ValueError('ZIP工作副本须有以source_archive为根的成员来源链')
    elif archive_sha is not None:
        raise ValueError('source_archive_sha256缺少对应source_archive')
    result = {'path': str(path), 'sha256': digest,
            'invoice_records': records, 'source_archive': str(archive) if archive else None,
            'source_archive_sha256': archive_sha, 'source_chain': copy.deepcopy(chain),
            'source_kind': 'archive_member_working_copy' if archive else 'declared_original_file'}
    if 'name' in material:
        name = material['name']
        if not isinstance(name, str) or not name or '\x00' in name:
            raise ValueError('materials.name须为非空文本')
        result['name'] = name
    if 'reviewed_page_evidence' in material:
        from case_invoice_page_evidence import declaration
        result['reviewed_page_evidence'] = declaration(material['reviewed_page_evidence'], _path, _sha)
    return result


def build_manifest(case, case_id, approved_roots, invoice_numbers=None):
    """仅核验声明来源，不修改case及选票状态；票号筛选是读取范围而非系统选票。"""
    if not isinstance(case, dict) or not isinstance(case_id, str) or not case_id or case.get('case_id') != case_id:
        raise ValueError('case_id与本案case.json不符')
    numbers = _numbers(invoice_numbers)
    if not isinstance(approved_roots, (list, tuple)) or not approved_roots:
        raise ValueError('须明确本案approved_roots')
    roots = list(dict.fromkeys(_path(root, 'approved_roots') for root in approved_roots))
    materials = case.get('materials')
    if not isinstance(materials, list):
        raise ValueError('本案materials须为列表')
    sources = []
    for material in materials:
        if not isinstance(material, dict):
            raise ValueError('materials每项须为对象')
        if material.get('status') == CANDIDATE_STATUS:
            sources.append(_source(material))
    if not sources:
        raise ValueError('本案未登记可用候选票据来源')
    by_number = {}
    for source in sources:
        for number in dict.fromkeys(record['number'] for record in source['invoice_records']):
            by_number.setdefault(number, set()).add(source['sha256'])
    requested = numbers if numbers is not None else list(by_number)
    missing = [number for number in requested if number not in by_number]
    if missing:
        raise ValueError('本案候选来源缺少完整票号：' + '、'.join(missing))
    ambiguous = [number for number in requested if len(by_number[number]) != 1]
    if ambiguous:
        raise ValueError('同票号对应不同文件内容，须先核对来源：' + '、'.join(ambiguous))
    requested_set = set(requested)
    included = [source for source in sources if requested_set.intersection(
        record['number'] for record in source['invoice_records'])]
    grouped, verified = {}, {}
    for source in included:
        for key, sha_key in (('path', 'sha256'), ('source_archive', 'source_archive_sha256')):
            if source[key] is None:
                continue
            identity = (source[key], source[sha_key])
            if identity not in verified:
                verified[identity] = _verified_sha(Path(source[key]), source[sha_key], roots)
        digest = source['sha256']
        if digest not in grouped:
            grouped[digest] = {**copy.deepcopy(source), 'invoice_number_candidates': [],
                               'invoice_records': [], 'sources': []}
        item = grouped[digest]
        if 'reviewed_page_evidence' in source:
            evidence = source['reviewed_page_evidence']
            if item.get('reviewed_page_evidence', evidence) != evidence:
                raise ValueError('同字节来源的已有逐页证据声明冲突')
            item['reviewed_page_evidence'] = copy.deepcopy(evidence)
        if source not in item['sources']:
            item['sources'].append(copy.deepcopy(source))
        for record in source['invoice_records']:
            if record not in item['invoice_records']:
                item['invoice_records'].append(copy.deepcopy(record))
            if record['number'] not in item['invoice_number_candidates']:
                item['invoice_number_candidates'].append(record['number'])
    return {'schema': SCHEMA, 'case_id': case_id, 'original_files': list(grouped.values()),
            'expected_count': len(grouped),
            'invoice_numbers_requested': numbers, 'selection_write': False, 'selected': False,
            'source_chain_member_bytes_rechecked': False}


def _write_bytes(data, output):
    _require_secure_io()
    output = _path(output, 'output')
    if WINDOWS_SECURE_IO_SUPPORTED:
        from windows_source_io import write_new
        write_new(data, output)
        return
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_fd = _open_directory(output.parent)
    try:
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(directory_fd)


def write_manifest(manifest, output):
    """独占新结果且权限为0600，不覆盖原材料或既有回执。"""
    _write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'), output)


def _recheck_sources(manifest, roots):
    checked = set()
    for item in manifest['original_files']:
        for source in item['sources']:
            for key, sha_key in (('path', 'sha256'), ('source_archive', 'source_archive_sha256')):
                identity = (source[key], source[sha_key])
                if source[key] is not None and identity not in checked:
                    _verified_sha(Path(source[key]), source[sha_key], roots)
                    checked.add(identity)


def _verified_bytes(path, expected, roots, limit):
    """解析固定字节快照；防止哈希后换文件或证据路径越过批准根。"""
    with _open_file(_path(path, 'evidence.path'), roots) as fd:
        before = os.fstat(fd)
        if before.st_size > limit:
            raise ValueError('来源或证据超过读取上限')
        data = bytearray()
        while block := os.read(fd, min(1024 * 1024, limit + 1 - len(data))):
            data.extend(block)
            if len(data) > limit:
                raise ValueError('读取期间来源超过上限')
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if tuple(getattr(before, k) for k in fields) != tuple(getattr(after, k) for k in fields):
            raise ValueError('读取期间来源或证据改变')
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('来源或证据SHA256不符')
        return bytes(data)


def _audit_with_existing_evidence(manifest, case_id, roots, output, native_output):
    from case_invoice_page_evidence import audit_existing_pages
    reports = []
    native_sources = []
    for source in manifest['original_files']:
        if 'reviewed_page_evidence' in source:
            reports.append(audit_existing_pages(source,
                lambda path, expected, limit: _verified_bytes(path, expected, roots, limit)))
        else:
            native_sources.append(source)
    native = None
    if native_sources:
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.case-invoice-native-part-', dir=output.parent) as temporary:
            partial = {**copy.deepcopy(manifest), 'original_files': native_sources, 'expected_count': len(native_sources)}
            native = _audit_guarded(partial, case_id, roots, Path(temporary) / 'native-wrapper.json', native_output)
            reports.extend(native['reports'])
    _recheck_sources(manifest, roots)
    for source in manifest['original_files']:
        for page in source.get('reviewed_page_evidence', []):
            for kind in ('ocr', 'visual'):
                evidence = page[kind]
                _verified_sha(Path(evidence['path']), evidence['sha256'], roots)
    order = {s['path']: n for n, s in enumerate(manifest['original_files'])}
    reports.sort(key=lambda report: order[report['path']])
    result = {'schema': AUDIT_SCHEMA, 'case_id': case_id, 'profile': 'ustc',
              'source_manifest': manifest, 'reports': reports, 'authenticity_verified': False,
              'system_write': False, 'source_bindings_verified': True, 'selection_write': False,
              'selected': False, 'existing_evidence_verified': True,
              'native_audit_path': native.get('native_audit_path') if native else None,
              'native_audit_sha256': native.get('native_audit_sha256') if native else None}
    write_manifest(result, output)
    return result


def _native_pdf_preflight(path, pdftotext):
    if path.suffix.lower() != '.pdf':
        raise ValueError(f'本地审核仅支持原生文字PDF，其他格式须走已有远程识别流程：{path}')
    result = subprocess.run([str(pdftotext), '-layout', str(path), '-'],
                            capture_output=True, text=True, encoding='utf-8', timeout=45,
                            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    if result.returncode:
        raise ValueError('PDF原生文字预检失败，须走已有远程识别流程：' + str(path))
    chunks = result.stdout.split('\f')
    if chunks and not chunks[-1].strip():
        chunks.pop()
    # 与extract_pdf使用同一阈值，任何将触发OCR的页都在审核前停止。
    insufficient = [page for page, text in enumerate(chunks, 1)
                    if len(re.sub(r'\s', '', text)) < 15]
    if not chunks or insufficient:
        pages = '、'.join(map(str, insufficient)) or '全部'
        raise ValueError(f'PDF第{pages}页原生文字不足15字，需远程OCR；未调用本地审核：{path}')
    return len(chunks)


def _check_audit_reports(native, manifest, page_counts):
    if not isinstance(native, dict):
        raise ValueError('既有审核CLI结果须为JSON对象')
    reports = native.get('reports')
    if native.get('authenticity_verified') is not False or not isinstance(reports, list):
        raise ValueError('既有审核CLI结果格式或真实性边界不符')
    expected = {item['path']: item for item in manifest['original_files']}
    seen = set()
    if len(reports) != len(expected):
        raise ValueError('既有审核报告数量与来源清单不符')
    for report in reports:
        if not isinstance(report, dict) or report.get('path') not in expected or report['path'] in seen:
            raise ValueError('既有审核报告路径与本案来源清单不符或重复')
        path = report['path']
        seen.add(path)
        source = expected[path]
        if report.get('sha256') != source['sha256']:
            raise ValueError('既有审核报告SHA256与本案来源清单不符：' + path)
        if report.get('authenticity_verified') is not False or report.get('system_write') is not False:
            raise ValueError('既有审核报告真实性或写入边界不符：' + path)
        pages = report.get('pages')
        if not isinstance(pages, list) or len(pages) != page_counts[path]:
            raise ValueError('既有审核报告页数与原生文字预检不符：' + path)
        if any(not isinstance(page, dict) or isinstance(page.get('page'), bool) for page in pages) or [
                page.get('page') for page in pages] != list(range(1, page_counts[path] + 1)):
            raise ValueError('既有审核报告页码不完整或重复：' + path)
        records = set()
        for page in pages:
            if not isinstance(page, dict) or page.get('extraction_method') != 'pdf_text':
                raise ValueError('既有审核报告含非原生文字页，须走已有远程OCR流程：' + path)
            number_candidates = page.get('invoice_number_candidates')
            if number_candidates is None and page.get('kind') == 'fee_detail' and page.get('invoice_counted') is False:
                number_candidates = []
            if not isinstance(number_candidates, list) or not isinstance(page.get('page'), int):
                raise ValueError('既有审核报告缺少逐页票号字段：' + path)
            records.update((number, page['page']) for number in number_candidates if isinstance(number, str))
        if not all((record['number'], record['page']) in records for record in source['invoice_records']):
            raise ValueError('本案登记票号及页码未获当前原件审核支持：' + path)
    return reports


def _generated_bytes(path, root):
    if not path.is_file():
        return None
    if not WINDOWS_SECURE_IO_SUPPORTED:
        return path.read_bytes()
    with _open_file(path, [root]) as fd:
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        return b''.join(chunks)


def _audit_guarded(manifest, case_id, roots, output, native_output):
    pdftotext = shutil.which('pdftotext')
    if not pdftotext:
        raise ValueError('pdftotext不可用；本入口不回退本地OCR，请走已有远程识别流程')
    pdftotext = Path(pdftotext).resolve()
    page_counts = {item['path']: _native_pdf_preflight(Path(item['path']), pdftotext)
                   for item in manifest['original_files']}
    _recheck_sources(manifest, roots)
    if WINDOWS_SECURE_IO_SUPPORTED:
        from windows_source_io import directory
        output_guard = directory(output.parent, create=True)
    else:
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_fd = _open_directory(output.parent)
        os.close(directory_fd)
        output_guard = nullcontext()
    with output_guard, tempfile.TemporaryDirectory(prefix='.case-invoice-audit-', dir=output.parent) as temporary:
        folder = Path(temporary)
        if WINDOWS_SECURE_IO_SUPPORTED:
            from platform_runtime import private_path
            private_path(folder)
        # 即使原件在预检后变成扫描页，既有CLI也找不到渲染及OCR程序。
        tools = folder / 'native-text-only'
        tools.mkdir(mode=0o700)
        alias = tools / ('pdftotext.exe' if WINDOWS_SECURE_IO_SUPPORTED else 'pdftotext')
        if WINDOWS_SECURE_IO_SUPPORTED:
            try:
                os.link(pdftotext, alias)
            except OSError:
                try:
                    alias.symlink_to(pdftotext)
                except OSError as exc:
                    raise ValueError('Windows原生文字隔离需要可创建硬链接或受控工具符号链接；未开放其他程序或OCR') from exc
        else:
            alias.symlink_to(pdftotext)
        environment = os.environ.copy()
        environment['PATH'] = str(tools)
        environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        native_file = folder / 'audit-native.json'
        command = [sys.executable, str(Path(__file__).with_name('invoice_audit.py').resolve()),
                   *(item['path'] for item in manifest['original_files']),
                   '--output', str(native_file), '--profile', 'ustc']
        try:
            platform_options = {'creationflags': subprocess.CREATE_NO_WINDOW} if WINDOWS_SECURE_IO_SUPPORTED else {'umask': 0o077}
            process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', env=environment,
                                     timeout=45 * (len(manifest['original_files']) + 1), **platform_options)
        except subprocess.TimeoutExpired as exc:
            data = _generated_bytes(native_file, folder)
            if data is not None:
                _write_bytes(data, native_output)
                raise ValueError('既有票面审核CLI超时；原CLI回执已保留：' + str(native_output)) from exc
            raise
        data = _generated_bytes(native_file, folder)
        if data is not None:
            # 失败回执也保留，但只有成功包装能作为后续选票的审核输入。
            _write_bytes(data, native_output)
        if process.returncode:
            note = '；原CLI回执已保留：' + str(native_output) if data is not None else '；CLI未生成JSON回执'
            raise ValueError('既有票面审核CLI失败：' + (process.stderr.strip()[-800:] or str(process.returncode)) + note)
        if data is None:
            raise ValueError('既有票面审核CLI未生成JSON回执')
        try:
            reports = _check_audit_reports(json.loads(data), manifest, page_counts)
            _recheck_sources(manifest, roots)
        except (ValueError, OSError) as exc:
            raise ValueError(str(exc) + '；原CLI回执已保留：' + str(native_output)) from exc
        result = {'schema': AUDIT_SCHEMA, 'case_id': case_id, 'profile': 'ustc',
                  'source_manifest': manifest, 'reports': reports, 'authenticity_verified': False,
                  'system_write': False, 'source_bindings_verified': True,
                  'selection_write': False, 'selected': False,
                  'native_audit_path': str(native_output),
                  'native_audit_sha256': hashlib.sha256(data).hexdigest()}
        write_manifest(result, output)
        return result


def audit_case(case, case_id, approved_roots, invoice_numbers, output):
    """固定USTC配置调用既有CLI；Windows在全部预检/CLI期间持有原件及来源包。"""
    _require_secure_io()
    if invoice_numbers is None:
        raise ValueError('audit须提供1至20个不同完整票号')
    output = _path(output, 'output')
    native_output = output.with_name(output.stem + '.audit-native.json')
    for destination in (output, native_output):
        if os.path.lexists(destination):
            raise ValueError('审核输出已存在，不覆盖：' + str(destination))
    manifest = build_manifest(case, case_id, approved_roots, invoice_numbers)
    roots = [_path(root, 'approved_roots') for root in approved_roots]
    with ExitStack() as held:
        if WINDOWS_SECURE_IO_SUPPORTED:
            paths = {source[key] for item in manifest['original_files'] for source in item['sources']
                     for key in ('path', 'source_archive') if source[key] is not None}
            for path in sorted(paths):
                held.enter_context(_open_file(Path(path), roots))
            # 清单完成与句柄取得之间也可能发生保存；锁定后、调用任何解析器前重新核对。
            _recheck_sources(manifest, roots)
        if any('reviewed_page_evidence' in source for source in manifest['original_files']):
            return _audit_with_existing_evidence(manifest, case_id, roots, output, native_output)
        return _audit_guarded(manifest, case_id, roots, output, native_output)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action, help_text in [('manifest', '核验本案声明候选并生成新来源清单'),
                              ('audit', '审核本案原生文字PDF或已绑定的既有OCR与目视字段，固定USTC配置')]:
        command = commands.add_parser(action, help=help_text)
        command.add_argument('--case-file', type=Path, required=True)
        command.add_argument('--case-id', required=True)
        command.add_argument('--material-root', type=Path, action='append', required=True)
        command.add_argument('--numbers', nargs='+', required=action == 'audit',
                             help='audit必填1至20个不同完整20位票号；manifest可省略')
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _require_secure_io()
        case_file = _path(args.case_file, 'case-file')
        case = json.loads(case_file.read_text(encoding='utf-8'))
        if args.command == 'audit':
            result = audit_case(case, args.case_id, args.material_root, args.numbers, args.output)
            source_manifest = result['source_manifest']
        else:
            result = build_manifest(case, args.case_id, args.material_root, args.numbers)
            write_manifest(result, args.output)
            source_manifest = result
    except UnsupportedPlatformError as exc:
        parser.exit(2, json.dumps({'ok': False, 'error_code': exc.code, 'error': str(exc),
                                  'system_write': False, 'sources_read': False}, ensure_ascii=False) + '\n')
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    print(json.dumps({'schema': result['schema'], 'case_id': args.case_id,
                      'files': len(source_manifest['original_files']), 'output': str(args.output),
                      'selection_write': False, 'selected': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
