#!/usr/bin/env python3
"""Mac 原生 iframe 打印的分阶段后端；默认只读，未适配的对话框明确阻断。

本模块不生成页面、不下载 URL、不调用顶层 print，也不点击任何“打印”按钮。
真实现场尚须逐项验收 TCC、Edge 系统打印面板、目录控件和打印内容。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

SCHEMA = 'ustc-mac-finance-print-v1'
KIND = 'native_html_draft_preview_not_final_print'
MAX_JSON = 2 * 1024 * 1024
MAX_PDF = 64 * 1024 * 1024
STAGES = {'open-system-dialog': 'system_dialog_requested', 'pdf-menu': 'pdf_menu_requested',
          'save-as-pdf': 'pdf_save_panel_requested', 'set-filename': 'filename_set', 'save': 'save_requested'}
PREVIOUS = {'open-system-dialog': {'print_requested', 'print_request_unknown'},
            'pdf-menu': {'print_requested', 'print_request_unknown', 'system_dialog_requested'},
            'save-as-pdf': {'pdf_menu_requested'}, 'set-filename': {'pdf_save_panel_requested'},
            'save': {'filename_set'}}


class _AEDesc(ctypes.Structure):
    # SDK AEDataModel.h在AEDesc周围使用#pragma pack(push, 2)。
    _pack_ = 2
    _fields_ = [('descriptorType', ctypes.c_uint32), ('dataHandle', ctypes.c_void_p)]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')


def _safe(path, *, exists=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('须给明确绝对路径，不接受上级跳转')
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('路径不能经过符号链接')
    if exists and not path.is_file():
        raise ValueError('须有原任务普通文件')
    return path


def _read(path):
    path = _safe(path, exists=True)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON:
            raise ValueError('原任务JSON须为有界普通文件')
        raw = os.read(fd, MAX_JSON + 1)
        after = os.fstat(fd)
        if len(raw) > MAX_JSON or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('原任务文件读取时改变')
        return json.loads(raw), sha(raw)
    finally:
        os.close(fd)


def _new(path, value):
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = encoded(value)
    with path.open('xb') as handle:
        os.chmod(path, 0o600)
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    return sha(raw)


def _checkpoint(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    _new(temporary, value)
    os.replace(temporary, path)


def _norm(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).replace(',', '').replace('，', '')


def validate_request(request, *, allow_existing=False):
    c = dict(request)
    for key, pattern in [('case_id', r'[a-z][a-z0-9_-]{1,63}'), ('serial', r'[0-9]+'),
                         ('tab_id', r'[0-9]+'), ('expected_userid', r'[A-Za-z0-9]{2,40}')]:
        if not isinstance(c.get(key), str) or not re.fullmatch(pattern, c[key]):
            raise ValueError('缺少明确案件身份：' + key)
    if not isinstance(c.get('amount'), str) or not re.fullmatch(r'\d+\.\d{2}', c['amount']) or Decimal(c['amount']) <= 0:
        raise ValueError('金额须为正数及两位小数')
    for key in ['summary', 'amount_anchor']:
        if not isinstance(c.get(key), str) or not c[key].strip() or len(c[key]) > 1000:
            raise ValueError('须给已核预览正文：' + key)
    amounts = re.findall(r'(?<![\d.])\d+\.\d{2}(?!\d)', _norm(c['amount_anchor']))
    if Decimal(c['amount']) not in [Decimal(x) for x in amounts]:
        raise ValueError('金额锚点未含本案准确金额，不从孤立票额猜合计')
    destination = _safe(c.get('destination', ''))
    if destination.suffix.lower() != '.pdf' or (destination.exists() and not allow_existing):
        raise ValueError('须给尚不存在的PDF目标，不覆盖已有文件')
    c['destination'] = str(destination)
    c['ax_backend'] = c.get('ax_backend', 'system-events')
    if c['ax_backend'] not in ['system-events', 'direct-ax-experimental']:
        raise ValueError('只允许明确的已实现AX后端类型')
    if type(c.get('foreground_authorized', False)) is not bool:
        raise ValueError('foreground_authorized须为明确布尔值')
    return c


def _content(text, c):
    normalized = _norm(text)
    serials = re.findall(r'报销单号[:：]([0-9]+)', normalized)
    if not serials or any(x != c['serial'] for x in serials):
        raise ValueError('正文单号缺失或混入其他单据')
    if _norm(c['summary']) not in normalized or _norm(c['amount_anchor']) not in normalized:
        raise ValueError('正文摘要或已核金额锚点不符')


def _observation(observed, c):
    expected = {'ok': True, 'case_id': c['case_id'], 'serial': c['serial'], 'userid': c['expected_userid'],
                'tab_id': c['tab_id'], 'frame_path': 'https://cwzh.ustc.edu.cn/YBX/redirectCustomPage.action'}
    if any(observed.get(key) != value for key, value in expected.items()):
        raise ValueError('原生预览回执身份不符')
    if any(not re.fullmatch(r'[a-f0-9]{64}', str(observed.get(key, ''))) for key in ['preview_html_sha256', 'print_source_sha256']):
        raise ValueError('缺少原生预览与打印函数SHA')
    _content(observed.get('text', ''), c)


# 固定探针。只检查已加载 iframe；ybprint 白名单来自已保存原生 inline 定义。
PAGE_JS = r'''(async()=>{
const c=CONFIG, digest=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),v=>v.toString(16).padStart(2,'0')).join('');
function inspect(){
 if(location.origin!=='https://cwzh.ustc.edu.cn'||location.pathname!=='/YBX/main.jsp'||window.$?.getProjID?.()!=='WF_YBX'||String($.UC.bindData('#61-serialno#'))!==c.serial||String($.UC.bindData('#0-userinfo.userid#'))!==c.expected_userid)throw Error('PRINT_IDENTITY_MISMATCH');
 const frame=document.getElementById('iframepage_print');if(!frame?.getClientRects().length)throw Error('PRINT_IFRAME_NOT_VISIBLE');
 const w=frame.contentWindow,d=frame.contentDocument;
 if(w.location.origin!==location.origin||w.location.pathname!=='/YBX/redirectCustomPage.action'||!d?.body||d.readyState!=='complete'||!d.getElementById('printContent')?.innerText.trim())throw Error('PRINT_NATIVE_HTML_NOT_READY');
 if([...document.querySelectorAll('.ui-dialog')].some(e=>e.getClientRects().length))throw Error('PRINT_OTHER_EDITOR_OPEN');
 if([...d.images].some(e=>!e.complete||e.naturalWidth<1)||d.fonts?.status==='loading')throw Error('PRINT_RESOURCES_NOT_READY');
 const desc=Object.getOwnPropertyDescriptor(w,'ybprint');if(!desc||typeof desc.value!=='function')throw Error('PRINT_OWN_NATIVE_FUNCTION_REQUIRED');
 const source=Function.prototype.toString.call(desc.value),canonical=source.replace(/\/\/[^\r\n]*/g,'').replace(/\s+/g,'');
 if(canonical!=='functionybprint(){CURRFUNC=$.UC.bindData("#0-CURRFUNC#");if(CURRFUNC!="4108"){document.execCommand("print",false,null);}}')throw Error('PRINT_NATIVE_FUNCTION_CHANGED');
 if(String(w.$?.UC?.bindData('#0-CURRFUNC#'))==='4108')throw Error('PRINT_NATIVE_FUNCTION_DISABLED');
 const html=d.documentElement.outerHTML,text=d.body.innerText;if(html.length>2000000||text.length>200000)throw Error('PRINT_PREVIEW_TOO_LARGE');
 return{w,fn:desc.value,html,text,source,frame_path:w.location.origin+w.location.pathname};
}
const a=inspect(),preview_html_sha256=await digest(a.html),print_source_sha256=await digest(a.source),b=inspect();
if(a.w!==b.w||a.fn!==b.fn||a.html!==b.html||a.text!==b.text||a.source!==b.source)throw Error('PRINT_PREVIEW_CHANGED');
const observed={ok:true,case_id:c.case_id,serial:c.serial,userid:c.expected_userid,tab_id:c.tab_id,frame_path:a.frame_path,preview_html_sha256,print_source_sha256,text:a.text};
if(ACTION==='request-print'){
 if(preview_html_sha256!==c.preview_html_sha256||print_source_sha256!==c.print_source_sha256)throw Error('PRINT_CONFIRMED_SHA_MISMATCH');
 const marker=document.getElementById(MARKER);marker.textContent=JSON.stringify({...observed,text:undefined,phase:'native_iframe_print_will_call'});
 b.fn.call(b.w);return{...observed,text:undefined,phase:'native_iframe_print_returned'};
}
return observed;
})()'''


def page_script(c, action, marker):
    if action not in ['preflight', 'request-print'] or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', marker):
        raise ValueError('只接受固定预览核验或原生iframe打印')
    # 不把路径、任意JS或HTML作为执行输入。
    config = {key: c.get(key) for key in ['case_id', 'serial', 'expected_userid', 'tab_id', 'preview_html_sha256', 'print_source_sha256']}
    return PAGE_JS.replace('const c=CONFIG,', 'const c=' + json.dumps(config, ensure_ascii=False) + ',').replace("if(ACTION===", 'if(' + json.dumps(action) + '===').replace('getElementById(MARKER)', 'getElementById(' + json.dumps(marker) + ')')


def permissions():
    """不弹授权提示；结果仅说明本机调用进程，现场仍须验收osascript责任主体。"""
    result = {'darwin': sys.platform == 'darwin', 'accessibility': False, 'edge_automation': False, 'system_events_automation': False}
    if sys.platform != 'darwin':
        return result
    try:
        ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        result['accessibility'] = bool(ax.AXIsProcessTrusted())
        ae = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/Frameworks/AE.framework/AE')
        ae.AECreateDesc.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(_AEDesc)]
        ae.AECreateDesc.restype = ctypes.c_int16
        ae.AEDisposeDesc.argtypes = [ctypes.POINTER(_AEDesc)]
        ae.AEDisposeDesc.restype = ctypes.c_int16
        ae.AEDeterminePermissionToAutomateTarget.argtypes = [ctypes.POINTER(_AEDesc), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
        ae.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
        for key, bundle in [('edge_automation', b'com.microsoft.edgemac'), ('system_events_automation', b'com.apple.systemevents')]:
            desc = _AEDesc()
            if ae.AECreateDesc(int.from_bytes(b'bund', 'big'), bundle, len(bundle), ctypes.byref(desc)) != 0:
                continue
            try:
                result[key] = ae.AEDeterminePermissionToAutomateTarget(ctypes.byref(desc), 0x2a2a2a2a, 0x2a2a2a2a, False) == 0
            finally:
                ae.AEDisposeDesc(ctypes.byref(desc))
    except (OSError, AttributeError):
        pass
    return result


# 不使用键盘、坐标、剪贴板、默认按钮或通用AX动作；只接打印/存储面板。
AX_JS = r'''
const c=CONFIG, se=Application('System Events');
const ps=se.applicationProcesses.whose({bundleIdentifier:'com.microsoft.edgemac'})();
if(ps.length!==1||Number(ps[0].unixId())!==c.process_id)throw Error('PRINT_PROCESS_CHANGED');
const p=ps[0];
if(!p.frontmost())throw Error('PRINT_EDGE_NOT_FRONTMOST');
const windows=p.windows().filter(w=>String(w.name())===c.window_title);
if(windows.length!==1)throw Error('PRINT_WINDOW_NOT_UNIQUE');
function attr(e,n){try{const v=e.attributes.byName(n).value();return ['string','number','boolean'].includes(typeof v)?v:null;}catch(_){return null;}}
const objects=[],nodes=[];let complete=true;
function walk(e,path,depth){
 if(nodes.length>=400||depth>14){complete=false;return;}
 const node={path,role:attr(e,'AXRole'),title:attr(e,'AXTitle'),description:attr(e,'AXDescription'),identifier:attr(e,'AXIdentifier'),value:attr(e,'AXValue'),url:attr(e,'AXURL'),enabled:attr(e,'AXEnabled')};
 for(const k of ['title','description','identifier','value','url'])if(typeof node[k]==='string'&&node[k].length>1024){complete=false;node[k]=node[k].slice(0,1024);}
 nodes.push(node);objects.push(e);let children=[];try{children=e.uiElements();}catch(_){}
 children.forEach((child,i)=>walk(child,path.concat(i),depth+1));
}
let roots=windows[0].sheets();
roots.forEach((e,i)=>walk(e,[i],0));
const snapshot={process_id:c.process_id,window_title:c.window_title,complete,nodes};
if(c.action==='inspect-dialog')return JSON.stringify(snapshot);
if(!complete||JSON.stringify(snapshot)!==JSON.stringify(c.expected))throw Error('PRINT_DIALOG_CHANGED');
const names=n=>[n.title,n.description].filter(v=>typeof v==='string');
const matches=(roles,labels)=>nodes.filter(n=>roles.includes(n.role)&&names(n).some(v=>labels.includes(v))&&n.enabled!==false);
let targets;
if(c.action==='open-system-dialog')targets=matches(['AXButton','AXLink'],['Print using system dialog…','Print using system dialog...','使用系统对话框打印…','使用系统对话框打印...']);
else if(c.action==='pdf-menu')targets=matches(['AXMenuButton','AXPopUpButton'],['PDF']);
else if(c.action==='save-as-pdf')targets=matches(['AXMenuItem'],['Save as PDF','Save as PDF…','存储为 PDF','存储为 PDF…','存储为PDF','存储为PDF…']);
else if(c.action==='set-filename')targets=nodes.filter(n=>n.role==='AXTextField'&&['saveAsNameTextField','NSSaveAsNameField'].includes(n.identifier)&&n.enabled!==false);
else if(c.action==='save')targets=matches(['AXButton'],['Save','存储']);
else throw Error('PRINT_UNKNOWN_ACTION');
if(targets.length!==1)throw Error('PRINT_CONTROL_NOT_UNIQUE_OR_UNSUPPORTED');
const element=objects[nodes.indexOf(targets[0])];
if(c.action==='set-filename')element.attributes.byName('AXValue').value=c.filename;
else element.actions.byName('AXPress').perform();
return JSON.stringify({ok:true,action:c.action,requested:true});
'''


def ax_script(config):
    if config.get('action') not in ['inspect-dialog', *STAGES]:
        raise ValueError('不提供任意AX动作')
    return '(function(){\n' + AX_JS.replace('CONFIG', json.dumps(config, ensure_ascii=False)) + '\n})()'


def _directory(snapshot, directory):
    """只接受原生完整目录路径；目录显示名或文件名不能证明目的目录。"""
    target = str(directory)
    for node in snapshot.get('nodes', []):
        if node.get('role') == 'AXPathControl' and node.get('value') == target:
            return True
        url = node.get('url')
        if isinstance(url, str) and url.startswith('file://'):
            parsed = urlparse(url)
            if parsed.netloc in ['', 'localhost'] and unquote(parsed.path).rstrip('/') == target.rstrip('/'):
                return True
    return False


class NativeMacBackend:
    required_permissions = ['darwin', 'accessibility', 'edge_automation', 'system_events_automation']

    def permissions(self):
        return permissions()

    def context(self, c):
        from finance_session import native_inventory
        observed = native_inventory()
        tabs = [t for t in observed['tabs'] if t['tab_id'] == c['tab_id'] and t['path'] == 'https://cwzh.ustc.edu.cn/YBX/main.jsp']
        if len(tabs) != 1:
            raise ValueError('PRINT_TARGET_TAB_NOT_UNIQUE')
        window = next(w for w in observed['windows'] if w['window_id'] == tabs[0]['window_id'])
        if not observed['edge_frontmost'] or window['active_tab_id'] != c['tab_id']:
            raise ValueError('PRINT_TARGET_MUST_ALREADY_BE_FRONTMOST_ACTIVE_NO_AUTOMATIC_FOCUS')
        # 固定AppleEvent只读取得目标窗口标题，与AX sheet父窗口绑定。
        import subprocess
        source = ('if application "Microsoft Edge" is not running then error "EdgeNotRunning"\n'
                  'tell application "Microsoft Edge"\nreturn name of window id ' + str(int(window['window_id'])) + '\nend tell')
        result = subprocess.run(['/usr/bin/osascript', '-'], input=source, text=True, capture_output=True, timeout=6)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError('PRINT_WINDOW_TITLE_UNAVAILABLE')
        return {'process_id': observed['frontmost_process_id'], 'window_title': result.stdout.strip(), 'window_id': window['window_id'], 'tab_id': c['tab_id']}

    def page(self, c, action, output):
        from edge_read_probe import evaluate
        from edge_runtime import read_marker
        marker = 'reimbursement-result-' + uuid.uuid4().hex
        source = page_script(c, action, marker)
        name = 'request-print' if action == 'request-print' else marker
        script_path, receipt_path = output / (name + '.js'), output / (name + '.json')
        with script_path.open('x', encoding='utf-8') as handle:
            os.chmod(script_path, 0o600); handle.write(source)
        receipt = {'schema': 'ustc-edge-operation-v2', 'phase': 'dispatch_intent', 'created_at_utc': stamp(),
                   'tab_id': c['tab_id'], 'script': str(script_path), 'source_sha256': sha(source.encode()),
                   'result_marker': marker, 'no_replay': True, 'operation_kind': 'read' if action == 'preflight' else 'browser-local'}
        _new(receipt_path, receipt)
        code = "Promise.resolve().then(()=> (" + source + ")).then(v=>{document.getElementById(" + json.dumps(marker) + ").textContent=JSON.stringify(v);}).catch(e=>{document.getElementById(" + json.dumps(marker) + ").textContent=JSON.stringify({ok:false,error:String(e.message)});});"
        injection = "(()=>{const e=document.createElement('script');e.type='application/json';e.id=" + json.dumps(marker) + ";e.textContent='pending';document.head.appendChild(e);const r=document.createElement('script');r.textContent=" + json.dumps(code) + ";document.head.appendChild(r);r.remove();return 'started';})()"
        try:
            if evaluate(injection, c['tab_id'], timeout=6) != 'started':
                raise RuntimeError('PRINT_DISPATCH_NOT_ACKNOWLEDGED')
            if action == 'request-print':
                receipt.update(phase='print_requested', executed_at_utc=stamp())
                _checkpoint(receipt_path, receipt)
                return {'ok': True, 'requested': True, 'receipt': str(receipt_path), 'result_marker': marker}
            result = read_marker(marker, c['tab_id'], timeout=6)
            receipt.update(phase='script_completed', executed_at_utc=stamp(), result=result)
            _checkpoint(receipt_path, receipt)
            if result.get('ok') is not True:
                raise RuntimeError(str(result.get('error', 'PRINT_NATIVE_PROBE_FAILED')))
            return result
        except Exception:
            receipt.update(phase='unknown_recover_original_marker', executed_at_utc=stamp())
            _checkpoint(receipt_path, receipt)
            raise

    def recover_marker(self, c, output):
        from edge_runtime import read_marker
        record, _ = _read(output / 'request-print.json')
        if record.get('tab_id') != c['tab_id'] or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(record.get('result_marker', ''))):
            raise ValueError('缺少原打印请求marker，不另发打印')
        if sha(_safe(record['script'], exists=True).read_bytes()) != record.get('source_sha256'):
            raise ValueError('原打印请求源码SHA变化')
        return read_marker(record['result_marker'], c['tab_id'], timeout=2)

    def ax(self, c, context, action='inspect-dialog', expected=None, filename=None, directory=None):
        import subprocess
        current = self.context(c)
        if current != context:
            raise ValueError('PRINT_BROWSER_CONTEXT_CHANGED')
        config = {**context, 'action': action, 'expected': expected, 'filename': filename}
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-'], input=ax_script(config),
                                text=True, capture_output=True, timeout=8)
        if result.returncode:
            raise RuntimeError('PRINT_AX_BLOCKED: ' + result.stderr.strip()[:300])
        return json.loads(result.stdout)


class DirectAXMacBackend(NativeMacBackend):
    """显式实验选择；复用同一Edge来源探针，只有原生sheet遍历改走C API。"""
    required_permissions = ['darwin', 'accessibility', 'edge_automation']

    def ax(self, c, context, action='inspect-dialog', expected=None, filename=None, directory=None):
        from mac_native_ax import PrintAXSession
        if self.context(c) != context:
            raise ValueError('PRINT_BROWSER_CONTEXT_CHANGED')
        with PrintAXSession(context['process_id'], context['window_title']) as native:
            if action == 'inspect-dialog':
                return native.snapshot()
            return native.perform(action, expected, filename=filename, directory=directory)


def verify_pdf(path, c):
    path = _safe(path, exists=True)
    if not 8 <= path.stat().st_size <= MAX_PDF:
        raise ValueError('原生PDF大小越界')
    raw = path.read_bytes()
    if not raw.startswith(b'%PDF-'):
        raise ValueError('原生保存结果不是PDF')
    import pymupdf
    with pymupdf.open(stream=raw, filetype='pdf') as document:
        if document.needs_pass or document.is_repaired or not 1 <= len(document) <= 2000:
            raise ValueError('PDF加密、修复或页数越界')
        if any(not (abs(min(page.rect.width, page.rect.height) - 595.28) < 4 and abs(max(page.rect.width, page.rect.height) - 841.89) < 4) for page in document):
            raise ValueError('原生PDF纸张不是A4，保留原文件，不重新打印')
        _content('\n'.join(page.get_text() for page in document), c)
        return raw, {'pdf_sha256': sha(raw), 'pages': len(document), 'bytes': len(raw), 'content_verified': True, 'paper': 'A4'}


def _binding(c):
    return {key: c[key] for key in ['case_id', 'serial', 'tab_id', 'expected_userid', 'amount', 'summary', 'amount_anchor', 'destination', 'ax_backend']}


def run(request, output, action='preflight', *, backend=None):
    """每次最多请求一次原生变更；恢复只读，不重新打印/保存。"""
    c = validate_request(request, allow_existing=action in ['verify', 'recover', 'inspect-dialog'])
    from finance_backend import mac_native_enabled
    if mac_native_enabled():
        return {'schema':SCHEMA, 'kind':KIND, **_binding(c), 'action':action, 'system_write':False,
                'final_print_verified':False, 'approval_verified':False, 'executed':False,
                'status':'blocked', 'reason':'native_print_requires_original_unlocked_gui_backend_no_fallback'}
    backend = backend or (DirectAXMacBackend() if c['ax_backend'] == 'direct-ax-experimental' else NativeMacBackend())
    output = _safe(output)
    if action not in ['preflight', 'request-print', 'inspect-dialog', 'recover', 'verify', *STAGES]:
        raise ValueError('未知打印阶段')
    result = {'schema': SCHEMA, 'kind': KIND, **_binding(c), 'action': action, 'system_write': False,
              'final_print_verified': False, 'approval_verified': False, 'executed': False}
    granted = backend.permissions()
    if not all(granted.get(k) is True for k in getattr(backend, 'required_permissions', NativeMacBackend.required_permissions)):
        return {**result, 'status': 'blocked', 'reason': 'native_permissions_not_granted_no_prompt', 'permissions': granted}
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    intent_path = output / 'intent.json'
    # parent统一调度仍须让出财务tab；此锁只覆盖本次有界调用，不跨进程长期占页。
    from edge_runtime import browser_lock
    stack = ExitStack()
    try:
        stack.enter_context(browser_lock())
    except (ValueError, OSError, RuntimeError) as error:
        stack.close()
        return {**result, 'status': 'blocked', 'reason': str(error)[:600], 'no_replay': True}
    with stack:
        try:
            if action == 'preflight':
                if intent_path.exists():
                    raise ValueError('原打印意图已存在，须recover原任务')
                context = backend.context(c)
                observed = backend.page(c, 'preflight', output)
                _observation(observed, c)
                snapshot = backend.ax(c, context)
                if not snapshot.get('complete') or snapshot.get('nodes'):
                    raise ValueError('已有或不完整的原生sheet，不能混入新打印')
                proof = {**result, 'status': 'preflight_only', 'created_at_utc': stamp(), 'native_observed': observed,
                         'context': context, 'source': 'fixed_native_iframe_probe', 'gui_live_verified': False,
                         'needed_inputs': ['preview_html_sha256', 'print_source_sha256', 'preflight_sha256', 'foreground_authorized']}
                _new(output / 'preflight.json', proof)
                return proof
            proof, proof_sha = _read(output / 'preflight.json')
            if proof.get('source') != 'fixed_native_iframe_probe' or any(proof.get(k) != v for k, v in _binding(c).items()):
                raise ValueError('原预检不属于本案同一导出请求')
            if action == 'request-print':
                if intent_path.exists():
                    raise ValueError('原打印请求已留意图，不重复打印；仅recover')
                age = datetime.now(timezone.utc) - datetime.fromisoformat(proof['created_at_utc'])
                if not 0 <= age.total_seconds() <= 300 or c.get('preflight_sha256') != proof_sha or c.get('foreground_authorized') is not True:
                    raise ValueError('须新鲜已核预检SHA及本次打印前台授权')
                observed = backend.page(c, 'preflight', output)
                _observation(observed, c)
                for key in ['preview_html_sha256', 'print_source_sha256']:
                    if not re.fullmatch(r'[a-f0-9]{64}', str(c.get(key, ''))) or c[key] != observed.get(key) or c[key] != proof['native_observed'].get(key):
                        raise ValueError('原生来源/预览SHA未确认或已变化')
                context = backend.context(c)
                if context != proof['context']:
                    raise ValueError('预检后目标窗口变化')
                snapshot = backend.ax(c, context)
                if not snapshot.get('complete') or snapshot.get('nodes'):
                    raise ValueError('已有其他原生sheet，不发打印')
                native_directory = output / 'native-pdf';native_directory.mkdir(mode=0o700)
                intent = {'schema': SCHEMA, 'binding': _binding(c), 'preflight_sha256': proof_sha,
                          'context': context, 'phase': 'print_dispatch_intent', 'started_at_utc': stamp(),
                          'native_pdf': str(native_directory / ('preview-' + uuid.uuid4().hex + '.pdf'))}
                _new(intent_path, intent)
                try:
                    reply = backend.page(c, 'request-print', output)
                    intent.update(phase='print_requested', request_receipt=reply)
                except Exception as error:
                    intent.update(phase='print_request_unknown', error=str(error)[:500])
                _checkpoint(intent_path, intent)
                return {**result, 'status': intent['phase'], 'executed': True, 'next_action': 'inspect-dialog', 'no_replay': True}
            intent, _ = _read(intent_path)
            if intent.get('binding') != _binding(c) or intent.get('preflight_sha256') != proof_sha:
                raise ValueError('须恢复同一打印任务，不换案或目标路径')
            native_pdf = _safe(intent.get('native_pdf', ''))
            if native_pdf.parent != output / 'native-pdf' or not re.fullmatch(r'preview-[a-f0-9]{32}\.pdf', native_pdf.name):
                raise ValueError('原生PDF必须来自原任务私有暂存目录')
            if intent.get('phase') == 'verified':
                _, pdf = verify_pdf(c['destination'], c)
                if pdf != intent.get('pdf'):
                    raise ValueError('已发布PDF与原验收回执不一致，保留文件')
                return {**result, **pdf, 'status': 'verified_existing', 'pdf': c['destination'], 'no_replay': True}
            if action in ['verify', 'recover'] and Path(intent['native_pdf']).exists():
                raw, pdf = verify_pdf(intent['native_pdf'], c)
                current = backend.page(c, 'preflight', output)
                _observation(current, c)
                if any(current.get(k) != proof['native_observed'].get(k) for k in ['preview_html_sha256', 'print_source_sha256']):
                    raise ValueError('原生PDF保存后单据预览改变，保留原文件')
                if intent['phase'] not in ['save_requested', 'save_dispatch_intent', 'save_unknown']:
                    raise ValueError('缺少原生存储意图，不接受任意PDF文件')
                from finance_pdf_export import _atomic_new
                if Path(c['destination']).exists():
                    if _safe(c['destination'], exists=True).read_bytes() != raw:
                        raise ValueError('已有PDF与本次原生暂存结果不一致，不覆盖')
                else:
                    _atomic_new(Path(c['destination']), raw)
                intent.update(phase='verified', pdf=pdf, verified_at_utc=stamp())
                _checkpoint(intent_path, intent)
                return {**result, **pdf, 'status': 'verified', 'executed': True, 'pdf': c['destination']}
            snapshot = backend.ax(c, intent['context'])
            if action in ['inspect-dialog', 'recover']:
                recovery = None
                if action == 'recover' and hasattr(backend, 'recover_marker'):
                    try:
                        recovery = backend.recover_marker(c, output)
                    except Exception as error:
                        recovery = {'state': 'original_marker_unavailable', 'reason': str(error)[:300]}
                return {**result, 'status': 'inspect_only', 'phase': intent['phase'], 'dialog': snapshot, 'original_marker': recovery,
                        'native_save_directory': str(Path(intent['native_pdf']).parent), 'native_filename': Path(intent['native_pdf']).name,
                        'no_replay': True, 'needed_inputs': ['完整已匹配的原生打印/存储sheet；目录必须明确等于native_save_directory，否则阻断，不盲键跳转']}
            if action not in STAGES or intent['phase'] not in PREVIOUS[action]:
                raise ValueError('当前阶段不允许此变更；未知结果只recover，不重复请求')
            if not snapshot.get('complete') or not snapshot.get('nodes'):
                raise ValueError('未取得完整原生打印sheet，不能猜控件')
            if action in ['set-filename', 'save'] and not _directory(snapshot, Path(intent['native_pdf']).parent):
                raise ValueError('原生完整存储目录未证实；本后端不盲键导航目录')
            if action == 'save':
                names = [n.get('value') for n in snapshot['nodes'] if n.get('role') == 'AXTextField' and n.get('identifier') in ['saveAsNameTextField', 'NSSaveAsNameField']]
                if names != [Path(intent['native_pdf']).name] or Path(intent['native_pdf']).exists():
                    raise ValueError('原生存储文件名不符或文件已存在；不覆盖')
            stage_path = output / (action + '.json')
            stage = {'schema': SCHEMA, 'binding': _binding(c), 'action': action, 'phase': 'dispatch_intent',
                     'created_at_utc': stamp(), 'dialog': snapshot, 'no_replay': True}
            _new(stage_path, stage)
            intent.update(phase=action + '_dispatch_intent', last_action=action, last_dialog=snapshot)
            _checkpoint(intent_path, intent)
            try:
                backend.ax(c, intent['context'], action, expected=snapshot, filename=Path(intent['native_pdf']).name, directory=str(Path(intent['native_pdf']).parent))
                intent['phase'] = STAGES[action]
                stage.update(phase='requested', executed_at_utc=stamp())
            except Exception as error:
                intent.update(phase=action + '_unknown', error=str(error)[:500])
                stage.update(phase='unknown', error=str(error)[:500], executed_at_utc=stamp())
            _checkpoint(stage_path, stage)
            _checkpoint(intent_path, intent)
            return {**result, 'status': intent['phase'], 'executed': True, 'no_replay': True, 'next_action': 'verify' if action == 'save' else 'inspect-dialog'}
        except (ValueError, OSError, RuntimeError, TimeoutError) as error:
            return {**result, 'status': 'blocked', 'reason': str(error)[:600], 'intent_retained': intent_path.exists(), 'no_replay': True}


def _case_cli_request(args):
    from validate_config import load_context, money
    required = ['tab_id', 'expected_userid', 'amount_anchor', 'destination']
    if any(getattr(args, key) is None for key in required):
        raise ValueError('--case须同时给--tab-id、--expected-userid、--amount-anchor及--destination')
    case_path = _safe(args.case, exists=True)
    registered, _ = _read(case_path)
    form = load_context(case_path)
    if form != registered:
        raise ValueError('本案form读取时改变')
    if form.get('expected_userid') not in (None, '', args.expected_userid):
        raise ValueError('本案已登记账号与本次账号不同')
    basic = form.get('basic')
    fields = basic.get('fields') if isinstance(basic, dict) else None
    summary = fields.get('t-abst') if isinstance(fields, dict) else None
    request = validate_request({
        'case_id': form['case_id'], 'serial': form['serial'], 'amount': format(money(form['amount']), '.2f'),
        'summary': summary, 'tab_id': args.tab_id, 'expected_userid': args.expected_userid,
        'amount_anchor': args.amount_anchor, 'destination': str(args.destination),
        'ax_backend': args.ax_backend or 'system-events',
        'foreground_authorized': args.foreground_authorized is True,
    }, allow_existing=args.action in ['verify', 'recover', 'inspect-dialog'])
    if args.action != 'preflight':
        proof, proof_sha = _read(_safe(args.output) / 'preflight.json')
        if (not isinstance(proof, dict) or proof.get('schema') != SCHEMA or proof.get('kind') != KIND
                or proof.get('source') != 'fixed_native_iframe_probe' or proof.get('status') != 'preflight_only'
                or proof.get('action') != 'preflight' or any(proof.get(k) != v for k, v in _binding(request).items())
                or not isinstance(proof.get('native_observed'), dict)):
            raise ValueError('原预检不属于本案同一导出请求')
        observed = proof['native_observed']
        _observation(observed, request)
        request.update(preflight_sha256=proof_sha, preview_html_sha256=observed['preview_html_sha256'],
                       print_source_sha256=observed['print_source_sha256'])
    return request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--request', type=Path, help='明确case/serial/user/tab、金额、摘要、金额锚点及PDF目标的JSON')
    source.add_argument('--case', type=Path, help='已登记form JSON绝对路径；单号、金额和摘要只从本案读取')
    parser.add_argument('--tab-id', help='--case入口必填：本次明确的财务标签ID')
    parser.add_argument('--expected-userid', help='--case入口必填：本次已核2至40位字母数字账号')
    parser.add_argument('--amount-anchor', help='--case入口必填：已核原生预览中的完整金额锚点')
    parser.add_argument('--destination', type=Path, help='--case入口必填：明确绝对PDF目的路径，不覆盖')
    parser.add_argument('--ax-backend', choices=['system-events', 'direct-ax-experimental'], help='--case入口的显式AX后端，默认system-events')
    parser.add_argument('--foreground-authorized', action='store_true', default=None, help='--case入口：仅本次明确授权前台打印，不自动授权')
    parser.add_argument('--output', type=Path, required=True, help='本打印任务独立绝对目录，不与其他任务共用')
    parser.add_argument('--action', default='preflight', choices=['preflight', 'request-print', 'inspect-dialog', 'recover', 'verify', *STAGES])
    args = parser.parse_args(argv)
    if args.request is not None and any(getattr(args, key) is not None for key in [
            'tab_id', 'expected_userid', 'amount_anchor', 'destination', 'ax_backend', 'foreground_authorized']):
        parser.error('--request与--case参数不可混用；旧请求JSON保持原有绑定')
    try:
        request = _case_cli_request(args) if args.case is not None else _read(args.request)[0]
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as error:
        parser.error(str(error))
    result = run(request, args.output, args.action)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result['status'] == 'blocked' or 'unknown' in result['status'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
