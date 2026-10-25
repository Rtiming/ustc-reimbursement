"""本机任务的进程与权限边界；Windows不使用具有终止语义的os.kill(pid, 0)。"""
import os
import json
import subprocess
import sys
import time
from pathlib import Path
from contextlib import contextmanager


@contextmanager
def file_lock(path, *, busy_message='原任务仍持有锁，请等待原任务完成'):
    """共享OS文件锁；本地材料和安装不依赖财务桥支持的平台。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError(busy_message) from error
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(busy_message) from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def windows_bridge_install_lock():
    """同一用户跨源码目录/桌面会话的桥安装串行；退出即释放，不创建全局文件。"""
    if os.name != 'nt':
        raise RuntimeError('Windows安装锁不能在其他平台使用')
    import ctypes
    from ctypes import wintypes as wt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    api.CreateMutexW.restype = wt.HANDLE
    api.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    api.ReleaseMutex.argtypes = [wt.HANDLE]
    api.CloseHandle.argtypes = [wt.HANDLE]
    handle = api.CreateMutexW(None, False, 'Global\\USTC-Reimbursement-Bridge-Install-' + current_user_sid())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        code = api.WaitForSingleObject(handle, 0)
        if code not in (0, 0x80):
            raise RuntimeError('另一个本用户桥安装仍在进行，先查原安装结果')
        acquired = True
        yield
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


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
        'GetSecurityDescriptorControl': ([ctypes.c_void_p, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'GetSecurityDescriptorDacl': ([ctypes.c_void_p, ctypes.POINTER(wt.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.BOOL)], wt.BOOL),
        'GetAclInformation': ([ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_int], wt.BOOL),
        'GetAce': ([ctypes.c_void_p, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)], wt.BOOL),
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
    # Windows会规范化SDDL中的SID别名、ACE顺序和自动继承标记；比较实际权限而非显示字符串。
    control, revision = wt.WORD(), wt.DWORD()
    if not adv.GetSecurityDescriptorControl(buf, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    present, defaulted, acl = wt.BOOL(), wt.BOOL(), ctypes.c_void_p()
    if not adv.GetSecurityDescriptorDacl(buf, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not present.value or not acl.value:
        return False

    class AclSize(ctypes.Structure):
        _fields_ = [('count', wt.DWORD), ('used', wt.DWORD), ('free', wt.DWORD)]

    class AllowedAce(ctypes.Structure):
        _fields_ = [('type', wt.BYTE), ('flags', wt.BYTE), ('size', wt.WORD), ('mask', wt.DWORD), ('sid_start', wt.DWORD)]

    info = AclSize()
    if not adv.GetAclInformation(acl, ctypes.byref(info), ctypes.sizeof(info), 2):
        raise ctypes.WinError(ctypes.get_last_error())
    if info.count != 3:
        return False
    entries = []
    for index in range(info.count):
        pointer = ctypes.c_void_p()
        if not adv.GetAce(acl, index, ctypes.byref(pointer)):
            raise ctypes.WinError(ctypes.get_last_error())
        ace = ctypes.cast(pointer, ctypes.POINTER(AllowedAce)).contents
        if ace.type != 0 or ace.size < 20:
            return False
        sid = wt.LPWSTR()
        if not adv.ConvertSidToStringSidW(ctypes.c_void_p(pointer.value + AllowedAce.sid_start.offset), ctypes.byref(sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entries.append((ace.type, ace.flags, ace.mask, sid.value))
        finally:
            kernel.LocalFree(sid)
    return _private_acl_matches(control.value, entries, current_user_sid(), path.is_dir())


def _private_acl_matches(control, entries, user_sid, is_directory):
    """只接受受保护DACL内当前用户、SYSTEM、Administrators三条完整权限，不放宽给其他主体。"""
    if not control & 0x1000 or len(entries) != 3:
        return False
    flags = 3 if is_directory else 0
    expected = {(0, flags, 0x1f01ff, sid) for sid in (user_sid, 'S-1-5-18', 'S-1-5-32-544')}
    return len(expected) == 3 and set(entries) == expected


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
