"""Windows材料安全IO：逐层持有原生句柄，拒绝重解析，不降级为普通open。

CreateFileW的共享模式和OPEN_REPARSE_POINT、GetFinalPathNameByHandleW为边界；
当前只接受本机盘符路径，UNC/映射盘/设备路径须另行适配后才启用。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import hashlib
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
from contextlib import contextmanager

AVAILABLE = os.name == 'nt' and hasattr(ctypes, 'WinDLL')
READ = 0x80000000
WRITE = 0x40000000
READ_ATTRIBUTES = 0x80
SHARE_READ = 1
OPEN_EXISTING = 3
CREATE_NEW = 1
OPEN_REPARSE_POINT = 0x00200000
BACKUP_SEMANTICS = 0x02000000
REPARSE = 0x400
DIRECTORY = 0x10
DISK = 1
INVALID_HANDLE = ctypes.c_void_p(-1).value


class WindowsSourceIOError(ValueError):
    code = 'windows_source_io_rejected'


class FileInformation(ctypes.Structure):
    _fields_ = [('attributes', wt.DWORD), ('creation', wt.FILETIME), ('access', wt.FILETIME),
                ('write', wt.FILETIME), ('volume', wt.DWORD), ('size_high', wt.DWORD),
                ('size_low', wt.DWORD), ('links', wt.DWORD), ('id_high', wt.DWORD), ('id_low', wt.DWORD)]


class BasicInformation(ctypes.Structure):
    _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                ('write', ctypes.c_longlong), ('change', ctypes.c_longlong), ('attributes', wt.DWORD)]


class FileIdInformation(ctypes.Structure):
    _fields_ = [('volume', ctypes.c_ulonglong), ('identifier', ctypes.c_ubyte * 16)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wt.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wt.BOOL)]


def _api():
    if not AVAILABLE:
        raise WindowsSourceIOError('unsupported_platform: Windows原生安全IO不可用')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateFileW': ([wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE),
        'CreateDirectoryW': ([wt.LPCWSTR, ctypes.c_void_p], wt.BOOL),
        'GetFileType': ([wt.HANDLE], wt.DWORD),
        'GetFileInformationByHandle': ([wt.HANDLE, ctypes.POINTER(FileInformation)], wt.BOOL),
        'GetFileInformationByHandleEx': ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD], wt.BOOL),
        'GetFinalPathNameByHandleW': ([wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD], wt.DWORD),
        'GetDriveTypeW': ([wt.LPCWSTR], wt.UINT),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def local_path(value):
    if not isinstance(value, (str, Path, PureWindowsPath)) or not str(value) or '\0' in str(value):
        raise WindowsSourceIOError('Windows材料路径须为明确绝对路径')
    raw = str(value)
    parsed = PureWindowsPath(raw)
    if (raw.startswith(('\\\\', '//')) or not parsed.is_absolute()
            or not re.fullmatch(r'[A-Za-z]:', parsed.drive) or '..' in parsed.parts):
        raise WindowsSourceIOError('仅支持本机盘符绝对路径；拒绝UNC、设备路径及上级跳转')
    for part in parsed.parts[1:]:
        if (part in ('', '.', '..') or part.rstrip(' .') != part or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or re.match(r'(?i)^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', part)):
            raise WindowsSourceIOError('拒绝备用数据流、设备名或歧义Windows路径')
    return Path(str(parsed))


def _extended(path):
    return '\\\\?\\' + str(path)


def _canonical(value):
    if value.startswith('\\\\?\\'):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def _final_path(api, handle):
    length = api.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not 0 < length <= 32768:
        raise WindowsSourceIOError('无法核对Windows句柄的真实路径')
    buffer = ctypes.create_unicode_buffer(length + 1)
    returned = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not 0 < returned < len(buffer):
        raise WindowsSourceIOError('Windows真实路径回读失败或被截断')
    return buffer.value


def _information(api, handle, expected_path, directory):
    info, basic, identifier = FileInformation(), BasicInformation(), FileIdInformation()
    if api.GetFileType(handle) != DISK or not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise WindowsSourceIOError('Windows来源必须为普通文件或目录，不接受管道/设备')
    if info.attributes & REPARSE:
        raise WindowsSourceIOError('Windows来源不接受软链接、junction或重解析点')
    if bool(info.attributes & DIRECTORY) != directory:
        raise WindowsSourceIOError('Windows来源必须为普通文件' if not directory else 'Windows路径层级必须为目录')
    if _canonical(_final_path(api, handle)) != _canonical(str(expected_path)):
        raise WindowsSourceIOError('Windows句柄真实路径与批准路径不符；拒绝别名或重定向')
    if (not api.GetFileInformationByHandleEx(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic))
            or not api.GetFileInformationByHandleEx(handle, 18, ctypes.byref(identifier), ctypes.sizeof(identifier))):
        raise WindowsSourceIOError('Windows文件身份或稳定性信息不可用，不能降级读取')
    return {'identity': (identifier.volume, bytes(identifier.identifier)),
            'size': (info.size_high << 32) | info.size_low,
            'write': basic.write, 'change': basic.change, 'attributes': info.attributes}


@contextmanager
def _private_security(directory=False):
    from platform_runtime import _security_api, current_user_sid
    _, _, adv, kernel = _security_api()
    descriptor = ctypes.c_void_p()
    flags = 'OICI' if directory else ''
    sddl = 'D:P' + ''.join(f'(A;{flags};FA;;;{sid})' for sid in (current_user_sid(), 'SY', 'BA'))
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise WindowsSourceIOError('无法在创建前设置私有Windows权限')
    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
    try:
        yield ctypes.byref(attributes)
    finally:
        kernel.LocalFree(descriptor)


def _open(api, path, access, creation=OPEN_EXISTING, security=None):
    handle = api.CreateFileW(_extended(path), access, SHARE_READ, security, creation,
                             OPEN_REPARSE_POINT | BACKUP_SEMANTICS, None)
    if handle in (None, INVALID_HANDLE):
        error = ctypes.get_last_error()
        if error in (80, 183):
            raise FileExistsError(error, 'File exists; refusing to overwrite', str(path))
        raise ctypes.WinError(error)
    return handle


class DirectoryLease:
    def __init__(self, api, entries):
        self.api, self.entries = api, entries

    def verify(self):
        for handle, path, identity in self.entries:
            if _information(self.api, handle, path, True)['identity'] != identity:
                raise WindowsSourceIOError('Windows目录身份在使用期间改变')


@contextmanager
def directory(path, create=False):
    """持有从盘符根开始的全部目录句柄，整个使用期间禁止写打开及改名/删除。"""
    path = local_path(path)
    api, entries = _api(), []
    if api.GetDriveTypeW(path.anchor) not in (2, 3):
        raise WindowsSourceIOError('仅验收本机磁盘；网络映射盘须另行适配')
    current = Path(path.anchor)
    try:
        for index in range(len(path.parts)):
            if index:
                current = current / path.parts[index]
            try:
                handle = _open(api, current, READ_ATTRIBUTES)
            except OSError as exc:
                if not create or index == 0 or getattr(exc, 'winerror', None) not in (2, 3):
                    raise
                with _private_security(directory=True) as security:
                    if not api.CreateDirectoryW(_extended(current), security) and ctypes.get_last_error() != 183:
                        raise ctypes.WinError(ctypes.get_last_error())
                handle = _open(api, current, READ_ATTRIBUTES)
            try:
                info = _information(api, handle, current, True)
            except BaseException:
                api.CloseHandle(handle)
                raise
            entries.append((handle, current, info['identity']))
        lease = DirectoryLease(api, entries)
        yield lease
        lease.verify()
    finally:
        for handle, _, _ in reversed(entries):
            api.CloseHandle(handle)


@contextmanager
def open_source(path, approved_roots):
    """返回只读fd；材料及所有祖先句柄维持到调用方结束，不能被改写或替换。"""
    import msvcrt
    path = local_path(path)
    roots = [local_path(root) for root in approved_roots]
    if not any(path != root and path.is_relative_to(root) for root in roots):
        raise WindowsSourceIOError('材料不在本案批准来源根内')
    with directory(path.parent) as lease:
        api = lease.api
        handle, fd = _open(api, path, READ), None
        try:
            before = _information(api, handle, path, False)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None
            yield fd
            after = _information(api, msvcrt.get_osfhandle(fd), path, False)
            if before != after:
                raise WindowsSourceIOError('读取期间来源改变，拒绝采用Windows材料')
            lease.verify()
        finally:
            if fd is not None:
                os.close(fd)
            elif handle is not None:
                api.CloseHandle(handle)


def write_new(data, output):
    """带私有DACL的CREATE_NEW；从创建到回读保持原句柄，绝不覆盖目标。"""
    import msvcrt
    from platform_runtime import is_private_path
    if not isinstance(data, bytes):
        raise TypeError('新输出必须为已冻结字节')
    path = local_path(output)
    with directory(path.parent, create=True) as lease:
        api = lease.api
        with _private_security() as security:
            handle = _open(api, path, READ | WRITE, CREATE_NEW, security)
        fd = None
        try:
            identity = _information(api, handle, path, False)['identity']
            if not is_private_path(path):
                raise WindowsSourceIOError('Windows新输出私有权限回读失败，未写入内容')
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            handle = None
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('Windows输出写入未完成；保留原输出，不覆盖重试')
                view = view[count:]
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            actual = hashlib.sha256()
            while block := os.read(fd, 1024 * 1024):
                actual.update(block)
            info = _information(api, msvcrt.get_osfhandle(fd), path, False)
            if info['identity'] != identity or info['size'] != len(data) or actual.hexdigest() != hashlib.sha256(data).hexdigest():
                raise WindowsSourceIOError('Windows新输出身份或SHA回读不符')
            lease.verify()
        finally:
            if fd is not None:
                os.close(fd)
            elif handle is not None:
                api.CloseHandle(handle)
