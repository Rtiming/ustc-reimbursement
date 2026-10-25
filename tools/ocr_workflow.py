"""材料OCR薄编排：同一内核、原任务恢复、按SHA回接文字证据。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import shutil
import sys
import time

from assistant_api import atomic_json, read_json
from remote_material_ocr import SCHEMA, PROBE_CODE, prepare, normalize_requests, recognize, sha256


def fingerprint(plan, runtime):
    raw = json.dumps(
        {'plan': plan, 'runtime': runtime, 'worker_sha256': plan['worker_sha256']},
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def verify_remote_collection(summary, plan, profile, source):
    destination = summary.get('collection', {}).get('destination')
    if not isinstance(destination, str) or not Path(destination).is_absolute():
        raise ValueError('计算工具未返回明确的回收目录')
    root = Path(destination).resolve()
    if not root.is_relative_to(source.parent.resolve()):
        raise ValueError('OCR回收目录不属于原工作流')
    collection = read_json(root / 'collection.json')
    n = len(plan['requests'])
    if (
        summary.get('status') != 'completed'
        or not summary.get('run_id')
        or collection.get('run_id') != summary['run_id']
        or collection.get('completeness') != 'complete'
        or collection.get('terminal_state') != 'completed'
        or collection.get('transfer_verified') is not True
        or collection.get('receipt_count') != n
        or collection.get('expected_receipt_count') != n
        or collection.get('missing_outputs')
        or collection.get('failed_tasks')
        or collection.get('missing_receipt_tasks')
    ):
        raise ValueError('OCR回收尚不完整，沿原run恢复，不发布或重投')
    files = {}
    for artifact in collection['artifacts']:
        relative = PurePosixPath(artifact['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in artifact['path']:
            raise ValueError('OCR回收路径越界')
        path = root / 'outputs' / relative
        if (
            not path.resolve().is_relative_to(root / 'outputs')
            or path.is_symlink()
            or sha256(path) != artifact['sha256']
        ):
            raise ValueError('OCR回收文件SHA或范围不符')
        files[str(relative)] = path
    reports = []
    for index, request in enumerate(plan['requests']):
        receipt = read_json(root / f'receipts/task-{index:06d}.json')
        if (
            receipt.get('state') != 'completed'
            or receipt.get('exit_code') != 0
            or receipt.get('worker_exit_code') != 0
            or receipt.get('run_id') != summary['run_id']
            or receipt.get('task_index') != index
            or receipt.get('project_id') != 'reimbursement-material-ocr'
            or receipt.get('environment_probe_sha256') != profile['probe_sha256']
        ):
            raise ValueError('OCR原任务身份、退出状态或环境回执不符')
        path = files.get(f'task-{index:06d}/result.json')
        if path is None:
            raise ValueError('缺少OCR逐项结果')
        value = read_json(path)
        if value.get('worker_sha256') != sha256(source / 'worker.py') or value.get(
            'manifest_sha256'
        ) != sha256(source / 'manifest.json'):
            raise ValueError('OCR结果与冻结工作程序或输入清单不符')
        reports.append(path)
    return reports


def publish_text(plan, reports, folder, backend):
    expected = {row['id']: row for row in plan['requests']}
    records, used, empty, evidence_files = [], set(), [], []
    imported = dict(plan['inventory'])
    imported['pages'] = [dict(p) for p in imported['pages']]
    for path in reports:
        value = read_json(path)
        item = value.get('item', {})
        requested = expected.get(item.get('id'))
        if (
            value.get('schema') != SCHEMA
            or value.get('status') != 'machine_read_only'
            or requested is None
            or item.get('sha256') != requested['expected_sha256']
            or item.get('id') in used
        ):
            raise ValueError('OCR结果不属于本批唯一源文件')
        if sha256(Path(requested['path'])) != requested['expected_sha256']:
            raise ValueError('回接前原件SHA已改变，不导入文字')
        used.add(item['id'])
        page_keys = []
        evidence_files.append({'path': str(path), 'sha256': sha256(path)})
        for page in value['pages']:
            number, angle = page['page'], page['rotation_clockwise']
            page_keys.append((number, angle))
            if number not in requested['pages'] or angle != plan['rotation']:
                raise ValueError('OCR页码或方向不属于本次请求')
            image = path.parent / page['image']
            if (
                image.is_symlink()
                or not image.resolve().is_relative_to(path.parent.resolve())
                or sha256(image) != page['image_sha256']
            ):
                raise ValueError('OCR实际识别图的字节证据不符')
            text = page['text']
            if not isinstance(text, str):
                raise ValueError('OCR文字格式不符')
            confidences = [line['confidence'] for line in page['lines']]
            if any(type(c) not in (int, float) or not 0 <= c <= 1 for c in confidences):
                raise ValueError('OCR置信度不合法')
            method = 'remote_ocr' if backend == 'rtime_compute' else 'local_ocr'
            record = {
                'source_sha256': item['sha256'],
                'page': number,
                'method': method,
                'text': text,
                'mean_confidence': 100 * sum(confidences) / len(confidences)
                if confidences
                else None,
                'evidence_path': str(path),
                'evidence_sha256': sha256(path),
                'rotation_clockwise': angle,
                'image_sha256': page['image_sha256'],
                'lines': page['lines'],
            }
            if text.strip():
                records.append(record)
            else:
                empty.append({'source_sha256': item['sha256'], 'page': number})
            matches = [
                p
                for p in imported['pages']
                if p['sha256'] == item['sha256'] and p['page'] == number
            ]
            if not matches:
                from material_relations import fields

                originals = [
                    row
                    for row in imported['files']
                    if row['sha256'] == item['sha256'] and row['kind'] != 'archive'
                ]
                if not originals or any(row['kind'] == 'pdf' for row in originals):
                    raise ValueError('回接页不在原材料清单中，不能补造PDF页')
                matches = [
                    {
                        'page_id': row['occurrence_id'] + f':p{number}',
                        'occurrence_id': row['occurrence_id'],
                        'source_chain': row['source_chain'],
                        'root_source': row['root_source'],
                        'sha256': item['sha256'],
                        'page': number,
                        'native_text': '',
                        **fields(''),
                    }
                    for row in originals
                ]
                imported['pages'].extend(matches)
            for match in matches:
                if match.get('native_text', '').strip():
                    raise ValueError('不覆盖已有原生文字页')
                match.update(ocr_text=text, ocr_evidence=record, verification='machine_read_only')
        if sorted(page_keys) != sorted((p, plan['rotation']) for p in requested['pages']):
            raise ValueError('OCR页集合不完整或重复')
    if used != set(expected):
        raise ValueError('OCR源文件集合不完整')
    evidence = {
        'schema': 'ustc-invoice-page-text-v1',
        'case_id': plan['case_id'],
        'records': records,
    }
    evidence_path = folder / 'text-evidence.json'
    inventory_path = folder / 'material_inventory_ocr.json'
    atomic_json(evidence_path, evidence)
    atomic_json(inventory_path, imported)
    from invoice_review import analyze

    sources = list(dict.fromkeys(row['path'] for row in plan['requests']))
    review = analyze(sources, plan['case_id'], text_evidence=evidence_path)
    review_path = folder / 'invoice-review.json'
    atomic_json(review_path, review)
    result = {
        'ok': True,
        'state': 'completed',
        'case_id': plan['case_id'],
        'backend': backend,
        'ocr_pages': sum(len(r['pages']) for r in plan['requests']),
        'text_records': len(records),
        'empty_pages': empty,
        'text_evidence': str(evidence_path),
        'inventory': str(inventory_path),
        'review': str(review_path),
        'raw_results': evidence_files,
        'source_intake': plan['source_intake'],
        'reused_text_pages': review['counts']['reused_text_pages'],
        'claim_amount': None,
        'machine_read_only': True,
        'system_write': False,
        'financial_write_authorized': False,
    }
    result['files'] = [
        {'name': p.name, 'path': str(p), 'sha256': sha256(p)}
        for p in (evidence_path, inventory_path, review_path)
    ]
    return result


def run(plan, runtime, root, *, execute=False, resume=False):
    profile = runtime['profile']
    backend = profile['backend']
    key = fingerprint(plan, runtime)
    root = Path(root)
    folder = root / key[:24]
    if not plan['requests']:
        return {
            'ok': True,
            'state': 'no_ocr_needed',
            'case_id': plan['case_id'],
            'ocr_pages': 0,
            'source_intake': plan['source_intake'],
            'system_write': False,
            'ocr_executed': False,
        }
    if not execute:
        return {
            'ok': True,
            'state': 'ocr_preview',
            'case_id': plan['case_id'],
            'backend': backend,
            'candidate_count': len(plan['requests']),
            'pages': sum(len(r['pages']) for r in plan['requests']),
            'system_write': False,
            'ocr_executed': False,
            'plan_fingerprint': key,
            'can_execute': backend != 'unconfigured',
        }
    for request in plan['requests']:
        if sha256(Path(request['path'])) != request['expected_sha256']:
            raise ValueError('识别前原件SHA变化')
    state_path = folder / 'state.json'
    if resume and not state_path.is_file():
        raise ValueError('原OCR工作目录缺失，恢复不能创建新计算')
    folder.mkdir(parents=True, exist_ok=True)
    state = read_json(state_path) if state_path.exists() else {'fingerprint': key, 'phase': 'new'}
    if state['fingerprint'] != key:
        raise ValueError('原OCR任务身份不同')
    if state['phase'] == 'completed':
        result = read_json(folder / 'result.json')
        for item in result['files'] + result['raw_results']:
            if sha256(Path(item['path'])) != item['sha256']:
                raise ValueError('既有OCR结果字节变化，不重算或覆盖')
        for item in result['raw_results']:
            path = Path(item['path'])
            for page in read_json(path)['pages']:
                image = path.parent / page['image']
                if (
                    image.is_symlink()
                    or not image.resolve().is_relative_to(path.parent.resolve())
                    or sha256(image) != page['image_sha256']
                ):
                    raise ValueError('原OCR识别图改变，不复用或重新识别')
        return {**result, 'reused_completed_run': True, 'workflow_dir': str(folder)}
    request_path = folder / 'request.json'
    request_rows = [
        {k: v for k, v in row.items() if k != 'expected_sha256'} for row in plan['requests']
    ]
    if state['phase'] == 'new':
        if resume:
            raise ValueError('原OCR工作目录缺失，恢复不能创建新计算')
        atomic_json(request_path, request_rows)
        atomic_json(folder / 'plan.json', plan)
        state.update(phase='prepared')
        atomic_json(state_path, state)
    try:
        if backend == 'rtime_compute':
            source, destination, collected = (
                folder / 'source',
                folder / 'run',
                folder / 'collection',
            )
            if state['phase'] == 'prepared':
                if not Path(profile['compute_cli']).is_file() or shutil.which('git') is None:
                    raise ValueError('远程OCR缺少已配置计算入口或Git；未派发任务')
                if (
                    sha256(Path(__file__).with_name('remote_material_ocr.py'))
                    != plan['worker_sha256']
                ):
                    raise ValueError('原OCR工作程序版本已变化，不能重新派发')
                prepare(
                    request_path,
                    source,
                    profile['server_python'],
                    profile['target'],
                    profile['dpi'],
                    profile['concurrency'],
                    profile['probe_sha256'],
                )
                for command in (
                    ['git', 'init', '-b', 'evidence'],
                    ['git', 'add', '--', 'worker.py', 'manifest.json', 'rtime-compute.json'],
                    [
                        'git',
                        '-c',
                        'user.name=OCR toolkit',
                        '-c',
                        'user.email=toolkit@local',
                        'commit',
                        '-m',
                        '冻结OCR源码与输入合同',
                    ],
                ):
                    process = subprocess.run(command, cwd=source, capture_output=True, text=True)
                    if process.returncode:
                        raise ValueError('OCR任务源码基线未完成：' + process.stderr)
                state.update(phase='dispatching', run_dir=str(destination))
                atomic_json(state_path, state)
                command = [
                    profile['compute_cli'],
                    'compute',
                    'run',
                    str(source),
                    '--target',
                    profile['target'],
                    '--mode',
                    'exploratory',
                    '--apply',
                    '--destination',
                    str(destination),
                    '--results-to',
                    str(collected),
                    '--wait-timeout',
                    str(profile['wait_seconds']),
                ]
            else:
                if not resume or not (destination / 'run.json').is_file():
                    raise ValueError('原OCR调度结果待核；只按原任务恢复，不重新提交')
                command = [
                    profile['compute_cli'],
                    'compute',
                    'wait',
                    str(destination),
                    '--timeout',
                    str(profile['wait_seconds']),
                    '--destination',
                    str(collected),
                ]
            if (
                sys.platform == 'win32'
                and command[0].lower().endswith(('.cmd', '.bat'))
                and any(
                    any(character in argument for character in '&|<>^%!()"\r\n\x00')
                    for argument in command
                )
            ):
                raise ValueError('Windows计算批处理路径含不支持的字符；保留原任务，不交给shell展开')
            process = subprocess.run(
                command, capture_output=True, text=True, encoding='utf-8', errors='replace'
            )
            atomic_json(
                folder / ('compute-' + str(time.time_ns()) + '.json'),
                {
                    'command': command,
                    'returncode': process.returncode,
                    'stdout': process.stdout,
                    'stderr': process.stderr,
                },
            )
            summary = json.loads(process.stdout)
            if summary.get('status') != 'completed':
                if summary.get('wait', {}).get('state') in {'failed', 'cancelled', 'canceled'}:
                    state.update(phase='failed_original_remote')
                    atomic_json(state_path, state)
                    return {
                        'ok': False,
                        'state': 'failed_original_remote',
                        'workflow_dir': str(folder),
                        'run_dir': str(destination),
                        'remote_summary': summary,
                        'backend': backend,
                        'error': '原远程任务已明确失败，读取原日志；不自动重投',
                        'system_write': False,
                    }
                state.update(phase='remote_pending')
                atomic_json(state_path, state)
                return {
                    'ok': True,
                    'state': 'waiting_original_remote',
                    'workflow_dir': str(folder),
                    'run_dir': str(destination),
                    'remote_summary': summary,
                    'backend': backend,
                    'system_write': False,
                    'ocr_completed': False,
                }
            reports = verify_remote_collection(summary, plan, profile, source)
        else:
            if state['phase'] != 'prepared' or profile.get('local_execution_enabled') is not True:
                raise ValueError('本机原OCR任务待核或未显式启用，不重复识别')
            if sha256(Path(__file__).with_name('remote_material_ocr.py')) != plan['worker_sha256']:
                raise ValueError('原本机OCR工作程序版本变化，不重放')
            probe = subprocess.run(
                [sys.executable, '-I', '-c', PROBE_CODE], capture_output=True, check=True
            )
            if hashlib.sha256(probe.stdout).hexdigest() != profile['probe_sha256']:
                raise ValueError('本机OCR环境已变化，先核对安装')
            rows = normalize_requests(request_rows)
            manifest = {'schema': SCHEMA, 'items': rows, 'dpi': profile['dpi']}
            manifest_path = folder / 'manifest.json'
            atomic_json(manifest_path, manifest)
            state['phase'] = 'local_running'
            atomic_json(state_path, state)
            reports = []
            for index, row in enumerate(rows):
                output = folder / f'local/task-{index:06d}'
                recognize(
                    manifest,
                    row,
                    Path(row['source_path']),
                    output,
                    'local-' + key[:24],
                    2,
                    manifest_sha256=sha256(manifest_path),
                    execution_site='local',
                )
                reports.append(output / 'result.json')
        result = publish_text(plan, reports, folder, backend)
        result.update(workflow_dir=str(folder), plan_fingerprint=key)
        atomic_json(folder / 'result.json', result)
        state['phase'] = 'completed'
        atomic_json(state_path, state)
        return result
    except Exception as error:
        atomic_json(
            folder / ('failure-' + str(time.time_ns()) + '.json'),
            {'phase': state['phase'], 'error': str(error)},
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        result = run(
            read_json(args.plan),
            read_json(args.runtime),
            args.work_root,
            execute=args.execute,
            resume=args.resume,
        )
    except Exception as error:
        result = {
            'ok': False,
            'state': 'original_ocr_needs_inspection',
            'error': str(error),
            'system_write': False,
        }
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
