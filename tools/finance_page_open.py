"""固定财务入口的新页生命周期；不复制登录、不执行网页脚本、不重放业务。"""
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from finance_access import write
from operator_support import stamp


def _observe(record, inventory):
    from finance_session import foreground_comparison
    before = record['before']
    old_ids = {t['tab_id'] for t in before['tabs']}
    created = record.get('native_created_tab_id')
    candidates = [t for t in inventory['tabs'] if t['tab_id'] not in old_ids
                  and (created is None or t['tab_id'] == created)]
    windows = {w['window_id']: w for w in inventory['windows']}
    record.update(after=inventory, new_tab_candidates=candidates,
        new_tab_id=candidates[0]['tab_id'] if len(candidates) == 1 else None,
        creation_state='identified' if len(candidates) == 1 else 'ambiguous' if candidates else 'unknown',
        frontmost_preserved=foreground_comparison(before, inventory),
        old_active_tabs_preserved=all(windows.get(w['window_id'], {}).get('active_tab_id') == w['active_tab_id'] for w in before['windows']),
        old_finance_tabs_present=all(any(t['tab_id'] == old['tab_id'] and t['path'] == old['path'] for t in inventory['tabs']) for old in before['tabs']))
    # 没有原生创建ID时，不能将用户同期打开的唯一财务页认作本工具创建。
    record['page_opened'] = bool(created and len(candidates) == 1)
    record['quiet_verified_at_observation_points'] = bool(record['frontmost_preserved'] and
        not record.get('foreground_change_observed') and not record.get('foreground_restoration', {}).get('requested'))
    record['ok'] = bool(record['page_opened'] and (not record.get('silent', True) or record['quiet_verified_at_observation_points']) and
        record['old_active_tabs_preserved'] and record['old_finance_tabs_present'])
    record['state'] = 'page_opened' if record['ok'] else 'page_opened_needs_review' if record['page_opened'] else 'creation_unconfirmed'
    record['next'] = '用实际new_tab_id运行access check/ensure，另核对账号；开页不证明登录或业务保存' if record['page_opened'] else '只读恢复本次开页意图，不再次创建'
    return record


def dedicated_window_script(window_id,active_id):
    from finance_session import ENTRY
    if any(not re.fullmatch(r'[0-9]+',str(v)) for v in [window_id,active_id]):raise ValueError('须明确原窗口与活动标签')
    return '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 8 seconds
