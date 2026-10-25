"""同案收件回执到OCR任务的固定接线；复用任务层，不开放路径或程序入口。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from assistant_api import APIError, PROJECT, read_json, canonical_request, request_fingerprint
from material_intake_service import _authorized, _freeze_json
from ocr_runtime import read_profile


def input_plan(service, case_id, request):
    if not isinstance(request, dict) or set(request) - {
        'intake_job_id',
        'candidate_ids',
        'rotation',
    }:
        raise APIError('ocr_request', 'OCR请求只接受本案收件job、候选ID及方向')
    job_id = request.get('intake_job_id')
    job = service.result(case_id, job_id)
    source_request = read_json(service.job_folder(case_id, job_id) / 'request.json')
    if (
        source_request.get('operation') != 'material_intake'
        or source_request.get('execute') is not True
        or job.get('status') != 'completed'
        or job.get('exit_code') != 0
    ):
        raise APIError('ocr_intake_source', '须引用本案已完成的真实收件任务')
    files = {}
    for name in ('material_inventory.json', 'ocr_request_candidates.json'):
        rows = [r for r in job['artifacts'] if r['name'] == name]
        if len(rows) != 1:
            raise APIError('ocr_intake_artifact', '收件任务缺少唯一候选或清单回执')
        row = rows[0]
        path = service.artifact(case_id, job_id, row['artifact_id'])
        files[name] = {'path': path, 'sha256': row['sha256'], 'artifact_id': row['artifact_id']}
    inventory = read_json(files['material_inventory.json']['path'])
    candidates = read_json(files['ocr_request_candidates.json']['path'])
    ids = request.get('candidate_ids')
    if ids is not None and (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(x, str) for x in ids)
        or len(set(ids)) != len(ids)
    ):
        raise APIError('ocr_selection', 'candidate_ids须为唯一非空列表')
    if ids is not None and set(ids) - {r['id'] for r in candidates}:
        raise APIError('ocr_selection', '候选ID不属于本案收件范围')
    rotation = request.get('rotation', 0)
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise APIError('ocr_rotation', '方向须为0/90/180/270')
    from material_intake import ocr_candidates

    allowed = {r['id']: r for r in ocr_candidates(inventory)}
    rows = []
    for candidate in candidates:
        if ids is not None and candidate['id'] not in ids:
            continue
        if candidate != allowed.get(candidate['id']):
            raise APIError('ocr_candidate_drift', '候选与冻结材料清单不一致')
        path = Path(_authorized(service, candidate['path']))
        matching = [r for r in inventory['files'] if r.get('working_copy') == str(path)]
        if not matching or len({r['sha256'] for r in matching}) != 1:
            raise APIError('ocr_source_binding', '源文件不能唯一绑定原件SHA')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != matching[0]['sha256']:
            raise APIError('ocr_source_changed', 'OCR前原件工作副本SHA改变')
        # 长扫描件按固定页段分任务，复用同一输入CAS，避免单项超过远程时限。
        pages = candidate['pages']
        for offset in range(0, len(pages), 8):
            identifier = (
                candidate['id'] if len(pages) <= 8 else candidate['id'] + f'-part{offset // 8:03d}'
            )
            rows.append(
                {
                    **candidate,
                    'id': identifier,
                    'path': str(path),
                    'pages': pages[offset : offset + 8],
                    'rotate': [rotation],
                    'expected_sha256': digest,
                }
            )
    return {
        'schema': 'ustc-material-ocr-plan-v1',
        'case_id': case_id,
        'requests': rows,
        'rotation': rotation,
        'worker_sha256': hashlib.sha256(
            (PROJECT / 'tools/remote_material_ocr.py').read_bytes()
        ).hexdigest(),
        'inventory': inventory,
        'source_intake': {
            'case_id': case_id,
            'job_id': job_id,
            'artifacts': {
                name: {'sha256': value['sha256'], 'artifact_id': value['artifact_id']}
                for name, value in files.items()
            },
        },
    }


def command(service, job_request, folder):
    case = job_request['case_id']
    params = job_request['parameters']
    folder = Path(folder)
    resume = params.get('action') == 'resume'
    if resume:
        if set(params) != {'action', 'ocr_job_id'}:
            raise APIError('ocr_resume', '恢复只接受原OCR job')
        old_folder = service.job_folder(case, params['ocr_job_id'])
        old = read_json(old_folder / 'request.json')
        if (
            old.get('operation') != 'material_ocr'
            or old.get('execute') is not True
            or old['parameters'].get('action') != 'run'
        ):
            raise APIError('ocr_resume', '只恢复本案原始OCR执行任务，不恢复预览或其他能力')
        if old.get('case_id') != case or old.get('fingerprint') != request_fingerprint(
            canonical_request(old['operation'], old['parameters'], old['execute'])
        ):
            raise APIError('ocr_resume_request', '原OCR请求身份或指纹改变')
        original_status = service.result(case, params['ocr_job_id'])
        verified = set()
        for name in ('ocr-plan.json', 'ocr-runtime.json'):
            found = [a for a in original_status.get('artifacts', []) if a['name'] == name]
            if len(found) == 1:
                service.artifact(case, params['ocr_job_id'], found[0]['artifact_id'])
                verified.add(name)
            elif (old_folder / name).is_symlink():
                raise APIError('ocr_resume_source', '原OCR冻结文件不能是链接')
        plan = input_plan(service, case, old['parameters']['request'])
        runtime = read_json(old_folder / 'ocr-runtime.json')
        if 'ocr-runtime.json' not in verified and runtime != read_profile(service.root):
            raise APIError('ocr_resume_runtime', '未注册的原OCR配置与当前已核提供方不同，保留现场')
        original_plan = read_json(old_folder / 'ocr-plan.json')
        # 接续只等/读原运行，沿原冻结worker身份，不因工具升级创建新计算。
        plan['worker_sha256'] = original_plan['worker_sha256']
        if plan != original_plan:
            raise APIError('ocr_resume_source', '原OCR的输入范围变化，保留原任务')
    else:
        if set(params) != {'action', 'request'} or params['action'] != 'run':
            raise APIError('ocr_request', '固定动作须为run或resume')
        plan = input_plan(service, case, params['request'])
        runtime = (
            read_profile(service.root)
            if plan['requests']
            else {'profile': {'backend': 'none', 'max_pages': 0}}
        )
    if runtime is None:
        if job_request['execute']:
            raise APIError('ocr_setup_required', 'OCR提供方未配置；用ocr_status读取具体下一步')
        runtime = {'profile': {'backend': 'unconfigured', 'max_pages': 1000}}
    if sum(len(r['pages']) for r in plan['requests']) > runtime['profile']['max_pages']:
        raise APIError('ocr_page_budget', '候选超过本机配置的页数上限；按明确候选ID分批')
    _freeze_json(folder / 'ocr-plan.json', plan)
    _freeze_json(folder / 'ocr-runtime.json', runtime)
    python = Path(service.python)
    cmd = [
        str(python),
        str(PROJECT / 'tools/ocr_workflow.py'),
        '--plan',
        str(folder / 'ocr-plan.json'),
        '--runtime',
        str(folder / 'ocr-runtime.json'),
        '--work-root',
        str(service.root / '.local/ocr-workflows' / case),
        '--output',
        str(folder / 'output.json'),
    ]
    if job_request['execute']:
        cmd.append('--execute')
    if resume:
        cmd.append('--resume')
    return cmd


def collect_artifacts(service, request, folder, value):
    if not isinstance(value, dict) or type(value.get('ok')) is not bool:
        raise APIError('ocr_result', 'OCR任务未返回完整结果；按原job恢复')
    folder = Path(folder)
    result = [folder / 'ocr-plan.json', folder / 'ocr-runtime.json', folder / 'output.json']
    if value.get('state') == 'completed':
        snapshot = folder / 'ocr-artifacts'
        snapshot.mkdir(exist_ok=True)
        root = service.root / '.local/ocr-workflows' / request['case_id']
        for item in value['files']:
            source = Path(item['path'])
            if (
                source.is_symlink()
                or not source.resolve().is_relative_to(root.resolve())
                or source.name != item['name']
            ):
                raise APIError('ocr_output_scope', 'OCR结果不属于本案工作流')
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise APIError('ocr_output_changed', 'OCR产物SHA改变')
            target = snapshot / source.name
            if target.exists() and target.read_bytes() != raw:
                raise APIError('ocr_output_changed', '原OCR快照已变化，不覆盖')
            if not target.exists():
                target.write_bytes(raw)
            result.append(target)
    return result


def result_summary(value):
    return {
        key: value.get(key)
        for key in (
            'ok',
            'state',
            'case_id',
            'backend',
            'ocr_pages',
            'candidate_count',
            'pages',
            'can_execute',
            'text_records',
            'empty_pages',
            'reused_text_pages',
            'reused_completed_run',
            'workflow_dir',
            'run_dir',
            'error',
            'machine_read_only',
            'system_write',
        )
        if key in value
    }
