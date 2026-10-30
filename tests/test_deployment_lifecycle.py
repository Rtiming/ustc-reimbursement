"""安装计划、成功接受与失败保留；不用网络、凭据或真实浏览器。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import portable_install as installer
from assistant_api import atomic_json
from workspace_config import deployment_options, installation_config, read_installation
from distribution_smoke import synthetic_workspace


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '新工具 with spaces'; self.root.mkdir()
        atomic_json(self.root / 'data/distribution.json', {'version': '1.0.0'})
        for name in ['requirements-mcp.lock.txt', 'requirements-materials.lock.txt']:
            (self.root / name).write_text('example==1.0\n')
        self.manifest = {'version': '1.0.0', 'verification_mode': 'release_manifest', 'source_tree_sha256': 'a' * 64}

    def test_synthetic_workspace_is_retained_on_success_and_failure_and_never_overwritten(self):
        folder = self.root / 'synthetic-work'
        with self.assertRaisesRegex(RuntimeError, 'original failure'):
            with synthetic_workspace(folder) as work:
                (Path(work) / 'receipt.json').write_text('{}')
                raise RuntimeError('original failure')
        self.assertEqual((folder / 'receipt.json').read_text(), '{}')
        with self.assertRaises(FileExistsError):
            with synthetic_workspace(folder): pass

    def test_declarative_config_is_fixed_typed_and_conflicts_fail(self):
        path = self.root / 'deploy.json'
        atomic_json(path, {'schema': 'ustc-deployment-v1', 'profile_id': 'alpha', 'with_ocr': False})
        self.assertEqual(deployment_options(path, profile_id='alpha')['profile_id'], 'alpha')
        with self.assertRaisesRegex(ValueError, '冲突'): deployment_options(path, profile_id='beta')
        for change in [{'command': 'anything'}, {'data_dir': 'relative'}, {'with_ocr': 'false'}, {'title': None}]:
            atomic_json(path, {'schema': 'ustc-deployment-v1', **change})
            with self.assertRaises(ValueError): deployment_options(path)
        with self.assertRaises(ValueError): installation_config(self.root, title='')

    @unittest.skipIf(sys.version_info < (3, 11), '发行安装入口要求Python3.11+')
    def test_plan_creates_no_state_or_material_directory_and_starts_nothing(self):
        data = self.root.parent / '私人材料'
        before = set(self.root.rglob('*'))
        with patch.object(installer, 'verify_members', return_value=self.manifest), patch.object(installer.subprocess, 'run') as run:
            result = installer.run(self.root, data_dir=data, plan_only=True)
        self.assertTrue(result['ok']); self.assertFalse(result['execute']); run.assert_not_called()
        self.assertEqual(set(self.root.rglob('*')), before); self.assertFalse(data.exists())
        self.assertFalse(result['installed_source_matches_current'])

    @unittest.skipIf(sys.version_info < (3, 11), '发行安装入口要求Python3.11+')
    def test_only_success_accepts_source_and_failed_upgrade_preserves_previous(self):
        def profile(service, config): Path(config['library_root']).mkdir(exist_ok=True)
        passed = {'ok': True, 'required': [{'name': 'synthetic', 'ok': True}]}
        with patch.object(installer, 'verify_members', return_value=self.manifest), patch.object(installer, 'ensure_profile', side_effect=profile), patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), patch.object(installer, 'checks', return_value=passed):
            self.assertTrue(installer.run(self.root)['ok'])
        before = read_installation(self.root)['deployed_source']
        newer = {**self.manifest, 'source_tree_sha256': 'b' * 64}
        with patch.object(installer, 'verify_members', return_value=newer), patch.object(installer, 'ensure_profile', side_effect=profile), patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), patch.object(installer, 'checks', return_value={'ok': False, 'required': []}):
            self.assertFalse(installer.run(self.root)['ok'])
        self.assertEqual(read_installation(self.root)['deployed_source'], before)
        with patch.object(installer, 'verify_members', return_value=newer), patch.object(installer, 'checks', return_value=passed), patch.object(installer.subprocess, 'run') as run:
            check = installer.run(self.root, check_only=True)
        self.assertFalse(check['ok']); run.assert_not_called()
        self.assertEqual(check['required'][-1]['state'], 'source_not_accepted')
        self.assertEqual(read_installation(self.root)['deployed_source'], before)

    @unittest.skipUnless(os.name == 'posix', 'POSIX启动器；Windows入口由三系统CI实装')
    def test_check_after_other_arguments_and_help_do_not_bootstrap(self):
        tools = self.root / 'tools'; tools.mkdir()
        for name in ['bootstrap_install.sh', 'bootstrap_boundary.sh']:
            shutil.copy2(ROOT / 'tools' / name, tools / name)
        help_result = subprocess.run(['sh', str(tools / 'bootstrap_install.sh'), '--with-ocr', '--help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0); self.assertFalse((self.root / '.local').exists())
        python = self.root / '.local/mcp-venv/bin/python'; python.parent.mkdir(parents=True)
        probe = self.root / 'arguments.txt'
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$PROBE"\n'); python.chmod(0o755)
        # 边界检查允许的真实运行时内链；这里是普通合成可执行文件。
        result = subprocess.run(['sh', str(tools / 'bootstrap_install.sh'), '--profile-id', 'alpha', '--check'], env={**os.environ, 'PROBE': str(probe)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(probe.read_text().splitlines()[-1], '--check')


if __name__ == '__main__': unittest.main()
