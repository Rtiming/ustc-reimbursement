"""本机任务的进程与权限边界；Windows不使用具有终止语义的os.kill(pid, 0)。"""
import os
import json
import subprocess
import sys
import time
from pathlib import Path


def background_popen_kwargs():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def start_command(command, *, cwd, env, stdout, stderr):
    """Windows实际命令在Job绑定后启动；门卫参数经管道传递，避免嵌套命令行。"""
    if os.name != 'nt':
        return subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                stdin=subprocess.DEVNULL, **background_popen_kwargs()), None
    owner = WindowsJob()
    process = None
    gate = ('import json,subprocess,sys; line=sys.stdin.buffer.readline(); '
            'sys.exit(subprocess.call(json.loads(line),stdin=subprocess.DEVNULL) if line else 127)')
    try:
        process = subprocess.Popen([sys.executable, '-c', gate], cwd=cwd, env=env, stdout=stdout,
                                   stderr=stderr, stdin=subprocess.PIPE, **background_popen_kwargs())
        owner.assign(process)
        process.stdin.write(json.dumps(command, ensure_ascii=True).encode('ascii') + b'\n')
        process.stdin.flush()
        process.stdin.close()
        return process, owner
    except Exception:
        if process is not None:
            process.kill()
            process.wait(timeout=5)
        owner.close()
        raise


def process_alive(pid):
    """确定退出返回False，权限/身份不可判断返回None；从不向进程发终止信号。"""
    if type(pid) is not int or not 0 < pid <= 0xffffffff:
        return None
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return None
    import ctypes
    from ctypes import wintypes as wt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    api.OpenProcess.restype = wt.HANDLE
    api.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    api.WaitForSingleObject.restype = wt.DWORD
    api.CloseHandle.argtypes = [wt.HANDLE]
    handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE，只查询退出状态。
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        result = api.WaitForSingleObject(handle, 0)
        return True if result == 258 else False if result == 0 else None
    finally:
        api.CloseHandle(handle)


def _security_api():
    import ctypes
    from ctypes import wintypes as wt
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'OpenProcessToken': ([wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], wt.BOOL),
        'GetTokenInformation': ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'ConvertSidToStringSidW': ([ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)], wt.BOOL),
        'ConvertStringSecurityDescriptorToSecurityDescriptorW': ([wt.LPCWSTR, wt.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p], wt.BOOL),
        'SetFileSecurityW': ([wt.LPCWSTR, wt.DWORD, ctypes.c_void_p], wt.BOOL),
        'GetFileSecurityW': ([wt.LPCWSTR, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'ConvertSecurityDescriptorToStringSecurityDescriptorW': ([ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(wt.LPWSTR), ctypes.c_void_p], wt.BOOL),
    }
    for name, (args, result) in signatures.items():
        getattr(adv, name).argtypes = args
        getattr(adv, name).restype = result
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    return ctypes, wt, adv, kernel


def current_user_sid():
    ctypes, wt, adv, kernel = _security_api()
    token = wt.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wt.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wt.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def _expected_dacl(path):
    # 管理员和SYSTEM保留恢复能力；拒绝普通其他用户及继承的宽泛授权。
    flags = 'OICI' if Path(path).is_dir() else ''
    return 'D:P' + ''.join(f'(A;{flags};FA;;;{sid})' for sid in [current_user_sid(), 'SY', 'BA'])


def _has_reparse(path):
    path = Path(path).absolute()
    paths = [path, *path.parents] if os.name == 'nt' else [path]
    return any(item.is_symlink() or (getattr(item.lstat(), 'st_file_attributes', 0) & 0x400) for item in paths)


def private_path(path):
    """在敏感内容写入之前设置本机私有权限；失败立即停止，不降级为chmod。"""
    path = Path(path)
    if _has_reparse(path):
        raise ValueError('私有运行路径不能是符号链接或reparse point')
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    ctypes, wt, adv, kernel = _security_api()
    descriptor = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(_expected_dacl(path), 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not adv.SetFileSecurityW(str(path.resolve()), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
    if not is_private_path(path):
        raise PermissionError('Windows运行文件ACL回读不符合当前用户隔离要求')


def is_private_path(path):
    path = Path(path)
    if _has_reparse(path):
        return False
    if os.name != 'nt':
        return (path.stat().st_mode & 0o777) == (0o700 if path.is_dir() else 0o600)
    ctypes, wt, adv, kernel = _security_api()
    size = wt.DWORD()
    adv.GetFileSecurityW(str(path.resolve()), 4, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if not adv.GetFileSecurityW(str(path.resolve()), 4, buf, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    text = wt.LPWSTR()
    if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf, 1, 4, ctypes.byref(text), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value == _expected_dacl(path)
    finally:
        kernel.LocalFree(text)


class WindowsJob:
    """私有内核Job绑定本次命令及后代，包括另建进程组的后代。"""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wt.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                        ('active_limit', wt.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wt.DWORD), ('scheduling', wt.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', Counters), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                        ('peak_job_memory', ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ('user', 'kernel', 'period_user', 'period_kernel')]
            _fields_ += [(name, wt.DWORD) for name in ('page_faults', 'total', 'active', 'terminated')]
        self.ctypes, self.accounting = ctypes, Accounting
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wt.LPCWSTR], wt.HANDLE),
            'SetInformationJobObject': ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD], wt.BOOL),
            'QueryInformationJobObject': ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p], wt.BOOL),
            'AssignProcessToJobObject': ([wt.HANDLE, wt.HANDLE], wt.BOOL),
            'TerminateJobObject': ([wt.HANDLE, wt.UINT], wt.BOOL),
            'OpenProcess': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            'CloseHandle': ([wt.HANDLE], wt.BOOL),
        }
        for name, (args, result) in signatures.items():
            getattr(self.api, name).argtypes = args
            getattr(self.api, name).restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits(); limits.basic.flags = 0x2000  # 关闭Job时结束其进程
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.api.CloseHandle(self.handle)
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        handle = self.api.OpenProcess(0x0101, False, process.pid)  # 仅赋Job和结束进程所需权限
        if not handle:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)

    def close(self):
        ok = bool(self.api.TerminateJobObject(self.handle, 124))
        active = None
        try:
            deadline = time.monotonic() + 3
            while ok:
                state = self.accounting()
                ok = bool(self.api.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(state),
                                                            self.ctypes.sizeof(state), None))
                active = state.active if ok else None
                if not ok or active == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        finally:
            ok = bool(self.api.CloseHandle(self.handle)) and ok
            self.handle = None
        return {'ok': ok and active == 0, 'backend': 'windows_kill_on_close_job', 'active_processes': active}
