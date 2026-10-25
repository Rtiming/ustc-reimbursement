"""固定后台worker启动：Windows由本机WMI创建，避免随MCP客户端的Job关闭。

只接收已冻结的本项目request.json，不暴露通用命令。业务命令仍由worker的
Windows Job Object管理；本入口不创建计划任务、不启动服务、不提高权限。
"""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from platform_runtime import background_popen_kwargs, current_user_sid, is_private_path, private_path, process_alive


class WorkerLaunchUnknown(RuntimeError):
    """启动请求可能已经到达WMI；原任务只能检查，不能换号重放。"""


class DetachedWorker:
    def __init__(self, pid):
        self.pid = pid

    def wait(self):
        while process_alive(self.pid) is True:
            time.sleep(0.25)
        return None  # 任务结果从status.json读取，不用PID退出推断成功。


_WMI_LAUNCH = r"""
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$stage='system_modules'
try {
# 只从本机Windows PowerShell读取系统模块，避免继承其他客户端的PSModulePath。
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules/CimCmdlets/CimCmdlets.psd1') -ErrorAction Stop
$stage='input'
$p=[Console]::In.ReadToEnd() | ConvertFrom-Json
$stage='startup'
$startup=New-CimInstance -Namespace root/cimv2 -ClassName Win32_ProcessStartup -ClientOnly -Property @{
 ShowWindow=[uint16]0; CreateFlags=[uint32]0x09000200; EnvironmentVariables=[string[]]$p.environment
}
$stage='dispatch'
$r=Invoke-CimMethod -Namespace root/cimv2 -ClassName Win32_Process -MethodName Create -Arguments @{
 CommandLine=[string]$p.command_line; CurrentDirectory=[string]$p.cwd; ProcessStartupInformation=$startup
}
[ordered]@{return_value=[int]$r.ReturnValue;pid=[int]$r.ProcessId} | ConvertTo-Json -Compress
} catch {
 [ordered]@{launcher_error=$true;stage=$stage;error_type=$_.Exception.GetType().FullName} | ConvertTo-Json -Compress
 exit 1
}
"""


def launch_worker(python, request_file, *, cwd):
    request_file, cwd = Path(request_file).resolve(strict=True), Path(cwd).resolve(strict=True)
    if request_file.name != 'request.json' or not is_private_path(request_file):
        raise ValueError('worker_request_must_be_private')
    if os.name != 'nt':
        with (request_file.parent/'worker.log').open('ab') as log:
            return subprocess.Popen([str(python), str(cwd/'tools/assistant_job.py'), str(request_file)],
                cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **background_popen_kwargs())
    digest = hashlib.sha256(request_file.read_bytes()).hexdigest()
    argv = [str(python), str(cwd/'tools/assistant_worker_launch.py'), '--run', str(request_file),
            '--sha256', digest, '--user-sid', current_user_sid()]
    allowed = {'PATH','SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA',
               'PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)','COMMONPROGRAMFILES','COMSPEC','USERNAME','USERDOMAIN'}
    environment = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    payload = {'command_line':subprocess.list2cmdline(argv), 'cwd':str(cwd),
               'environment':[k+'='+v for k,v in environment.items()]}
    powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    encoded = base64.b64encode(_WMI_LAUNCH.encode('utf-16le')).decode('ascii')
    try:
        process = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            input=json.dumps(payload, ensure_ascii=True).encode('ascii'), capture_output=True,
            timeout=60, **background_popen_kwargs())
        result = json.loads(process.stdout)
        if process.returncode or not isinstance(result, dict) or type(result.get('return_value')) is not int:
            detail = ':'.join(str(result.get(k, 'unknown')) for k in ('stage', 'error_type')) if isinstance(result, dict) else 'invalid_response'
            detail = re.sub(r'[^A-Za-z0-9_.:]', '', detail)[:160]
            raise WorkerLaunchUnknown('Windows后台启动结果未知（'+detail+'）；只检查原任务')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise WorkerLaunchUnknown('Windows后台启动结果未知（'+type(exc).__name__+'）；只检查原任务') from None
    if result['return_value'] != 0:
        raise RuntimeError('Windows后台启动已拒绝，代码'+str(result['return_value']))
    if type(result.get('pid')) is not int or result['pid'] <= 0:
        raise WorkerLaunchUnknown('Windows后台启动缺少进程回执；只检查原任务')
    return DetachedWorker(result['pid'])


def run_frozen(request_file, digest, expected_sid):
    from assistant_api import atomic_json
    path = Path(request_file)
    if os.name != 'nt' or current_user_sid() != expected_sid:
        raise ValueError('worker_identity_mismatch')
    if path.name != 'request.json' or not path.is_absolute() or not is_private_path(path):
        raise ValueError('worker_request_must_be_private')
    raw = path.read_bytes()
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('worker_request_changed')
    request = json.loads(raw)
    expected = Path(request['workspace'])/'.local/assistant_jobs'/request['case_id']/path.parent.name/'request.json'
    if path.resolve() != expected.resolve() or not re.fullmatch(r'j_[0-9a-f]{24}', path.parent.name):
        raise ValueError('worker_request_location_mismatch')
    claim = path.parent/'worker_claim.json'
    with claim.open('x', encoding='utf-8') as stream:
        private_path(claim)
        json.dump({'worker_pid':os.getpid(),'request_sha256':digest,'backend':'windows_local_wmi'}, stream)
    atomic_json(path.parent/'process.json', {'worker_pid':os.getpid(),'backend':'windows_local_wmi','request_sha256':digest})
    log = path.parent/'worker.log'
    with log.open('a', encoding='utf-8') as stream:
        private_path(log)
        with redirect_stdout(stream), redirect_stderr(stream):
            from assistant_job import run
            run(path.resolve(), frozen_request=request)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--user-sid', required=True)
    args = parser.parse_args()
    run_frozen(args.run, args.sha256, args.user_sid)
