"""安装器在下载、写入和--check执行前拒绝越出工具的运行目录链接。"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapBoundaryTests(unittest.TestCase):
    def fixture(self, temp):
        root = Path(temp).resolve() / 'tool'
        (root / 'tools').mkdir(parents=True)
        for name in ('bootstrap_install.sh', 'bootstrap_boundary.sh', 'bootstrap_install.ps1'):
            shutil.copy2(ROOT / 'tools' / name, root / 'tools' / name)
        return root

    @unittest.skipIf(os.name == 'nt', 'POSIX installer')
    def test_external_state_link_and_external_runtime_link_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            outside = Path(temp).resolve() / 'outside'; outside.mkdir()
            executable = outside / 'mcp-venv/bin/python'
            executable.parent.mkdir(parents=True)
            executable.write_text('#!/bin/sh\nbase=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd -P)\n: > "$base/unexpected-execution"\n')
            executable.chmod(0o700)
            (root / '.local').symlink_to(outside, target_is_directory=True)
            for args in ([], ['--check']):
                result = subprocess.run(['sh', str(root / 'tools/bootstrap_install.sh'), *args], capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'.local cannot be a link', result.stderr)
            self.assertFalse((executable.parent / 'unexpected-execution').exists())
            (root / '.local').unlink(); (root / '.local').mkdir()
            (root / '.local/bootstrap').symlink_to(outside, target_is_directory=True)
            result = subprocess.run(['sh', str(root / 'tools/bootstrap_install.sh')], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Runtime link escapes', result.stderr)
            self.assertEqual(sorted(x.name for x in outside.iterdir()), ['mcp-venv'])

    @unittest.skipIf(os.name == 'nt', 'POSIX installer')
    def test_internal_interpreter_links_work_but_parent_escape_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            base = root / '.local/python'; base.mkdir(parents=True)
            target = base / 'python'; target.write_text('fixture')
            (root / '.local/python-link').symlink_to(target)
            command = ['sh', str(root / 'tools/bootstrap_boundary.sh'), str(root)]
            self.assertEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 0)
            (base / 'sub').mkdir()
            (root / '.local/escape').symlink_to('python/sub/../../..', target_is_directory=True)
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction guard')
    def test_windows_junction_is_rejected_before_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.fixture(temp)
            outside = Path(temp).resolve() / 'outside'; outside.mkdir()
            link = root / '.local'
            made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
            self.assertEqual(made.returncode, 0, made.stderr)
            try:
                for args in ([], ['--check']):
                    done = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(root / 'tools/bootstrap_install.ps1'), *args], capture_output=True, timeout=20)
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn(b'.local cannot be a reparse point', done.stderr)
                self.assertEqual(list(outside.iterdir()), [])
            finally:
                link.rmdir()
