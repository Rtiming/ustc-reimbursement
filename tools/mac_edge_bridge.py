"""Explicit macOS Native Messaging transport; no AppleEvents or fallback backend.

Tab IDs are Chromium IDs only when paired with this connection's namespace and
the observed window ID. A new host epoch requires a new explicit binding.
"""
import argparse
import json
from pathlib import Path
import re
import sys

import windows_edge_bridge as queue_bridge
from platform_runtime import is_private_path

LAST_PAGE_READ_STEPS = []
ROOT = queue_bridge.ROOT
STATE = queue_bridge.STATE
HOST_NAME = queue_bridge.HOST_NAME
BACKEND = 'edge_native_messaging'
ENTRY = 'https://cwzh.ustc.edu.cn/YBX/main.jsp'


class BridgeUnavailable(RuntimeError):
    code = 'mac_native_bridge_unavailable'


def _mac():
    if sys.platform != 'darwin':
        raise BridgeUnavailable('Mac Native Messaging仅支持macOS；未调用其他后端')


def _namespace(value):
    extension, epoch = value.get('extension_id'), value.get('epoch')
    if (not isinstance(extension, str) or not re.fullmatch(r'[a-p]{32}', extension)
            or not isinstance(epoch, str) or not re.fullmatch(r'[a-f0-9]{32}', epoch)):
        raise BridgeUnavailable('本机主机连接缺少完整extension/epoch身份')
    return 'edge-native-messaging:' + extension + ':' + epoch


def status(*, state=None):
    """Local registration/heartbeat only; never contacts or starts a browser."""
    _mac()
    directory = STATE if state is None else Path(state)
    try:
        installation = directory/'installation.json'
        if installation.exists() and not is_private_path(installation):
            raise BridgeUnavailable('Mac主机安装记录权限不符')
        value = queue_bridge.status(state=directory)
    except queue_bridge.BridgeUnavailable as error:
        raise BridgeUnavailable(str(error).replace('Windows', 'Mac')) from error
    except (OSError, ValueError, TypeError) as error:
        raise BridgeUnavailable('Mac主机本地登记或心跳记录不完整；未派发请求') from error
    ready = value.get('ready') is True and value.get('synthetic_profile') is not True
    return {**value, 'ready':ready, 'backend':BACKEND,
            'tab_id_namespace':_namespace(value) if value.get('ready') is True else None,
            'synthetic_test_connected':value.get('ready') is True and value.get('synthetic_profile') is True,
            'secure_password_transport_available':bool(ready and value.get('auth_socket_path') and value.get('auth_transport_alive') and value.get('auth_pipe_acl_verified')), 'foreground_state':'unknown',
            'screen_locked':'unknown', 'lock_screen_operation_verified':False,
            'browser_started':False, 'page_script_executed':False, 'system_write':False}


def _request(operation, arguments=None, *, timeout=20, current=None):
    _mac(); current = current or status()
    if current.get('ready') is not True:
        raise BridgeUnavailable('Mac本机扩展未就绪；保留原请求，不启动浏览器或切换后端')
    try:
        return queue_bridge.request(operation, {**(arguments or {}), 'expected_epoch':current['epoch']},
                                    timeout=timeout, state=STATE)
    except queue_bridge.BridgeUnavailable as error:
        raise BridgeUnavailable(str(error).replace('Windows', 'Mac')) from error
    except TimeoutError as error:
        raise TimeoutError(str(error).replace('Windows', 'Mac')) from error


def _target(tab_id, tab_id_namespace, expected_window_id):
    current = status()
    if (current.get('ready') is not True or not isinstance(tab_id_namespace, str)
            or tab_id_namespace != current.get('tab_id_namespace')):
        raise BridgeUnavailable('标签命名空间缺失或连接epoch已变；不能把旧AppleEvent标签或未知请求转到新后端')
    if (not isinstance(tab_id, (str, int)) or isinstance(tab_id, bool) or not re.fullmatch(r'[0-9]+', str(tab_id))
            or not isinstance(expected_window_id, (str, int)) or isinstance(expected_window_id, bool)
            or not re.fullmatch(r'[0-9]+', str(expected_window_id))):
        raise ValueError('Mac原生标签须明确本次inventory的数字tab_id与expected_window_id')
    return current, {'tab_id':str(tab_id), 'expected_window_id':str(expected_window_id)}


