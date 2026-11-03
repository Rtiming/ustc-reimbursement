"""登记流水线的本机原生桥；Mac复用原适配，Windows绑定同一完整连接身份。"""
from __future__ import annotations

import re
import sys
import windows_edge_bridge as queue

STATE = queue.STATE
BACKEND = 'edge_native_messaging'
BridgeUnavailable = queue.BridgeUnavailable


def _mac():
    if sys.platform == 'darwin':
        import mac_edge_bridge
        return mac_edge_bridge
    if sys.platform != 'win32':
        raise BridgeUnavailable('登记流水线仅支持已接入原生桥的Windows和Mac')
    return None


def status():
    mac = _mac()
    if mac:
        return mac.status()
    value = queue.status()
    ready = value.get('ready') is True and value.get('synthetic_profile') is not True
    namespace = None
    if ready:
        extension, epoch = value.get('extension_id'), value.get('epoch')
        if not re.fullmatch('[a-p]{32}', extension or '') or not re.fullmatch('[a-f0-9]{32}', epoch or ''):
            raise BridgeUnavailable('Windows连接缺少完整扩展及epoch身份')
        namespace = f'edge-native-messaging:{extension}:{epoch}'
    return {**value, 'ready': ready, 'backend': BACKEND, 'tab_id_namespace': namespace,
            'system_write': False, 'screen_locked': 'unknown', 'lock_screen_operation_verified': False}


def _request(operation, arguments=None, *, timeout=20, current=None):
    current = current or status()
    if current.get('ready') is not True:
        raise BridgeUnavailable('本机财务扩展未连接；保留原任务')
    return queue.request(operation, {**(arguments or {}), 'expected_epoch': current['epoch']}, timeout=timeout)


def target_binding(tab_id, tab_id_namespace, expected_window_id):
    current = status()
    if current.get('ready') is not True or tab_id_namespace != current.get('tab_id_namespace'):
        raise BridgeUnavailable('原连接身份改变，不能将旧任务转入新连接')
    for value in [tab_id, expected_window_id]:
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch('[0-9]+', str(value)):
            raise ValueError('必须绑定当前盘点的数字标签与窗口')
    return current, {'tab_id': str(tab_id), 'expected_window_id': str(expected_window_id)}


def inventory(*, timeout=20):
    mac = _mac()
    if mac:
        return mac.inventory(timeout=timeout)
    current = status()
    value = _request('inventory', timeout=timeout, current=current)
    ids = value.get('all_tab_ids') if isinstance(value, dict) else None
    if (not isinstance(ids, list) or len(ids)>10000 or any(not isinstance(x, str) or not re.fullmatch('[0-9]+', x) for x in ids)
            or len(set(ids)) != len(ids) or type(value.get('all_tab_count')) is not int
            or value['all_tab_count'] != len(ids) or value.get('tab_inventory_complete') is not True
            or value.get('tab_inventory_scope') != 'extension_visible_profile_tabs'
            or value.get('window_inventory_scope') != 'normal'
            or not isinstance(value.get('windows'), list) or not isinstance(value.get('tabs'), list)):
        raise BridgeUnavailable('原生桥未取得完整标签和窗口盘点')
    for tab in value['tabs']:
        if (not isinstance(tab, dict) or tab.get('tab_id') not in ids
                or not isinstance(tab.get('window_id'), str) or not re.fullmatch('[0-9]+', tab['window_id'])):
            raise BridgeUnavailable('财务标签与完整盘点中的窗口身份不一致')
    return {**value, 'backend': BACKEND, 'tab_id_namespace': current['tab_id_namespace'], 'epoch': current['epoch'],
            'system_write': False, 'browser_started': False,
            'tabs': [{**t, 'tab_id_namespace': current['tab_id_namespace'], 'backend': BACKEND} for t in value['tabs']]}


def new_finance_window():
    mac = _mac()
    if mac:
        return mac.new_finance_window()
    current = status()
    value = _request('create-finance-window', current=current)
    if (not isinstance(value, dict) or value.get('window_created') is not True
            or value.get('window_focus_requested') is not False
            or any(not re.fullmatch('[0-9]+', str(value.get(k, ''))) for k in ['tab_id', 'window_id'])):
        raise BridgeUnavailable('后台财务窗口未取得明确回执；不重复创建')
    return {**value, 'backend': BACKEND, 'tab_id_namespace': current['tab_id_namespace'], 'system_write': False}


