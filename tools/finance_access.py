"""科大财务入口与原生CAS登录；凭据由密码库注入，不写进回执。"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from edge_runtime import browser_lock
from operator_support import stamp
from edge_read_probe import activate_tab
from finance_backend import mac_native_enabled, mac_target, transport_identity, assert_original_transport

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 'https://cwzh.ustc.edu.cn/WFManager/home2.jsp'
ORIGINS = ['https://cwzh.ustc.edu.cn', 'http://cwzh.ustc.edu.cn', 'https://id.ustc.edu.cn']

ERROR_MESSAGES = {
    'bridge_timeout': 'Edge原生桥接超时；仅回读原意图，不重发登录。',
    'tab_missing': '指定Edge标签不存在；先核对原界面回执。',
    'wrong_origin': '指定标签不在允许的财务或统一登录来源，已停止。',
    'edge_not_running': 'Edge未运行；财务回读不会启动应用。',
    'bridge_error': 'Edge原生桥接失败；先只读核对，不重发登录。',
    'bridge_invalid_response': 'Edge原生桥接未返回有效状态；先只读核对。',
    'background_entry_intent_retained': '已有自动开页意图；保留原回执，不重复开页。',
    'background_entry_loading': '已创建的原财务标签仍在加载；保留原开页回执，不重复创建。',
    'native_error': '页面检查或原生登录步骤未完成；不重发登录。',
    'wrong_login_page': '当前页面不是已确认的统一登录页，已停止。',
    'wrong_login_service': '统一登录目标不是已确认的财务入口，已停止。',
    'login_controls_not_ready': '统一登录控件尚未就绪，未继续提交。',
    'invalid_request': '入口请求或原回执不匹配，已停止。',
    'credential_unavailable': '密码库凭据不可用；不猜其他密码。',
    'access_failed': '入口或登录操作未完成，先只读检查原回执；不重发登录。',
    'windows_manual_login_required': 'Windows请在本机Edge正常登录；此桥不落盘或转存登录密码。',
    'background_manual_login_required': 'Mac后台通道尚未接通内存凭据登录；请沿本机正常登录页完成登录后再核对，不把密码写入文件队列。',
}
ERROR_STAGES = {'tabs', 'inspect', 'official_entry', 'login_submit', 'finance_entry',
                'validate_request', 'activate_tab', 'credentials', 'access'}


class AccessError(RuntimeError):
    """Only fixed protocol values cross the process/log boundary; never raw stderr."""
    def __init__(self, code, stage):
        self.code = code if isinstance(code, str) and code in ERROR_MESSAGES else 'access_failed'
        self.stage = stage if isinstance(stage, str) and stage in ERROR_STAGES else 'access'
        super().__init__(ERROR_MESSAGES[self.code])

    def record(self):
        return {'ok': False, 'state': 'access_error', 'error_code': self.code,
                'error_stage': self.stage, 'error': str(self), 'system_write': False,
                'automatic_retry': False}


def bridge_run(apple, stage):
    try:
        result = subprocess.run(['/usr/bin/osascript', '-'], input=apple, text=True,
                                capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise AccessError('bridge_timeout', stage) from None
    except OSError:
        raise AccessError('bridge_error', stage) from None
    if result.returncode:
        # Match only terminal OS codes or our exact sentinel, not source echoed by osascript.
        diagnostic = result.stderr or ''
        if re.search(r'\(-1712\)\s*$', diagnostic):
            code = 'bridge_timeout'
        elif re.search(r'\(-27001\)\s*$', diagnostic):
            code = 'tab_missing'
        elif re.search(r'execution error: EdgeNotRunning(?: \(-?\d+\))?\s*$', diagnostic):
            code = 'edge_not_running'
        else:
            code = 'bridge_error'
        raise AccessError(code, stage) from None
    return result.stdout


def guarded_source(source):
    # Wrong origin is reported before evaluating source. Never return exception text/type.
    known = ['wrong_origin', 'wrong_login_page', 'wrong_login_service', 'login_controls_not_ready']
    return ("(()=>{if(!" + json.dumps(ORIGINS) + ".includes(location.origin))"
            "return JSON.stringify({state:'native_error',error_code:'wrong_origin'});"
            "try{return (" + source + ");}catch(e){const code=" + json.dumps(known) +
            ".includes(e.message)?e.message:'native_error';"
            "return JSON.stringify({state:'native_error',error_code:code});}})()")


def evaluate(tab_id, source, *, stage='inspect'):
    if not re.fullmatch(r'\d+', str(tab_id)):
        raise AccessError('invalid_request', 'validate_request')
    if stage not in ERROR_STAGES:
        raise AccessError('invalid_request', 'validate_request')
    if mac_native_enabled() and stage == 'login_submit':
        raise AccessError('background_manual_login_required', stage)
    if sys.platform == 'win32' and stage == 'login_submit':
        raise AccessError('windows_manual_login_required', stage)
    guarded = guarded_source(source)
    apple = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 15 seconds
tell application "Microsoft Edge"
repeat with w in windows
repeat with t in tabs of w
if (id of t as text) is %s then
return execute t javascript %s
end if
end repeat
end repeat
error "Finance tab missing" number -27001
end tell
end timeout''' % (json.dumps(str(tab_id)), json.dumps(guarded, ensure_ascii=False))
    if mac_native_enabled():
        from mac_edge_bridge import evaluate as native_evaluate
        try:
            raw = native_evaluate(guarded, tab_id, access=True, **mac_target())
        except TimeoutError:
            raise AccessError('bridge_timeout', stage) from None
        except RuntimeError:
            raise AccessError('bridge_error', stage) from None
    elif sys.platform == 'win32':
        from windows_edge_bridge import evaluate as windows_evaluate
        try:
            raw = windows_evaluate(guarded, tab_id, access=True)
        except TimeoutError:
            raise AccessError('bridge_timeout', stage) from None
        except RuntimeError:
            raise AccessError('bridge_error', stage) from None
    else:
        raw = bridge_run(apple, stage)
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        raise AccessError('bridge_invalid_response', stage) from None
    if not isinstance(result, dict) or not isinstance(result.get('state'), str):
        raise AccessError('bridge_invalid_response', stage)
    if result['state'] == 'native_error':
        code = result.get('error_code')
        if code not in {'wrong_origin', 'wrong_login_page', 'wrong_login_service', 'login_controls_not_ready'}:
            code = 'native_error'
        raise AccessError(code, stage)
    return result


def tabs():
    if mac_native_enabled():
        from mac_edge_bridge import list_tabs
        return list_tabs(access=True)
    if sys.platform == 'win32':
        from windows_edge_bridge import list_tabs
        return list_tabs(access=True)
    apple = '''if application "Microsoft Edge" is not running then return {}
tell application "Microsoft Edge"
set ids to {}
repeat with w in windows
repeat with t in tabs of w
if URL of t starts with "https://cwzh.ustc.edu.cn/" or URL of t starts with "https://id.ustc.edu.cn/cas/login" then
set end of ids to id of t
end if
end repeat
end repeat
return ids
end tell'''
    raw = bridge_run(apple, 'tabs')
    ids = [s.strip() for s in raw.strip().split(',') if s.strip()]
    if any(not re.fullmatch(r'\d+', value) for value in ids):
        raise AccessError('bridge_invalid_response', 'tabs')
    return ids


def tab_origin(tab_id):
    """登录跳转异常时仅读原生标签来源；不执行页面脚本，不记录任意路径或查询凭据。"""
    if not isinstance(tab_id,str) or not re.fullmatch(r'[0-9]+',tab_id):
        raise AccessError('invalid_request','validate_request')
    if mac_native_enabled():
        from mac_edge_bridge import tab_origin as native_origin
        result = native_origin(tab_id, **mac_target())
        return {'tab_id':tab_id,'origin':result['origin'],'allowed_origin':result['origin'] in ORIGINS,
                'known_path':result['pathname'] if result['pathname'] in ['/','/WFManager/home2.jsp','/YBX/main.jsp','/YBX/login.html'] else None,
                'page_script_executed':False,'system_write':False}
    if sys.platform == 'win32':
        from windows_edge_bridge import request
        result = request('tab-origin', {'tab_id': tab_id})
        return {'tab_id': tab_id, 'origin': result['origin'], 'allowed_origin': result['origin'] in ORIGINS,
                'known_path': result['pathname'] if result['pathname'] in ['/','/WFManager/home2.jsp','/YBX/main.jsp','/YBX/login.html'] else None,
                'page_script_executed': False, 'system_write': False}
    apple='''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 5 seconds
tell application "Microsoft Edge"
repeat with w in windows
repeat with t in tabs of w
if (id of t as text) is TAB_ID then return URL of t
end repeat
end repeat
error "Finance tab missing" number -27001
end tell
end timeout'''.replace('TAB_ID',json.dumps(tab_id))
    raw=bridge_run(apple,'tabs').strip()
    try:
        u=urlsplit(raw)
        origin=u.scheme+'://'+(u.hostname or '')+((':'+str(u.port)) if u.port else '')
    except ValueError:
        raise AccessError('bridge_invalid_response','tabs') from None
    pathname=u.path.split(';')[0] or '/'
    return {'tab_id':tab_id,'origin':origin,'allowed_origin':origin in ORIGINS,
            'known_path':pathname if pathname in ['/','/WFManager/home2.jsp','/YBX/main.jsp','/YBX/login.html'] else None,
            'page_script_executed':False,'system_write':False}


def tab_error_surface(tab_id):
    """仅针对登录来源故障读取错误页身份；不读取表单、业务正文、凭据或执行动作。"""
    native=tab_origin(tab_id)
    if not native['allowed_origin']:
        return {**native,'error_surface_read':False}
    source="location.origin!=='null'?JSON.stringify({origin:location.origin}):JSON.stringify({origin:location.origin,title:document.title,errorCode:document.querySelector('.error-code')?.innerText||null,errorMessage:document.getElementById('main-message')?.innerText||null,heading:document.querySelector('h1')?.innerText||null})"
    apple='''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of 5 seconds
tell application "Microsoft Edge"
repeat with w in windows
repeat with t in tabs of w
if (id of t as text) is TAB_ID then return execute t javascript SOURCE
end repeat
end repeat
error "Finance tab missing" number -27001
end tell
end timeout'''.replace('TAB_ID',json.dumps(tab_id)).replace('SOURCE',json.dumps(source))
    if mac_native_enabled():
        from mac_edge_bridge import evaluate as native_evaluate
        data=json.loads(native_evaluate(source, tab_id, access=True, **mac_target()))
    elif sys.platform == 'win32':
        from windows_edge_bridge import evaluate as windows_evaluate
        data=json.loads(windows_evaluate(source, tab_id, access=True))
    else:
        data=json.loads(bridge_run(apple,'inspect'))
    return {**native,'error_surface_read':True,'page_script_executed':True,'document_origin':data.get('origin'),'document_title':data.get('title'),'error_code':data.get('errorCode'),'error_message':data.get('errorMessage'),'heading':data.get('heading')}



SNAPSHOT = r'''JSON.stringify((()=>{
 const vis=e=>e&&e.getClientRects().length;
 const pathname=location.pathname.replace(/;jsessionid=[^/]+/ig,'');
 if(location.origin==='https://id.ustc.edu.cn'&&pathname==='/cas/login'){
  const service=new URL(location.href).searchParams.get('service');
  const serviceHost=service?new URL(service).hostname:'';
  const user=document.getElementById('nameInput');
  const passwords=[...document.querySelectorAll('input[type=password]')].filter(vis);
  const button=document.getElementById('submitBtn');
  const challenge=[...document.querySelectorAll('input')].some(e=>vis(e)&&/captcha|sms|otp|verification|验证码/i.test(e.name+' '+e.id+' '+e.placeholder));
  const errors=[...document.querySelectorAll('[role=alert],.error,.error-message,.alert-danger')].filter(vis).map(e=>e.textContent.trim()).filter(Boolean);
  return {state:challenge?'challenge_required':errors.length?'login_error':'login_required',path:location.origin+pathname,service_host:serviceHost,username_ready:!!user?.value,password_ready:passwords.length===1&&!!passwords[0].value,login_enabled:!!button&&!button.disabled,errors:errors.map(s=>s.slice(0,180)),login_verified:false};
 }
 const path=location.origin+pathname;
 const financeOrigin=['https://cwzh.ustc.edu.cn','http://cwzh.ustc.edu.cn'].includes(location.origin);
 if(financeOrigin&&pathname==='/YBX/login.html')return {state:'finance_login_entry',path,login_verified:false};
 const business=location.origin==='https://cwzh.ustc.edu.cn'&&pathname==='/YBX/main.jsp';let project=null;
 if(business){
  // AppleScript隔离上下文无法直接读取页面的$，通过主页面只读探针确认业务身份。
  const id='reimbursement-access-'+crypto.randomUUID();const result=document.createElement('script');result.type='application/json';result.id=id;document.head.appendChild(result);
  const run=document.createElement('script');run.textContent='try{document.getElementById('+JSON.stringify(id)+').textContent=JSON.stringify({project:typeof $!=="undefined"&&typeof $.getProjID==="function"?$.getProjID():null});}catch(e){}';
  try{document.head.appendChild(run);project=JSON.parse(result.textContent||'{}').project||null;}finally{run.remove();result.remove();}
 }
 return {state:business?(project==='WF_YBX'?'ready':'finance_loading'):(financeOrigin&&pathname==='/WFManager/home2.jsp'?'finance_home':'finance_unknown_path'),path,login_verified:business&&project==='WF_YBX'};
})())'''

OFFICIAL_ENTRY_NAVIGATION = r'''JSON.stringify((()=>{
 const pathname=location.pathname.replace(/;jsessionid=[^/]+/ig,'');
 if(!['https://cwzh.ustc.edu.cn','http://cwzh.ustc.edu.cn'].includes(location.origin)||pathname!=='/YBX/login.html')throw new Error('wrong_finance_login_entry');
 location.assign('https://cwzh.ustc.edu.cn/WFManager/home2.jsp');
 return {state:'official_finance_entry_opened',login_verified:false};
})())'''


def snapshot(tab_id):
    source=SNAPSHOT
    if sys.platform=='win32':
        # 密码可能刚由内存动作提交；不把站点错误正文写进文件队列或认证回执。
        source=source.replace('errors:errors.map(s=>s.slice(0,180))','errors:[],login_error_present:errors.length>0')
    return evaluate(tab_id, source)


def login_source(username, password, submit=True):
    # 密码只存在于消费者进程内及发送给原生浏览器的stdin，不保留脚本文件。
    return r'''JSON.stringify((()=>{
 const pathname=location.pathname.replace(/;jsessionid=[^/]+/ig,'');
 globalThis.__reimbursementLoginPhase='identity';if(location.origin!=='https://id.ustc.edu.cn'||pathname!=='/cas/login')throw new Error('wrong_login_page');
 const service=new URL(location.href).searchParams.get('service');
 if(!service||new URL(service).hostname!=='cwzh.ustc.edu.cn'||!['http:','https:'].includes(new URL(service).protocol)||!['/WFManager/home2.jsp','/YBX/main.jsp'].includes(new URL(service).pathname.replace(/;jsessionid=[^/]+/ig,'')))throw new Error('wrong_login_service');
 const vis=e=>e&&e.getClientRects().length;
 const notice=[...document.querySelectorAll('button')].find(e=>vis(e)&&e.textContent.trim()==='知道了');if(notice)notice.click();
 const user=document.getElementById('nameInput');const passwords=[...document.querySelectorAll('input[type=password]')].filter(vis);const b=document.getElementById('submitBtn');
 if(!vis(user)||passwords.length!==1||!vis(b)||b.disabled)throw new Error('login_controls_not_ready');
 globalThis.__reimbursementLoginPhase='fields';const set=(e,v)=>{if(v){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,v);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}};
 set(user,USERNAME);passwords[0].focus();set(passwords[0],PASSWORD);passwords[0].blur();
 if(!user.value||!passwords[0].value) return {state:'credential_required',login_submitted:false};
 if(!SUBMIT)return {state:'credentials_prepared',password_ready:true,login_submitted:false};
 if(globalThis.__reimbursementLoginSubmitted)return {state:'login_pending',login_submitted:true,reused:true};
 globalThis.__reimbursementLoginPhase='submit';
 globalThis.__reimbursementLoginSubmitted=true;b.click();return {state:'login_submitted',login_submitted:true};
})())'''.replace('USERNAME', json.dumps(username)).replace('PASSWORD', json.dumps(password)).replace('SUBMIT', json.dumps(submit))


def write(path, record):
    if sys.platform == 'win32':
        from windows_edge_bridge import atomic_private
        atomic_private(path, record)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def ensure(tab_id, request_id, output, username='', password='', timeout=35, foreground=False, session_only=False, wait_for_submitted_login=False):
    if type(session_only) is not bool or (session_only and (username or password)):
        raise ValueError('session_only不得同时携带账号或密码')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', request_id):
        raise ValueError('登录须使用稳定request_id')
    prior = json.loads(output.read_text()) if output.exists() else None
    if prior:
        assert_original_transport(prior)
    if prior and (prior['request_id'] != request_id or str(prior['tab_id']) != str(tab_id)):
        raise ValueError('登录回执与request_id/标签不一致')
    if prior and prior.get('session_only', False) != session_only:
        raise ValueError('同一登录请求的session_only模式不可改变')
    record = prior or {'request_id': request_id, 'tab_id': str(tab_id), 'browser_transport':transport_identity(), 'started_at_utc': stamp(), 'login_submitted': False, 'session_only': session_only}
    if session_only:
        record['credential_accessed'] = False
    deadline = time.monotonic() + timeout
    navigated = record.get('finance_entry_opened', False)
    original_tabs = set(record['entry_tabs_before'] if 'entry_tabs_before' in record else tabs())
    while True:
        s = snapshot(tab_id)
        if navigated and s['state'] == 'finance_home':
            fresh = [id_ for id_ in tabs() if id_ not in original_tabs]
            ready = [id_ for id_ in fresh if snapshot(id_)['state'] == 'ready']
            if len(ready) == 1:
                record.update(result=snapshot(ready[0]), finance_tab_id=ready[0])
                write(output, record)
                return dict(record['result'], tab_id=ready[0], output=str(output), system_write=False)
            if len(ready) > 1:
                return {'state': 'select_finance_tab', 'tab_ids': ready, 'system_write': False}
        record.update(observed_at_utc=stamp(), result=s)
        write(output, record)
        if s['state'] == 'ready':
            return dict(s, tab_id=str(tab_id), output=str(output), system_write=False)
        if s['state'] in ['challenge_required', 'login_error', 'finance_unknown_path']:
            return dict(s, tab_id=str(tab_id), output=str(output), system_write=False)
        if s['state'] == 'finance_login_entry' and not record.get('official_entry_navigation_attempted'):
            # 新 main.jsp 会落到本地登录壳；仅通过已确认官方入口进入既有 CAS。
            # 先落意图，传输未知后同 request 只回读，不再次跳转或提交登录。
            record['official_entry_navigation_attempted'] = True
            write(output, record)
            record['official_entry_navigation_result'] = evaluate(tab_id, OFFICIAL_ENTRY_NAVIGATION, stage='official_entry')
            write(output, record)
        elif s['state'] == 'login_required' and not record['login_submitted']:
            if s['service_host'] != 'cwzh.ustc.edu.cn':
                raise ValueError('CAS登录目标不是财务系统，停止')
            if session_only:
                record.update(session_only=True, credential_accessed=False)
                write(output, record)
                if wait_for_submitted_login:
                    if time.monotonic()>=deadline:return dict(s,state='login_pending',tab_id=str(tab_id),output=str(output),system_write=False)
                    time.sleep(.5)
                    continue
                return dict(s, state='owner_login_required', tab_id=str(tab_id), output=str(output),
                            session_only=True, credential_accessed=False, login_submitted=False, system_write=False)
            if not (s['password_ready'] or password) or not (s['username_ready'] or username):
                return dict(s, state='credential_required', tab_id=str(tab_id), output=str(output), system_write=False)
            # 先留尝试标记。超时后同意图只回读；不重复点击造成账号锁定。
            record['login_submitted'] = True
            write(output, record)
            result = evaluate(tab_id, login_source(username, password), stage='login_submit')
            record['login_result'] = result
            write(output, record)
        elif s['state'] == 'finance_home' and not navigated:
            if s['path'].startswith('http://'):
                evaluate(tab_id, "JSON.stringify((()=>{const path=location.pathname;location.replace('https://cwzh.ustc.edu.cn'+path);return {state:'secure_finance_entry'};})())", stage='official_entry')
                time.sleep(.5)
                continue
            record['entry_tabs_before'] = list(original_tabs)
            record['finance_entry_opened'] = True
            write(output, record)
            result = evaluate(tab_id, r'''JSON.stringify((()=>{if(location.origin!=='https://cwzh.ustc.edu.cn')throw new Error('wrong_origin');const vis=e=>e.getClientRects().length;const candidates=[...document.querySelectorAll('a,button,[onclick]')].filter(e=>vis(e)&&e.textContent.trim()==='智能报销');const leaf=candidates.filter(e=>!candidates.some(x=>x!==e&&e.contains(x)));if(leaf.length!==1)return {state:'finance_entry_required',matches:leaf.length};leaf[0].click();return {state:'finance_entry_opened'};})())''', stage='finance_entry')
            navigated = True
            if result['state'] == 'finance_entry_required':
                return dict(s, state='finance_entry_required', tab_id=str(tab_id), output=str(output), system_write=False)
        if time.monotonic() >= deadline:
            return dict(s, state='login_pending', tab_id=str(tab_id), output=str(output), system_write=False)
        time.sleep(.5)


def run(args):
    ids = tabs()
    if not ids and args.action == 'ensure' and not args.tab_id and not (args.output and args.output.exists()):
        if mac_native_enabled():
            if not args.output or not args.request_id:
                raise ValueError('自动开页须有原请求编号及私有回执')
            from finance_backend import mac_target
            from mac_edge_bridge import reopen_page
            from windows_edge_bridge import atomic_private
            intent=args.output.with_name(args.output.stem+'.open-entry.json')
            if intent.exists():raise AccessError('background_entry_intent_retained','access')
            record={'schema':'ustc-mac-auto-entry-v1','request_id':args.request_id,'phase':'open_intent',
                    'window_id':mac_target()['expected_window_id'],'system_write':False}
            atomic_private(intent,record,exclusive=True)
            with browser_lock():
                opened=reopen_page(window_id=record['window_id'])
            args.tab_id=str(opened['new_tab_id']);ids=[args.tab_id]
            atomic_private(intent,{**record,'phase':'opened','tab_id':args.tab_id,'receipt':opened})
            # 创建回执早于导航完成；只盘点原标签的来源和加载状态，不能立刻注入页面。
            from mac_edge_bridge import inventory
            deadline=time.monotonic()+15
            while True:
                entries=[t for t in inventory().get('tabs',[]) if t.get('tab_id')==args.tab_id
                         and t.get('window_id')==record['window_id']]
                if len(entries)==1 and entries[0].get('loading') is False:break
                if time.monotonic()>=deadline:raise AccessError('background_entry_loading','access')
                time.sleep(.25)
        elif sys.platform == 'win32':
            raise AccessError('windows_manual_login_required', 'access')
        else:
            # 默认后台打开入口，避免登录/续办把系统焦点和输入法切到Edge。
            subprocess.run(['open', '-g', '-a', 'Microsoft Edge', ENTRY], check=True)
            for _ in range(20):
                ids = tabs()
                if ids:
                    break
                time.sleep(.2)
    tab = args.tab_id or (ids[0] if len(ids) == 1 else None)
    if tab is None:
        print(json.dumps({'ok': False, 'state': 'select_finance_tab', 'tab_ids': ids, 'system_write': False}))
        return 2
    # An explicit ID is probed directly: the filtered finance list cannot distinguish
    # a disappeared tab from an existing tab whose origin changed. This probe is read-only.
    args.tab_id = tab
    if args.action == 'check':
        with browser_lock():
            result = dict(snapshot(tab), tab_id=tab, system_write=False)
        if args.output:
            if args.output.exists(): raise ValueError('只读登录回执已存在，保留原回执')
            write(args.output, {'request_id': args.request_id, 'observed_at_utc': stamp(), 'result': result})
    else:
        if not args.request_id or not args.output:
            raise ValueError('ensure须提供request-id及私有回执output')
        if getattr(args, 'session_only', False) or sys.platform == 'win32' or mac_native_enabled():
            # 在读取账号配置、密码库引用或凭据环境变量之前结束这一独立路径。
            if args.with_credentials:
                raise AccessError('background_manual_login_required' if mac_native_enabled() else 'windows_manual_login_required', 'access')
            with browser_lock():
                result = ensure(tab, args.request_id, args.output, session_only=True)
            if ((sys.platform=='win32' or mac_native_enabled()) and result['state']=='owner_login_required' and
                not getattr(args,'session_only',False) and getattr(args,'allow_credential_login',False)):
                from windows_finance_auth import use_credentials
                auth_output=args.output.with_name(args.output.stem+'.auth.json')
                auth=use_credentials(args.case_id,args.request_id,tab,auth_output)
                with browser_lock():
                    if mac_native_enabled() and auth.get('state') in ['login_submitted','session_reused']:
                        current=ensure(tab,args.request_id,args.output.with_name(args.output.stem+'.after-auth.json'),session_only=True,wait_for_submitted_login=auth.get('login_submitted')is True)
                    else:
                        current=snapshot(tab)
                saved=json.loads(args.output.read_text(encoding='utf-8'))
                saved.update(case_id=args.case_id,credential_use_receipt=str(auth_output),credential_use_result=auth,result=current)
                write(args.output,saved)
                result={**current,'tab_id':current.get('tab_id',tab),'output':str(args.output),'system_write':False,
                        'credential_login_state':auth.get('state'),'login_submitted':auth.get('login_submitted'),
                        'credential_values_persisted':False,'credential_use_receipt':str(auth_output)}
            print(json.dumps(dict(ok=result['state']=='ready', **result), ensure_ascii=False))
            return 0 if result['state']=='ready' else 2
        cfg = ROOT / 'data/finance_access.json'
        conf = json.loads(cfg.read_text()) if cfg.exists() else {}
        with browser_lock():
            current = snapshot(tab)
        try:
            activate_tab(tab, foreground=args.foreground)
        except subprocess.TimeoutExpired:
            raise AccessError('bridge_timeout', 'activate_tab') from None
        except (ValueError, RuntimeError, OSError):
            raise AccessError('bridge_error', 'activate_tab') from None
        if current['state'] == 'ready':
            with browser_lock():
                ready = ensure(tab, args.request_id, args.output, foreground=args.foreground)
            print(json.dumps(dict(ok=ready['state']=='ready', **ready), ensure_ascii=False))
            return 0
        if not args.with_credentials:
            # 先沿当前登录壳/已有CAS会话继续；只有确实缺凭据才访问密码库。
            # 子进程复用此处落盘的导航与登录意图，不重复导航或提交。
            with browser_lock():
                observed = ensure(tab, args.request_id, args.output, foreground=args.foreground)
            if observed['state'] != 'credential_required':
                print(json.dumps(dict(ok=observed['state']=='ready', **observed), ensure_ascii=False))
                return 0 if observed['state']=='ready' else 2
        if conf.get('credential_reference') and not args.with_credentials:
            credential_cli = Path.home() / '.ai-skills/rtime-credentials/scripts/cli.py'
            use = {'bindings': {name: {'reference': conf['credential_reference'], 'field': field} for name, field in [('FINANCE_CAS_USERNAME', 'username'), ('FINANCE_CAS_PASSWORD', 'password')]},
                   'argv': [sys.executable, str(Path(__file__).resolve()), 'ensure', '--tab-id', tab, '--request-id', args.request_id, '--output', str(args.output.resolve()), '--with-credentials', *(['--foreground'] if args.foreground else [])], 'timeout': 55}
            try:
                child = subprocess.run([sys.executable, str(credential_cli), 'use'], input=json.dumps(use), text=True, capture_output=True, timeout=60)
                wrapped = json.loads(child.stdout)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                raise AccessError('credential_unavailable', 'credentials') from None
            if child.returncode or not isinstance(wrapped, dict) or not wrapped.get('ok'):
                raise AccessError('credential_unavailable', 'credentials')
            r = wrapped.get('result')
            if not isinstance(r, dict) or type(r.get('returncode')) is not int or not isinstance(r.get('stdout'), str):
                raise AccessError('credential_unavailable', 'credentials')
            if r['returncode']:
                # The child may have a precise bridge failure; propagate only allowlisted
                # codes/stages, never its stdout/stderr (which are credential-adjacent).
                try:
                    failure = json.loads(r['stdout'])
                except (ValueError, TypeError):
                    failure = {}
                if isinstance(failure, dict) and failure.get('state') == 'access_error':
                    raise AccessError(failure.get('error_code'), failure.get('error_stage'))
                raise AccessError('access_failed', 'credentials')
            print(r['stdout'].strip())
            return 0
        username = os.environ.get('FINANCE_CAS_USERNAME', '')
        password = os.environ.get('FINANCE_CAS_PASSWORD', '')
        if conf.get('username') and username and username != conf['username']:
            raise ValueError('密码库账号与已配置账号不一致')
        with browser_lock():
            result = ensure(tab, args.request_id, args.output, username, password)
    print(json.dumps(dict(ok=result['state'] == 'ready', **result), ensure_ascii=False))
    return 0


def persist_failure(args, result):
    """Preserve an existing intent verbatim apart from the latest safe diagnostic."""
    if not args.output:
        return 'not_requested'
    try:
        prior = json.loads(args.output.read_text()) if args.output.exists() else None
        if prior is not None:
            if (args.action != 'ensure' or not isinstance(prior, dict)
                    or prior.get('request_id') != args.request_id
                    or str(prior.get('tab_id')) != str(args.tab_id)):
                return 'existing_receipt_preserved'
            record = prior
        else:
            record = {'request_id': args.request_id, 'tab_id': args.tab_id,
                      'started_at_utc': stamp()}
            if args.action == 'ensure':
                record['login_submitted'] = False
        record.update(observed_at_utc=stamp(), result=result)
        write(args.output, record)
        return 'saved'
    except (OSError, ValueError, TypeError):
        return 'unavailable'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['check', 'ensure', 'diagnose'])
    ap.add_argument('--tab-id')
    ap.add_argument('--request-id')
    ap.add_argument('--output', type=Path)
    ap.add_argument('--with-credentials', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--session-only', action='store_true', help='仅接续当前官方登录会话；不访问账号配置、密码库或凭据环境变量，不提交密码登录')
    ap.add_argument('--case-id', help='本次已授权接续的真实案件；安全凭据登录必需')
    ap.add_argument('--allow-credential-login',action='store_true',help='仅会话缺失且本案明确授权时调用已配置密码库；不注销或重认证已有会话')
    ap.add_argument('--keep-internal-tab', action='store_true', help='后台登录期间保持本财务标签，结束恢复原标签；不激活应用')
    ap.add_argument('--foreground', action='store_true', help='仅供兼容；须本次用户新授权才激活窗口，默认静默且不自动前台回退')
    args = ap.parse_args(argv)
    try:
        if args.session_only and args.action != 'ensure':
            raise AccessError('invalid_request', 'validate_request')
        if args.allow_credential_login and (args.action!='ensure' or args.session_only or (not args.tab_id and not mac_native_enabled()) or
                not isinstance(args.case_id,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',args.case_id)):
            raise AccessError('invalid_request','validate_request')
        if args.tab_id is not None and not re.fullmatch(r'\d+', args.tab_id):
            raise AccessError('invalid_request', 'validate_request')
        if args.action in ['ensure','diagnose'] and (not args.request_id or not args.output
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', args.request_id)):
            raise AccessError('invalid_request', 'validate_request')
        if args.action=='diagnose':
            if not args.tab_id:raise AccessError('invalid_request','validate_request')
            if args.output.exists():raise ValueError('诊断回执已存在，不覆盖')
            with browser_lock():result=tab_error_surface(args.tab_id)
            result.update(ok=True,state='browser_error_page' if result.get('error_code') else 'origin_observed',login_verified=False,observed_at_utc=stamp())
            write(args.output,result)
            print(json.dumps(result,ensure_ascii=False));return 0
        if args.keep_internal_tab:
            if args.action != 'ensure' or not args.tab_id or args.with_credentials: raise AccessError('invalid_request','validate_request')
            from finance_session import quiet_tab_context
            with quiet_tab_context(args.tab_id,args.output.with_suffix('.tab-context.json'),access_pages=True):
                return run(args)
        return run(args)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        failure = exc if isinstance(exc, AccessError) else AccessError(
            'invalid_request' if isinstance(exc, ValueError) else 'access_failed', 'access')
        result = failure.record()
        # Invalid caller values are not copied into a durable receipt.
        if failure.stage != 'validate_request':
            result['error_receipt_status'] = persist_failure(args, dict(result))
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