def inventory(*, timeout=20):
    current = status(); value = _request('inventory', timeout=timeout, current=current)
    if not isinstance(value, dict):
        raise BridgeUnavailable('本机扩展未返回完整标签盘点')
    ids = value.get('all_tab_ids')
    if (not isinstance(ids, list) or len(ids)>10000 or any(not isinstance(x,str) or not re.fullmatch(r'[0-9]+',x) for x in ids)
            or len(set(ids))!=len(ids) or type(value.get('all_tab_count')) is not int or value['all_tab_count']!=len(ids)
            or value.get('tab_inventory_complete') is not True
            or value.get('tab_inventory_scope')!='extension_visible_profile_tabs'
            or value.get('window_inventory_scope')!='normal'):
        raise BridgeUnavailable('扩展可见profile的完整标签ID盘点缺失；不得用财务筛选清单证明原标签关闭')
    if not isinstance(value.get('windows'),list) or not isinstance(value.get('tabs'),list):
        raise BridgeUnavailable('标签窗口清单不完整')
    namespace = current['tab_id_namespace']
    for tab in value['tabs']:
        if (not isinstance(tab,dict) or tab.get('tab_id') not in ids
                or not isinstance(tab.get('window_id'),str) or not re.fullmatch(r'[0-9]+',tab['window_id'])):
            raise BridgeUnavailable('财务标签与全量ID/窗口身份不闭合')
    return {**value, 'backend':BACKEND, 'tab_id_namespace':namespace, 'epoch':current['epoch'],
            'tabs':[{**tab,'backend':BACKEND,'tab_id_namespace':namespace} for tab in value['tabs']],
            'extension_window_focused':value.get('edge_frontmost'), 'edge_frontmost':None,
            'frontmost_bundle_id':None, 'frontmost_process_id':None,
            'foreground_state':'unknown', 'screen_locked':'unknown', 'foreground_identity_observed':False,
            'lock_screen_operation_verified':False, 'browser_started':False, 'system_write':False}


def evaluate(source, tab_id, *, tab_id_namespace, expected_window_id, timeout=20, access=False):
    if not isinstance(source,str): raise ValueError('原生执行源须为正式工具生成的字符串')
    current, target = _target(tab_id,tab_id_namespace,expected_window_id)
    # 调用方也包含保存动作：只派发一次，不广播到所有frame、不激活、不超时重注入。
    result = _request('evaluate', {**target,'source':source,'scope':'access' if access else 'finance'},
                      timeout=timeout, current=current)
    if not isinstance(result,str): raise BridgeUnavailable('本机扩展回执不是原生字符串；保留原请求')
    LAST_PAGE_READ_STEPS[:] = []
    return result


def validate_tab(tab_id, foreground=False, *, tab_id_namespace, expected_window_id, timeout=20):
    if foreground: raise BridgeUnavailable('Mac原生标签校验不激活窗口；没有前台回退')
    current,target = _target(tab_id,tab_id_namespace,expected_window_id)
    result = _request('validate-tab',target,timeout=timeout,current=current)
    if (not isinstance(result,dict) or result.get('verified') is not True
            or result.get('tab_id')!=target['tab_id'] or result.get('window_id')!=target['expected_window_id']):
        raise BridgeUnavailable('同一原生标签/窗口校验未明确成功')
    return {**result,'backend':BACKEND,'tab_id_namespace':tab_id_namespace}


def ping(*, timeout=8):
    """救援探针：绕过业务队列，只证明扩展进程还在应答。"""
    current = status()
    result = _request('ping', {}, timeout=timeout, current=current)
    if not isinstance(result, dict) or result.get('pong') is not True:
        raise BridgeUnavailable('扩展救援探针未明确应答')
    return {**result, 'backend': BACKEND}


def new_finance_window():
    current=status();value=_request('create-finance-window',current=current)
    if (not isinstance(value,dict)or value.get('window_created')is not True or value.get('window_focus_requested')is not False
        or not re.fullmatch('[0-9]+',str(value.get('tab_id','')))or not re.fullmatch('[0-9]+',str(value.get('window_id','')))):
        raise BridgeUnavailable('新后台财务窗口未取得明确回执，保留原操作，不重复开窗')
    return{**value,'backend':BACKEND,'tab_id_namespace':current['tab_id_namespace'],'system_write':False}


def wake_finance_tab(tab_id, *, tab_id_namespace, expected_window_id, timeout=10):
    current,target=_target(tab_id,tab_id_namespace,expected_window_id)
    result=_request('wake-finance-tab',target,timeout=timeout,current=current)
    if (not isinstance(result,dict) or result.get('tab_id')!=str(tab_id)
            or result.get('window_id')!=str(expected_window_id) or result.get('woken') is not True
            or result.get('foreground_requested') is not False or result.get('business_replayed') is not False):
        raise BridgeUnavailable('未取得明确解除冻结回执；不重放原业务')
    return{**result,'backend':BACKEND,'tab_id_namespace':tab_id_namespace}


def relocate_finance_tab(tab_id, *, tab_id_namespace, expected_window_id, timeout=10):
    """原标签迁入后台独立窗；先登记新窗口，再判断是否解除冻结。"""
    current,target=_target(tab_id,tab_id_namespace,expected_window_id)
    result=_request('relocate-finance-tab',target,timeout=timeout,current=current)
    if (not isinstance(result,dict) or result.get('tab_id')!=str(tab_id)
            or result.get('old_window_id')!=str(expected_window_id) or result.get('relocated') is not True
            or not re.fullmatch(r'[0-9]+',str(result.get('window_id','')))
            or any(result.get(k) is not False for k in ('foreground_requested','navigation_requested','business_replayed'))):
        raise BridgeUnavailable('原页面迁移结果未知；只盘点原tab，不重试迁移或业务')
    return {**result,'backend':BACKEND,'tab_id_namespace':tab_id_namespace}


