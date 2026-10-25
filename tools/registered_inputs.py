"""登记资料与内容指纹的唯一入口；不连接浏览器或推进业务。"""

from __future__ import annotations

import hashlib
import json
import re

from assistant_api import APIError, read_json
from batch_workflow import _json_path


class WorkflowDefinition(dict):
    """配置值和开始读取时的内容身份一起冻结；属性不写回业务JSON。"""
    def __init__(self, value, reference, files):
        super().__init__(value)
        self._reference = dict(reference)
        self._files = tuple(dict(item) for item in files)

    @property
    def source_reference(self):
        return dict(self._reference)


def checked_data(service, ref):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise APIError('registered_reference', '登记引用须有path和sha256')
    path = _json_path(service, ref['path'])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise APIError('registered_source_changed', '登记资料SHA已改变，须使用新版本')
    return path, json.loads(raw)


def assert_definition_current(service, cfg):
    if not isinstance(cfg, WorkflowDefinition):
        return  # 内部合成夹具兼容；公开流程均由load_definition创建冻结对象。
    _, paths = service.resolve(cfg['case_id'])
    current = read_json(paths['case']).get('material_context', {}).get('workflow_entry')
    if current != cfg.source_reference:
        raise APIError('registered_definition_changed', '运行期间登记或授权已变更；保留原任务，不推进旧配置')
    for ref in cfg._files:
        checked_data(service, ref)


def checked_reference(service, ref):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise APIError('registered_reference', '登记引用须有path和sha256')
    path = _json_path(service, ref['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
        raise APIError('registered_source_changed', '登记资料SHA已改变，须发布本案新版本')
    return path


def load_definition(service, case_id):
    _, paths = service.resolve(case_id)
    ref = read_json(paths['case']).get('material_context', {}).get('workflow_entry')
    if not ref:
        raise APIError('registered_workflow_missing', '本案尚未登记一键工作流资料指针')
    path, cfg = checked_data(service, ref)
    files = [dict(ref)]
    if cfg.get('schema') != 'ustc-registered-workflow-v1' or cfg.get('case_id') != case_id:
        raise APIError('registered_workflow_scope', '运行资料须绑定本案')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}', cfg.get('expected_userid') or ''):
        raise APIError('registered_workflow_user', '本案须明确本机财务登录人')
    if cfg.get('batch_index'):
        index, _ = checked_data(service, cfg['batch_index'])
        files.append(dict(cfg['batch_index']))
    else:
        # 旧登记指针仅保留兼容；新用户由登记接口生成通用、冻结的batch_index引用。
        pointer_path = _json_path(service, cfg['current_input_pointer'])
        raw = pointer_path.read_bytes()
        pointer = json.loads(raw)
        files.append({'path': str(pointer_path), 'sha256': hashlib.sha256(raw).hexdigest()})
        index_ref = {'path': pointer['current_30_index'], 'sha256': pointer['current_30_index_sha256']}
        index, _ = checked_data(service, index_ref)
        files.append(index_ref)
    auth, _ = checked_data(service, cfg['preparation_authorization'])
    files.append(dict(cfg['preparation_authorization']))
    for name in ('approval_plans', 'approval_manifest', 'system_pdf_manifest', 'list_export_manifest'):
        if cfg.get(name):
            checked_data(service, cfg[name])
            files.append(dict(cfg[name]))
    return WorkflowDefinition(cfg, ref, files), index, auth, path
