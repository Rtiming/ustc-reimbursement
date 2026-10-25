"""登记资料与内容指纹的唯一入口；不连接浏览器或推进业务。"""

from __future__ import annotations

import hashlib
import re

from assistant_api import APIError, read_json
from batch_workflow import _json_path


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
    path = checked_reference(service, ref)
    cfg = read_json(path)
    if cfg.get('schema') != 'ustc-registered-workflow-v1' or cfg.get('case_id') != case_id:
        raise APIError('registered_workflow_scope', '运行资料须绑定本案')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}', cfg.get('expected_userid') or ''):
        raise APIError('registered_workflow_user', '本案须明确本机财务登录人')
    pointer_path = _json_path(service, cfg['current_input_pointer'])
    pointer = read_json(pointer_path)
    index = checked_reference(
        service, {'path': pointer['current_30_index'], 'sha256': pointer['current_30_index_sha256']}
    )
    auth = checked_reference(service, cfg['preparation_authorization'])
    return cfg, index, auth, path