def wake_finance_tab(tab_id, *, tab_id_namespace, expected_window_id, timeout=10):
    mac = _mac()
    if mac:
        return mac.wake_finance_tab(tab_id, tab_id_namespace=tab_id_namespace, expected_window_id=expected_window_id, timeout=timeout)
    current, target = target_binding(tab_id, tab_id_namespace, expected_window_id)
    value = _request('wake-finance-tab', target, timeout=timeout, current=current)
    if (not isinstance(value, dict) or value.get('tab_id') != str(tab_id) or value.get('window_id') != str(expected_window_id)
            or value.get('woken') is not True or value.get('foreground_requested') is not False or value.get('business_replayed') is not False):
        raise BridgeUnavailable('原页唤醒未明确核实；不重放原业务')
    return {**value, 'backend': BACKEND, 'tab_id_namespace': tab_id_namespace}


def relocate_finance_tab(tab_id, *, tab_id_namespace, expected_window_id, timeout=10,
                         allow_access=False, expected_active_tab_id=None):
    mac = _mac()
    if mac:
        return mac.relocate_finance_tab(tab_id, tab_id_namespace=tab_id_namespace, expected_window_id=expected_window_id,
            timeout=timeout, allow_access=allow_access, expected_active_tab_id=expected_active_tab_id)
    if type(allow_access) is not bool:
        raise ValueError('allow_access须为布尔值')
    current, target = target_binding(tab_id, tab_id_namespace, expected_window_id)
    if allow_access:
        target['scope'] = 'access'
    if expected_active_tab_id is not None:
        if not isinstance(expected_active_tab_id, str) or not re.fullmatch('[0-9]+', expected_active_tab_id) or expected_active_tab_id == str(tab_id):
            raise ValueError('原窗口活动标签必须是另一个明确标签')
        target['expected_active_tab_id'] = expected_active_tab_id
    value = _request('relocate-finance-tab', target, timeout=timeout, current=current)
    if (not isinstance(value, dict) or value.get('tab_id') != str(tab_id) or value.get('old_window_id') != str(expected_window_id)
            or value.get('relocated') is not True or not re.fullmatch('[0-9]+', str(value.get('window_id', '')))
            or any(value.get(k) is not False for k in ['foreground_requested', 'navigation_requested', 'business_replayed'])):
        raise BridgeUnavailable('原页迁移结果未核实；只查原任务，不重试迁移')
    if expected_active_tab_id is not None and (value.get('source_active_tab_id') != expected_active_tab_id or value.get('source_active_tab_preserved') is not True):
        raise BridgeUnavailable('迁移后的用户活动标签保护未核实；保留原任务')
    return {**value, 'backend': BACKEND, 'tab_id_namespace': tab_id_namespace}


def frozen_relocation_options(observed, tab_id, window_id):
    # 该函数仅判断结构化盘点，不调用Mac系统或浏览器。
    from mac_edge_bridge import frozen_relocation_options as choose
    return choose(observed, tab_id, window_id)


def close_tab(tab_id, *, tab_id_namespace, expected_window_id, timeout=10):
    mac = _mac()
    if mac:
        return mac.close_tab(tab_id, tab_id_namespace=tab_id_namespace, expected_window_id=expected_window_id, timeout=timeout)
    current, target = target_binding(tab_id, tab_id_namespace, expected_window_id)
    value = _request('tab-close', target, timeout=timeout, current=current)
    if (not isinstance(value, dict) or value.get('closed') is not True or value.get('tab_id') != str(tab_id)
            or value.get('window_id') != str(expected_window_id)):
        raise BridgeUnavailable('关闭原标签未取得明确回执')
    return {**value, 'backend': BACKEND, 'tab_id_namespace': tab_id_namespace, 'system_write': False}


