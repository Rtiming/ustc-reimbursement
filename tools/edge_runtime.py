#!/usr/bin/env python3
"""在已登录Edge财务标签页执行留档脚本，并保留结果与源码哈希。"""
import argparse
import hashlib
import json
import math
import os
import sys
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from edge_read_probe import evaluate, activate_tab
from finance_backend import transport_identity
LOCK_PATH = Path(__file__).resolve().parents[1] / '.local' / 'edge-runtime.lock'


class UnsupportedPlatformError(RuntimeError):
    code = 'unsupported_platform'


@contextmanager
def file_lock(path):
    """OS持有锁；进程退出释放，不靠删除锁文件。"""
    if sys.platform not in ('darwin', 'win32'):
        raise UnsupportedPlatformError('unsupported_platform: 当前原生财务桥仅支持macOS或Windows；未执行浏览器操作')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        if sys.platform == 'win32':
            import msvcrt
            if path.stat().st_size == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError('另一浏览器操作仍在执行；等待原任务，不并发回读或重发') from error
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一浏览器操作仍在执行；等待原任务，不并发回读或重发')
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def browser_lock():
    """所有底层桥接串行执行；Windows和Mac沿同一业务任务边界。"""
    with file_lock(LOCK_PATH):
        yield


def read_marker(marker, tab_id=None, timeout=45):
    """桥接暂时阻塞时只重试读取同一标记，绝不重新注入原操作。"""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('marker读取timeout须为有限正数')
    deadline = time.monotonic() + timeout
    last_error = None
    pause = .35
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(last_error or 'Finance script timed out; original marker retained')
        try:
            raw = evaluate("document.getElementById(" + json.dumps(marker) + ")?.textContent || 'missing'", tab_id,
                           timeout=min(20, remaining))
            if time.monotonic() >= deadline:
                raise TimeoutError('Finance marker read exceeded its budget; original marker retained')
            if raw not in ('pending', 'missing', 'missing value', ''):
                return json.loads(raw)
        except (RuntimeError, TimeoutError) as exc:
            last_error = str(exc)
            # 只有传输超时可重读；权限关闭、异标签等错误不靠重试解决。
            if '-1712' not in last_error and 'timeout' not in last_error.lower():
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(last_error or 'Finance script timed out; original marker retained')
        # The first read is immediate. Slow pages get progressively fewer
        # AppleEvents; the original marker and absolute budget never change.
        time.sleep(min(pause, remaining))
        pause = min(1.5, pause * 1.5)