def show_verification(tab_id, *, tab_id_namespace, expected_window_id, serial, expected_userid):
    """仅按用户明确要求显示同案短信页，不读取或代填验证码。"""
    current,target=_target(tab_id,tab_id_namespace,expected_window_id)
    if not re.fullmatch(r'[0-9]+',serial or '') or not re.fullmatch(r'[A-Za-z0-9]{2,40}',expected_userid or ''):
        raise ValueError('显示验证码须绑定真实单号和账号')
    result=_request('show-verification',{**target,'serial':serial,'expected_userid':expected_userid},current=current)
    if (not isinstance(result,dict) or result.get('shown') is not True or result.get('tab_id')!=str(tab_id)
            or result.get('window_id')!=str(expected_window_id) or result.get('serial')!=serial
            or result.get('verification_only') is not True or result.get('code_read') is not False):
        raise BridgeUnavailable('未取得同案验证码界面展示回执')
    return {**result,'backend':BACKEND,'tab_id_namespace':tab_id_namespace}


def reload_extension(*, timeout=8):
    """让扩展用磁盘上的新代码自 reload（救援操作，绕过业务队列）。"""
    current = status()
    result = _request('reload', {}, timeout=timeout, current=current)
    if not isinstance(result, dict) or result.get('reloading') is not True:
        raise BridgeUnavailable('扩展自更新未明确受理')
    return {**result, 'backend': BACKEND, 'requested_epoch': current.get('epoch')}


def close_tab(tab_id, *, tab_id_namespace, expected_window_id, timeout=10):
    """关闭本项目财务标签（救援操作，绕过业务队列）；不碰其他站点标签。"""
    current, target = _target(tab_id, tab_id_namespace, expected_window_id)
    result = _request('tab-close', target, timeout=timeout, current=current)
    if (not isinstance(result, dict) or result.get('closed') is not True
            or result.get('tab_id') != target['tab_id']
            or result.get('window_id') != target['expected_window_id']):
        raise BridgeUnavailable('关标签未明确成功；不作已关闭结论')
    return {**result, 'backend': BACKEND, 'tab_id_namespace': tab_id_namespace,
            'system_write': False}


def reopen_page(*, close_tab_id=None, window_id=None, silent=True, timeout=20):
    """坏标签处置：先关掉可疑财务标签，再静默开一个干净入口页并返回新标签。

    页面卡死、表单落在未知frame、导航结果不可读时，关+开比"猜着修"可靠。
    silent=True不把窗口置前；开页后由调用方重新inventory取新绑定。
    """
    current = status()
    if current.get('ready') is not True:
        raise BridgeUnavailable('扩展未就绪；不关标签、不开新页')
    namespace = current['tab_id_namespace']
    snapshot = inventory(timeout=timeout)
    windows = snapshot.get('windows') or []
    target_window = str(window_id) if window_id is not None else None
    if target_window is None:
        if len(windows) != 1:
            raise BridgeUnavailable('窗口不唯一；须明确window_id')
        target_window = windows[0]['window_id']
    window = next((w for w in windows if w['window_id'] == target_window), None)
    if window is None:
        raise BridgeUnavailable('目标窗口不在本次完整盘点中')
    result = {'closed_tab_id': None, 'new_tab_id': None, 'silent': bool(silent),
              'window_id': target_window, 'backend': BACKEND, 'system_write': False}
    if close_tab_id is not None:
        finance = {t['tab_id'] for t in snapshot.get('tabs') or []}
        if str(close_tab_id) not in finance:
            raise BridgeUnavailable('待关标签不在本次财务标签盘点中；不关')
        closed = close_tab(str(close_tab_id), tab_id_namespace=namespace,
                           expected_window_id=target_window)
        result['closed_tab_id'] = closed['tab_id']
        snapshot = inventory(timeout=timeout)
        window = next((w for w in snapshot['windows'] if w['window_id'] == target_window), None)
        if window is None:
            raise BridgeUnavailable('关标签后目标窗口消失；不继续开页')
    created = _request('create-page', {'window_id': target_window,
                                       'active_tab_id': window['active_tab_id'],
                                       'silent': bool(silent)},
                       timeout=timeout, current=current)
    if not isinstance(created, dict) or not str(created.get('tab_id') or '').isdigit():
        raise BridgeUnavailable('新页回执不完整；按回执独立盘点')
    result['new_tab_id'] = str(created['tab_id'])
    result['create_receipt'] = created
    return result


def tab_origin(tab_id, *, tab_id_namespace, expected_window_id, timeout=20):
    current,target = _target(tab_id,tab_id_namespace,expected_window_id)
    return _request('tab-origin',target,timeout=timeout,current=current)


def list_tabs(*, access=False):
    return [tab['tab_id'] for tab in inventory()['tabs'] if access or tab.get('path')==ENTRY]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','inventory'])
    parser.add_argument('--backend',choices=[BACKEND],required=True)
    args=parser.parse_args()
    print(json.dumps({'status':status,'inventory':inventory}[args.action](),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
