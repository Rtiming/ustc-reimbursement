#!/usr/bin/env python3
"""离线验证入口：编译、合成材料单测及Node合同检查，不访问财务系统。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def _posix_inventory():
    """只读取进程身份，不读取命令正文、环境或凭据。"""
    result = subprocess.run(['ps', '-A', '-o', 'pid=', '-o', 'ppid=', '-o', 'pgid=',
                             '-o', 'stat=', '-o', 'lstart='], capture_output=True,
                            text=True, encoding='utf-8', timeout=3,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise OSError('无法核对本次测试的进程树')
    rows = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=4)
        if len(parts) != 5:
            raise OSError('本次测试的进程身份信息不完整')
        pid, parent, group = map(int, parts[:3])
        rows[pid] = {'parent': parent, 'group': group, 'state': parts[3], 'birth': parts[4]}
    return rows


class _PosixTree:
    def __init__(self, process):
        self.process = process
        self.identities = {}
        # Popen尚未回收根进程，其PID仍被占用，不能在此时被其他进程复用。
        root = _posix_inventory().get(process.pid)
        if root is None or root['parent'] != os.getpid() or root['group'] != process.pid:
            raise OSError('无法绑定本次测试的根进程身份')
        self.identities[process.pid] = root['birth']

    def refresh(self):
        rows = _posix_inventory()
        owned = {pid for pid, birth in self.identities.items()
                 if pid in rows and rows[pid]['birth'] == birth}
        if any(rows[pid]['group'] == self.process.pid for pid in owned):
            owned.update(pid for pid, row in rows.items() if row['group'] == self.process.pid)
        while True:
            children = {pid for pid, row in rows.items() if row['parent'] in owned}
            if children <= owned:
                break
            owned.update(children)
        for pid in owned:
            self.identities[pid] = rows[pid]['birth']
        return rows, owned

    def signal_owned(self, pid, sig):
        row = _posix_inventory().get(pid)
        if row and row['birth'] == self.identities.get(pid) and not row['state'].startswith('Z'):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def close(self):
        verified = True
        try:
            # 先停已发现的父进程，再按身份回收；不只依赖进程组编号。
            for _ in range(2):
                rows, owned = self.refresh()
                for pid in owned:
                    self.signal_owned(pid, signal.SIGSTOP)
        except (OSError, ValueError):
            verified = False
        if self.process.poll() is None:
            # 根进程仍存活且由本调用持有，独立进程组只能属于本次检查。
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except OSError:
                verified = False
        deadline = time.monotonic() + 3
        while True:
            try:
                rows, owned = self.refresh()
                live = [pid for pid in owned if not rows[pid]['state'].startswith('Z')]
                for pid in live:
                    self.signal_owned(pid, signal.SIGKILL)
            except (OSError, ValueError):
                verified = False
                break
            if not live:
                break
            if time.monotonic() >= deadline:
                verified = False
                break
            time.sleep(0.05)
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            verified = False
        return {'ok': verified, 'observed_tree_cleared': verified,
                'backend': 'posix_session_and_descendant_identities', 'tracked_processes': len(self.identities),
                'process_identities': self.identities,
                'coverage': '已观察后代和独立进程组；快速脱组双fork孤儿不在证明范围'}


from platform_runtime import WindowsJob as _WindowsJob


def run_step(command, stream, *, cwd, env, timeout=180):
    """有界回收离线检查；POSIX超时不能证明全部快速脱组孤儿已结束。"""
    windows = sys.platform == 'win32'
    owner = None
    process = None
    cleanup = {'ok': True, 'backend': 'not_started'}
    code = 127
    timed_out = False
    try:
        if windows:
            owner = _WindowsJob()
            # 阻塞的启动器进入Job后才允许实际检查执行。
            gate = "import json,subprocess,sys; token=sys.stdin.buffer.read(1); sys.exit(subprocess.call(json.loads(sys.argv[1]),stdin=subprocess.DEVNULL) if token==b'1' else 127)"
            process = subprocess.Popen([sys.executable, '-c', gate, json.dumps(command)], cwd=cwd, env=env,
                                       stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            owner.assign(process)
            process.stdin.write(b'1'); process.stdin.flush(); process.stdin.close()
        else:
            _posix_inventory()  # 进程盘点不可用时不启动检查。
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                owner = _PosixTree(process)
            except (OSError, ValueError):
                # 身份不确定时仅停新建组，明确保留日志未核验状态并停止套件。
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                cleanup = {'ok': False, 'backend': 'posix_identity_unavailable'}
                raise
        deadline = time.monotonic() + timeout
        while True:
            if not windows:
                owner.refresh()
            value = process.poll()
            if value is not None:
                code = value
                break
            if time.monotonic() >= deadline:
                code = 124
                timed_out = True
                stream.write(f'TimeoutExpired: 检查超过{timeout}秒\n')
                break
            time.sleep(0.05)
    except (OSError, ValueError) as exc:
        stream.write(type(exc).__name__ + ': ' + str(exc) + '\n')
    finally:
        if owner is not None:
            cleanup = owner.close()
        if process is not None:
            if windows and process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if windows and process.poll() is None:
                process.kill()  # 赋Job失败时仍未打开启动器的执行闸门。
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cleanup['ok'] = False
        stream.flush()
    if timed_out and not windows:
        cleanup.update(ok=False, reason='POSIX超时无法完整证明快速脱组孤儿已回收；停止后续检查，日志不登记SHA')
    return {'exit_code': code if cleanup['ok'] else 125, 'command_exit_code': code,
            'timed_out': timed_out, 'cleanup': cleanup}


def commands(suite, python):
    modules = sorted(p.stem for p in (ROOT/'tests').glob('test_*.py')
                     if not p.stem.endswith('_mcp') and p.stem != 'test_assistant_smoke')
    compile_code = "import pathlib,py_compile;[py_compile.compile(str(p),doraise=True) for p in pathlib.Path('tools').glob('*.py')];[py_compile.compile(p,doraise=True) for p in ('assistant.py','reimburse.py')]"
    tests = ['discover', '-s', 'tests'] if suite == 'all' else modules
    steps = [('compile', [python, '-c', compile_code]), ('python', [python, '-m', 'unittest', *tests])]
    steps.extend((p.stem, [shutil.which('node') or 'node', str(p)]) for p in sorted((ROOT/'tests').glob('test_*')) if p.suffix in {'.cjs', '.mjs'})
    if suite == 'all':
        steps.append(('mcp_smoke', [python, str(ROOT/'tools/smoke_reimbursement_mcp.py')]))
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['core', 'all'], default='all')
    parser.add_argument('--python', help='显式指定已配置依赖的Python；默认复用项目MCP环境')
    parser.add_argument('--output-dir', type=Path, help='新建结果目录，不覆盖历史验证')
    parser.add_argument('--plan', action='store_true', help='只列命令，不执行')
    parser.add_argument('--keep-going', action='store_true', help='离线诊断时失败后继续独立检查；最终仍返回失败')
    args = parser.parse_args()
    local = ROOT/'.local/mcp-venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python = str(Path(args.python).expanduser().absolute()) if args.python else str(local if local.exists() else Path(sys.executable))
    todo = commands(args.suite, python)
    if args.plan:
        print(json.dumps({'suite': args.suite, 'python': python, 'commands': todo, 'browser_access': False}, ensure_ascii=False))
        return 0
    output = (args.output_dir or ROOT/'.local/checks'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])).resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {'schema': 'reimbursement-project-check-v1', 'suite': args.suite, 'python': python,
              'browser_access': False, 'keep_going': args.keep_going,
              'created_at_utc': datetime.now(timezone.utc).isoformat(), 'checks': []}
    env = dict(os.environ)
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONPATH'] = str(ROOT/'tests')+os.pathsep+env.get('PYTHONPATH', '')
    for name, command in todo:
        start = time.monotonic()
        log = output/(name+'.log')
        live_log = output/(name+'.live.log')
        with live_log.open('w', encoding='utf-8') as stream:
            step = run_step(command, stream, cwd=ROOT, env=env)
            code = step['exit_code']
        if step['cleanup']['ok']:
            # 复制到新inode；遗漏的继承句柄只能改live日志，不能改关闭快照。
            shutil.copyfile(live_log, log)
        result['checks'].append({'name': name, 'command': command, 'exit_code': code,
                                 'command_exit_code': step.get('command_exit_code', code),
                                 'timed_out': step.get('timed_out', False),
                                 'elapsed_seconds': round(time.monotonic()-start, 4),
                                 'log': str(log if step['cleanup']['ok'] else live_log), 'live_log': str(live_log),
                                 'log_closed': step['cleanup']['ok'], 'cleanup': step['cleanup'],
                                 'sha256': hashlib.sha256(log.read_bytes()).hexdigest() if step['cleanup']['ok'] else None})
        if not step['cleanup']['ok'] or (code and not args.keep_going):
            break
    result['ok'] = len(result['checks']) == len(todo) and all(c['exit_code'] == 0 for c in result['checks'])
    result['mcp_checked'] = args.suite == 'all' and result['ok']
    (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'ok': result['ok'], 'suite': args.suite, 'report': str(output/'result.json'),
                      'checks_run': len(result['checks']), 'failed': [c['name'] for c in result['checks'] if c['exit_code']]}, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
