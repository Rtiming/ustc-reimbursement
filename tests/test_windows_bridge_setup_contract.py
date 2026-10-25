"""注册表迁移只改本项目键；预览漂移、旧在途请求和部分失败都不覆盖原接线。"""

from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import windows_bridge_setup as setup
from assistant_api import atomic_json, read_json


class Key:
    def __init__(self, name): self.name = name
    def __enter__(self): return self
    def __exit__(self, *args): pass


class Registry:
    HKEY_CURRENT_USER = 'HKCU'
    REG_SZ = 1
    def __init__(self, values): self.values, self.fail_once = values, None
    def OpenKey(self, root, name):
        if name not in self.values: raise FileNotFoundError(name)
        return Key(name)
    def CreateKey(self, root, name):
        self.values.setdefault(name, None)
        return Key(name)
    def QueryValueEx(self, key, name):
        if self.values[key.name] is None: raise FileNotFoundError(name)
        return self.values[key.name], 1
    def SetValueEx(self, key, *args):
        if key.name == self.fail_once:
            self.fail_once = None
            raise PermissionError('synthetic write failure')
        self.values[key.name] = args[-1]
    def DeleteKey(self, root, name): del self.values[name]
    def DeleteValue(self, key, name): self.values[key.name] = None


class RegistrationMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.old = self.root / 'old/.local/edge-bridge'
        self.new = self.root / 'new/.local/edge-bridge'
        self.ext = 'a' * 32
        self.write_manifest(self.old)
        self.registry = Registry({setup.COMPAT_REGISTRY: str(self.old / 'native-host.json')})
        for context in (patch('sys.platform', 'win32'), patch.object(setup, 'STATE', self.new),
                        patch.object(setup, 'extension_id', return_value=self.ext),
                        patch.dict(sys.modules, {'winreg': self.registry}),
                        patch('platform_runtime.windows_bridge_install_lock', side_effect=lambda: nullcontext()),
                        patch.object(setup, '_prepare_unlocked', side_effect=lambda: self.write_manifest(self.new))):
            context.start(); self.addCleanup(context.stop)

    def write_manifest(self, directory):
        atomic_json(directory / 'native-host.json', {'name': setup.HOST_NAME, 'type': 'stdio',
                    'path': str(directory / 'native-host.cmd'), 'allowed_origins': ['chrome-extension://' + self.ext + '/']})
        from platform_runtime import private_path
        private_path(directory)
        return {'prepared': True}

    def test_explicit_preview_binds_migration_and_preserves_previous_registration(self):
        preview = setup.registration_snapshot()
        self.assertTrue(preview['migration_required'])
        with self.assertRaises(ValueError): setup.install()
        result = setup.install(preview['registration_sha256'])
        self.assertTrue(result['registration_migrated'])
        self.assertEqual(set(self.registry.values.values()), {str(self.new / 'native-host.json')})
        self.assertEqual(read_json(self.new / 'previous-registration.json'), preview)

    def test_partial_write_restores_old_value_and_removes_only_new_key(self):
        before = dict(self.registry.values)
        preview = setup.registration_snapshot()
        self.registry.fail_once = setup.COMPAT_REGISTRY
        with self.assertRaises(PermissionError): setup.install(preview['registration_sha256'])
        self.assertEqual(self.registry.values, before)

    def test_changed_preview_or_active_old_request_stops_before_registry_write(self):
        preview = setup.registration_snapshot()
        before = dict(self.registry.values)
        with self.assertRaises(ValueError): setup.install('0' * 64)
        atomic_json(self.old / 'status.json', {'active_request_id': 'original'})
        with self.assertRaisesRegex(ValueError, '未完成请求'):
            setup.install(preview['registration_sha256'])
        self.assertEqual(self.registry.values, before)

    def test_same_directory_pending_request_blocks_prepare_and_install_before_write(self):
        self.write_manifest(self.new)
        self.registry.values = {setup.REGISTRY: str(self.new / 'native-host.json')}
        atomic_json(self.new / 'status.json', {'connected': False, 'active_request_id': 'original'})
        before = (self.new / 'native-host.json').read_bytes()
        snapshot = setup.registration_snapshot()
        with patch.object(setup, '_prepare_unlocked') as build:
            with self.assertRaisesRegex(ValueError, '未完成请求'):
                setup.prepare()
            with self.assertRaisesRegex(ValueError, '未完成请求'):
                setup.install(snapshot['registration_sha256'])
            build.assert_not_called()
        self.assertEqual((self.new / 'native-host.json').read_bytes(), before)

    def test_same_directory_live_host_lock_is_preserved(self):
        from platform_runtime import file_lock
        self.write_manifest(self.new)
        with file_lock(self.new / 'host.lock'), patch.object(setup, '_prepare_unlocked') as build:
            with self.assertRaises(RuntimeError):
                setup.prepare()
            build.assert_not_called()
