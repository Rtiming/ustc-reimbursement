"""原样归档与材料归一化薄入口；不执行OCR、财务或金额核定。"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import types
import unicodedata
import zipfile

SCHEMA = 'reimbursement.material-intake.v1'
REVIEW_SCHEMA = 'reimbursement.material-field-review.v1'
ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
SHA_RE = re.compile(r'[a-f0-9]{64}\Z')
IMAGES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'}
OTHER_ARCHIVES = {'.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz'}
DEFAULT_LIMITS = dict(max_source_files=1000, max_source_bytes=512 * 1024**2,
                      max_archive_members=5000, max_expanded_bytes=1024**3,
                      max_member_bytes=100 * 1024**2, max_archive_depth=5,
                      max_compression_ratio=200)
MAX_LIMITS = {**DEFAULT_LIMITS, 'max_source_files': 10000, 'max_source_bytes': 1024**3,
              'max_archive_members': 10000, 'max_compression_ratio': 1000}


class IntakeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise IntakeError(code, message)


def keys(value, allowed, label):
    require(isinstance(value, dict), 'invalid_request', label + '须为对象')
    require(not set(value) - set(allowed), 'unknown_key', label + '含未知字段：' + ', '.join(sorted(set(value) - set(allowed))))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def absolute(value, label):
    require(isinstance(value, str) and value.strip(), 'invalid_path', label + '须为明确绝对路径')
    result = Path(value).expanduser()
    require(result.is_absolute(), 'invalid_path', label + '须为绝对路径')
    return result.absolute()


def reject_links(path):
    """在resolve之前检查，以免丢失目录链接/Windows junction的身份。"""
    path = Path(path)
    for component in [*reversed(path.parents), path]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                'source_link', '不跟随文件或目录链接：' + str(component))


def normalize(request):
    keys(request, {'schema', 'case_id', 'sources', 'output_dir', 'limits', 'field_review'}, 'request')
    require(request.get('schema') == SCHEMA, 'invalid_schema', '材料请求schema不支持')
    require(isinstance(request.get('case_id'), str) and ID_RE.fullmatch(request['case_id']),
            'invalid_case_id', 'case_id须为稳定ASCII标识')
    output = absolute(request.get('output_dir'), 'output_dir')
    supplied = request.get('limits', {})
    keys(supplied, DEFAULT_LIMITS, 'limits')
    limits = {**DEFAULT_LIMITS, **supplied}
    for name, value in limits.items():
        require(type(value) is int and 1 <= value <= MAX_LIMITS[name], 'invalid_limit', name + '超出支持范围')
    sources = request.get('sources')
    require(isinstance(sources, list) and sources, 'invalid_sources', 'sources须为非空数组')
    seen, normalized = set(), []
    for source in sources:
        keys(source, {'id', 'path', 'expected_sha256', 'group_strip_dirs'}, 'source')
        sid = source.get('id')
        require(isinstance(sid, str) and ID_RE.fullmatch(sid) and sid not in seen,
                'invalid_source_id', 'source.id须为唯一稳定ASCII标识')
        seen.add(sid)
        strip = source.get('group_strip_dirs', 0)
        require(strip is None or type(strip) is int and 0 <= strip <= 20,
                'invalid_grouping', 'group_strip_dirs须为0—20，null表示暂不分组')
        expected = source.get('expected_sha256')
        require(expected is None or isinstance(expected, str) and SHA_RE.fullmatch(expected),
                'invalid_sha', 'expected_sha256须为64位小写十六进制')
        normalized.append({**source, 'path': str(absolute(source.get('path'), 'source.path')), 'group_strip_dirs': strip})
    review = request.get('field_review')
    if review is not None:
        keys(review, {'path', 'sha256'}, 'field_review')
        absolute(review.get('path'), 'field_review.path')
        require(isinstance(review.get('sha256'), str) and SHA_RE.fullmatch(review['sha256']),
                'invalid_sha', 'field_review须冻结文件SHA')
    return {**request, 'sources': normalized, 'output_dir': str(output), 'limits': limits}


def dependency(archive_helper):
    """依赖由受信任的本机配置/工程调用者注入，不从业务request取Python路径。"""
    if archive_helper is None:
        return None
    keys(archive_helper, {'path', 'sha256'}, 'archive_helper dependency')
    path = absolute(archive_helper.get('path'), 'archive_helper.path')
    expected = archive_helper.get('sha256')
    require(isinstance(expected, str) and SHA_RE.fullmatch(expected), 'invalid_dependency', '共享归档工具须预登记SHA')
    require(path.is_file() and digest(path) == expected, 'dependency_changed', '共享归档工具不存在或SHA与预登记不符')
    return {'path': str(path), 'sha256': expected}


def discover(config):
    files, total = [], 0
    output = Path(config['output_dir']).resolve()
    for source in config['sources']:
        root = Path(source['path'])
        reject_links(root)
        require(root.is_file() or root.is_dir(), 'source_missing', '来源不存在或不是普通文件/目录：' + str(root))
        if root.is_dir():
            require(not output.is_relative_to(root.resolve()), 'output_inside_source', '输出目录不能放在输入目录内')
            require(source.get('expected_sha256') is None, 'invalid_sha', '目录请按逐文件计划冻结，不能给单文件SHA')
            selected = []
            for directory, dirs, names in os.walk(root, followlinks=False):
                for name in [*dirs, *names]:
                    reject_links(Path(directory) / name)
                selected.extend(Path(directory) / name for name in names)
                require(len(files) + len(selected) <= config['limits']['max_source_files'],
                        'source_budget_exceeded', '原件数量超过预算，停止枚举')
            selected.sort(key=lambda p: p.relative_to(root).as_posix())
        else:
            selected = [root]
        for path in selected:
            require(path.is_file(), 'invalid_source', '来源不是普通文件：' + str(path))
            size = path.stat().st_size
            total += size
            require(len(files) < config['limits']['max_source_files'] and total <= config['limits']['max_source_bytes'],
                    'source_budget_exceeded', '原件数量或字节预算超限')
            sha = digest(path)
            require(not source.get('expected_sha256') or sha == source['expected_sha256'],
                    'source_changed', '来源与expected_sha256不一致：' + str(path))
            files.append(dict(source_id=source['id'], path=str(path.resolve()),
                              relative_path=path.relative_to(root).as_posix() if root.is_dir() else path.name,
                              sha256=sha, bytes=size, group_strip_dirs=source['group_strip_dirs']))
    require(files, 'empty_sources', '输入目录没有文件')
    return files


def safe_member(name):
    value = unicodedata.normalize('NFC', name.replace('\\', '/'))
    parts = PurePosixPath(value).parts
    require(value and not value.startswith('/') and '\x00' not in value and not any(ord(c) < 32 for c in value)
            and parts and '..' not in parts and not any(':' in part for part in parts),
            'unsafe_archive_path', '压缩包成员路径不安全：' + repr(name))
    return PurePosixPath(value).as_posix() + ('/' if value.endswith('/') else '')


def archive_budget(files, limits):
    """只在内存核对ZIP；成员永不按其声明路径写入文件系统。"""
    from material_coverage import archive_name
    totals = dict(source_bytes=sum(f['bytes'] for f in files), expanded_bytes=sum(f['bytes'] for f in files), archive_members=0)
    directories = set()

    def walk(source, chain, depth):
        require(depth <= limits['max_archive_depth'], 'archive_depth_exceeded', 'ZIP嵌套深度超限：' + chain)
        try:
            with zipfile.ZipFile(source) as archive:
                names = set()
                for member in archive.infolist():
                    require('\x00' not in member.orig_filename, 'unsafe_archive_path', 'ZIP原始成员名含NUL')
                    name = safe_member(archive_name(member))
                    normalized = name.rstrip('/').casefold()
                    require(normalized not in names, 'ambiguous_archive_member', 'ZIP有重复或跨平台同名成员：' + chain + '!/' + name)
                    names.add(normalized)
                    mode = stat.S_IFMT(member.external_attr >> 16)
                    require(mode in {0, stat.S_IFREG, stat.S_IFDIR} and not (member.external_attr & 0x400),
                            'archive_link', 'ZIP不接受链接或特殊文件：' + chain + '!/' + name)
                    require(not (member.flag_bits & 1), 'encrypted_archive', '不自动解密ZIP：' + chain)
                    totals['archive_members'] += 1
                    totals['expanded_bytes'] += member.file_size
                    require(totals['archive_members'] <= limits['max_archive_members'] and totals['expanded_bytes'] <= limits['max_expanded_bytes']
                            and member.file_size <= limits['max_member_bytes'], 'archive_budget_exceeded', 'ZIP成员数量或展开字节预算超限：' + chain)
                    if member.is_dir() or name.endswith('/'):
                        continue
                    require(not member.file_size or member.file_size / max(1, member.compress_size) <= limits['max_compression_ratio'],
                            'compression_ratio_exceeded', 'ZIP压缩倍率超限：' + chain + '!/' + name)
                    directories.add(str(PurePosixPath(name).parent))
                    suffix = PurePosixPath(name).suffix.lower()
                    require(suffix not in OTHER_ARCHIVES, 'unsupported_archive', '本入口只展开ZIP，其他压缩格式请走对应工具：' + name)
                    if suffix == '.zip':
                        payload = archive.read(member)
                        require(len(payload) == member.file_size, 'archive_size_mismatch', 'ZIP成员实际字节数不符')
                        walk(io.BytesIO(payload), chain + '!/' + name, depth + 1)
        except zipfile.BadZipFile as exc:
            raise IntakeError('invalid_archive', 'ZIP目录或数据损坏：' + chain) from exc

    for record in files:
        path = Path(record['path'])
        require(path.suffix.lower() not in OTHER_ARCHIVES, 'unsupported_archive', '本入口只展开ZIP，未支持：' + str(path))
        if path.suffix.lower() == '.zip':
            walk(path, str(path), 0)
    require(totals['expanded_bytes'] <= limits['max_expanded_bytes'], 'archive_budget_exceeded', '累计读取字节预算超限')
    return {**totals, 'member_directories_preview': sorted(directories)[:200], 'directories_truncated': len(directories) > 200}


def read_review(config):
    record = config.get('field_review')
    if record is None:
        return None
    path = Path(record['path'])
    reject_links(path)
    require(path.is_file() and digest(path) == record['sha256'], 'review_changed', '人工字段核对文件缺失或SHA变化')
    review = json.loads(path.read_text(encoding='utf-8-sig'))
    keys(review, {'schema', 'case_id', 'records'}, 'field_review file')
    require(review.get('schema') == REVIEW_SCHEMA and review.get('case_id') == config['case_id'], 'review_case_mismatch', '人工复核schema或案件不匹配')
    require(isinstance(review.get('records'), list), 'invalid_review', '人工复核records须为数组')
    for row in review['records']:
        keys(row, {'source_sha256', 'page', 'fields', 'group_id', 'basis'}, 'review record')
        require(isinstance(row.get('source_sha256'), str) and SHA_RE.fullmatch(row['source_sha256'])
                and type(row.get('page', 1)) is int and row.get('page', 1) >= 1 and isinstance(row.get('fields'), dict),
                'invalid_review', '复核记录须含来源SHA、有效页码和fields对象')
    return review


def preview(request, *, archive_helper=None):
    config = normalize(request)
    reject_links(Path(config['output_dir']))
    require(not Path(config['output_dir']).exists(), 'output_exists', '输出目录已存在；先只读核对原receipt.json，不覆盖或换目录重放未知结果')
    helper = dependency(archive_helper)
    files = discover(config)
    budget = archive_budget(files, config['limits'])
    review = read_review(config)
    selection = json.dumps(files, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return dict(ok=True, schema=SCHEMA, case_id=config['case_id'], output_dir=config['output_dir'],
                mode='preview', files=files, file_count=len(files), limits=config['limits'], budget=budget,
                source_selection_sha256=hashlib.sha256(selection).hexdigest(), archive_helper=helper,
                field_review_count=len(review['records']) if review else 0,
                system_write=False, filesystem_write=False, ocr_started=False)


def group_inventory(inventory, originals):
    origins = {str(Path(r['snapshot']).resolve()): r for r in originals}
    groups, issues = {}, []
    for row in inventory['files']:
        origin = origins[row['root_source']]
        relative = row['source_chain'].split('!/', 1)[1] if '!/' in row['source_chain'] else origin['relative_path']
        key = origin['source_id'] + '/' + origin['relative_path'] + ('!/' + relative if '!/' in row['source_chain'] else '')
        row['material_id'] = 'mat-' + hashlib.sha256(key.encode()).hexdigest()[:16]
        row['source_id'] = origin['source_id']
        row['member_chain'] = relative
        row['group_id'] = None
        if row['kind'] == 'archive':
            continue
        parts = PurePosixPath(relative.replace('\\', '/').replace('!/', '/')).parts[:-1]
        strip = origin['group_strip_dirs']
        if strip is None or len(parts) <= strip:
            issues.append(dict(code='group_unresolved', material_id=row['material_id'], reason='未指定分组或来源没有可用活动目录；不按文件名推定摘要'))
            continue
        directory = '/'.join(parts[:strip + 1])
        group_key = origin['source_id'] + '/' + directory
        gid = 'group-' + hashlib.sha256(group_key.encode()).hexdigest()[:16]
        row['group_id'] = gid
        group = groups.setdefault(gid, dict(group_id=gid, source_id=origin['source_id'], source_directory=directory,
                                            original_summary=parts[strip], status='directory_candidate', material_ids=[],
                                            claim_amount=None, selected_contact=None, payees=[]))
        group['material_ids'].append(row['material_id'])
    return list(groups.values()), issues


def ocr_candidates(inventory):
    pages = {}
    for page in inventory['pages']:
        if not page['native_text'].strip():
            pages.setdefault(page['sha256'], set()).add(page['page'])
    candidates, seen = [], set()
    for row in inventory['files']:
        if row['kind'] == 'archive' or row['sha256'] in seen:
            continue
        selected = [1] if '.' + row['kind'] in IMAGES else sorted(pages.get(row['sha256'], []))
        if selected:
            seen.add(row['sha256'])
            candidates.append(dict(id='ocr-' + row['sha256'][:20], path=row['working_copy'], pages=selected, rotate=[0]))
    return candidates


def execute(request, *, archive_helper=None):
    config = normalize(request)
    output = Path(config['output_dir'])
    reject_links(output)
    require(not output.exists(), 'output_exists', '输出目录已存在；先只读核对原回执 ' + str(output / 'receipt.json') + '，不覆盖或换目录重放未知结果')
    if len(output.parts) > 2 and output.parts[1] == 'Volumes':
        require(os.path.ismount(Path(*output.parts[:3])), 'output_mount_missing', '目标卷没有挂载，不创建伪挂载目录')
    # 先拒绝把结果写回源树，之后仅在独占的新目录内留下诊断。
    for source in config['sources']:
        source_path = Path(source['path'])
        require(not source_path.is_dir() or not output.resolve().is_relative_to(source_path.resolve()),
                'output_inside_source', '输出目录不能在输入目录内')
    output.mkdir(parents=True, exist_ok=False)
    receipt_path = output / 'receipt.json'
    receipt = dict(schema='reimbursement.material-intake-receipt.v1', case_id=config['case_id'],
                   status='running', phase='preview', originals=[], system_write=False, ocr_started=False)
    write_json(receipt_path, receipt)
    try:
        # preview拒绝已有目录，独占目录已经创建，故在此复用各个只读检查函数。
        helper_record = dependency(archive_helper)
        require(helper_record is not None, 'archive_helper_unconfigured', '须由本机受信任配置注入共享归档工具及SHA')
        files = discover(config)
        budget = archive_budget(files, config['limits'])
        review = read_review(config)
        receipt.update(budget=budget, archive_helper=helper_record, phase='archive')
        write_json(receipt_path, receipt)
        # 编译已验SHA的同一字节，避免加载器读到后来替换的源码或旁边的pyc。
        helper_bytes = Path(helper_record['path']).read_bytes()
        require(hashlib.sha256(helper_bytes).hexdigest() == helper_record['sha256'], 'dependency_changed', '归档依赖在加载前发生变化')
        helper = types.ModuleType('_reimbursement_archive_helper')
        helper.__file__ = helper_record['path']
        exec(compile(helper_bytes, helper_record['path'], 'exec'), helper.__dict__)
        require(callable(getattr(helper, 'archive', None)) and callable(getattr(helper, 'verify_receipt', None)),
                'invalid_dependency', '共享归档工具缺少archive/verify_receipt')
        for record in files:
            destination = output / 'originals' / record['source_id'] / Path(record['relative_path']).parent
            result = helper.archive(record['path'], destination)
            require(result['status'] == 'archived' and Path(result['snapshot']).resolve().is_relative_to(output.resolve())
                    and result['sha256'] == record['sha256'] and helper.verify_receipt(result['receipt'])['ok'],
                    'archive_verification_failed', '归档目标、来源SHA或回读不符：' + record['path'])
            receipt['originals'].append({**record, **result})
            write_json(receipt_path, receipt)
        require(discover(config) == files, 'source_changed', '归档期间来源选择或字节发生变化；保留已归档材料并重新选择新目录')
        receipt['phase'] = 'inventory'
        write_json(receipt_path, receipt)
        from material_relations import scan
        inventory = scan([Path(row['snapshot']) for row in receipt['originals']], output / 'working_copies')
        groups, issues = group_inventory(inventory, receipt['originals'])
        requests = ocr_candidates(inventory)
        review_links = []
        if review is not None:
            review_archive = helper.archive(config['field_review']['path'], output / 'field_review')
            require(review_archive['status'] == 'archived' and Path(review_archive['snapshot']).resolve().is_relative_to(output.resolve())
                    and review_archive['sha256'] == config['field_review']['sha256'] and helper.verify_receipt(review_archive['receipt'])['ok'],
                    'review_changed', '人工复核归档SHA不符')
            receipt['field_review_archive'] = review_archive
            by_sha = {}
            for row in inventory['files']:
                if row['kind'] != 'archive':
                    by_sha.setdefault(row['sha256'], []).append(row)
            for row in review['records']:
                materials = by_sha.get(row['source_sha256'], [])
                in_page = [r for r in materials if row.get('page', 1) <= (r['page_count'] or (1 if '.' + r['kind'] in IMAGES else 0))]
                known_group = row.get('group_id') is None or any(g['group_id'] == row['group_id'] for g in groups)
                status = 'source_bound_candidate' if in_page and known_group else 'unresolved_candidate'
                review_links.append({**row, 'status': status, 'material_ids': [r['material_id'] for r in in_page]})
                if status == 'unresolved_candidate':
                    issues.append(dict(code='review_link_unresolved', source_sha256=row['source_sha256'], reason='复核页或组不在本批；保留候选，不应用字段'))
        products = {'intake_manifest.json': {'schema': SCHEMA, 'case_id': config['case_id'], 'originals': receipt['originals']},
                    'material_inventory.json': inventory,
                    'groups.json': {'case_id': config['case_id'], 'groups': groups, 'field_review_candidates': review_links},
                    'ocr_request_candidates.json': requests, 'issues.json': issues}
        for name, value in products.items():
            write_json(output / name, value)
        receipt.update(status='completed', phase='completed', groups=len(groups), original_count=len(files),
                       ocr_candidates=len(requests), issues=len(issues), artifacts=[{'path': name, 'sha256': digest(output / name)} for name in products])
        write_json(receipt_path, receipt)
        return dict(ok=True, output_dir=str(output), receipt=str(receipt_path), original_count=len(files),
                    groups=len(groups), ocr_candidates=len(requests), system_write=False, ocr_started=False,
                    next_step='核对groups及issues；难页请求交remote_material_ocr.prepare和既有远程工作流。人工字段与金额核定仍在私有案件记录。')
    except Exception as exc:
        receipt.update(status='failed', error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)})
        write_json(receipt_path, receipt)
        return dict(ok=False, output_dir=str(output), receipt=str(receipt_path), error=receipt['error'], system_write=False, ocr_started=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--archive-helper', type=Path, help='工程调用专用，受信任archive_file.py；不属于业务request')
    parser.add_argument('--archive-helper-sha256', help='本机预登记的共享归档工具SHA')
    args = parser.parse_args()
    try:
        require(bool(args.archive_helper) == bool(args.archive_helper_sha256), 'invalid_dependency', '归档工具路径和SHA须同时提供')
        dep = {'path': str(args.archive_helper.resolve()), 'sha256': args.archive_helper_sha256} if args.archive_helper else None
        request = json.loads(args.request.read_text(encoding='utf-8-sig'))
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(request, archive_helper=dep) if args.execute else preview(request, archive_helper=dep)
    except Exception as exc:
        result = dict(ok=False, error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)}, system_write=False)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
