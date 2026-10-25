"""实验性直接ApplicationServices打印面板适配器；不依赖System Events。

SDK依据：HIServices/AXUIElement.h、AXError.h、CoreFoundation各类型头。
Create/Copy结果由本次session持有并CFRelease；数组/URL的Get结果仅借用，
在父对象仍被持有时使用。没有键盘、坐标、任意属性写入或默认Print操作。
"""
from __future__ import annotations

import ctypes
import math
import re
import sys
import time
from contextlib import AbstractContextManager
from pathlib import Path
from urllib.parse import unquote, urlparse

TRANSPORT = 'applicationservices-direct-ax-experimental'
MAX_NODES, MAX_DEPTH, MAX_STRING = 400, 14, 1024
OPTIONAL_ABSENT = {-25205, -25212}  # AttributeUnsupported / NoValue；CannotComplete绝不能当空。
UTF8 = 0x08000100


class AXBlocked(RuntimeError):
    pass


class AXBudget(AXBlocked):
    pass


class _CoreAX(AbstractContextManager):
    """仅内部CF桥；所有引用只在本次session内有效，不返回原生指针。"""
    def __init__(self, *, budget_seconds=5):
        if sys.platform != 'darwin':
            raise AXBlocked('DIRECT_AX_REQUIRES_DARWIN')
        if isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float)) or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 6:
            raise ValueError('直接AX预算须为0至6秒')
        self.deadline = time.monotonic() + budget_seconds
        self.owned, self.strings = [], {}
        self.ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self._bind()
        self.types = {name: getattr(self.cf, name + 'GetTypeID')() for name in ['CFString', 'CFBoolean', 'CFNumber', 'CFArray', 'CFURL']}
        self.ax_type = self.ax.AXUIElementGetTypeID()

    def _bind(self):
        p, i, b, index = ctypes.c_void_p, ctypes.c_int32, ctypes.c_ubyte, ctypes.c_long
        signatures = {
            'AXIsProcessTrusted': ([], b), 'AXUIElementGetTypeID': ([], ctypes.c_ulong),
            'AXUIElementCreateSystemWide': ([], p), 'AXUIElementCreateApplication': ([i], p),
            'AXUIElementGetPid': ([p, ctypes.POINTER(i)], i),
            'AXUIElementCopyAttributeValue': ([p, p, ctypes.POINTER(p)], i),
            'AXUIElementCopyActionNames': ([p, ctypes.POINTER(p)], i),
            'AXUIElementIsAttributeSettable': ([p, p, ctypes.POINTER(b)], i),
            'AXUIElementSetAttributeValue': ([p, p, p], i), 'AXUIElementPerformAction': ([p, p], i),
            'AXUIElementSetMessagingTimeout': ([p, ctypes.c_float], i)}
        for name, (args, result) in signatures.items():
            method = getattr(self.ax, name); method.argtypes, method.restype = args, result
        signatures = {
            'CFRelease': ([p], None), 'CFGetTypeID': ([p], ctypes.c_ulong), 'CFEqual': ([p, p], b),
            'CFStringCreateWithCString': ([p, ctypes.c_char_p, ctypes.c_uint32], p),
            'CFStringGetLength': ([p], index), 'CFStringGetMaximumSizeForEncoding': ([index, ctypes.c_uint32], index),
            'CFStringGetCString': ([p, p, index, ctypes.c_uint32], b),
            'CFBooleanGetValue': ([p], b), 'CFNumberGetValue': ([p, i, p], b),
            'CFArrayGetCount': ([p], index), 'CFArrayGetValueAtIndex': ([p, index], p),
            'CFURLGetString': ([p], p)}
        for name in ['CFString', 'CFBoolean', 'CFNumber', 'CFArray', 'CFURL']:
            signatures[name + 'GetTypeID'] = ([], ctypes.c_ulong)
        for name, (args, result) in signatures.items():
            method = getattr(self.cf, name); method.argtypes, method.restype = args, result

    def _budget(self, element=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AXBudget('DIRECT_AX_TOTAL_BUDGET_EXHAUSTED')
        if element is not None and self.ax.AXUIElementSetMessagingTimeout(element, min(.12, remaining)) != 0:
            raise AXBlocked('DIRECT_AX_TIMEOUT_CONFIGURATION_FAILED')

    def _own(self, pointer):
        if not pointer:
            raise AXBlocked('DIRECT_AX_NULL_OWNED_OBJECT')
        self.owned.append(pointer)
        return pointer

    def _string(self, value):
        if value not in self.strings:
            if not isinstance(value, str) or '\0' in value or len(value) > MAX_STRING:
                raise AXBlocked('DIRECT_AX_INVALID_STRING_INPUT')
            self.strings[value] = self._own(self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), UTF8))
        return self.strings[value]

    def __enter__(self):
        if not self.ax.AXIsProcessTrusted():
            raise AXBlocked('DIRECT_AX_ACCESSIBILITY_PERMISSION_REQUIRED_NO_PROMPT')
        return self

    def __exit__(self, *args):
        for pointer in reversed(self.owned):
            self.cf.CFRelease(pointer)
        self.owned.clear(); self.strings.clear()

    def application(self, pid):
        self._budget()
        return self._own(self.ax.AXUIElementCreateApplication(pid))

    def pid(self, element):
        self._budget(element); result = ctypes.c_int32()
        if self.ax.AXUIElementGetPid(element, ctypes.byref(result)) != 0:
            raise AXBlocked('DIRECT_AX_PID_UNAVAILABLE')
        return result.value

    def front_pid(self):
        system = self._own(self.ax.AXUIElementCreateSystemWide())
        # SDK: system-wide timeout影响本进程；无需改变它，Copy只设置短的对象timeout，
        # 随后立即重置global默认，避免影响同进程其他调用者。
        try:
            focused = self._copy(system, 'AXFocusedApplication', optional=False)
        finally:
            self.ax.AXUIElementSetMessagingTimeout(system, 0)
        if self.cf.CFGetTypeID(focused) != self.ax_type:
            raise AXBlocked('DIRECT_AX_FOCUSED_APPLICATION_TYPE_MISMATCH')
        return self.pid(focused)

    def _copy(self, element, name, *, optional=True):
        self._budget(element); result = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyAttributeValue(element, self._string(name), ctypes.byref(result))
        if error in OPTIONAL_ABSENT and optional:
            return None
        if error != 0:
            raise AXBlocked('DIRECT_AX_ATTRIBUTE_READ_FAILED:' + str(error))
        return self._own(result.value)

    def _text(self, pointer):
        length = self.cf.CFStringGetLength(pointer)
        if not 0 <= length <= MAX_STRING:
            raise AXBudget('DIRECT_AX_STRING_LIMIT')
        size = self.cf.CFStringGetMaximumSizeForEncoding(length, UTF8) + 1
        if not 1 <= size <= MAX_STRING * 4 + 1:
            raise AXBudget('DIRECT_AX_STRING_BUFFER_LIMIT')
        data = ctypes.create_string_buffer(size)
        if not self.cf.CFStringGetCString(pointer, data, size, UTF8):
            raise AXBlocked('DIRECT_AX_STRING_CONVERSION_FAILED')
        return data.value.decode('utf-8')

    def _scalar(self, pointer):
        if pointer is None:
            return None
        kind = self.cf.CFGetTypeID(pointer)
        if kind == self.types['CFString']:
            return self._text(pointer)
        if kind == self.types['CFBoolean']:
            return bool(self.cf.CFBooleanGetValue(pointer))
        if kind == self.types['CFNumber']:
            value = ctypes.c_int64()
            if not self.cf.CFNumberGetValue(pointer, 4, ctypes.byref(value)):  # SDK kCFNumberSInt64Type=4
                raise AXBlocked('DIRECT_AX_NUMBER_CONVERSION_FAILED')
            return value.value
        if kind == self.types['CFURL']:
            borrowed = self.cf.CFURLGetString(pointer)
            if not borrowed or self.cf.CFGetTypeID(borrowed) != self.types['CFString']:
                raise AXBlocked('DIRECT_AX_URL_STRING_TYPE_MISMATCH')
            return self._text(borrowed)  # Get rule: URL父对象仍owned，不单独release该string。
        return None

    def attribute(self, element, name):
        return self._scalar(self._copy(element, name))

    def _array(self, pointer, limit):
        if pointer is None:
            return []
        if self.cf.CFGetTypeID(pointer) != self.types['CFArray']:
            raise AXBlocked('DIRECT_AX_EXPECTED_CFARRAY')
        count = self.cf.CFArrayGetCount(pointer)
        if not 0 <= count <= limit:
            raise AXBudget('DIRECT_AX_ARRAY_LIMIT')
        # Get rule:只借用；arena一直持有Copy获得的array，直至session结束。
        return [self.cf.CFArrayGetValueAtIndex(pointer, i) for i in range(count)]

    def children(self, element, name, limit):
        result = self._array(self._copy(element, name), limit)
        if any(not child or self.cf.CFGetTypeID(child) != self.ax_type for child in result):
            raise AXBlocked('DIRECT_AX_CHILD_NOT_AXUIELEMENT')
        return result

    def equal(self, a, b):
        return bool(self.cf.CFEqual(a, b))

    def press(self, element):
        self._budget(element); actions = ctypes.c_void_p()
        if self.ax.AXUIElementCopyActionNames(element, ctypes.byref(actions)) != 0:
            raise AXBlocked('DIRECT_AX_ACTIONS_UNAVAILABLE')
        names = [self._scalar(p) for p in self._array(self._own(actions.value), 16)]
        if 'AXPress' not in names:
            raise AXBlocked('DIRECT_AX_PRESS_NOT_SUPPORTED')
        self._budget(element)
        if self.ax.AXUIElementPerformAction(element, self._string('AXPress')) != 0:
            raise AXBlocked('DIRECT_AX_PRESS_RESULT_UNKNOWN_NO_RETRY')

    def filename(self, element, value):
        self._budget(element); writable = ctypes.c_ubyte()
        if self.ax.AXUIElementIsAttributeSettable(element, self._string('AXValue'), ctypes.byref(writable)) != 0 or not writable.value:
            raise AXBlocked('DIRECT_AX_FILENAME_NOT_SETTABLE')
        self._budget(element)
        if self.ax.AXUIElementSetAttributeValue(element, self._string('AXValue'), self._string(value)) != 0:
            raise AXBlocked('DIRECT_AX_FILENAME_RESULT_UNKNOWN_NO_RETRY')
        if self.attribute(element, 'AXValue') != value:
            raise AXBlocked('DIRECT_AX_FILENAME_READBACK_MISMATCH_NO_RETRY')


