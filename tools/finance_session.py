#!/usr/bin/env python3
"""新建固定财务后台界面；保留旧页，按原意图恢复标签与前台，不执行页面脚本。"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from edge_runtime import browser_lock
from finance_access import write
from operator_support import stamp
from finance_backend import mac_native_enabled, mac_target, transport_identity, assert_original_transport

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / '.local' / 'finance_sessions'
ENTRY = 'https://cwzh.ustc.edu.cn/YBX/main.jsp'
EDGE_BUNDLE = 'com.microsoft.edgemac'

# 小型同步只读探针。不等待全表，不启动导航，不使用计时器/注入marker。
# 连接健康、主页面可读与单据预览就绪是三个不同条件。
PAGE_HEALTH_BODY = r'''(()=>{
 const f=document.getElementById('iframepage_print');
 let frame=null;
 if(f&&f.getClientRects().length){
  try{const text=f.contentDocument?.body?.innerText||'';
   frame={url:f.contentWindow.location.href,characters:text.length,
    serial:(text.match(/报销单号[：:]\s*(\d+)/)||[])[1]||null,
    navigationReady:typeof f.contentWindow.pageOpen==='function'};
  }catch(e){frame={unreadable:true,error:e.name};}
 }
 return{origin:location.origin,pathname:location.pathname,
  documentState:document.readyState,
  project:window.$?.getProjID?.()||null,
  userid:window.$?.UC?.bindData?.('#0-userinfo.userid#')||null,
  serial:window.$?.UC?.bindData?.('#61-serialno#')||null,frame};
})()'''

# Edge的AppleScript脚本环境看得到DOM，但不一定看得到页面的$。
# 与正式运行器一致进入MAIN world；同步取回后清理本地探针节点。
PAGE_HEALTH = r'''(()=>{
 const id='reimbursement-health-'+crypto.randomUUID(),out=document.createElement('script'),run=document.createElement('script');
 out.type='application/json';out.id=id;document.head.appendChild(out);
 run.textContent='document.getElementById('+JSON.stringify(id)+').textContent=JSON.stringify('+BODY+');';
 try{document.head.appendChild(run);return out.textContent;}finally{run.remove();out.remove();}
})()'''.replace('BODY', json.dumps(PAGE_HEALTH_BODY))


def classify_page_health(value, expected_userid):
    if not isinstance(value, dict):
        raise ValueError('页面健康回执不是对象')
    if value.get('origin') != 'https://cwzh.ustc.edu.cn' or value.get('pathname') != '/YBX/main.jsp' or value.get('project') != 'WF_YBX':
        return 'wrong_page'
    if value.get('userid') != expected_userid:
        return 'identity_mismatch'
    frame = value.get('frame')
    if frame is None:
        return 'main_readable'
    if not isinstance(frame, dict) or frame.get('unreadable'):
        return 'preview_unreadable'
    if frame.get('url') == 'about:blank' or not frame.get('characters'):
        return 'preview_not_loaded'
    if not value.get('serial') or frame.get('serial') != value.get('serial'):
        return 'preview_identity_mismatch'
    return 'preview_ready' if frame.get('navigationReady') is True else 'preview_not_ready'


def check_page(case_id, tab_id, expected_userid, output):
    """短预算检查固定财务页；超时归连接层，绝不重发业务或切换标签。"""
    from edge_read_probe import evaluate
    if (not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', case_id) or
        not re.fullmatch(r'[0-9]+', str(tab_id)) or
        not isinstance(expected_userid, str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', expected_userid)):
        raise ValueError('须明确案件、财务标签与预期登录人')
    output = Path(output)
    if output.exists(): raise ValueError('保留旧回执，不覆盖')
    record = dict(schema='ustc-finance-page-health-v1', case_id=case_id, tab_id=tab_id,
        browser_transport=transport_identity(),
        expected_userid=expected_userid, observed_at_utc=stamp(), system_write=False,
        business_replayed=False, navigation_requested=False, foreground_requested=False,
        probe_transport_budget_seconds=3, preview_ready=False, system_result_verified=False)
    started = time.monotonic()
    if mac_native_enabled():
        from mac_session_state import lock_state
        record['lock_state_before'] = lock_state()
    try:
        value = json.loads(evaluate(PAGE_HEALTH, tab_id, timeout=3))
        record.update(observation=value, state=classify_page_health(value, expected_userid))
        record['ok'] = record['state'] in ['main_readable', 'preview_ready']
        record['preview_ready'] = record['state'] == 'preview_ready'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(ok=False, state='transport_unavailable', error=str(error)[:500])
    record['elapsed_seconds'] = round(time.monotonic()-started, 3)
    if mac_native_enabled():
        record['lock_state_after'] = lock_state()
        record['lock_screen_read_verified'] = (record.get('ok') is True
            and record['lock_state_before'].get('locked') is True
            and record['lock_state_after'].get('locked') is True)
    record['next'] = {
        'main_readable':'主页面可读；按原任务核对，尚未证明单据可填写',
        'preview_ready':'预览身份和栏目入口已加载；继续独立核对本案票数/金额，仍不证明保存或提交',
        'preview_not_loaded':'主页面可读、预览未加载；定位页面或连接故障，停止反复大快照/换请求重试',
        'transport_unavailable':'固定页短探针失败；停止该页重复读取，保留原写入意图，恢复连接后核对原单',
    }.get(record['state'], '页面或身份未就绪；不填写、不重放业务，先核对本案与当前登录')
    write(output, record)
    return record


def foreground_comparison(before, after):
    """两个未知桌面不能相等为“保持前台”；SSH/锁屏不可观察时保留None。"""
    for item in (before, after):
        if (item.get('foreground_identity_observed') is False or not item.get('frontmost_bundle_id')
                or type(item.get('frontmost_process_id')) is not int or item['frontmost_process_id'] <= 0):
            return None
    return (before['frontmost_bundle_id'], before['frontmost_process_id']) == (after['frontmost_bundle_id'], after['frontmost_process_id'])


@contextmanager
def quiet_tab_context(tab_id, output, access_pages=False):
    """后台应用内临时保持财务标签；前台使用中的标签不动，不激活应用。"""
    if not re.fullmatch(r'[0-9]+', str(tab_id)): raise ValueError('须明确财务标签')
    if mac_native_enabled():
        from mac_edge_bridge import validate_tab
        before = native_inventory()
        validate_tab(tab_id, **mac_target())
        record = dict(schema='ustc-finance-quiet-tab-v1', tab_id=tab_id, before=before,
                      browser_transport=transport_identity(), system_write=False,
                      edge_foreground_requested=False, active_restoration=[],
                      frontmost_preserved=None, foreground_observation_supported=False)
        write(output, record)
        try:
            yield
        finally:
            after = native_inventory()
            record.update(after=after, old_active_tabs_preserved=before['windows'] == after['windows'])
            write(output, record)
        return
    if sys.platform == 'win32':
        # MV3执行固定后台标签，不需要Mac内部active-tab唤醒技巧。
        from windows_edge_bridge import validate_tab
        before = native_inventory()
        validate_tab(tab_id)
        record = dict(schema='ustc-finance-quiet-tab-v1', tab_id=tab_id, before=before,
                      system_write=False, edge_foreground_requested=False, active_restoration=[])
        write(output, record)
        try:
            yield
        finally:
            after = native_inventory()
            record.update(after=after, frontmost_preserved=foreground_comparison(before, after),
                          old_active_tabs_preserved=before['windows'] == after['windows'])
            write(output, record)
        return
    before=native_inventory()
    allowed_paths={ENTRY}
    if access_pages: allowed_paths.update({'https://cwzh.ustc.edu.cn/YBX/login.html','https://cwzh.ustc.edu.cn/WFManager/home2.jsp','http://cwzh.ustc.edu.cn/WFManager/home2.jsp','https://id.ustc.edu.cn/cas/login'})
    hits=[t for t in before['tabs'] if t['tab_id']==tab_id and t['path'] in allowed_paths]
    if len(hits)!=1: raise ValueError('固定财务标签不唯一')
    win=next(w for w in before['windows'] if w['window_id']==hits[0]['window_id'])
    record={'schema':'ustc-finance-quiet-tab-v1','tab_id':tab_id,'before':before,'system_write':False,'edge_foreground_requested':False}
    changed=False
    write(output,record)
    try:
        if not before['edge_frontmost'] and win['active_tab_id']!=tab_id:
            source='''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 5 seconds
tell application "Microsoft Edge"
if frontmost then return "user_foreground"
repeat with w in windows
if (id of w as text) is WINDOW_ID then
if (id of active tab of w as text) is not ORIGINAL_ID then return "selection_changed"
set tabNumber to 0
repeat with t in tabs of w
set tabNumber to tabNumber + 1
if (id of t as text) is TAB_ID then
set targetURL to URL of t as text
if not (targetURL is ENTRY_URL or targetURL starts with (ENTRY_URL & "?") or targetURL starts with (ENTRY_URL & ";")) then return "path_changed"
set active tab index of w to tabNumber
delay 0.5
return "selected"
end if
end repeat
end if
end repeat
return "tab_missing"
end tell
end timeout'''.replace('WINDOW_ID',json.dumps(win['window_id'])).replace('ORIGINAL_ID',json.dumps(win['active_tab_id'])).replace('TAB_ID',json.dumps(tab_id)).replace('ENTRY_URL',json.dumps(hits[0]['path']))
            native=subprocess.run(['/usr/bin/osascript','-'],input=source,text=True,capture_output=True,timeout=8)
            if native.returncode or native.stdout.strip() not in ['selected','user_foreground']:
                raise RuntimeError('后台标签保持未确认，未派发页面操作')
            changed=native.stdout.strip()=='selected'
        yield
    finally:
        current=native_inventory()
        # 用户自行切换到Edge或其他应用时保留其状态；本入口从不激活应用。
        same_front=(before.get('frontmost_bundle_id'),before.get('frontmost_process_id'))==(current.get('frontmost_bundle_id'),current.get('frontmost_process_id'))
        created_ids={t['tab_id'] for t in current['tabs']} - {t['tab_id'] for t in before['tabs']} if access_pages else set()
        record['active_restoration']=restore_active(before,current,{tab_id}|created_ids) if changed and same_front else []
        record['after']=native_inventory()
        record['frontmost_preserved']=(before.get('frontmost_bundle_id'),before.get('frontmost_process_id'))==(record['after'].get('frontmost_bundle_id'),record['after'].get('frontmost_process_id'))
        Path(output).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

# 仅原生标签/窗口属性。没有 execute javascript、activate、URL 写入或页面刷新。
INVENTORY = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
set fieldSep to ASCII character 9
with timeout of 5 seconds
tell application "Microsoft Edge"
set resultText to "frontmost" & fieldSep & (frontmost as text)
set allTabCount to 0
repeat with w in windows
set activeId to ""
try
set activeId to id of active tab of w as text
end try
set resultText to resultText & linefeed & "window" & fieldSep & (id of w as text) & fieldSep & (visible of w as text) & fieldSep & (minimized of w as text) & fieldSep & activeId
repeat with t in tabs of w
set allTabCount to allTabCount + 1
set resultText to resultText & linefeed & "all-tab-id" & fieldSep & (id of t as text)
set tabURL to URL of t as text
if tabURL starts with "https://cwzh.ustc.edu.cn/" or tabURL starts with "https://id.ustc.edu.cn/cas/login" then
set resultText to resultText & linefeed & "tab" & fieldSep & (id of w as text) & fieldSep & (id of t as text) & fieldSep & (loading of t as text) & fieldSep & tabURL
end if
end repeat
end repeat
set resultText to resultText & linefeed & "all-tabs-complete" & fieldSep & (allTabCount as text)
return resultText
end tell
end timeout'''

FRONTMOST = '''ObjC.import('AppKit');
var app = $.NSWorkspace.sharedWorkspace.frontmostApplication;
app ? JSON.stringify({bundle_id:ObjC.unwrap(app.bundleIdentifier),process_id:Number(app.processIdentifier)}) : '';'''


def foreground_restore_script(bundle_id, process_id=None):
    """只选择已运行的原应用；不以名称猜替代，不启动应用，不执行网页脚本。"""
    if not isinstance(bundle_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', bundle_id):
        raise ValueError('原前台应用标识不完整，不能恢复')
    if process_id is not None and (type(process_id) is not int or process_id <= 0):
        raise ValueError('原前台进程标识不合法，不能恢复')
    return r'''ObjC.import('AppKit');
(function(){
 const original=ORIGINAL,expectedPid=EXPECTED_PID,edge=EDGE;
 const front=()=>{const app=$.NSWorkspace.sharedWorkspace.frontmostApplication;
  return app?{bundle_id:ObjC.unwrap(app.bundleIdentifier),process_id:Number(app.processIdentifier)}:null;};
 const before=front();
 const result={original_bundle_id:original,expected_process_id:expectedPid,before,requested:false};
 const done=(state)=>JSON.stringify({...result,state,after:front()});
 const allowed=value=>value&&(value.bundle_id===edge||value.bundle_id===original);
 if(!before)return done('frontmost_unavailable');
 if(!allowed(before))return done('user_foreground_preserved');
 if(before.bundle_id===original&&(expectedPid===null||before.process_id===expectedPid))return done('already_preserved');
 const apps=$.NSRunningApplication.runningApplicationsWithBundleIdentifier(original),matches=[];
 for(let i=0;i<Number(apps.count);i++){
  const app=apps.objectAtIndex(i);
  if(expectedPid===null||Number(app.processIdentifier)===expectedPid)matches.push(app);
 }
 if(matches.length!==1)return done(matches.length?'original_app_ambiguous':'original_app_missing');
 // 用户可能在查询运行列表时切换应用；发送恢复请求前重新检查。
 const current=front();
 if(!allowed(current))return done(current?'user_foreground_preserved':'frontmost_unavailable');
 if(current.bundle_id===original&&(expectedPid===null||current.process_id===expectedPid))return done('already_preserved');
 result.requested=true;
 result.activation_accepted=!!matches[0].activateWithOptions($.NSApplicationActivateIgnoringOtherApps);
 return done(result.activation_accepted?'restoration_requested':'restoration_rejected');
})()'''.replace('ORIGINAL', json.dumps(bundle_id)).replace('EXPECTED_PID', json.dumps(process_id)).replace('EDGE', json.dumps(EDGE_BUNDLE))


def restore_foreground(before, current):
    if sys.platform == 'win32':
        return {'state': 'not_requested_by_windows_bridge', 'requested': False}
    original = before.get('frontmost_bundle_id')
    process_id = before.get('frontmost_process_id')
    observed = current.get('frontmost_bundle_id')
    if observed not in [original, EDGE_BUNDLE]:
        return {'state': 'user_foreground_preserved' if observed else 'frontmost_unavailable', 'requested': False}
    if observed == original and (process_id is None or current.get('frontmost_process_id') == process_id):
        return {'state': 'already_preserved', 'requested': False}
    script = foreground_restore_script(original, process_id)
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-'], input=script,
                            text=True, capture_output=True, timeout=5)
    if result.returncode:
        raise RuntimeError('原前台恢复未确认；未启动替代应用：' + result.stderr.strip()[:300])
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get('original_bundle_id') != original or not isinstance(value.get('requested'), bool):
        raise ValueError('原前台恢复回执不完整')
    value['native_probe_performed'] = True
    return value


def native_inventory():
    if mac_native_enabled():
        from mac_edge_bridge import inventory
        return inventory()
    if sys.platform == 'win32':
        from windows_edge_bridge import inventory
        return inventory()
    return native_inventory_apple_event()


def native_inventory_apple_event():
    """Explicit Mac attribute observation; never executes page JS or changes backend."""
    if sys.platform != 'darwin':
        raise RuntimeError('遗留AppleEvent标签属性盘点仅支持Mac；未调用其他后端')
    r = subprocess.run(['/usr/bin/osascript', '-'], input=INVENTORY,
                       text=True, capture_output=True, timeout=8)
    if r.returncode:
        raise RuntimeError('原生标签盘点失败；未创建新界面：' + r.stderr.strip()[:500])
    f = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-'], input=FRONTMOST,
                       text=True, capture_output=True, timeout=5)
    if f.returncode or not f.stdout.strip():
        raise RuntimeError('无法核对系统前台应用；未创建新界面')
    foreground = json.loads(f.stdout)
    if not isinstance(foreground, dict) or not isinstance(foreground.get('bundle_id'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', foreground['bundle_id']) or type(foreground.get('process_id')) is not int or foreground['process_id'] <= 0:
        raise RuntimeError('系统前台应用标识不完整；未创建新界面')
    result = {'observed_at_utc': stamp(), 'edge_frontmost': None,
              'backend': 'edge_applescript', 'tab_id_namespace': 'edge-applescript',
              'frontmost_bundle_id': foreground['bundle_id'], 'frontmost_process_id': foreground['process_id'],
              'windows': [], 'tabs': [], 'all_tab_ids': [], 'tab_inventory_complete': False}
    all_tab_count = None
    for line in r.stdout.splitlines():
        fields = line.split('\t')
        if fields[0] == 'frontmost' and len(fields) == 2:
            result['edge_frontmost'] = fields[1] == 'true'
        elif fields[0] == 'window' and len(fields) == 5:
            result['windows'].append(dict(window_id=fields[1], visible=fields[2] == 'true',
                minimized=fields[3] == 'true', active_tab_id=fields[4]))
        elif fields[0] == 'all-tab-id' and len(fields) == 2:
            result['all_tab_ids'].append(fields[1])
        elif fields[0] == 'all-tabs-complete' and len(fields) == 2 and re.fullmatch(r'[0-9]+', fields[1]):
            all_tab_count = int(fields[1])
        elif fields[0] == 'tab' and len(fields) == 5:
            u = urlsplit(fields[4]); service = parse_qs(u.query).get('service', [''])[0]
            financial = u.hostname == 'cwzh.ustc.edu.cn' or (
                u.hostname == 'id.ustc.edu.cn' and u.path == '/cas/login' and
                urlsplit(service).hostname == 'cwzh.ustc.edu.cn')
            if financial:
                # query/Cookie/session 不进入回执；只保留原生 origin/path/标签信息。
                result['tabs'].append(dict(window_id=fields[1], tab_id=fields[2],
                    loading=fields[3] == 'true', path=u.scheme+'://'+u.netloc+u.path.split(';')[0]))
    if result['edge_frontmost'] is None or not result['windows']:
        raise RuntimeError('Edge 原生盘点不完整；不创建新界面')
    ids = result['all_tab_ids']
    result['tab_inventory_complete'] = (all_tab_count == len(ids) and len(set(ids)) == len(ids)
        and all(isinstance(value, str) and re.fullmatch(r'[0-9]+', value) for value in ids)
        and {tab['tab_id'] for tab in result['tabs']}.issubset(set(ids)))
    return result


def restore_active(before, current, new_ids):
    """仅恢复被本次新增标签取代的原 active；用户自行切到其他标签则保留。"""
    if sys.platform == 'win32':
        raise RuntimeError('Windows开页恢复由Native Messaging原动作完成；不另切换用户标签')
    current_windows = {w['window_id']: w for w in current['windows']}
    targets = [w for w in before['windows'] if w.get('active_tab_id') and
               current_windows.get(w['window_id'], {}).get('active_tab_id') in new_ids and
               current_windows[w['window_id']]['active_tab_id'] != w['active_tab_id']]
    outcomes = []
    for w in targets:
        if not re.fullmatch(r'[0-9]+', w['window_id']) or not re.fullmatch(r'[0-9]+', w['active_tab_id']):
            raise ValueError('原生窗口或标签 ID 不合法，不能恢复')
        allowed = ' or '.join('(id of active tab of w as text) is '+json.dumps(x) for x in new_ids)
        code = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 5 seconds
tell application "Microsoft Edge"
repeat with w in windows
if (id of w as text) is WINDOW_ID then
if not (ALLOWED) then return "user_selection_preserved"
set tabNumber to 0
repeat with t in tabs of w
set tabNumber to tabNumber + 1
if (id of t as text) is TAB_ID then
set active tab index of w to tabNumber
return "restored"
end if
end repeat
return "original_tab_missing"
end if
end repeat
return "original_window_missing"
end tell
end timeout'''.replace('WINDOW_ID', json.dumps(w['window_id'])).replace('TAB_ID', json.dumps(w['active_tab_id'])).replace('ALLOWED', allowed)
        r = subprocess.run(['/usr/bin/osascript', '-'], input=code, text=True,
                           capture_output=True, timeout=8)
        if r.returncode:
            raise RuntimeError('原标签恢复未确认；不切前台、不关闭页面')
        outcomes.append(dict(window_id=w['window_id'], original_active_tab_id=w['active_tab_id'], state=r.stdout.strip()))
    return outcomes


def background_tab_script(window_id, active_tab_id):
    """使用Edge自身字典创建固定财务标签，不经系统URL启动器。"""
    for value in [window_id,active_tab_id]:
        if not isinstance(value,str) or not re.fullmatch(r'[0-9]+',value):raise ValueError('须有原窗口和活动标签')
    return '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 5 seconds
tell application "Microsoft Edge"
repeat with w in windows
if (id of w as text) is WINDOW_ID then
if (id of active tab of w as text) is not ACTIVE_ID then return "selection_changed"
set createdTab to make new tab at end of tabs of w with properties {URL:ENTRY_URL}
set createdId to id of createdTab as text
if (id of active tab of w as text) is createdId then
set tabNumber to 0
repeat with originalTab in tabs of w
set tabNumber to tabNumber + 1
if (id of originalTab as text) is ACTIVE_ID then
set active tab index of w to tabNumber
exit repeat
end if
end repeat
end if
return "created" & (ASCII character 9) & createdId
end if
end repeat
return "window_missing"
end tell
end timeout'''.replace('WINDOW_ID',json.dumps(window_id)).replace('ACTIVE_ID',json.dumps(active_tab_id)).replace('ENTRY_URL',json.dumps(ENTRY))


def finish(record, inventory):
    before = record['before']; old_ids = {x['tab_id'] for x in before['tabs']}
    new = [x for x in inventory['tabs'] if x['tab_id'] not in old_ids and
           (not record.get('native_created_tab_id') or x['tab_id']==record['native_created_tab_id'])]
    new_ids = {x['tab_id'] for x in new}
    record.update(observed=inventory, new_tab_candidates=new,
        creation_state='identified' if len(new) == 1 else 'ambiguous' if new else 'unknown',
        new_tab_id=new[0]['tab_id'] if len(new) == 1 else None)
    # 第三应用表示用户已另有操作；连后台标签恢复也暂缓，避免诱发Edge置前。
    restoration_allowed = inventory.get('frontmost_bundle_id') in [before.get('frontmost_bundle_id'), EDGE_BUNDLE]
    record['active_restoration'] = restore_active(before, inventory, new_ids) if new_ids and restoration_allowed else []
    final = native_inventory() if record['active_restoration'] else inventory
    record['foreground_restoration'] = restore_foreground(before, final)
    if record['foreground_restoration'].get('native_probe_performed'):
        final = native_inventory()  # 接受激活请求不等于已恢复；独立盘点才可确认。
    windows = {w['window_id']: w for w in final['windows']}
    record.update(after=final,
        frontmost_preserved=before['frontmost_bundle_id'] == final['frontmost_bundle_id'] and
            (before.get('frontmost_process_id') is None or before['frontmost_process_id'] == final.get('frontmost_process_id')),
        old_active_tabs_restored=all(windows.get(w['window_id'], {}).get('active_tab_id') == w['active_tab_id'] for w in before['windows']),
        old_finance_tabs_present=all(any(x['tab_id'] == old['tab_id'] and x['path'] == old['path'] for x in final['tabs']) for old in before['tabs']),
        browser_internal_selection_may_have_changed=True,
        system_write=False, browser_ui_changed=True, page_script_executed=False,
        session_cookie_copied=False, edge_foreground_requested=False,
        foreground_requested=record['foreground_restoration'].get('requested', False))
    record['ok'] = bool(record['creation_state'] == 'identified' and record['frontmost_preserved'] and
                        record['old_active_tabs_restored'] and record['old_finance_tabs_present'])
    record['foreground_change_observed']=any(
        (s.get('frontmost_bundle_id'),s.get('frontmost_process_id'))!=(before.get('frontmost_bundle_id'),before.get('frontmost_process_id'))
        for s in [inventory,final])
    record['quiet_verified_at_observation_points']=bool(record['ok'] and not record['foreground_change_observed'] and not record['foreground_requested'])
    if record.get('launch_method')=='native_tab_no_activate':
        record['ok']=record['ok'] and record['quiet_verified_at_observation_points']
    record['next'] = '用实际 new_tab_id 执行正式只读 snapshot；该回执不证明登录或财务页面脚本可用' if record['ok'] else '只盘点本次新增标签/恢复原选择；不盲目再次创建，不刷新关闭旧页'
    return record


def run(case_id, request_id, old_tab_id, output, receipt=None, wait=5):
    if sys.platform == 'win32' or mac_native_enabled():
        raise RuntimeError('Windows不接续Mac旧设备开页回执；使用new-interface并保留本机新请求')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', case_id): raise ValueError('须明确案件')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', request_id): raise ValueError('须使用稳定 request_id')
    if not re.fullmatch(r'[0-9]+', old_tab_id): raise ValueError('须明确要保留的旧 tab_id')
    output = Path(output)
    if output.exists(): raise ValueError('保留旧回执，不覆盖')
    cache = SESSIONS / case_id / (hashlib.sha256(request_id.encode()).hexdigest()+'.json')
    source = Path(receipt) if receipt else cache
    record = json.loads(source.read_text()) if source.exists() else None
    if receipt and record is None: raise ValueError('恢复须有原界面创建回执')
    if record:
        if record.get('schema') != 'ustc-finance-session-v1' or record.get('case_id') != case_id or record.get('request_id') != request_id or record.get('old_tab_id') != old_tab_id:
            raise ValueError('原创建意图与案件/请求/旧标签不一致')
        if receipt:
            record['recovery_source'] = {'path': str(source.resolve()), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    else:
        before = native_inventory()
        if old_tab_id not in {t['tab_id'] for t in before['tabs']}: raise ValueError('旧财务标签不存在，未创建新界面')
        record = dict(schema='ustc-finance-session-v1', case_id=case_id, request_id=request_id,
            old_tab_id=old_tab_id, entry=ENTRY, before=before, started_at_utc=stamp(),
            launch_intent_recorded=True, launch_acknowledged=False, system_write=False,
            launch_method='native_tab_no_activate')
        write(cache, record)  # 先落意图；即使启动响应未知，同一请求也绝不再次 open。
        try:
            target=next(t for t in before['tabs'] if t['tab_id']==old_tab_id)
            window=next(w for w in before['windows'] if w['window_id']==target['window_id'])
            native=subprocess.run(['/usr/bin/osascript','-'],input=background_tab_script(window['window_id'],window['active_tab_id']),
                                  check=True,capture_output=True,text=True,timeout=8)
            match=re.fullmatch(r'created\t([0-9]+)',native.stdout.strip())
            record['launch_acknowledged']=bool(match)
            record['native_created_tab_id']=match.group(1) if match else None
            record['native_launch_result']=native.stdout.strip()[:100]
        except (OSError, subprocess.SubprocessError) as error:
            record['launch_error_type'] = type(error).__name__
        write(cache, record)
    record['reopened'] = False
    try:
        deadline = time.monotonic()+wait
        while True:
            inventory = native_inventory()
            if any(t['tab_id'] not in {x['tab_id'] for x in record['before']['tabs']} for t in inventory['tabs']) or time.monotonic() >= deadline:
                break
            time.sleep(.2)
        finish(record, inventory)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(ok=False, creation_state='unknown', error=str(error)[:700],
            new_tab_id=record.get('new_tab_id'), next='原创建意图已保留，只恢复盘点，不再次 open')
    write(cache, record); write(output, record)
    return record


def prepare_background(case_id,request_id,old_tab_id,output):
    """旧助手浏览器开页通道已停用；保留明确能力回执，不派发替代渠道。"""
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request_id) or not re.fullmatch(r'[0-9]+',old_tab_id):raise ValueError('须有明确案件、请求和旧页')
    if Path(output).exists():raise ValueError('保留旧回执，不覆盖')
    record=dict(schema='ustc-finance-session-capability-v1',case_id=case_id,request_id=request_id,
        old_tab_id=old_tab_id,created_at_utc=stamp(),ok=False,
        state='script_background_open_unavailable',page_opened=False,system_write=False,
        external_action_requested=False,
        next='纯脚本静默开页尚未验收；继续使用现有财务标签的正式脚本，缺能力先修工具，不调用助手电脑操控或浏览器工具')
    if sys.platform == 'win32':
        record.update(state='use_windows_native_new_interface', next='Windows使用正式new-interface默认silent=true；先查capabilities核对本机扩展连接')
    write(output,record)
    return record


def verify_background(case_id,request_id,old_tab_id,output,receipt):
    source=Path(receipt);record=json.loads(source.read_text())
    if record.get('schema')!='ustc-finance-background-plan-v1' or record.get('case_id')!=case_id or record.get('request_id')!=request_id or record.get('old_tab_id')!=old_tab_id:raise ValueError('须引用本案原后台开页计划')
    if record.get('command') not in [{'method':'browser.tabs.new','navigation_url':ENTRY},{'method':'Target.createTarget','params':{'url':ENTRY,'background':True,'newWindow':False}}]:raise ValueError('后台命令发生变化')
    if Path(output).exists():raise ValueError('回执已存在，不覆盖')
    record['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    current=native_inventory();old={t['tab_id'] for t in record['before']['tabs']}
    added=[t for t in current['tabs'] if t['tab_id'] not in old]
    before=record['before']
    record.update(after=current,new_tab_candidates=added,new_tab_id=added[0]['tab_id'] if len(added)==1 else None,
        foreground_preserved=(before['frontmost_bundle_id'],before.get('frontmost_process_id'))==(current['frontmost_bundle_id'],current.get('frontmost_process_id')),
        original_windows_preserved=before['windows']==current['windows'],
        old_tabs_preserved=all(any(t['tab_id']==v['tab_id'] and t['path']==v['path'] for t in current['tabs'])for v in before['tabs']),
        creation_state='identified' if len(added)==1 else 'ambiguous' if added else 'unknown',reopened=False)
    record['ok']=len(added)==1 and added[0]['path']==ENTRY and record['foreground_preserved'] and record['original_windows_preserved'] and record['old_tabs_preserved']
    record['phase']='background_created_observed' if record['ok'] else 'verification_incomplete_do_not_replay'
    write(output,record);return record


def wake_script(window_id, tab_id, original_tab_id):
    for value in [window_id,tab_id,original_tab_id]:
        if not isinstance(value,str) or not re.fullmatch(r'[0-9]+',value):raise ValueError('原生窗口/标签ID不合法')
    return '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 5 seconds
tell application "Microsoft Edge"
if frontmost then return "foreground_refused"
repeat with w in windows
if (id of w as text) is WINDOW_ID then
if (id of active tab of w as text) is not ORIGINAL_ID then return "user_selection_preserved"
set targetIndex to 0
set originalIndex to 0
set tabNumber to 0
repeat with t in tabs of w
set tabNumber to tabNumber + 1
if (id of t as text) is TAB_ID then
if (URL of t as text) is not "https://cwzh.ustc.edu.cn/YBX/main.jsp" then return "path_changed"
set targetIndex to tabNumber
end if
if (id of t as text) is ORIGINAL_ID then set originalIndex to tabNumber
end repeat
if targetIndex is 0 or originalIndex is 0 then return "tab_missing"
if frontmost then return "foreground_refused"
set active tab index of w to targetIndex
delay 0.5
if frontmost then return "user_foreground_preserved"
if (id of active tab of w as text) is not TAB_ID then return "user_selection_preserved"
set originalIndex to 0
set tabNumber to 0
repeat with t in tabs of w
set tabNumber to tabNumber + 1
if (id of t as text) is ORIGINAL_ID then set originalIndex to tabNumber
end repeat
if originalIndex is 0 then return "original_tab_missing"
if frontmost then return "user_foreground_preserved"
set active tab index of w to originalIndex
return "woken_and_restored"
end if
end repeat
return "window_missing"
end tell
end timeout'''.replace('WINDOW_ID',json.dumps(window_id)).replace('TAB_ID',json.dumps(tab_id)).replace('ORIGINAL_ID',json.dumps(original_tab_id))


def wake(case_id, request_id, tab_id, output):
    """仅在Edge后台时唤醒已有固定财务页并恢复原标签；不执行页面脚本或重发业务。"""
    if sys.platform == 'win32' or mac_native_enabled():
        raise RuntimeError('Windows原生扩展不切换标签唤醒；先check-page检查连接，再恢复原marker')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',request_id) or not re.fullmatch(r'[0-9]+',tab_id):raise ValueError('须有合法案件、请求和标签ID')
    output=Path(output)
    if output.exists():raise ValueError('保留旧回执，不覆盖')
    cache=SESSIONS/case_id/('wake-'+hashlib.sha256(request_id.encode()).hexdigest()+'.json')
    if cache.exists():
        record=json.loads(cache.read_text());record['repeated_without_pulse']=True
        write(output,record);return record
    before=native_inventory()
    hits=[t for t in before['tabs'] if t['tab_id']==tab_id and t['path']==ENTRY]
    if len(hits)!=1:raise ValueError('没有唯一的固定财务标签')
    if before['edge_frontmost'] or before['frontmost_bundle_id']==EDGE_BUNDLE:raise ValueError('Edge在前台，不改变用户当前标签')
    windows=[w for w in before['windows'] if w['window_id']==hits[0]['window_id']]
    if len(windows)!=1:raise ValueError('目标原生窗口不唯一')
    record=dict(schema='ustc-finance-wake-v1',case_id=case_id,request_id=request_id,tab_id=tab_id,before=before,started_at_utc=stamp(),intent_recorded=True,system_write=False,page_script_executed=False,edge_foreground_requested=False)
    write(cache,record)
    try:
        script=wake_script(windows[0]['window_id'],tab_id,windows[0]['active_tab_id'])
        native=subprocess.run(['/usr/bin/osascript','-'],input=script,text=True,capture_output=True,timeout=8)
        record['native_result']=native.stdout.strip()
        if native.returncode:raise RuntimeError(native.stderr.strip()[:300])
        after=native_inventory();record['after']=after
        record['frontmost_preserved']=before['frontmost_bundle_id']==after['frontmost_bundle_id'] and before.get('frontmost_process_id')==after.get('frontmost_process_id')
        record['old_active_tabs_restored']=before['windows']==after['windows']
        record['ok']=record['native_result']=='woken_and_restored' and record['frontmost_preserved'] and record['old_active_tabs_restored'] and before['tabs']==after['tabs']
    except (OSError,RuntimeError,subprocess.SubprocessError) as error:
        record.update(ok=False,error=str(error)[:500],unknown_native_result=True)
    record['next']='只读恢复原财务任务；本回执不证明原业务结果或页面脚本可用'
    write(cache,record);write(output,record);return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['check-page','new-interface', 'recover-interface','wake-interface','prepare-background','verify-background'])
    p.add_argument('--case-id', required=True); p.add_argument('--request-id')
    p.add_argument('--expected-userid')
    p.add_argument('--allow-foreground-open', action='store_true', help='仅新开页允许原生短暂置前；其他财务动作仍后台')
    p.add_argument('--tab-id'); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--receipt', type=Path)
    a = p.parse_args()
    if a.action != 'check-page' and not a.request_id: p.error('派发或恢复须有原请求号')
    if (a.action == 'check-page') != bool(a.expected_userid): p.error('check-page须有预期登录人；其他动作不接受该参数')
    if (a.action in ['recover-interface','verify-background']) != bool(a.receipt): p.error('恢复/核验仅引用原回执，创建不接任意 receipt')
    if a.allow_foreground_open and a.action not in ['new-interface','recover-interface']: p.error('开页开关不能用于其他动作')
    if a.action not in ['new-interface','recover-interface'] and not a.tab_id: p.error('此动作须有明确标签ID')
    with browser_lock():
        if a.action == 'check-page':r=check_page(a.case_id,a.tab_id,a.expected_userid,a.output)
        elif a.action=='new-interface' or (a.action=='recover-interface' and json.loads(a.receipt.read_text()).get('schema')=='ustc-finance-page-open-v2'):
            from finance_page_open import open_page
            r=open_page(a.case_id,a.request_id,a.tab_id,a.output,a.receipt,silent=not a.allow_foreground_open)
        elif a.action=='prepare-background':r=prepare_background(a.case_id,a.request_id,a.tab_id,a.output)
        elif a.action=='verify-background':r=verify_background(a.case_id,a.request_id,a.tab_id,a.output,a.receipt)
        elif a.action=='wake-interface':r=wake(a.case_id,a.request_id,a.tab_id,a.output)
        elif a.receipt and json.loads(a.receipt.read_text()).get('schema')=='ustc-finance-background-plan-v1':r=verify_background(a.case_id,a.request_id,a.tab_id,a.output,a.receipt)
        else:r=run(a.case_id, a.request_id, a.tab_id, a.output, a.receipt)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get('ok') else 2


if __name__ == '__main__': raise SystemExit(main())
