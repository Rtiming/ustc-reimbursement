"""归档核心保持冻结，Windows复用原生只读句柄，反复新文件仍逐字节一致。"""

import hashlib
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from material_archive import archive_core, windows_stable_copy


class ArchiveAdapterTests(unittest.TestCase):
    def test_real_intake_loads_adapter_module_and_reads_back_original(self):
        from material_intake import execute
        script = Path(__file__).resolve().parents[1] / 'tools/material_archive.py'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / '合成原件.txt'
            source.write_bytes(b'synthetic original')
            result = execute({'schema': 'reimbursement.material-intake.v1', 'case_id': 'adapter_fixture',
                              'sources': [{'id': 'fixture', 'path': str(source), 'group_strip_dirs': None}],
                              'output_dir': str(root / 'output')},
                             archive_helper={'path': str(script), 'sha256': hashlib.sha256(script.read_bytes()).hexdigest()})
            self.assertTrue(result['ok'], result)
            import json
            receipt = json.loads((root / 'output/receipt.json').read_text(encoding='utf-8'))
            self.assertEqual(Path(receipt['originals'][0]['snapshot']).read_bytes(), source.read_bytes())

    def test_cli_preserves_core_failure_exit_status(self):
        script = Path(__file__).resolve().parents[1] / 'tools/material_archive.py'
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(script), '--verify', str(Path(temp) / 'missing.json')], capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_snapshot_uses_original_core_and_never_overwrites(self):
        core = archive_core()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for n in range(12):
                source, target = root / ('原件%d.bin' % n), root / ('copy%d.bin' % n)
                data = bytes(range(256)) * (n + 1)
                source.write_bytes(data)
                self.assertEqual(core.stable_copy(source, target), hashlib.sha256(data).hexdigest())
                self.assertEqual(target.read_bytes(), data)
                self.assertEqual(source.read_bytes(), data)
                with self.assertRaises(FileExistsError):
                    core.stable_copy(source, target)

    @unittest.skipUnless(os.name == 'nt', 'Windows原生拒绝改写由Windows CI执行')
    def test_windows_source_remains_write_locked_during_copy(self):
        import material_archive
        original = material_archive.shutil.copyfileobj
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / '原件.txt', Path(temp) / 'copy.txt'
            source.write_bytes(b'unchanged source')

            def checked_copy(src, dst):
                with self.assertRaises(PermissionError):
                    with source.open('wb'):
                        pass
                original(src, dst)

            with patch.object(material_archive.shutil, 'copyfileobj', checked_copy):
                windows_stable_copy(source, target)
            self.assertEqual(source.read_bytes(), b'unchanged source')
            self.assertEqual(target.read_bytes(), source.read_bytes())
