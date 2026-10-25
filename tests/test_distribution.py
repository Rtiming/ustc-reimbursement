"""分发只纳入声明成员；故意混入私人资料、链接和不完整文件必须失败。"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_distribution import build, collect_sources, validate_member, ALIASES
from audit_materials import audit
from assistant_api import AssistantService
import operator_support
import setup_reimbursement_mcp


class DistributionTests(unittest.TestCase):
    def test_bundled_archive_registration_does_not_replace_existing_configuration(self):
        from material_intake_service import configure_bundled_archive

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()
            preview = configure_bundled_archive(workspace, package_root=ROOT, check_only=True)
            self.assertFalse(preview['configured'])
            self.assertFalse((workspace / '.local').exists())
            result = configure_bundled_archive(workspace, package_root=ROOT)
            target = Path(result['path'])
            before = target.read_bytes()
            configure_bundled_archive(workspace, package_root=ROOT)
            self.assertEqual(target.read_bytes(), before)
            target.write_text('{}')
            with self.assertRaisesRegex(ValueError, '不同归档配置'):
                configure_bundled_archive(workspace, package_root=ROOT)
            self.assertEqual(target.read_text(), '{}')

    def test_explicit_members_exclude_private_state_and_owner_defaults(self):
        members = collect_sources()
        self.assertNotIn('data/finance_access.json', members)
        self.assertNotIn('data/operator_default.json', members)
        self.assertFalse(
            any(Path(p).parts[0] in {'.local', 'cases', '规范库', '.git'} for p in members)
        )
        defaults = json.loads(members['data/workflow_defaults.json'])
        self.assertIsNone(defaults['contract_exists_default'])
        self.assertIsNone(defaults['single_funding_budget_allocation_default'])
        self.assertIn('reimbursement_standards_init', members['mcp/tool_catalog.json'].decode())

    def test_forbidden_members_and_sensitive_source_are_rejected(self):
        for name in (
            '../secret',
            '/secret',
            'C:/secret',
            '..\\secret',
            'tools/CON.py',
            'cases/real/form.json',
            '.local/client-configs/mcp.json',
        ):
            with self.assertRaises(ValueError):
                validate_member(name, b'')
        for raw in (
            b'-----BEGIN ' + b'PRIVATE KEY-----',
            ('/' + 'Users/owner/private/key').encode(),
            b'PB' + b'12345678',
        ):
            with self.assertRaises(ValueError):
                validate_member('tools/bad.py', raw)

    def test_allowlist_does_not_sweep_new_files_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            names = set(ALIASES.values()) | {
                'distribution/source-files.json',
                'distribution/dependencies.json',
                'distribution/vendor/archive_file.py',
            }
            for name in names:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('{}')
            recipe = root / 'distribution/source-files.json'
            (root / 'distribution/dependencies.json').write_text(
                json.dumps(
                    {
                        'archive_helper': {
                            'path': 'distribution/vendor/archive_file.py',
                            'sha256': hashlib.sha256(b'{}').hexdigest(),
                        }
                    }
                )
            )
            recipe.write_text(
                json.dumps(
                    {'schema': 'ustc-distribution-sources-v1', 'source_files': sorted(names)}
                )
            )
            (root / 'distribution/private-note.json').write_text('{"private":true}')
            self.assertNotIn('distribution/private-note.json', collect_sources(root))
            p = root / 'distribution/README.md'
            p.unlink()
            p.symlink_to(root / 'distribution/AGENTS.md')
            with self.assertRaisesRegex(ValueError, '链接'):
                collect_sources(root)

    def test_package_is_repeatable_and_manifest_matches_extracted_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview = build(root / 'preview')
            self.assertFalse((root / 'preview').exists())
            a = build(root / 'first', execute=True)
            b = build(root / 'second', execute=True)
            self.assertEqual(a['archive_sha256'], b['archive_sha256'])
            with patch('zipfile.sys.platform', 'win32'):
                other_os = build(root / 'windows-metadata', execute=True)
            self.assertEqual(a['archive_sha256'], other_os['archive_sha256'])
            with zipfile.ZipFile(a['archive']) as archive:
                manifest = json.loads(archive.read('ustc-reimbursement/MANIFEST.json'))
                self.assertEqual(len(manifest['files']), preview['file_count'])
                for row in manifest['files']:
                    self.assertEqual(
                        hashlib.sha256(
                            archive.read('ustc-reimbursement/' + row['path'])
                        ).hexdigest(),
                        row['sha256'],
                    )
            with self.assertRaises(ValueError):
                build(root / 'first', execute=True)

    def test_portable_defaults_and_access_scope_are_local_and_unconfirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data').mkdir()
            shutil.copy2(
                ROOT / 'distribution/data/workflow_defaults.json',
                root / 'data/workflow_defaults.json',
            )
            (root / 'data/distribution.json').write_text('{}')
            service = AssistantService(root)
            self.assertEqual(service.material_roots, [root.resolve() / 'materials'])
            with patch.object(operator_support, 'ROOT', root):
                service.new_case('new_user')
            form = json.loads((root / 'cases/new_user/form.json').read_text())
            self.assertIsNone(form['contract']['exists'])
            self.assertIsNone(form['budget_allocation_mode'])
            self.assertFalse(form['payments'])
            self.assertFalse(form['funding'])
            with (
                patch.object(setup_reimbursement_mcp, 'PROJECT', root),
                patch.object(setup_reimbursement_mcp.subprocess, 'run') as run,
            ):
                with self.assertRaisesRegex(ValueError, 'material-root'):
                    setup_reimbursement_mcp.setup()
                run.assert_not_called()
            with self.assertRaisesRegex(ValueError, '尚未配置'):
                audit(
                    {'case_id': 'new_user'},
                    {'configuration_status': 'needs_confirmation', 'rules': []},
                )


if __name__ == '__main__':
    unittest.main()
