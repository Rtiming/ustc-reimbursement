"""权限回读比较真实ACE，允许系统规范化显示，拒绝额外授权与继承。"""

import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from platform_runtime import _private_acl_matches, private_path, is_private_path, _security_api, _expected_dacl


class PrivatePermissionTests(unittest.TestCase):
    def test_canonical_order_is_irrelevant_but_privilege_changes_fail(self):
        user = 'S-1-5-21-111-222-333-1001'
        entries = [(0, 0, 0x1f01ff, s) for s in (user, 'S-1-5-18', 'S-1-5-32-544')]
        self.assertTrue(_private_acl_matches(0x1400, list(reversed(entries)), user, False))
        self.assertFalse(_private_acl_matches(0x400, entries, user, False))
        self.assertFalse(_private_acl_matches(0x1400, entries + [(0, 0, 0x1f01ff, 'S-1-1-0')], user, False))
        self.assertFalse(_private_acl_matches(0x1400, entries[:-1], user, False))
        for bad in ((0, 16, 0x1f01ff, user), (1, 0, 0x1f01ff, user), (0, 0, 0x120089, user), (0, 0, 0x1f01ff, 'S-1-1-0')):
            self.assertFalse(_private_acl_matches(0x1400, [bad] + entries[1:], user, False))

    @unittest.skipUnless(os.name == 'nt', '真实Windows ACL由Windows CI执行')
    def test_real_windows_acl_roundtrip_and_extra_principal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / '权限 with spaces'
            folder.mkdir()
            private_path(folder)
            self.assertTrue(is_private_path(folder))
            path = folder / 'record.json'
            path.write_text('{}', encoding='utf-8')
            private_path(path)
            self.assertTrue(is_private_path(path))
            ctypes, wt, adv, kernel = _security_api()
            descriptor = ctypes.c_void_p()
            # 只对本次合成临时文件增加Everyone读取，确认校验拒绝后立即恢复。
            self.assertTrue(adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(_expected_dacl(path) + '(A;;FR;;;WD)', 1, ctypes.byref(descriptor), None))
            try:
                self.assertTrue(adv.SetFileSecurityW(str(path), 0x80000004, descriptor))
            finally:
                kernel.LocalFree(descriptor)
            try:
                self.assertFalse(is_private_path(path))
            finally:
                private_path(path)
            self.assertTrue(is_private_path(path))
