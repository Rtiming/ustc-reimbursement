"""Windows Edge Native Messaging适配器；只使用本机已授权扩展，不启动浏览器。"""
import hashlib
import json
import math
import re
import os
from pathlib import Path
import sys
import time
import uuid

from platform_runtime import private_path, is_private_path, process_alive

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.local' / 'edge-bridge'
HOST_NAME = 'cn.edu.ustc.reimbursement'
MAX_REQUEST = 800_000


class BridgeUnavailable(RuntimeError):
    code = 'windows_bridge_unavailable'


def atomic_private(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    private_path(path.parent)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            private_path(temporary)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # 同卷硬链接的目标排他创建，不能覆盖另一个请求/意图。
            os.link(temporary, path)
            temporary.unlink()
        else:
            os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def status(*, state=None):
    directory = STATE if state is None else Path(state)
    path = directory / 'status.json'
    if not path.is_file():
        return {'ready': False, 'reason': 'extension_not_connected', 'system_write': False}
    if not is_private_path(path) or not is_private_path(directory):
        raise BridgeUnavailable('Windows财务桥运行目录权限不符；未读取或派发浏览器动作')
    value = json.loads(path.read_text(encoding='utf-8'))
    alive = process_alive(value.get('host_pid'))
    installed = json.loads((directory/'installation.json').read_text(encoding='utf-8'))
    identity = value.get('build_sha256') == installed.get('build_sha256') and value.get('extension_id') == installed.get('extension_id')
    ready = identity and value.get('connected') is True and alive is True and 0 <= time.time() - value.get('heartbeat', 0) < 60
    return {**value, 'ready': ready, 'reason': None if ready else 'extension_build_mismatch' if not identity else 'extension_disconnected', 'system_write': False}


RESCUE_OPERATIONS = frozenset({'ping', 'tab-close', 'reload', 'page-read'})


def request(operation, arguments=None, *, timeout=20, state=None, request_id=None):
    if operation not in {'inventory', 'evaluate', 'validate-tab', 'create-page', 'tab-origin', 'smoke',
                         'ping', 'tab-close', 'reload', 'page-read','create-finance-window','wake-finance-tab','relocate-finance-tab','show-verification'}:
        raise ValueError('未支持的Windows财务桥操作')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 20:
        raise ValueError('单次传输timeout须为0至20秒之间的有限正数')
    directory = STATE if state is None else Path(state)
    current = status() if state is None else status(state=directory)
    if not current['ready']:
        raise BridgeUnavailable('Windows财务扩展未连接；先安装/启用本项目扩展，工具不会启动浏览器或切换前台')
    if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id)):
        raise ValueError('原生请求ID须为32位小写十六进制')
    request_id = request_id or uuid.uuid4().hex
    request_path = directory / 'requests' / (request_id + '.json')
    existing = request_path.exists()
    if current.get('active_request_id') and operation not in RESCUE_OPERATIONS and not existing:
        raise BridgeUnavailable('Windows财务桥仍在等待原请求；不排队重放：' + str(current['active_request_id']))
    if current.get('synthetic_profile') and operation not in {'inventory', 'smoke'}:
        raise BridgeUnavailable('合成测试profile不接受真实财务动作；使用本机本人已登录的浏览器')
    packet = {'schema': 'ustc-native-edge-v1', 'id': request_id, 'epoch': current['epoch'],
              'operation': operation, 'arguments': arguments or {}, 'created_at': time.time(),
              'expires_at': time.time() + timeout}
    if len(json.dumps(packet, ensure_ascii=False).encode('utf-8')) > MAX_REQUEST:
        raise ValueError('单次浏览器脚本超过Native Messaging预算；未派发，不截断执行')
    if existing:
        if not is_private_path(request_path):
            raise BridgeUnavailable('原生请求权限改变；保留原任务')
        original = json.loads(request_path.read_text(encoding='utf-8'))
        if any(original.get(k) != packet[k] for k in ('schema', 'id', 'epoch', 'operation', 'arguments')):
            raise BridgeUnavailable('原请求身份、连接或参数不同；不重新派发')
        # 只轮询原响应；不改原截止时间、不覆盖原意图，不再排入第二份请求。
    else:
        atomic_private(request_path, packet, exclusive=True)
    deadline = time.monotonic() + timeout
    output = directory / 'responses' / (request_id + '.json')
    while time.monotonic() < deadline:
        if output.exists():
            if not is_private_path(output):
                raise BridgeUnavailable('Windows财务桥响应权限不符；保留原请求')
            reply = json.loads(output.read_text(encoding='utf-8'))
            late = output.with_name(output.stem + '.late.json')
            if reply.get('error_code') == 'request_stalled' and late.exists():
                if not is_private_path(late):
                    raise BridgeUnavailable('原生迟到回执权限不符')
                reply = json.loads(late.read_text(encoding='utf-8'))
            if reply.get('id') != request_id or reply.get('epoch') != current['epoch']:
                raise BridgeUnavailable('Windows财务桥响应身份不一致；保留原请求')
            if reply.get('ok') is not True:
                # 扩展错误不能包含注入的源代码、登录密码或页面正文。
                raise BridgeUnavailable('Windows财务桥拒绝/失败：' + str(reply.get('error_code', 'native_error')))
            return reply['result']
        time.sleep(min(.04, max(0, deadline - time.monotonic())))
    raise TimeoutError('Windows财务桥timeout；原请求已保留，不重新派发写入：' + request_id)


def inventory():
    # SSH/AI客户端可能处于Session 0；桌面身份只能由浏览器原生主机观察。
    from native_finance_bridge import inventory as native_inventory
    return native_inventory()


def evaluate(source, tab_id=None, *, timeout=20, access=False):
    if os.environ.get('REIMBURSEMENT_EDGE_TAB_NAMESPACE'):
        from native_finance_bridge import evaluate as native_evaluate
        from finance_backend import native_target
        return native_evaluate(source, tab_id, timeout=timeout, access=access, **native_target())
    if tab_id is not None and (not isinstance(tab_id, (str, int)) or not str(tab_id).isdigit()):
        raise ValueError('须明确合法数字标签ID')
    result = request('evaluate', {'tab_id': str(tab_id) if tab_id is not None else None,
                                 'source': source, 'scope': 'access' if access else 'finance'}, timeout=timeout)
    if not isinstance(result, str):
        raise BridgeUnavailable('Windows财务桥返回值不是原生字符串')
    return result


def list_tabs(*, access=False):
    tabs = inventory()['tabs']
    return [t['tab_id'] for t in tabs if access or t['path'] == 'https://cwzh.ustc.edu.cn/YBX/main.jsp']


def validate_tab(tab_id, foreground=False):
    if foreground:
        raise BridgeUnavailable('既有标签常规校验不切前台；开页和PDF导出须走各自明确授权的开关')
    if os.environ.get('REIMBURSEMENT_EDGE_TAB_NAMESPACE'):
        from native_finance_bridge import validate_tab as native_validate
        from finance_backend import native_target
        return native_validate(tab_id, **native_target())
    return request('validate-tab', {'tab_id': str(tab_id)})
