"""当前用户限定的Windows命名管道；认证载荷只经过内存，不接任意命令。"""
import ctypes
from ctypes import wintypes as wt
import json
import os
import struct
import threading
import time

MAX_FRAME=32768


class PipeError(RuntimeError):
    pass


def _api():
    if os.name!='nt': raise PipeError('windows_required')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateNamedPipeW':([wt.LPCWSTR,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,wt.DWORD,ctypes.c_void_p],wt.HANDLE),
        'ConnectNamedPipe':([wt.HANDLE,ctypes.c_void_p],wt.BOOL),
        'DisconnectNamedPipe':([wt.HANDLE],wt.BOOL),
        'CreateFileW':([wt.LPCWSTR,wt.DWORD,wt.DWORD,ctypes.c_void_p,wt.DWORD,wt.DWORD,wt.HANDLE],wt.HANDLE),
        'WaitNamedPipeW':([wt.LPCWSTR,wt.DWORD],wt.BOOL),
        'GetNamedPipeServerProcessId':([wt.HANDLE,ctypes.POINTER(wt.ULONG)],wt.BOOL),
        'PeekNamedPipe':([wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.c_void_p,ctypes.POINTER(wt.DWORD),ctypes.c_void_p],wt.BOOL),
        'ReadFile':([wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p],wt.BOOL),
        'WriteFile':([wt.HANDLE,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD),ctypes.c_void_p],wt.BOOL),
        'CloseHandle':([wt.HANDLE],wt.BOOL),
        'FlushFileBuffers':([wt.HANDLE],wt.BOOL),
    }
    for name,(args,result) in signatures.items():
        getattr(api,name).argtypes=args;getattr(api,name).restype=result
    return api


def _valid_handle(handle):
    return bool(handle) and handle!=ctypes.c_void_p(-1).value


def _read(api,handle,count,deadline):
    result=bytearray()
    while len(result)<count:
        if time.monotonic()>=deadline: raise PipeError('auth_transport_timeout')
        available=wt.DWORD()
        if not api.PeekNamedPipe(handle,None,0,None,ctypes.byref(available),None): raise PipeError('auth_pipe_disconnected')
        if not available.value:
            time.sleep(.01);continue
        size=min(available.value,count-len(result));buffer=ctypes.create_string_buffer(size);received=wt.DWORD()
        if not api.ReadFile(handle,buffer,size,ctypes.byref(received),None): raise PipeError('auth_pipe_disconnected')
        result.extend(buffer.raw[:received.value])
    return bytes(result)


def read_message(api,handle,deadline):
    size=struct.unpack('<I',_read(api,handle,4,deadline))[0]
    if not 0<size<=MAX_FRAME: raise PipeError('invalid_auth_frame')
    try: return json.loads(_read(api,handle,size,deadline).decode('utf-8'))
    except (ValueError,UnicodeError): raise PipeError('invalid_auth_frame') from None


def write_message(api,handle,value):
    body=json.dumps(value,ensure_ascii=True,allow_nan=False).encode('ascii')
    if len(body)>MAX_FRAME: raise PipeError('invalid_auth_frame')
    frame=struct.pack('<I',len(body))+body;written=wt.DWORD()
    if not api.WriteFile(handle,frame,len(frame),ctypes.byref(written),None) or written.value!=len(frame):
        raise PipeError('auth_pipe_disconnected')


def call(name,server_pid,packet,timeout=20):
    if not isinstance(name,str) or not name.startswith('\\\\.\\pipe\\ustc-reimbursement-auth-'):
        raise PipeError('invalid_auth_pipe')
    api=_api()
    deadline=time.monotonic()+2
    while True:
        handle=None
        if api.WaitNamedPipeW(name,100):
            handle=api.CreateFileW(name,0xC0000000,0,None,3,0,None)
            if _valid_handle(handle):break
        if ctypes.get_last_error() not in [2,121,231,233] or time.monotonic()>=deadline:
            raise PipeError('auth_pipe_unavailable')
        # 服务端收回上一连接并重建实例的短暂间隙；此时尚未写任何认证载荷。
        time.sleep(.01)
    try:
        observed=wt.ULONG()
        if not api.GetNamedPipeServerProcessId(handle,ctypes.byref(observed)) or observed.value!=server_pid:
            raise PipeError('auth_pipe_identity_mismatch')
        write_message(api,handle,packet)
        return read_message(api,handle,time.monotonic()+timeout)
    finally:
        api.CloseHandle(handle)


