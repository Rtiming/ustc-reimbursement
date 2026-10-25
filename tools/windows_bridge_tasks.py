"""只把本项目扩展安装器交给当前已登录用户桌面；不接通用命令、不保存密码、不办财务。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from distribution_smoke import verify_members
from platform_runtime import current_user_sid, private_path, is_private_path, file_lock, windows_bridge_install_lock
from windows_edge_bridge import STATE, atomic_private


def powershell(source, data):
    exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    encoded = base64.b64encode(source.encode('utf-16le')).decode('ascii')
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            input=json.dumps(data, ensure_ascii=True).encode('ascii'), capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=35)
    if result.returncode:
        raise RuntimeError('本项目桌面安装任务未取得成功回执；保留原request_id，错误：' + result.stderr.decode('utf-8', errors='replace')[-1600:])
    return json.loads(result.stdout)


LAUNCH = r'''
$ErrorActionPreference='Stop'
$p=[Console]::In.ReadToEnd() | ConvertFrom-Json
if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $p.sid) {throw 'Current user changed'}
$svc=New-Object -ComObject 'Schedule.Service'; $svc.Connect(); $folder=$svc.GetFolder('\')
try { $existing=$folder.GetTask($p.task) } catch { $existing=$null }
if($null -ne $existing) { throw 'Original setup task already exists; do not run it again' }
$task=$svc.NewTask(0)
$task.RegistrationInfo.Description='USTC reimbursement: one-time fixed extension installer, no credentials'
$task.Principal.UserId=$p.sid; $task.Principal.LogonType=3; $task.Principal.RunLevel=0
$task.Settings.Enabled=$true; $task.Settings.AllowDemandStart=$true; $task.Settings.Hidden=$true
$task.Settings.DisallowStartIfOnBatteries=$false; $task.Settings.StopIfGoingOnBatteries=$false
$task.Settings.ExecutionTimeLimit='PT5M'; $task.Settings.MultipleInstances=2
$action=$task.Actions.Create(0); $action.Path=$p.python; $action.Arguments=$p.arguments; $action.WorkingDirectory=$p.cwd
$registered=$folder.RegisterTaskDefinition($p.task,$task,2,$p.sid,$null,3,$null)
$running=$registered.Run($null)
@{registered=$true;task_name=$p.task;instance=$running.InstanceGuid;interactive_token=$true;password_stored=$false}|ConvertTo-Json -Compress
'''

CLEAN = r'''
$ErrorActionPreference='Stop'
$p=[Console]::In.ReadToEnd() | ConvertFrom-Json
if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $p.sid) {throw 'Current user changed'}
$svc=New-Object -ComObject 'Schedule.Service'; $svc.Connect(); $folder=$svc.GetFolder('\')
try { $task=$folder.GetTask($p.task) } catch { @{removed=$false;already_absent=$true}|ConvertTo-Json -Compress; exit }
if($task.Definition.Actions.Count -ne 1 -or $task.Definition.Actions.Item(1).Path -ne $p.python -or $task.Definition.Actions.Item(1).Arguments -ne $p.arguments -or $task.Definition.Actions.Item(1).WorkingDirectory -ne $p.cwd -or $task.Definition.Principal.UserId -ne $p.sid) {throw 'Setup task identity changed'}
if($task.State -eq 4) { @{removed=$false;running=$true}|ConvertTo-Json -Compress; exit }
$folder.DeleteTask($p.task,0); @{removed=$true}|ConvertTo-Json -Compress
'''


def folder_for(request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', request_id):
        raise ValueError('安装须有稳定request_id，重复调用只查原任务')
    key = hashlib.sha256((str(ROOT) + '\0' + request_id).encode()).hexdigest()[:24]
    return STATE / 'setup-jobs' / key


def result(request_id):
    folder = folder_for(request_id)
    request_file, report = folder / 'request.json', folder / 'result.json'
    if not request_file.exists():
        return {'ok': False, 'state': 'setup_request_not_found', 'request_id': request_id, 'system_write': False}
    request = json.loads(request_file.read_text(encoding='utf-8'))
    if (request.get('root') != str(ROOT) or request.get('request_id') != request_id
            or request.get('user_sid') != current_user_sid() or not is_private_path(request_file)):
        raise ValueError('原安装请求不属于当前用户、项目或请求号；不读取结果或清理任务')
    if report.exists():
        answer = json.loads(report.read_text(encoding='utf-8'))
        if not (folder / 'task-cleanup.json').exists():
            try:
                cleanup = powershell(CLEAN, request['task'])
                if cleanup.get('removed') or cleanup.get('already_absent'):
                    atomic_private(folder / 'task-cleanup.json', cleanup)
                answer['setup_task_cleanup'] = cleanup
            except (RuntimeError, OSError, subprocess.SubprocessError) as error:
                answer['setup_task_cleanup'] = {'removed': False, 'error': str(error), 'original_task_preserved': True}
        return {**answer, 'request_id': request_id, 'report_path': str(report)}
    return {'ok': False, 'state': 'setup_running_or_unknown', 'request_id': request_id,
            'original_request': str(request_file), 'system_write': False,
            'next_action': 'bridge installation-result读取原任务，不换request_id再次启动'}


def start(request_id, expected_registration_sha256):
    if os.name != 'nt':
        raise ValueError('桌面安装任务只用于Windows')
    folder = folder_for(request_id)
    from windows_bridge_setup import ensure_private_state
    ensure_private_state()
    with file_lock(STATE / 'setup-request.lock'):
        if (folder / 'request.json').exists():
            previous = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
            if previous['expected_registration_sha256'] != expected_registration_sha256:
                raise ValueError('同一安装request_id不能改变原注册表绑定')
            return result(request_id)
        from windows_bridge_setup import registration_snapshot
        if registration_snapshot()['registration_sha256'] != expected_registration_sha256:
            raise ValueError('先预览安装，再携同一registration_sha256执行')
        folder.mkdir(parents=True, exist_ok=False)
        private_path(folder)
        source = verify_members(ROOT)
        python = Path(sys.executable).with_name('pythonw.exe')
        if not python.is_file():
            python = Path(sys.executable)
        record = {'schema': 'ustc-fixed-bridge-setup-v1', 'request_id': request_id,
                  'root': str(ROOT), 'user_sid': current_user_sid(),
                  'source_tree_sha256': source['source_tree_sha256'],
                  'expected_registration_sha256': expected_registration_sha256}
        # 任务只绑定本文件及固定私有请求；任意命令、脚本和密码均不能成为业务参数。
        payload_file = folder / 'payload.json'
        atomic_private(payload_file, record, exclusive=True)
        digest = hashlib.sha256(payload_file.read_bytes()).hexdigest()
        task = {'sid': record['user_sid'], 'task': 'USTC-Reimbursement-Setup-' + folder.name,
                'python': str(python), 'cwd': str(ROOT),
                'arguments': subprocess.list2cmdline(['-X', 'utf8', '-I', str(Path(__file__).resolve()), '--run', str(payload_file), '--sha256', digest])}
        atomic_private(folder / 'request.json', {**record, 'task': task}, exclusive=True)
        try:
            accepted = powershell(LAUNCH, task)
            atomic_private(folder / 'task-receipt.json', accepted)
        except Exception as error:
            atomic_private(folder / 'launch-error.json', {'error_type': type(error).__name__, 'no_replay': True})
            raise
    return result(request_id)


def run(payload, expected_sha):
    payload = Path(payload).resolve(strict=True)
    if os.name != 'nt' or payload.name != 'payload.json' or not payload.is_relative_to((STATE / 'setup-jobs').resolve()) or not is_private_path(payload):
        raise ValueError('安装工作进程只读本项目的私有冻结请求')
    raw = payload.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('安装请求字节改变，未执行')
    data = json.loads(raw)
    if data.get('root') != str(ROOT) or data.get('user_sid') != current_user_sid() or folder_for(data['request_id']) != payload.parent:
        raise ValueError('安装用户或项目身份不符')
    if verify_members(ROOT)['source_tree_sha256'] != data['source_tree_sha256']:
        raise ValueError('安装程序源码改变，未执行原请求')
    claim = payload.parent / 'claim.json'
    atomic_private(claim, {'claimed': True, 'pid': os.getpid()}, exclusive=True)
    try:
        import ctypes
        from ctypes import wintypes as wt
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.ProcessIdToSessionId.argtypes = [wt.DWORD, ctypes.POINTER(wt.DWORD)]
        session = wt.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) or not session.value:
            raise ValueError('没有当前用户已登录的交互桌面；未安装或登录其他用户')
        from windows_bridge_install_ui import install
        with windows_bridge_install_lock():
            installed = install(data['expected_registration_sha256'])
        report = {'ok': installed.get('ok', False), 'state': 'connected' if installed.get('ok') else 'needs_attention',
                  'session_id': session.value, 'result': installed, 'financial_write_executed': False, 'system_write': False}
    except Exception as error:
        report = {'ok': False, 'state': 'setup_failed_preserve_original', 'error': str(error),
                  'financial_write_executed': False, 'system_write': False, 'no_replay': True}
    atomic_private(payload.parent / 'result.json', report, exclusive=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    run(args.run, args.sha256)