def _directory_verified(nodes, directory):
    for node in nodes:
        if node.get('role') == 'AXPathControl' and node.get('value') == str(directory):
            return True
        url = node.get('url')
        if isinstance(url, str) and url.startswith('file://'):
            parsed = urlparse(url)
            if parsed.netloc in ['', 'localhost'] and unquote(parsed.path).rstrip('/') == str(directory).rstrip('/'):
                return True
    return False


class PrintAXSession(AbstractContextManager):
    """只清点指定Edge窗口的原生sheet；只允许已匹配的PDF/Save动作。"""
    def __init__(self, process_id, window_title, *, api=None):
        if type(process_id) is not int or process_id <= 0 or not isinstance(window_title, str) or not window_title or len(window_title) > MAX_STRING:
            raise ValueError('直接AX须绑定明确前台PID及窗口标题')
        self.process_id, self.window_title = process_id, window_title
        self.api = api if api is not None else _CoreAX()
        self.window = None
        self.attempted = False

    def __enter__(self):
        self.api.__enter__()
        try:
            self.app = self.api.application(self.process_id)
            self._guard()
        except Exception:
            self.api.__exit__(*sys.exc_info()); raise
        return self

    def __exit__(self, *args):
        return self.api.__exit__(*args)

    def _guard(self):
        if self.api.front_pid() != self.process_id or self.api.pid(self.app) != self.process_id or self.api.attribute(self.app, 'AXFrontmost') is not True:
            raise AXBlocked('DIRECT_AX_FRONTMOST_PID_CHANGED')
        windows = self.api.children(self.app, 'AXWindows', 32)
        matches = [w for w in windows if self.api.pid(w) == self.process_id and self.api.attribute(w, 'AXTitle') == self.window_title]
        if len(matches) != 1:
            raise AXBlocked('DIRECT_AX_TARGET_WINDOW_NOT_UNIQUE')
        if self.window is not None and not self.api.equal(self.window, matches[0]):
            raise AXBlocked('DIRECT_AX_WINDOW_REPLACED')
        self.window = matches[0]

    def snapshot(self):
        self._guard(); self.objects = {}; nodes = []; complete = True
        names = {'role': 'AXRole', 'title': 'AXTitle', 'description': 'AXDescription', 'identifier': 'AXIdentifier',
                 'value': 'AXValue', 'url': 'AXURL', 'enabled': 'AXEnabled'}
        def walk(element, path, depth):
            if len(nodes) >= MAX_NODES or depth > MAX_DEPTH:
                raise AXBudget('DIRECT_AX_TREE_LIMIT')
            if self.api.pid(element) != self.process_id:
                raise AXBlocked('DIRECT_AX_SHEET_CHILD_PID_MISMATCH')
            row = {'path': path, **{key: self.api.attribute(element, name) for key, name in names.items()}}
            if any(isinstance(value, str) and len(value) > MAX_STRING for value in row.values()):
                raise AXBudget('DIRECT_AX_STRING_LIMIT')
            nodes.append(row); self.objects[tuple(path)] = element
            for i, child in enumerate(self.api.children(element, 'AXChildren', MAX_NODES - len(nodes))):
                walk(child, path + [i], depth + 1)
        try:
            roots = self.api.children(self.window, 'AXSheets', 4)
            for i, root in enumerate(roots):
                if self.api.attribute(root, 'AXRole') != 'AXSheet':
                    raise AXBlocked('DIRECT_AX_ROOT_IS_NOT_NATIVE_SHEET')
                walk(root, [i], 0)
        except AXBudget:
            complete = False
        self._guard()
        return {'process_id': self.process_id, 'window_title': self.window_title, 'complete': complete,
                'nodes': nodes, 'transport': TRANSPORT, 'gui_live_verified': False}

    def perform(self, action, expected, *, filename=None, directory=None):
        if self.attempted:
            raise AXBlocked('DIRECT_AX_OPERATION_ALREADY_ATTEMPTED_NO_RETRY')
        allowed = {
            'open-system-dialog': (['AXButton', 'AXLink'], ['Print using system dialog…', 'Print using system dialog...', '使用系统对话框打印…', '使用系统对话框打印...']),
            'pdf-menu': (['AXMenuButton', 'AXPopUpButton'], ['PDF']),
            'save-as-pdf': (['AXMenuItem'], ['Save as PDF', 'Save as PDF…', '存储为 PDF', '存储为 PDF…', '存储为PDF', '存储为PDF…']),
            'save': (['AXButton'], ['Save', '存储'])}
        if action not in [*allowed, 'set-filename']:
            raise AXBlocked('DIRECT_AX_ACTION_NOT_WHITELISTED')
        observed = self.snapshot()
        if not observed['complete'] or observed != expected or not observed['nodes']:
            raise AXBlocked('DIRECT_AX_EXPECTED_SNAPSHOT_CHANGED_OR_INCOMPLETE')
        nodes = observed['nodes']
        if action in ['save', 'set-filename']:
            if not isinstance(filename, str) or not re.fullmatch(r'preview-[a-f0-9]{32}\.pdf', filename):
                raise AXBlocked('DIRECT_AX_NATIVE_STAGING_FILENAME_REQUIRED')
            directory = Path(directory or '')
            if not directory.is_absolute() or '..' in directory.parts or not _directory_verified(nodes, directory):
                raise AXBlocked('DIRECT_AX_NATIVE_COMPLETE_DIRECTORY_REQUIRED_NO_NAVIGATION')
        if action == 'set-filename':
            matches = [n for n in nodes if n['role'] == 'AXTextField' and n['identifier'] in ['saveAsNameTextField', 'NSSaveAsNameField'] and n['enabled'] is True]
        else:
            roles, labels = allowed[action]
            matches = [n for n in nodes if n['role'] in roles and any(n[k] in labels for k in ['title', 'description']) and n['enabled'] is True]
        if len(matches) != 1:
            raise AXBlocked('DIRECT_AX_CONTROL_NOT_UNIQUE_OR_UNSUPPORTED')
        if action == 'save':
            fields = [n['value'] for n in nodes if n['role'] == 'AXTextField' and n['identifier'] in ['saveAsNameTextField', 'NSSaveAsNameField']]
            if fields != [filename] or (directory / filename).exists():
                raise AXBlocked('DIRECT_AX_SAVE_FILENAME_CHANGED_OR_TARGET_EXISTS')
        element = self.objects[tuple(matches[0]['path'])]
        self._guard()  # 动作前再次核前台/窗口；不重试任何变更。
        keys = {'role': 'AXRole', 'title': 'AXTitle', 'description': 'AXDescription', 'identifier': 'AXIdentifier', 'value': 'AXValue', 'enabled': 'AXEnabled'}
        if self.api.pid(element) != self.process_id or any(self.api.attribute(element, attribute) != matches[0][key] for key, attribute in keys.items()):
            raise AXBlocked('DIRECT_AX_SELECTED_CONTROL_CHANGED')
        self.attempted = True
        if action == 'set-filename':
            self.api.filename(element, filename)
        else:
            self.api.press(element)
        return {'ok': True, 'action': action, 'requested': True, 'transport': TRANSPORT, 'gui_live_verified': False}