def show_verification(tab_id, *, tab_id_namespace, expected_window_id, serial, expected_userid):
    mac = _mac()
    if mac:
        return mac.show_verification(tab_id, tab_id_namespace=tab_id_namespace, expected_window_id=expected_window_id,
                                     serial=serial, expected_userid=expected_userid)
    current, target = target_binding(tab_id, tab_id_namespace, expected_window_id)
    if not re.fullmatch('[0-9]+', serial or '') or not re.fullmatch('[A-Za-z0-9]{2,40}', expected_userid or ''):
        raise ValueError('本人验证须绑定明确单号与账号')
    value = _request('show-verification', {**target, 'serial': serial, 'expected_userid': expected_userid}, current=current)
    if (not isinstance(value, dict) or value.get('shown') is not True or value.get('tab_id') != str(tab_id)
            or value.get('window_id') != str(expected_window_id) or value.get('serial') != serial
            or value.get('verification_only') is not True or value.get('code_read') is not False):
        raise BridgeUnavailable('未取得本案验证界面展示回执')
    return {**value, 'backend': BACKEND, 'tab_id_namespace': tab_id_namespace}


def show_approver_verification(tab_id, *, tab_id_namespace, expected_window_id, serial, expected_userid, node_id):
    """两平台共用的本案角色验证展示；原申请人入口继续固定508。"""
    current,target=target_binding(tab_id,tab_id_namespace,expected_window_id)
    if not re.fullmatch('[0-9]+',serial or'')or not re.fullmatch('[0-9]+',node_id or'')or not re.fullmatch('[A-Za-z0-9]{2,40}',expected_userid or''):
        raise ValueError('展示须绑定本案单号、本人账号和已核审批节点')
    value=_request('show-approver-verification',{**target,'serial':serial,'expected_userid':expected_userid,'node_id':node_id},current=current)
    if (not isinstance(value,dict)or value.get('shown')is not True or value.get('tab_id')!=str(tab_id)
            or value.get('window_id')!=str(expected_window_id)or value.get('serial')!=serial or value.get('node_id')!=node_id
            or value.get('verification_only')is not True or value.get('code_read')is not False):
        raise BridgeUnavailable('未取得本案原角色验证页展示回执')
    return {**value,'backend':BACKEND,'tab_id_namespace':tab_id_namespace}


def reopen_page(*, close_tab_id=None, window_id=None, silent=True, timeout=20):
    mac = _mac()
    if mac:
        return mac.reopen_page(close_tab_id=close_tab_id, window_id=window_id, silent=silent, timeout=timeout)
    if type(silent) is not bool:
        raise ValueError('silent须为布尔值')
    current = status()
    snapshot = inventory(timeout=timeout)
    windows = snapshot['windows']
    if window_id is None:
        if len(windows) != 1:
            raise BridgeUnavailable('窗口不唯一，须使用当前盘点的明确窗口')
        window_id = windows[0]['window_id']
    target_window = str(window_id)
    matches = [w for w in windows if w.get('window_id') == target_window]
    if len(matches) != 1:
        raise BridgeUnavailable('目标窗口不在完整盘点中')
    result = {'closed_tab_id': None, 'new_tab_id': None, 'silent': silent,
              'window_id': target_window, 'backend': BACKEND, 'system_write': False}
    if close_tab_id is not None:
        matches_tab = [t for t in snapshot['tabs'] if t['tab_id'] == str(close_tab_id) and t['window_id'] == target_window]
        if len(matches_tab) != 1:
            raise BridgeUnavailable('待关闭标签不属于本次财务窗口')
        closed = close_tab(close_tab_id, tab_id_namespace=current['tab_id_namespace'], expected_window_id=target_window)
        result['closed_tab_id'] = closed['tab_id']
        windows = inventory(timeout=timeout)['windows']
        matches = [w for w in windows if w.get('window_id') == target_window]
        if len(matches) != 1:
            raise BridgeUnavailable('原窗口已消失，不继续创建')
    opened = _request('create-page', {'window_id': target_window, 'active_tab_id': matches[0]['active_tab_id'],
                                     'silent': silent}, timeout=timeout, current=current)
    if not isinstance(opened, dict) or not re.fullmatch('[0-9]+', str(opened.get('tab_id', ''))):
        raise BridgeUnavailable('新页尚未取得明确回执，只核对原请求')
    result.update(new_tab_id=str(opened['tab_id']), create_receipt=opened)
    return result