class AuthPipeServer:
    def __init__(self,name,callback):
        from platform_runtime import _security_api,current_user_sid
        self.api=_api();self.name=name;self.callback=callback;self.running=True;self.ready=False;self.handle=None
        _,_,self.adv,self.kernel=_security_api()
        self.descriptor=ctypes.c_void_p()
        sddl='D:P'+''.join('(A;;GA;;;'+sid+')' for sid in [current_user_sid(),'SY','BA'])
        if not self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,ctypes.byref(self.descriptor),None):
            raise PipeError('auth_pipe_acl_unavailable')
        class Security(ctypes.Structure):
            _fields_=[('length',wt.DWORD),('descriptor',ctypes.c_void_p),('inherit',wt.BOOL)]
        self.security=Security(ctypes.sizeof(Security),self.descriptor,False)
        try: self._create()
        except Exception:
            self.kernel.LocalFree(self.descriptor)
            raise
        self.thread=threading.Thread(target=self._serve,daemon=True)
        self.thread.start()

    def _create(self):
        # FIRST_PIPE_INSTANCE防止同名占位；PIPE_REJECT_REMOTE_CLIENTS不开放远程入口。
        handle=self.api.CreateNamedPipeW(self.name,0x00000003|0x00080000,0x00000008,1,65536,65536,2000,ctypes.byref(self.security))
        if not _valid_handle(handle): raise PipeError('auth_pipe_create_failed')
        self.handle=handle
        # 同步Connect挂起后同句柄的安全描述符查询也可能等待；先回读ACL再监听。
        self.private_acl=self._read_private_acl()
        if not self.private_acl:
            self.api.CloseHandle(handle);self.handle=None
            raise PipeError('auth_pipe_acl_unverified')
        self.ready=True

    def _serve(self):
        while self.running:
            handle=self.handle
            connected=self.api.ConnectNamedPipe(handle,None) or ctypes.get_last_error()==535
            if not connected: break
            self.ready=False
            packet=None
            dispatched=False
            try:
                if not self.running: break
                packet=read_message(self.api,handle,time.monotonic()+5)
                dispatched=True
                result=self.callback(packet)
                write_message(self.api,handle,result)
                self.api.FlushFileBuffers(handle)
            except Exception:
                try:
                    write_message(self.api,handle,{'ok':False,'state':'auth_result_unknown' if dispatched else 'auth_transport_failed',
                                                  'login_submitted':None if dispatched else False})
                    self.api.FlushFileBuffers(handle)
                except Exception: pass
            finally:
                if isinstance(packet,dict): packet.clear()
                self.api.DisconnectNamedPipe(handle)
                self.api.CloseHandle(handle);self.handle=None
            if not self.running: break
            try: self._create()
            except PipeError: break
        self.ready=False
        if self.handle:
            self.api.CloseHandle(self.handle);self.handle=None
        self.kernel.LocalFree(self.descriptor)

    def close(self):
        self.running=False
        # 唤醒仅监听中的线程，避免跨线程关闭后产生句柄复用/二次关闭。
        if self.ready and self.api.WaitNamedPipeW(self.name,100):
            wake=self.api.CreateFileW(self.name,0xC0000000,0,None,3,0,None)
            if _valid_handle(wake): self.api.CloseHandle(wake)
        self.thread.join(timeout=.2)

    def acl_is_private(self):
        return self.private_acl

    def _read_private_acl(self):
        from platform_runtime import current_user_sid
        self.adv.GetKernelObjectSecurity.argtypes=[wt.HANDLE,wt.DWORD,ctypes.c_void_p,wt.DWORD,ctypes.POINTER(wt.DWORD)]
        self.adv.GetKernelObjectSecurity.restype=wt.BOOL
        size=wt.DWORD()
        self.adv.GetKernelObjectSecurity(self.handle,4,None,0,ctypes.byref(size))
        buf=ctypes.create_string_buffer(size.value)
        if not self.adv.GetKernelObjectSecurity(self.handle,4,buf,size,ctypes.byref(size)): return False
        text=wt.LPWSTR()
        if not self.adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf,1,4,ctypes.byref(text),None): return False
        try:
            import re
            aces=re.findall(r'\(A;;(?:GA|FA);;;([^;)]+)\)',text.value)
            return text.value.startswith('D:P') and len(aces)==3 and set(aces)=={current_user_sid(),'SY','BA'} and text.value.count('(')==3
        finally:self.kernel.LocalFree(text)
