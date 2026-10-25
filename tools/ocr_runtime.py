"""OCR提供方配置与就绪度；默认不启用本机识别，不探测浏览器。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from assistant_contract import APIError
from workspace_config import runtime_python

SCHEMA = 'ustc-ocr-provider-v1'
EXPECTED_MODELS = {
    'ch_PP-OCRv4_det_infer.onnx': 'd2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9',
    'ch_PP-OCRv4_rec_infer.onnx': '48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b',
    'ch_ppocr_mobile_v2.0_cls_infer.onnx': 'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c',
}


def read_profile(root):
    path = Path(root) / '.local/ocr-provider.json'
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 16384:
        raise APIError('ocr_profile', 'OCR配置不能是链接或超过16KiB')
    raw = path.read_bytes()
    value = json.loads(raw)
    common = {'schema', 'backend', 'probe_sha256', 'dpi', 'max_pages', 'wait_seconds'}
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('backend') not in {'rtime_compute', 'local'}:
        raise APIError('ocr_profile', 'OCR提供方须为已支持的远程或显式本机模式')
    extra = (
        {'target', 'server_python', 'compute_cli', 'concurrency'}
        if value['backend'] == 'rtime_compute'
        else {'local_execution_enabled'}
    )
    if set(value) != common | extra:
        raise APIError('ocr_profile', 'OCR配置字段不完整或含未知项')
    if not re.fullmatch(r'[a-f0-9]{64}', value.get('probe_sha256', '')):
        raise APIError('ocr_probe', '须冻结实际环境探针SHA，不能用口头就绪代替')
    for key, low, high in [('dpi', 72, 400), ('max_pages', 1, 1000), ('wait_seconds', 1, 180)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise APIError('ocr_limits', 'OCR资源边界无效：' + key)
    if value['backend'] == 'local':
        if value['local_execution_enabled'] is not True or value['max_pages'] > 20:
            raise APIError('ocr_local_not_enabled', '本机OCR须显式启用，单任务最多20页')
    else:
        from remote_material_ocr import server_python_path

        server_python_path(value['server_python'])
        if (
            value['target'] != 'master'
            or type(value['concurrency']) is not int
            or not 1 <= value['concurrency'] <= 8
        ):
            raise APIError('ocr_target', '远程提供方当前只接已核验master及1—8并发')
        if (
            not isinstance(value['compute_cli'], str)
            or not Path(value['compute_cli']).is_absolute()
        ):
            raise APIError('ocr_compute_cli', '计算入口须是本机明确的绝对路径')
    return {'profile': value, 'sha256': hashlib.sha256(raw).hexdigest(), 'path': str(path)}


def probe_local(root):
    from remote_material_ocr import PROBE_CODE

    python = runtime_python(root)
    if not python.is_file():
        python = Path(sys.executable)
    process = subprocess.run([str(python), '-I', '-c', PROBE_CODE], capture_output=True, timeout=30)
    if process.returncode:
        raise APIError(
            'ocr_dependencies_missing', 'OCR依赖尚未完整安装；按指南显式启用本机OCR或配置远程提供方'
        )
    environment = json.loads(process.stdout)
    if {m['name']: m['sha256'] for m in environment.get('models', [])} != EXPECTED_MODELS:
        raise APIError('ocr_models_changed', '锁定OCR模型缺失或SHA变化，不能重新签名放行')
    return {
        'probe_sha256': hashlib.sha256(process.stdout).hexdigest(),
        'environment': environment,
    }


def status(root):
    saved = read_profile(root)
    result = {
        'ok': True,
        'configured': saved is not None,
        'system_write': False,
        'ocr_executed': False,
        'remote_connection_checked': False,
    }
    if saved is None:
        return {
            **result,
            'ready_for_request': False,
            'backend': None,
            'needed_inputs': ['OCR提供方配置'],
            'next': '沿guide ocr选择远程提供方；新用户也可显式安装--with-ocr',
        }
    profile = saved['profile']
    ready = True
    issue = None
    if profile['backend'] == 'local':
        try:
            ready = probe_local(root)['probe_sha256'] == profile['probe_sha256']
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            ready = False
            issue = str(error)
    else:
        ready = Path(profile['compute_cli']).is_file() and shutil.which('git') is not None
    return {
        **result,
        'ready_for_request': ready,
        'backend': profile['backend'],
        'issue': issue,
        'profile_sha256': saved['sha256'],
        'max_pages': profile['max_pages'],
        'note': '就绪度不是识别或审核结果；远程连接与环境在实际任务时核验',
    }


def configure_local(root):
    """只能由显式--with-ocr安装分支调用；不覆盖已有远程配置。"""
    from assistant_api import atomic_json

    old = read_profile(root)
    if old and old['profile']['backend'] != 'local':
        raise APIError('ocr_provider_conflict', '已有远程OCR配置，不自动改成本机执行')
    probe = probe_local(root)
    profile = {
        'schema': SCHEMA,
        'backend': 'local',
        'local_execution_enabled': True,
        'probe_sha256': probe['probe_sha256'],
        'dpi': 250,
        'max_pages': 20,
        'wait_seconds': 180,
    }
    atomic_json(Path(root) / '.local/ocr-provider.json', profile)
    return status(root)
