"""公开发行更新的来源、完整性、隔离及重跑合同；合成ZIP，无联网。"""
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import release_update as update
from build_distribution import encoded
from assistant_api import atomic_json


def package(version='2.0.0-beta.1', extra=None):
    members = {'README.md': b'public example\n', 'data/distribution.json': encoded({'version': version})}
    files = [{'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in sorted(members.items())]
    members['MANIFEST.json'] = encoded({'version': version, 'private_data_included': False, 'source_tree_sha256': hashlib.sha256(encoded(files)).hexdigest(), 'files': files})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, raw in members.items(): archive.writestr('ustc-reimbursement/' + name, raw)
        if extra: archive.writestr(*extra)
    return buffer.getvalue()


def release(raw, version='2.0.0-beta.1'):
    name = 'ustc-reimbursement-' + version + '.zip'; tag = 'v' + version
    return {'tag_name': tag, 'draft': False, 'prerelease': '-' in version, 'assets': [
        {'name': n, 'state': 'uploaded', 'browser_download_url': update.DOWNLOAD + tag + '/' + n,
         **({'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()} if n == name else {})}
        for n in [name, 'SHA256SUMS']]}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '原版本'; self.root.mkdir()
        atomic_json(self.root / 'data/distribution.json', {'version': '1.0.0-beta.1'})
        atomic_json(self.root / 'cases/example/original.json', {'keep': '原件及未知状态不迁移'})
        self.raw = package(); self.row = release(self.raw)

    def fetch(self, url, limit):
        if url == update.API + '?per_page=100': return json.dumps([self.row]).encode()
        if url.endswith('/SHA256SUMS'): return (hashlib.sha256(self.raw).hexdigest() + '  ustc-reimbursement-2.0.0-beta.1.zip\n').encode()
        if url.endswith('.zip'): return self.raw
        self.fail('unexpected URL')

    def test_default_check_reads_only_metadata_and_leaves_files_unchanged(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(update, 'fetch', side_effect=self.fetch) as fetch:
            result = update.run(self.root)
        self.assertTrue(result['update_available']); self.assertEqual(fetch.call_count, 1)
        self.assertFalse(result['state_migrated'])
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_side_by_side_download_is_verified_and_reusable_and_old_case_is_preserved(self):
        target = self.root.parent / '新版 with spaces'
        before = (self.root / 'cases/example/original.json').read_bytes()
        with patch.object(update, 'fetch', side_effect=self.fetch):
            result = update.run(self.root, download=True, destination=target)
            again = update.run(self.root, download=True, destination=target)
        self.assertTrue(result['ok']); self.assertEqual(result['archive_sha256'], again['archive_sha256'])
        self.assertEqual((self.root / 'cases/example/original.json').read_bytes(), before)
        self.assertFalse((target / 'cases').exists())

    def test_existing_user_directory_and_original_tree_are_never_overwritten(self):
        target = self.root.parent / 'already'; target.mkdir(); (target / 'keep').write_text('user')
        with patch.object(update, 'fetch', side_effect=self.fetch):
            with self.assertRaisesRegex(ValueError, '不覆盖'): update.run(self.root, download=True, destination=target)
            with self.assertRaisesRegex(ValueError, '之外'): update.run(self.root, download=True, destination=self.root / 'nested')
        self.assertEqual((target / 'keep').read_text(), 'user')

    def test_wrong_sha_and_untrusted_asset_url_fail(self):
        def wrong(url, limit):
            if url.endswith('/SHA256SUMS'): return ('0' * 64 + '  ustc-reimbursement-2.0.0-beta.1.zip\n').encode()
            return self.fetch(url, limit)
        with patch.object(update, 'fetch', side_effect=wrong):
            with self.assertRaisesRegex(ValueError, '不一致'): update.run(self.root, download=True)
        self.row['assets'][0]['browser_download_url'] = 'https://example.invalid/untrusted.zip'
        with patch.object(update, 'fetch', side_effect=self.fetch):
            with self.assertRaisesRegex(ValueError, '不属于'): update.run(self.root)

    def test_archive_traversal_extra_members_links_and_corruption_are_rejected_before_write(self):
        for extra in [('ustc-reimbursement/../escape', b'x'), ('ustc-reimbursement/extra.txt', b'x')]:
            archive = self.root.parent / 'bad.zip'; archive.write_bytes(package(extra=extra))
            out = self.root.parent / 'extract'
            with self.assertRaises(ValueError): update.extract_verified(archive, out, '2.0.0-beta.1')
            self.assertFalse(out.exists())
        raw = package(); archive.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, '版本'): update.extract_verified(archive, out, '9.0.0')
        self.assertFalse(out.exists())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as link_zip:
            entry = zipfile.ZipInfo('ustc-reimbursement/linked'); entry.create_system = 3
            entry.external_attr = 0o120777 << 16; link_zip.writestr(entry, '../outside')
        archive.write_bytes(buffer.getvalue())
        with self.assertRaisesRegex(ValueError, '链接'): update.extract_verified(archive, out, '2.0.0-beta.1')
        self.assertFalse(out.exists())

    def test_failed_new_installation_is_reported_and_old_runtime_and_case_unchanged(self):
        target = self.root.parent / 'prepared'
        with patch.object(update, 'fetch', side_effect=self.fetch), patch.object(update.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            result = update.run(self.root, install=True, destination=target)
        self.assertFalse(result['ok']); self.assertEqual(result['phase'], 'installation_incomplete')
        self.assertTrue((self.root / 'cases/example/original.json').exists())
        self.assertFalse((self.root / '.local').exists())
        self.assertTrue((target / '.local/update-result.json').exists())

    def test_success_requires_fresh_installation_receipt_and_local_ocr_choice_is_preserved(self):
        target = self.root.parent / 'prepared'
        def installed(*args, **kwargs):
            atomic_json(target / '.local/self-check-current.json', {'ok': True})
            return subprocess.CompletedProcess([], 0)
        with patch.object(update, 'fetch', side_effect=self.fetch), patch.object(update.subprocess, 'run', side_effect=installed), patch('ocr_runtime.read_profile', return_value={'profile': {'backend': 'local'}}):
            result = update.run(self.root, install=True, destination=target)
        self.assertTrue(result['ok']); self.assertEqual(result['phase'], 'installed')
        self.assertTrue(json.loads((target / '.local/update-deployment.json').read_text())['with_ocr'])

    def test_channel_and_version_ordering(self):
        rows = [self.row, release(self.raw, '1.5.0')]
        with patch.object(update, 'fetch', return_value=json.dumps(rows).encode()):
            self.assertEqual(update.select_release('1.0.0', 'stable')['version'], '1.5.0')
            self.assertEqual(update.select_release('1.0.0-beta.1')['version'], '2.0.0-beta.1')
        self.assertLess(update.version_key('1.0.0-beta.9'), update.version_key('1.0.0-rc.1'))
        self.assertLess(update.version_key('1.0.0-rc.1'), update.version_key('1.0.0'))
        with patch.object(update, 'fetch') as fetch:
            with self.assertRaises(ValueError): update.select_release('1.0.0', requested='../bad')
            fetch.assert_not_called()


if __name__ == '__main__': unittest.main()
