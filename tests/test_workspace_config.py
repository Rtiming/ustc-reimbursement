"""同一绑定供服务、安装与自检消费，避免各入口默认到不同资料根。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import AssistantService, APIError, atomic_json
from workspace_config import installation_config, resolve_material_roots, runtime_python


class WorkspaceConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        atomic_json(self.root / 'data/distribution.json', {})

    def test_existing_installation_is_shared_by_service_setup_and_installer(self):
        data = self.root / 'specific-materials'
        data.mkdir()
        config = installation_config(self.root, data_dir=data, profile_id='example')
        atomic_json(self.root / '.local/installation.json', config)
        self.assertEqual(AssistantService(self.root).material_roots, [data])
        self.assertEqual(resolve_material_roots(self.root, for_setup=True), [data])
        self.assertEqual(installation_config(self.root), config)

    def test_fresh_setup_requires_explicit_scope_and_overrides_are_deduplicated(self):
        self.assertEqual(AssistantService(self.root).material_roots, [self.root / 'materials'])
        with self.assertRaises(APIError):
            resolve_material_roots(self.root, for_setup=True)
        data = self.root / 'materials'
        data.mkdir()
        self.assertEqual(resolve_material_roots(self.root, [data, data], for_setup=True), [data])

    def test_wrong_binding_fails_consistently_without_reading_other_project(self):
        config = installation_config(self.root)
        config['tool_root'] = '/unrelated'
        atomic_json(self.root / '.local/installation.json', config)
        for action in (
            lambda: AssistantService(self.root),
            lambda: installation_config(self.root),
            lambda: resolve_material_roots(self.root, for_setup=True),
        ):
            with self.assertRaises(APIError):
                action()

    def test_dependency_probe_reads_versions_instead_of_install_marker(self):
        from setup_reimbursement_mcp import installed_versions

        python = self.root / 'python'
        python.touch()
        result = type('Result', (), {'returncode': 0, 'stdout': json.dumps({'mcp': None})})()
        with patch('setup_reimbursement_mcp.subprocess.run', return_value=result) as run:
            self.assertEqual(installed_versions(python, ['mcp']), {'mcp': None})
        self.assertIn('-I', run.call_args.args[0])
        self.assertEqual(runtime_python(self.root).parent.parent, self.root / '.local/mcp-venv')


if __name__ == '__main__':
    unittest.main()
