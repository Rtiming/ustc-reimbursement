"""Windows真实OS边界及两端传输合同；不访问学校财务。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import edge_native_host
import edge_read_probe
import edge_runtime
import finance_access
import finance_page_open
import finance_session
import platform_runtime
import windows_edge_bridge as bridge
import windows_bridge_setup
import assistant_job
from assistant_api import AssistantService, atomic_json, read_json
from types import SimpleNamespace


class PlatformRuntimeTests(unittest.TestCase):
    def test_private_files_and_directories_have_actual_platform_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform_runtime.private_path(root)
            self.assertTrue(platform_runtime.is_private_path(root))
            path = root/'秘密.json'
            path.touch()
            platform_runtime.private_path(path)
            self.assertTrue(platform_runtime.is_private_path(path))

    def test_current_process_probe_never_uses_windows_kill_zero(self):
        if os.name == 'nt':
            with patch.object(platform_runtime.os, 'kill', side_effect=AssertionError('must not signal')):
                self.assertTrue(platform_runtime.process_alive(os.getpid()))
                self.assertFalse(platform_runtime.process_alive(99999999))
        else:
            self.assertTrue(platform_runtime.process_alive(os.getpid()))
        for invalid in [None, True, '12', -1, 0]:
            self.assertIsNone(platform_runtime.process_alive(invalid))

    def test_reading_child_liveness_does_not_terminate_it(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(2)'], **platform_runtime.background_popen_kwargs())
        try:
            for _ in range(3):
                self.assertTrue(platform_runtime.process_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(platform_runtime.process_alive(child.pid))

    @unittest.skipUnless(os.name == 'nt', 'Windows内核Job验收')
    def test_owned_command_closes_detached_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'child.json'
            code = "import os,subprocess,sys,time,json; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=subprocess.CREATE_NEW_PROCESS_GROUP);open(sys.argv[1],'w').write(json.dumps(p.pid));time.sleep(30)"
            with (Path(directory)/'log').open('w') as log:
                process, owner = platform_runtime.start_command([sys.executable, '-c', code, str(path)], cwd=directory, env=os.environ.copy(), stdout=log, stderr=log)
                try:
                    deadline = time.monotonic()+5
                    while not path.exists() and time.monotonic()<deadline:
                        time.sleep(.03)
                    child_pid = json.loads(path.read_text())
                    self.assertTrue(platform_runtime.process_alive(child_pid))
                finally:
                    result = owner.close()
                    process.wait(timeout=5)
                self.assertTrue(result['ok'])
                self.assertFalse(platform_runtime.process_alive(child_pid))

    @unittest.skipUnless(sys.platform in ['darwin', 'win32'], '支持平台OS实锁')
    def test_browser_lock_is_os_owned_and_not_reentrant(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(edge_runtime, 'LOCK_PATH', Path(directory)/'lock'):
            with edge_runtime.browser_lock():
                with self.assertRaisesRegex(RuntimeError, '仍在执行'):
                    with edge_runtime.browser_lock():
                        self.fail('second owner')
            with edge_runtime.browser_lock():
                pass


class NativeProtocolTests(unittest.TestCase):
    def test_unicode_frames_and_truncation(self):
        stream = io.BytesIO()
        edge_native_host.write_frame(stream, {'name': '财务静默', 'ok': True})
        stream.seek(0)
        self.assertEqual(edge_native_host.read_frame(stream)['name'], '财务静默')
        self.assertIsNone(edge_native_host.read_frame(stream))
        for invalid in [b'\x01', b'\x01\x00\x00\x00', b'\xff\xff\xff\xff']:
            with self.assertRaises(ValueError):
                edge_native_host.read_frame(io.BytesIO(invalid))

    def test_not_connected_does_not_create_request_or_start_browser(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'STATE', Path(directory)), patch.object(subprocess, 'Popen') as spawn:
            self.assertFalse(bridge.status()['ready'])
            with self.assertRaises(bridge.BridgeUnavailable):
                bridge.request('inventory')
            self.assertFalse((Path(directory)/'requests').exists())
            spawn.assert_not_called()

    def test_timeout_retains_exact_request_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'STATE', Path(directory)), patch.object(bridge, 'status', return_value={'ready': True, 'epoch': 'fixture'}):
            with self.assertRaisesRegex(TimeoutError, '不重新派发'):
                bridge.request('evaluate', {'source': 'fixture', 'scope': 'finance', 'tab_id': '12'}, timeout=.02)
            paths = list((Path(directory)/'requests').glob('*.json'))
            self.assertEqual(len(paths), 1)
            self.assertEqual(json.loads(paths[0].read_text())['arguments']['source'], 'fixture')

    def test_synthetic_connection_cannot_execute_finance(self):
        with patch.object(bridge, 'status', return_value={'ready': True, 'epoch': 'test', 'synthetic_profile': True}), patch.object(bridge, 'atomic_private') as save:
            with self.assertRaisesRegex(bridge.BridgeUnavailable, '合成测试'):
                bridge.request('evaluate', {'source': 'anything'})
            save.assert_not_called()

    def test_busy_connection_does_not_queue_a_second_operation(self):
        with patch.object(bridge, 'status', return_value={'ready': True, 'epoch': 'test', 'active_request_id': 'original'}), patch.object(bridge, 'atomic_private') as save:
            with self.assertRaisesRegex(bridge.BridgeUnavailable, '原请求'):
                bridge.request('inventory')
            save.assert_not_called()

    @unittest.skipUnless(sys.platform in ['darwin', 'win32'], '主机OS实锁')
    def test_host_dispatches_once_and_skips_expired_previous_epoch_and_existing_intents(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(edge_native_host, 'STATE', Path(directory)):
            root = Path(directory)
            bridge.atomic_private(root/'installation.json', {'extension_id': 'fixture', 'build_sha256': 'build'})
            read_fd, write_fd = os.pipe()
            source = os.fdopen(read_fd, 'rb')
            writer = os.fdopen(write_fd, 'wb')
            destination = io.BytesIO()
            errors = []
            def host():
                try:
                    edge_native_host.serve(source, destination)
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=host, daemon=True)
            worker.start()
            try:
                edge_native_host.write_frame(writer, {'type': 'hello', 'protocol': 1, 'extension_id': 'fixture', 'build_sha256': 'build'})
                deadline = time.monotonic()+5
                while not (root/'status.json').exists() and time.monotonic()<deadline:
                    time.sleep(.01)
                epoch = json.loads((root/'status.json').read_text())['epoch']
                for number, packet_epoch, expiration, recorded in [(1, 'old-epoch', time.time()+10, False), (2, epoch, time.time()-1, False), (3, epoch, time.time()+10, True), (4, epoch, time.time()+10, False)]:
                    key = str(number)*32
                    if recorded:
                        bridge.atomic_private(root/'intents'/(key+'.json'), {'old_intent': True})
                    bridge.atomic_private(root/'requests'/(key+'.json'), {'schema': 'ustc-native-edge-v1', 'id': key, 'epoch': packet_epoch, 'expires_at': expiration, 'operation': 'inventory', 'arguments': {}})
                deadline = time.monotonic()+5
                while not destination.getvalue() and time.monotonic()<deadline:
                    time.sleep(.01)
                frame = edge_native_host.read_frame(io.BytesIO(destination.getvalue()))
                self.assertEqual(frame['id'], '4'*32)
                edge_native_host.write_frame(writer, {'id': frame['id'], 'ok': True, 'result': 'done'})
                deadline = time.monotonic()+5
                while not (root/'responses'/('4'*32+'.json')).exists() and time.monotonic()<deadline:
                    time.sleep(.01)
                time.sleep(.1)
                frames = io.BytesIO(destination.getvalue())
                edge_native_host.read_frame(frames)
                self.assertIsNone(edge_native_host.read_frame(frames))
                self.assertFalse((root/'intents'/('1'*32+'.json')).exists())
                self.assertFalse((root/'intents'/('2'*32+'.json')).exists())
                self.assertIsNone(json.loads((root/'status.json').read_text()).get('active_request_id'))
            finally:
                writer.close()
                worker.join(timeout=5)
                source.close()
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])

    def test_oversize_and_unknown_operations_are_rejected_without_file(self):
        with patch.object(bridge, 'status', return_value={'ready': True, 'epoch': 'test'}), patch.object(bridge, 'atomic_private') as save:
            with self.assertRaises(ValueError):
                bridge.request('arbitrary-js')
            with self.assertRaisesRegex(ValueError, '超过'):
                bridge.request('evaluate', {'source': 'x'*900000})
            save.assert_not_called()

    def test_windows_route_passes_budget_and_never_runs_applescript(self):
        with patch('sys.platform', 'win32'), patch.object(bridge, 'evaluate', return_value='ok') as ev, patch.object(edge_read_probe.subprocess, 'run') as native:
            self.assertEqual(edge_read_probe.evaluate('source', '15', timeout=.5), 'ok')
            ev.assert_called_once_with('source', '15', timeout=.5)
            native.assert_not_called()

    def test_windows_password_injection_is_rejected_before_queueing(self):
        with patch('sys.platform', 'win32'), patch.object(bridge, 'request') as native:
            with self.assertRaises(finance_access.AccessError) as caught:
                finance_access.evaluate('15', 'password-must-not-be-written', stage='login_submit')
            self.assertEqual(caught.exception.code, 'windows_manual_login_required')
            native.assert_not_called()

    def test_default_validation_never_activates_and_foreground_is_not_a_fallback(self):
        with patch.object(bridge, 'request', return_value={'verified': True}) as call:
            bridge.validate_tab('1')
            call.assert_called_once_with('validate-tab', {'tab_id': '1'})
            with self.assertRaises(bridge.BridgeUnavailable):
                bridge.validate_tab('1', foreground=True)
            self.assertEqual(call.call_count, 1)

    def test_extension_id_is_stable_and_production_has_no_loopback_permission(self):
        self.assertEqual(windows_bridge_setup.extension_id(), 'mbpfmmpinknacoaiifdgiamknficipmc')
        manifest = json.loads((ROOT/'extension/manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(any('127.0.0.1' in x for x in manifest['host_permissions']))
        self.assertNotIn('debugger', manifest['permissions'])
        self.assertNotIn('externally_connectable', manifest)

    def test_prepared_launcher_has_literal_paths_and_does_not_touch_binary_stdin(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(windows_bridge_setup, 'STATE', Path(directory).resolve()/'state'):
            record = windows_bridge_setup.prepare()
            command = (Path(directory)/'state/native-host.cmd').read_bytes()
            self.assertNotIn(b'\t', command)
            self.assertNotIn(b'\r\r\n', command)
            self.assertNotIn(b'chcp', command)
            self.assertIn(rb'%~dp0..\..\tools\edge_native_host.py', command)
            built = (Path(record['extension_directory'])/'native_bridge.js').read_text(encoding='utf-8')
            self.assertIn('const BUILD_SHA = "'+record['build_sha256']+'";', built)
            self.assertIn('const SYNTHETIC = false;', built)


class WindowsOpenTests(unittest.TestCase):
    def inventory(self, created=False):
        return {'frontmost_bundle_id': 'windows-hwnd-42', 'frontmost_process_id': 45,
                'edge_frontmost': False, 'windows': [{'window_id': '1', 'active_tab_id': '2'}],
                'tabs': ([{'tab_id': '3', 'window_id': '1', 'path': finance_session.ENTRY}] if created else [])}

    def test_silent_open_and_recovery_use_same_intent_without_reopening(self):
        with tempfile.TemporaryDirectory() as directory, patch('sys.platform', 'win32'), patch.object(finance_session, 'SESSIONS', Path(directory)/'sessions'), patch.object(finance_session, 'native_inventory', side_effect=[self.inventory(), self.inventory(True)]), patch.object(bridge, 'request', return_value={'tab_id': '3', 'old_active_tab_restored': True}) as call:
            output = Path(directory)/'one.json'
            result = finance_page_open.open_page('fixture_case', 'test01', None, output, wait=0)
            self.assertTrue(result['ok'])
            self.assertTrue(result['silent'])
            self.assertEqual(call.call_args.args[1]['silent'], True)
            with patch.object(finance_session, 'native_inventory', return_value=self.inventory(True)):
                again = finance_page_open.open_page('fixture_case', 'test01', None, Path(directory)/'two.json', wait=0)
            self.assertFalse(again['reopened'])
            self.assertEqual(call.call_count, 1)


class WorkerRecoveryTests(unittest.TestCase):
    def test_unverified_tree_cleanup_keeps_unknown_and_both_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = AssistantService(root, [root], sys.executable)
            service.new_case('fixture_case')
            folder = service.jobs/'fixture_case'/('j_'+'d'*24)
            folder.mkdir(parents=True)
            request = {'workspace': str(root), 'material_roots': [str(root)], 'python': sys.executable,
                       'case_id': 'fixture_case', 'request_id': 'cleanup-test', 'operation': 'tabs',
                       'parameters': {}, 'execute': False, 'revision': service.revision('fixture_case')}
            atomic_json(folder/'request.json', request)
            process = SimpleNamespace(pid=os.getpid(), wait=lambda timeout=None: 0)
            owner = SimpleNamespace(close=lambda: {'ok': False, 'active_processes': None})
            with patch.object(assistant_job, 'start_command', return_value=(process, owner)):
                assistant_job.run(folder/'request.json')
            result = read_json(folder/'status.json')
            self.assertEqual(result['status'], 'unknown')
            self.assertTrue(result['recovery_required'])
            for name in ['browser.lock', 'case-fixture_case.lock']:
                self.assertTrue(read_json(root/'.local/assistant_locks'/name)['retain_for_recovery'])


if __name__ == '__main__':
    unittest.main()
