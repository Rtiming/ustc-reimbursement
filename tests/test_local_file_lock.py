"""安装锁跨平台有效，同时保留财务桥的平台拒绝边界。"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from platform_runtime import file_lock
from edge_runtime import file_lock as browser_file_lock, UnsupportedPlatformError


class LocalFileLockTests(unittest.TestCase):
    def test_process_contention_and_release_without_deleting_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'local.lock'
            child = 'import sys;sys.path.insert(0,sys.argv[1]);from platform_runtime import file_lock\nwith file_lock(sys.argv[2]): print("acquired")'
            command = [sys.executable, '-c', child, str(TOOLS), str(path)]
            with file_lock(path):
                blocked = subprocess.run(command, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertNotIn('acquired', blocked.stdout)
            self.assertTrue(path.exists())
            released = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertIn('acquired', released.stdout)

    def test_linux_local_lock_does_not_enable_finance_browser(self):
        with tempfile.TemporaryDirectory() as temp, patch('edge_runtime.sys.platform', 'linux'):
            with file_lock(Path(temp) / 'install.lock'):
                pass
            with self.assertRaises(UnsupportedPlatformError):
                with browser_file_lock(Path(temp) / 'browser.lock'):
                    self.fail('Linux不应进入财务浏览器动作')
