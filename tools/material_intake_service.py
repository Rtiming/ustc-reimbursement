"""材料任务的授权根、受信任依赖及job回执接线；不接浏览器。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from assistant_api import APIError, PROJECT, atomic_json, read_json
from material_intake import IntakeError, dependency, normalize, reject_links

RUNTIME_SCHEMA = 'reimbursement.material-intake-runtime.v1'
ARTIFACT_NAMES = ('intake_manifest.json', 'material_inventory.json', 'groups.json',
                  'ocr_request_candidates.json', 'issues.json')


def configure_bundled_archive(workspace, *, package_root=PROJECT, check_only=False):
    """分发包固定依赖按已发布SHA接入本机；不替换用户已有的不同依赖。"""
    package_root, workspace = Path(package_root), Path(workspace)
    metadata = package_root / 'distribution/dependencies.json'
    config = read_json(metadata)['archive_helper']
    if config.get('path') != 'tools/material_archive.py':
        raise APIError('archive_dependency_path', '分发归档依赖位置不同，先核对版本')
    helper_path = package_root.resolve() / config['path']
    reject_links(helper_path)
    helper = dependency({'path': str(helper_path), 'sha256': config['sha256']})
    runtime = {'schema': RUNTIME_SCHEMA, 'archive_helper': {'path': helper['path'], 'sha256': helper['sha256']}}
    target = workspace / '.local/material_intake.json'
    reject_links(target)
    if target.exists() and read_json(target) != runtime:
        raise APIError('archive_dependency_conflict', '本机已有不同归档配置，保留原配置，不自动替换')
    if not check_only and not target.exists():
        atomic_json(target, runtime)
    return {'configured': target.exists(), 'path': str(target), 'archive_helper_sha256': helper['sha256']}


def _authorized(service, value):
    path = Path(value)
    if not path.is_absolute():
        raise APIError('material_root', '材料任务只接受已授权根内的绝对路径')
    try:
        reject_links(path)
    except IntakeError as error:
        raise APIError(error.code, str(error)) from error
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in service.material_roots):
        raise APIError('material_root', '来源、人工复核和输出目录均须位于启动时授权的material-root内')
    return str(resolved)


def validate_request(service, case_id, request):
    """只校验业务结构和授权路径；实际盘点在异步worker中执行。"""
    service.case_id(case_id)
    try:
        normalized = normalize(copy.deepcopy(request))
    except IntakeError as error:
        raise APIError(error.code, str(error)) from error
    if normalized['case_id'] != case_id:
        raise APIError('material_intake_case_mismatch', 'request.case_id必须与本机材料任务登记case_id一致')
    normalized['output_dir'] = _authorized(service, normalized['output_dir'])
    for source in normalized['sources']:
        source['path'] = _authorized(service, source['path'])
        source.setdefault('expected_sha256', None)
    normalized.setdefault('field_review', None)
    if normalized['field_review'] is not None:
        normalized['field_review']['path'] = _authorized(service, normalized['field_review']['path'])
    return normalized


def trusted_runtime(service):
    path = service.root / '.local/material_intake.json'
    try:
        reject_links(path)
        if not path.is_file():
            raise APIError('material_intake_setup_required', '本机尚未登记材料归档依赖：.local/material_intake.json')
        if path.stat().st_size > 16 * 1024:
            raise APIError('material_intake_runtime', '材料运行配置超过支持大小')
        raw = path.read_bytes()
        config = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(config, dict) or set(config) != {'schema', 'archive_helper'} or config['schema'] != RUNTIME_SCHEMA:
            raise APIError('material_intake_runtime', '本机材料运行配置schema或字段不符')
        helper = dependency(config['archive_helper'])
        if helper is None:
            raise APIError('material_intake_setup_required', '本机归档依赖未登记')
    except IntakeError as error:
        raise APIError(error.code, str(error)) from error
    return {'schema': RUNTIME_SCHEMA, 'config_sha256': hashlib.sha256(raw).hexdigest(), 'archive_helper': helper}


def _freeze_json(path, value):
    """command在入队前和worker内各调用一次，复用同字节绑定而非悄悄换配置。"""
    if path.exists():
        reject_links(path)
        if read_json(path) != value:
            raise APIError('material_intake_runtime_changed', '原任务输入或受信任运行配置已变；保留原job检查，不重放')
    else:
        atomic_json(path, value)


def command(service, job_request, folder):
    parameters = job_request['parameters']
    if set(parameters) != {'request'}:
        raise APIError('unknown_operation', '材料任务只接受request业务对象；工具依赖不对调用者开放')
    business = validate_request(service, job_request['case_id'], parameters['request'])
    runtime = trusted_runtime(service)
    folder = Path(folder)
    _freeze_json(folder / 'material_intake_request.json', business)
    _freeze_json(folder / 'material_intake_runtime.json', runtime)
    helper = runtime['archive_helper']
    return [service.python, str(PROJECT / 'tools/material_intake.py'),
            '--request', str(folder / 'material_intake_request.json'),
            '--archive-helper', helper['path'], '--archive-helper-sha256', helper['sha256']] + (
                ['--execute'] if job_request['execute'] else [])


def collect_artifacts(service, job_request, folder, value):
    """只快照原任务固定JSON产物到job内，以复用既有artifact的同案SHA守卫。"""
    if not isinstance(value, dict) or type(value.get('ok')) is not bool:
        raise APIError('material_intake_result_missing', '材料工具没有返回完整JSON结果；检查原日志，不重放')
    folder = Path(folder)
    candidates = [folder / 'output.json']

    def save_output():
        target = folder / 'output.json'
        if target.exists():
            if read_json(target) != value:
                raise APIError('material_intake_artifact_changed', '原job结果已存在不同内容，不覆盖')
        else:
            atomic_json(target, value)

    if not job_request['execute'] or not value.get('receipt'):
        save_output()
        return candidates
    business = validate_request(service, job_request['case_id'], job_request['parameters']['request'])
    output = Path(business['output_dir'])
    expected = output / 'receipt.json'
    if Path(value['receipt']).resolve() != expected.resolve() or Path(value.get('output_dir', '')).resolve() != output:
        raise APIError('material_intake_artifact_scope', '材料回执不属于本任务声明输出目录')
    reject_links(expected)
    receipt_bytes = expected.read_bytes()
    receipt = json.loads(receipt_bytes.decode('utf-8'))
    if receipt.get('schema') != 'reimbursement.material-intake-receipt.v1' or receipt.get('case_id') != job_request['case_id']:
        raise APIError('material_intake_artifact_scope', '归档回执案件或schema不符')
    frozen_artifacts = []
    for artifact in receipt.get('artifacts', []):
        name = artifact.get('path')
        if name not in ARTIFACT_NAMES:
            raise APIError('material_intake_artifact_scope', '归档工具声明了不在固定目录内的产物')
        source = output / name
        reject_links(source)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.get('sha256'):
            raise APIError('material_intake_artifact_changed', '材料产物SHA已变，不能登记为原任务结果')
        frozen_artifacts.append((name, raw))
    save_output()
    snapshot = folder / 'material_intake_artifacts'
    snapshot.mkdir(exist_ok=True)

    def save(name, raw):
        target = snapshot / name
        if target.exists() and target.read_bytes() != raw:
            raise APIError('material_intake_artifact_changed', 'job内原材料快照已改变，不覆盖')
        if not target.exists():
            with target.open('xb') as stream:
                stream.write(raw)
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(raw).digest():
            raise APIError('material_intake_artifact_changed', 'job内材料快照回读失败')
        candidates.append(target)

    save('receipt.json', receipt_bytes)
    for name, raw in frozen_artifacts:
        save(name, raw)
    return candidates


def result_summary(value):
    """普通result保持简短；完整来源与逐页结果通过artifact_id回读。"""
    allowed = ('ok', 'mode', 'schema', 'case_id', 'file_count', 'original_count', 'groups',
               'ocr_candidates', 'output_dir', 'receipt', 'error', 'source_selection_sha256', 'next_step')
    summary = {key: value[key] for key in allowed if key in value}
    if isinstance(value.get('budget'), dict):
        summary['budget'] = {key: value['budget'][key] for key in ('source_bytes', 'expanded_bytes', 'archive_members') if key in value['budget']}
    summary.update(system_write=False, ocr_started=False)
    return summary
