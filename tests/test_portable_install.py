"""安装入口的范围、恢复和必需/可选依赖，不执行真实安装或财务操作。"""

import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from assistant_api import AssistantService, atomic_json, read_json
from portable_install import installation_config, private_environment, ensure_profile, checks


class PortableInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_private_environment_excludes_author_credentials_and_keeps_temp_local(self):
        with patch.dict(
            os.environ,
            {
                'PYTHONPATH': '/private/python',
                'PIP_INDEX_URL': 'private-index',
                'UV_CONFIG_FILE': '/private/uv.toml',
                'API_SECRET': 'secret',
            },
        ):
            result = private_environment(self.root)
            for key in (
                'PYTHONPATH',
                'PIP_INDEX_URL',
                'UV_CONFIG_FILE',
                'API_SECRET',
                'HOME',
                'CODEX_HOME',
            ):
                self.assertNotIn(key, result)
            self.assertEqual(result['TMPDIR'], str(self.root / '.local/tmp'))
            self.assertEqual(os.environ['API_SECRET'], 'secret')

    def test_installation_reuses_bound_scope_and_rejects_directory_change(self):
        config = installation_config(self.root, profile_id='alpha', title='专项甲')
        atomic_json(self.root / '.local/installation.json', config)
        self.assertEqual(installation_config(self.root), config)
        with self.assertRaisesRegex(ValueError, '授权范围'):
            installation_config(self.root, data_dir=self.root / 'other')
        config['tool_root'] = '/different'
        atomic_json(self.root / '.local/installation.json', config)
        with self.assertRaisesRegex(ValueError, '其他工具位置'):
            installation_config(self.root)

    def test_repeat_profile_setup_preserves_user_rules_and_adds_explicit_new_profile(self):
        config = installation_config(self.root, profile_id='alpha')
        Path(config['material_root']).mkdir()
        service = AssistantService(self.root, [config['material_root']])
        ensure_profile(service, config)
        path = Path(config['library_root']) / 'profiles/alpha.json'
        data = read_json(path)
        data['pending_questions'] = ['保留用户编辑']
        atomic_json(path, data)
        before = path.read_bytes()
        self.assertTrue(ensure_profile(service, config)['reused'])
        self.assertEqual(path.read_bytes(), before)
        second = {**config, 'profile_id': 'beta', 'title': '专项乙'}
        self.assertTrue(ensure_profile(service, second)['executed'])
        self.assertEqual(path.read_bytes(), before)

    def test_missing_required_dependency_is_not_hidden_by_optional_status(self):
        folder = self.root / 'report'
        folder.mkdir()
        config = installation_config(self.root)
        service = Mock()
        service.standards_read.return_value = {'ok': True}
        service.ocr_status.return_value = {'configured': False, 'backend': None}
        with (
            patch('portable_install.verify_members', return_value={'version': 'test'}),
            patch('portable_install.setup', return_value={'ready': False}),
            patch('portable_install.AssistantService', return_value=service),
            patch('portable_install.subprocess.run') as run,
        ):
            result = checks(self.root, config, folder)
        self.assertFalse(result['ok'])
        run.assert_not_called()
        self.assertEqual(result['required'][-1]['state'], 'not_run_missing_dependency')
        self.assertTrue(result['optional'])

    def test_bootstrap_sources_are_versioned_and_have_sha_on_all_declared_platforms(self):
        with (ROOT / 'distribution/bootstrap.tsv').open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({r['platform'] for r in rows}), 6)
        for row in rows:
            self.assertTrue(row['url'].startswith('https://files.pythonhosted.org/packages/'))
            self.assertEqual(len(row['sha256']), 64)
            self.assertNotIn('latest', row['url'])
            self.assertEqual(row['python'], '3.12.15')


if __name__ == '__main__':
    unittest.main()