def main_unlocked(lock_browser=False):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("script", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--quiet", action="store_true", help="只打印结果摘要；完整结果保存在output")
    ap.add_argument("--timeout", type=float, default=45, help="最长等待秒数，不自动重试写入")
    ap.add_argument('--tab-id', help='财务标签ID，多标签时必须明确选择')
    ap.add_argument('--foreground', action='store_true', help='确需用户交互时才激活财务窗口；默认静默后台执行')
    ap.add_argument("--operation-kind", choices=["read", "browser-local", "write", "unknown"], default="unknown", help="仅分类回执；不授予写入权限")
    ap.add_argument("--keep-internal-tab", action="store_true", help="仅Edge后台时保持固定财务标签，结束恢复；不激活应用")
    args = ap.parse_args()
    if not 1 <= args.timeout <= 60: ap.error("timeout须为1至60秒")
    if args.output.exists():
        raise ValueError('操作回执已存在，禁止覆盖或重放；先recover/readback核对')
    source = args.script.read_text(encoding='utf-8')
    marker = "reimbursement-result-" + uuid.uuid4().hex
    # 后台标签的计时器会节流；微任务延后到当前调用栈结束，外层仍立即返回started。
    main_code = "Promise.resolve().then(()=> (" + source + ")).then(value=>{const el=document.getElementById(" + json.dumps(marker) + ");if(el)el.textContent=JSON.stringify(value);}).catch(e=>{const el=document.getElementById(" + json.dumps(marker) + ");if(el)el.textContent=JSON.stringify({ok:false,error:String(e.message)});});"
    start = '''(() => {
      if(location.origin!=='https://cwzh.ustc.edu.cn') throw new Error('Wrong origin');
      const out=document.createElement('script');out.type='application/json';out.id=%s;out.textContent='pending';document.head.appendChild(out);
      const run=document.createElement('script');run.textContent=%s;document.head.appendChild(run);run.remove();return 'started';
    })()''' % (json.dumps(marker), json.dumps(main_code, ensure_ascii=False))
    started = False
    attempted = False
    completed = False
    transport = None
    transport_error = None
    try:
        transport = transport_identity()
    except ValueError as exc:
        # Invalid platform/backend/binding must still retain a not-dispatched
        # receipt, without manufacturing a usable transport identity.
        transport_error = (UnsupportedPlatformError(str(exc))
                           if sys.platform not in ('darwin', 'win32') else exc)
    record = {"schema": "ustc-edge-operation-v2", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "browser_transport": transport,
              "script": str(args.script.resolve()), "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "source_bytes": len(source.encode()), "injection_bytes": len(start.encode()),
              "tab_id": args.tab_id, "operation_kind": args.operation_kind, "result_marker": marker,
              "phase": "intent_prepared", "no_replay": True,
              "result": {"ok": False, "execution_status": "prepared_not_dispatched", "result_marker": marker}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 排他落盘在任何浏览器调用之前；即使进程被杀也留下原marker和源码身份。
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(record, output, ensure_ascii=False, indent=2)
        output.write('\n'); output.flush(); os.fsync(output.fileno())

    def checkpoint():
        temporary = args.output.with_name(args.output.name+'.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, args.output)

    quiet_context=nullcontext()
    if args.keep_internal_tab:
        from finance_session import quiet_tab_context
        quiet_context=quiet_tab_context(args.tab_id,args.output.with_suffix('.tab-context.json'))
    try:
        if transport_error is not None:
            raise transport_error
        with (browser_lock() if lock_browser else nullcontext()), quiet_context:
            if args.tab_id:
                activate_tab(args.tab_id, foreground=args.foreground)
            injection_error = None
            record.update(phase='dispatch_intent', dispatch_intent_at_utc=datetime.now(timezone.utc).isoformat())
            record['result'] = {'ok': False, 'execution_status': {'read':'unknown_read_recover_original_marker','browser-local':'unknown_browser_local_recover_original_marker'}.get(args.operation_kind,'unknown_verify_before_retry'), 'result_marker': marker, 'injection_acknowledged': False}
            checkpoint()
            attempted = True
            try:
                if evaluate(start, args.tab_id) != 'started':
                    raise RuntimeError('No finance tab or MAIN-world execution failed')
                started = True
            except (RuntimeError, TimeoutError) as exc:
                injection_error = str(exc)
                if '-1712' not in injection_error and 'timeout' not in injection_error.lower():
                    raise
            record.update(phase='await_original_marker', injection_acknowledged=started, injection_transport_error=injection_error)
            checkpoint()
            data = read_marker(marker, args.tab_id, args.timeout)
            record.update(executed_at_utc=datetime.now(timezone.utc).isoformat(), phase='script_completed', result=data)
            checkpoint()
            completed = True
            summary = {"ok": data.get("ok", True), "output": str(args.output),
                       "error": data.get("error"), "currentPage": data.get("currentPage"),
                       "gridCount": len(data.get("grids", [])), "formCount": len(data.get("forms", []))}
            print(json.dumps(summary if args.quiet else data, ensure_ascii=False, indent=2))
            # 清理也处于同一浏览器互斥区间，不能和下一请求并发。
            try:
                evaluate("(() => {document.getElementById(" + json.dumps(marker) + ")?.remove();return 'cleaned';})()", args.tab_id)
            except Exception:
                pass
            return 0 if not isinstance(data, dict) or data.get("ok", True) else 1
    except Exception as exc:
        status = ({'read':'unknown_read_recover_original_marker','browser-local':'unknown_browser_local_recover_original_marker'}.get(args.operation_kind,'unknown_verify_before_retry')) if attempted else 'not_dispatched'
        record.update(executed_at_utc=datetime.now(timezone.utc).isoformat(), phase='unknown' if attempted else 'not_dispatched',
                      result={"ok": False, "error": str(exc), "execution_status": status,
                              "injection_acknowledged": started, "result_marker": marker})
        if isinstance(exc, UnsupportedPlatformError):
            record['result']['error_code'] = exc.code
        checkpoint()
        summary = {"ok": False, "output": str(args.output), "error": str(exc),
                   "execution_status": status, "operation_kind": args.operation_kind}
        if isinstance(exc, UnsupportedPlatformError):
            summary['error_code'] = exc.code
        print(json.dumps(summary, ensure_ascii=False))
        return 1


def main():
    return main_unlocked(lock_browser=True)


if __name__ == "__main__":
    raise SystemExit(main())