tell application "Microsoft Edge"
if (id of front window as text) is not ORIGINAL_WINDOW then return "selection_changed"
if (id of active tab of front window as text) is not ORIGINAL_TAB then return "selection_changed"
set financeWindow to make new window
set financeTab to active tab of financeWindow
set URL of financeTab to ENTRY_URL
set tabId to id of financeTab as text
set windowId to id of financeWindow as text
if (id of front window as text) is windowId then
repeat with originalWindow in windows
if (id of originalWindow as text) is ORIGINAL_WINDOW then set index of originalWindow to 1
end repeat
end if
return "created" & (ASCII character 9) & tabId & (ASCII character 9) & windowId
end tell
end timeout'''.replace('ORIGINAL_WINDOW',json.dumps(str(window_id))).replace('ORIGINAL_TAB',json.dumps(str(active_id))).replace('ENTRY_URL',json.dumps(ENTRY))


def open_page(case_id, request_id, old_tab_id, output, receipt=None, wait=5, silent=True, separate_window=False):
    from finance_session import SESSIONS, ENTRY, EDGE_BUNDLE, native_inventory, background_tab_script, restore_foreground, foreground_comparison
    from finance_backend import mac_native_enabled, bound_native_enabled
    if mac_native_enabled() or (sys.platform == 'win32' and separate_window and bound_native_enabled()):
        return open_native_page(case_id,request_id,old_tab_id,output,receipt,wait=wait,
                                silent=silent,separate_window=separate_window)
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', case_id) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
        raise ValueError('须明确合法案件和稳定请求号')
    if old_tab_id is not None and not re.fullmatch(r'[0-9]+', old_tab_id):
        raise ValueError('旧财务标签须为原生数字ID，或省略以新开入口')
    if type(silent) is not bool: raise ValueError('silent须为布尔开关')
    if type(separate_window) is not bool or separate_window and sys.platform!='darwin':raise ValueError('独立窗口当前仅支持Mac原生通道')
    output = Path(output)
    if output.exists(): raise ValueError('保留旧回执，不覆盖')
    cache = SESSIONS / case_id / ('open-' + hashlib.sha256(request_id.encode()).hexdigest() + '.json')
    source = Path(receipt) if receipt else cache
    record = json.loads(source.read_text()) if source.exists() else None
    if receipt and record is None: raise ValueError('恢复须引用原开页回执')
    if record:
        if record.get('schema') != 'ustc-finance-page-open-v2' or record.get('case_id') != case_id or record.get('request_id') != request_id or record.get('old_tab_id') != old_tab_id or record.get('silent', True) != silent or record.get('separate_window',False)!=separate_window:
            raise ValueError('原开页意图与案件、请求或旧页不一致')
        record['reopened'] = False
        if receipt: record['recovery_source'] = dict(path=str(source.resolve()), sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        # 恢复只读，不再次创建，也不切换用户当前前台或标签。
        _observe(record, native_inventory())
        write(output, record)
        return record
    before = native_inventory()
    if silent and sys.platform != 'win32':
        record = dict(schema='ustc-finance-page-open-v2', case_id=case_id, request_id=request_id,
            old_tab_id=old_tab_id, silent=True, before=before, ok=False, page_opened=False,
            state='silent_open_unavailable', system_write=False, launch_intent_recorded=False,
            next='原生建页会短暂置前；需本案明确允许silent=false，不自动回退前台')
        write(output, record)
        return record
    if old_tab_id is not None:
        hits = [t for t in before['tabs'] if t['tab_id'] == old_tab_id]
        if len(hits) != 1: raise ValueError('指定旧财务标签不存在，未创建')
        window = next(w for w in before['windows'] if w['window_id'] == hits[0]['window_id'])
    else:
        window = before['windows'][0]
    record = dict(schema='ustc-finance-page-open-v2', case_id=case_id, request_id=request_id,
        old_tab_id=old_tab_id, silent=silent, separate_window=separate_window, before=before, entry=ENTRY, started_at_utc=stamp(),
        launch_intent_recorded=True, launch_acknowledged=False, page_opened=False,
        system_write=False, business_replayed=False, session_cookie_copied=False,
        page_script_executed=False, edge_foreground_requested=False, reopened=False)
    write(cache, record)
    try:
        if sys.platform == 'win32':
            from windows_edge_bridge import request
            native = request('create-page', {'window_id': window['window_id'], 'active_tab_id': window['active_tab_id'], 'silent': silent}, timeout=8)
            record.update(launch_acknowledged=True, native_created_tab_id=native['tab_id'],
                          native_result='created', native_exit_code=0, native_details=native)
        else:
            native = subprocess.run(['/usr/bin/osascript', '-'],
                input=dedicated_window_script(before['windows'][0]['window_id'],before['windows'][0]['active_tab_id']) if separate_window else background_tab_script(window['window_id'], window['active_tab_id']),
                text=True, capture_output=True, timeout=8)
            match = re.fullmatch(r'created\t([0-9]+)\t([0-9]+)' if separate_window else r'created\t([0-9]+)', native.stdout.strip()) if native.returncode == 0 else None
            record.update(launch_acknowledged=bool(match), native_created_tab_id=match.group(1) if match else None,
                native_result='created' if match else 'not_acknowledged', native_exit_code=native.returncode)
            if separate_window and match:record['native_created_window_id']=match.group(2)
        write(cache, record)
        deadline = time.monotonic() + wait
        while True:
            after = native_inventory()
            if any(t['tab_id'] == record.get('native_created_tab_id') for t in after['tabs']) or time.monotonic() >= deadline: break
            time.sleep(.2)
        same_foreground = foreground_comparison(before, after)
        record['foreground_change_observed'] = None if same_foreground is None else not same_foreground
        record['foreground_restoration'] = ({'state': 'not_requested_by_windows_bridge', 'requested': False}
                                            if sys.platform == 'win32' else restore_foreground(before, after))
        if record['foreground_restoration'].get('native_probe_performed'): after = native_inventory()
        _observe(record, after)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(ok=False, state='creation_unconfirmed', error_type=type(error).__name__,
            next='开页意图已保留；只读恢复，不重复创建')
    write(cache, record)
    write(output, record)
    return record


def open_native_page(case_id, request_id, old_tab_id, output, receipt=None, wait=5, silent=True, separate_window=False):
    """同一公开开页合同接入原生桥；新页与旧页分开，不关闭原页或重放业务。"""
    from finance_session import SESSIONS, ENTRY
    from finance_backend import mac_target, assert_original_transport, transport_identity
    from registered_bridge import inventory, new_finance_window, reopen_page
    if (not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request_id)
            or old_tab_id is not None and not re.fullmatch(r'\d+',old_tab_id)
            or type(silent) is not bool or type(separate_window) is not bool):
        raise ValueError('原生新界面须明确案件、稳定意图及布尔开页模式')
    output=Path(output)
    if output.exists(): raise ValueError('保留原开页回执，不覆盖')
    cache=SESSIONS/case_id/('open-'+hashlib.sha256(request_id.encode()).hexdigest()+'.json')
    prior=Path(receipt) if receipt else cache
    if prior.exists():
        record=json.loads(prior.read_text())
        if (record.get('schema')!='ustc-finance-page-open-v2' or record.get('case_id')!=case_id
                or record.get('request_id')!=request_id or record.get('old_tab_id')!=old_tab_id
                or record.get('silent')!=silent or record.get('separate_window')!=separate_window):
            raise ValueError('原生开页意图或模式不同，不覆盖、不重新创建')
        assert_original_transport(record)
        _observe_native(record,inventory())
        record['reopened']=False
        write(output,record);return record
    if receipt: raise ValueError('只能恢复已有原生开页意图')
    before=inventory()
    if old_tab_id is not None and len([t for t in before['tabs'] if t['tab_id']==old_tab_id])!=1:
        raise ValueError('原财务页不在当前完整盘点中')
    record={'schema':'ustc-finance-page-open-v2','case_id':case_id,'request_id':request_id,
        'old_tab_id':old_tab_id,'silent':silent,'separate_window':separate_window,'before':before,
        'browser_transport':transport_identity(),'entry':ENTRY,'started_at_utc':stamp(),
        'launch_intent_recorded':True,'launch_acknowledged':False,'page_opened':False,
        'system_write':False,'business_replayed':False,'session_cookie_copied':False,
        'page_script_executed':False,'edge_foreground_requested':False,'reopened':False}
    write(cache,record)
    try:
        if separate_window:
            opened=new_finance_window();created=opened['tab_id'];window=opened['window_id']
        else:
            window=next((t['window_id'] for t in before['tabs'] if t['tab_id']==old_tab_id),None) or mac_target()['expected_window_id']
            opened=reopen_page(window_id=window,silent=True);created=opened['new_tab_id']
        record.update(launch_acknowledged=True,native_created_tab_id=str(created),native_created_window_id=str(window),native_details=opened)
        write(cache,record)
        deadline=time.monotonic()+min(max(wait,0),15)
        while True:
            observed=inventory()
            if any(t['tab_id']==str(created) for t in observed['tabs']) or time.monotonic()>=deadline:break
            time.sleep(.2)
        _observe_native(record,observed)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        record.update(ok=False,state='creation_unconfirmed',error_type=type(error).__name__,
                      next='保留本次开页意图，只盘点原创建ID，不再次开页')
    write(cache,record);write(output,record);return record


def _observe_native(record, observed):
    _observe(record,observed)
    # 原生桥不观察其他应用前台，不能用两个None相等冒称前台已核实。
    record.update(frontmost_preserved=None,foreground_observation_supported=False)
    proof=record.get('native_details',{})
    no_focus=(proof.get('window_focus_requested') is False if record['separate_window'] else proof.get('silent') is True)
    record['foreground_requested']=False
    record['quiet_verified_at_observation_points']=None
    expected_namespace=record['browser_transport']['tab_id_namespace']
    same_namespace=(observed.get('tab_id_namespace')==expected_namespace and
                    record['before'].get('tab_id_namespace')==expected_namespace)
    matches=[t for t in observed['tabs'] if t['tab_id']==record.get('native_created_tab_id')]
    same_window=len(matches)==1 and matches[0]['window_id']==record.get('native_created_window_id')
    record['ok']=bool(record['page_opened'] and no_focus and same_namespace and same_window and
                      record['old_active_tabs_preserved'] and record['old_finance_tabs_present'])
    record['state']='page_opened' if record['ok'] else 'page_opened_needs_review' if record['page_opened'] else 'creation_unconfirmed'
    return record
