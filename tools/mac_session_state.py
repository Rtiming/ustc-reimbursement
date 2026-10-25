"""有界只读会话状态；不解锁、不唤醒、不请求辅助功能权限。"""
import ctypes
import sys


def lock_state():
    result = {'locked':None, 'observed':False, 'source':'CGSessionCopyCurrentDictionary',
              'session_changed':False, 'unlock_requested':False}
    if sys.platform != 'darwin':
        return {**result, 'reason':'unsupported_platform'}
    session = key = None
    cf = None
    try:
        cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cg.CGSessionCopyCurrentDictionary.argtypes = []
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryGetValueIfPresent.restype = ctypes.c_bool
        cf.CFDictionaryGetValueIfPresent.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        cf.CFGetTypeID.restype = cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFBooleanGetTypeID.argtypes = []
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        session = cg.CGSessionCopyCurrentDictionary()
        # 此可选字典键不是所有系统均提供。缺失时保持未知，不据此宣称已锁屏。
        key = cf.CFStringCreateWithCString(None, b'CGSSessionScreenIsLocked', 0x08000100)
        value = ctypes.c_void_p()
        if not session or not key:
            return {**result, 'reason':'session_unavailable'}
        if not cf.CFDictionaryGetValueIfPresent(session, key, ctypes.byref(value)):
            return {**result, 'reason':'lock_key_not_exposed'}
        if not value.value or cf.CFGetTypeID(value) != cf.CFBooleanGetTypeID():
            return {**result, 'reason':'lock_value_not_boolean'}
        return {**result, 'locked':bool(cf.CFBooleanGetValue(value)), 'observed':True,
                'source':'CGSessionCopyCurrentDictionary.CGSSessionScreenIsLocked'}
    except (OSError, AttributeError, TypeError, ValueError):
        return {**result, 'reason':'session_probe_unavailable'}
    finally:
        if cf is not None:
            if key: cf.CFRelease(key)
            if session: cf.CFRelease(session)
