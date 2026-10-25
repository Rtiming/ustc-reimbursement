#!/usr/bin/env python3
"""复用Edge财务登录，通过页面已有的只读菜单查询验证请求能力。"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from finance_backend import mac_native_enabled, mac_target

ROOT = Path(__file__).resolve().parents[1]


def activate_tab(tab_id, foreground=False):
    """校验已运行Edge的财务标签；前台兼容参数须有本次用户新授权，默认静默。"""
    if not str(tab_id).isdigit():
        raise ValueError('须明确当前数字标签ID')
    if mac_native_enabled():
        from mac_edge_bridge import validate_tab
        return validate_tab(tab_id, foreground=foreground, **mac_target())
    if sys.platform == 'win32':
        from windows_edge_bridge import validate_tab
        return validate_tab(tab_id, foreground=foreground)
    if not foreground:
        code = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
tell application "Microsoft Edge"
repeat with w in windows
repeat with t in tabs of w
if (id of t as text) is %s then
if not (URL of t starts with "https://cwzh.ustc.edu.cn/" or URL of t starts with "https://id.ustc.edu.cn/") then error "Wrong finance tab"
return "verified"
end if
end repeat
end repeat
error "Finance tab missing"
end tell''' % json.dumps(str(tab_id))
        r = subprocess.run(['/usr/bin/osascript', '-'], input=code, text=True, capture_output=True, timeout=20)
        if r.returncode and 'EdgeNotRunning' in r.stderr:
            raise RuntimeError('Edge未运行；标签校验不会启动应用')
        if r.returncode or r.stdout.strip() != 'verified': raise RuntimeError('指定财务标签不存在或地址不符')
        return
    code = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
tell application "Microsoft Edge"
repeat with w in windows
set tabNumber to 0
repeat with t in tabs of w
set tabNumber to tabNumber + 1
if (id of t as text) is %s then
if not (URL of t starts with "https://cwzh.ustc.edu.cn/" or URL of t starts with "https://id.ustc.edu.cn/") then error "Wrong finance tab"
set active tab index of w to tabNumber
set minimized of w to false
set index of w to 1
activate
return "activated"
end if
end repeat
end repeat
error "Finance tab missing"
end tell''' % json.dumps(str(tab_id))
    r = subprocess.run(['/usr/bin/osascript', '-'], input=code, text=True, capture_output=True, timeout=20)
    if r.returncode and 'EdgeNotRunning' in r.stderr:
        raise RuntimeError('Edge未运行；标签校验不会启动应用')
    if r.returncode or r.stdout.strip() != 'activated':
        raise RuntimeError('无法恢复指定财务窗口；先核对当前tab-id')


def evaluate(js, tab_id=None, *, timeout=20):
    """单次桥接；timeout 是本次传输预算，未知结果由调用者只读恢复。"""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 20:
        raise ValueError('单次桥接timeout须为0至20秒之间的有限正数')
    if mac_native_enabled():
        from mac_edge_bridge import evaluate as native_evaluate
        return native_evaluate(js, tab_id or os.environ.get('REIMBURSEMENT_EDGE_TAB_ID'), timeout=timeout, **mac_target())
    if sys.platform == 'win32':
        from windows_edge_bridge import evaluate as windows_evaluate
        return windows_evaluate(js, tab_id or os.environ.get('REIMBURSEMENT_EDGE_TAB_ID'), timeout=timeout)
    # AppleEvent 的整秒限额不能大于进程预算；不足1秒时由进程限额兜底。
    apple_timeout = max(1, min(15, math.floor(timeout)))
    literal = json.dumps(js, ensure_ascii=False)
    tab_id = tab_id or os.environ.get('REIMBURSEMENT_EDGE_TAB_ID')
    tab_filter = ' and id of t is ' + json.dumps(str(tab_id)) if tab_id else ''
    code = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of %s seconds
tell application "Microsoft Edge"
set financeTabs to {}
repeat with w in windows
repeat with t in tabs of w
if URL of t contains "https://cwzh.ustc.edu.cn/YBX/main.jsp"%s then
set end of financeTabs to contents of t
end if
end repeat
end repeat
if (count financeTabs) is 0 then error "No matching finance tab"
if (count financeTabs) is not 1 then error "Multiple finance tabs; specify REIMBURSEMENT_EDGE_TAB_ID or --tab-id"
return execute (item 1 of financeTabs) javascript %s
end tell
end timeout''' % (apple_timeout, tab_filter, literal)
    try:
        r = subprocess.run(["/usr/bin/osascript", "-"], input=code, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Edge AppleEvent transport timeout; original operation may have executed') from exc
    if r.returncode:
        if 'EdgeNotRunning' in r.stderr:
            raise RuntimeError('Edge未运行；只读执行不会启动应用')
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def list_tabs():
    if mac_native_enabled():
        from mac_edge_bridge import list_tabs as native_tabs
        return native_tabs()
    if sys.platform == 'win32':
        from windows_edge_bridge import list_tabs as windows_tabs
        return windows_tabs()
    code = '''if application "Microsoft Edge" is not running then return {}
tell application "Microsoft Edge"
set financeTabs to {}
repeat with w in windows
repeat with t in tabs of w
if URL of t contains "https://cwzh.ustc.edu.cn/YBX/main.jsp" then
set end of financeTabs to id of t
end if
end repeat
end repeat
return financeTabs
end tell'''
    r = subprocess.run(['/usr/bin/osascript', '-'], input=code, text=True, capture_output=True, timeout=20)
    if r.returncode: raise RuntimeError(r.stderr.strip())
    return [x.strip() for x in r.stdout.strip().split(',') if x.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--definitions", action="store_true", help="只读取已确认窗口33/41/70的定义")
    ap.add_argument('--tab-id', help='财务标签ID，多标签时必须明确选择')
    ap.add_argument('--list-tabs', action='store_true', help='只列出财务标签ID，不执行页面脚本')
    args = ap.parse_args()
    if args.list_tabs:
        print(json.dumps({'finance_tab_ids': list_tabs()}, ensure_ascii=False))
        return 0
    filename = "finance_definition_probe.js" if args.definitions else "finance_read_probe.js"
    source = (ROOT / "tools" / filename).read_text()
    start = '''(() => {
      const id='reimbursement-read-probe-result';
      if(document.getElementById(id)) throw new Error('A read probe is already active');
      const out=document.createElement('script');out.type='application/json';out.id=id;out.textContent='pending';document.head.appendChild(out);
      const run=document.createElement('script');run.textContent=%s;document.head.appendChild(run);run.remove();
      return 'started';
    })()''' % json.dumps(source, ensure_ascii=False)
    try:
        if evaluate(start, args.tab_id) != "started":
            raise RuntimeError("No finance tab or failed MAIN-world injection")
        for _ in range(30):
            text = evaluate("document.getElementById('reimbursement-read-probe-result')?.textContent || 'missing'", args.tab_id)
            if text not in ("pending", "missing", "missing value", ""):
                result = json.loads(text)
                output = ROOT / ("evidence/authenticated_definitions.json" if args.definitions else "evidence/authenticated_read_probe.json")
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                if args.definitions:
                    summary = {"ok": result.get("ok"), "output": str(output), "error": result.get("error"),
                      "definitions": [{"winno": d["winno"], "title": d["definition"].get("pageName"),
                        "windows": [{"winno": w.get("winno"), "title": w.get("title"),
                          "function": w.get("funcno"), "function_type": w.get("func_type")} for w in d["definition"].get("wins", [])]}
                        for d in result.get("definitions", [])]}
                else:
                    summary = result
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 1
            time.sleep(0.4)
        raise TimeoutError("Read probe did not finish")
    finally:
        evaluate("(() => {document.getElementById('reimbursement-read-probe-result')?.remove();return 'cleaned';})()", args.tab_id)


if __name__ == "__main__":
    raise SystemExit(main())
