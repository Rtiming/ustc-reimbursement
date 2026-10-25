"""同一原生工作页合同覆盖Windows与Mac；不连接浏览器。"""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_finance_bridge as bridge
import windows_edge_bridge as queue
from finance_backend import transport_identity, assert_original_transport
from registered_session import RegisteredSession
from assistant_api import atomic_json, read_json, APIError

NS = 'edge-native-messaging:' + 'a' * 32 + ':' + 'b' * 32
HEALTH = {'ready': True, 'extension_id': 'a' * 32, 'epoch': 'b' * 32, 'tab_id_namespace': NS}


class NativeWorkflowBridgeTests(unittest.TestCase):
    def test_unknown_desktop_is_not_reported_as_preserved(self):
        from finance_session import foreground_comparison
        from finance_page_open import _observe
        before = {'frontmost_bundle_id': None, 'frontmost_process_id': None, 'foreground_identity_observed': False,
                  'tabs': [], 'windows': [{'window_id': '1', 'active_tab_id': '2'}]}
        after = {**before, 'tabs': [{'tab_id': '3', 'window_id': '1', 'path': 'https://cwzh.ustc.edu.cn/YBX/main.jsp'}]}
        self.assertIsNone(foreground_comparison(before, after))
        result = _observe({'before': before, 'native_created_tab_id': '3', 'silent': True}, after)
        self.assertTrue(result['page_opened'])
        self.assertFalse(result['quiet_verified_at_observation_points'])
        self.assertFalse(result['ok'])

    def test_windows_targets_and_manual_verification_share_exact_namespace(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), patch('sys.platform', platform), patch.object(bridge, 'status', return_value=HEALTH), patch.object(queue, 'request', return_value='fixture') as send:
                bridge.evaluate('fixed fixture', '12', tab_id_namespace=NS, expected_window_id='3')
                self.assertEqual(send.call_args.args[1]['expected_epoch'], HEALTH['epoch'])
                self.assertEqual(send.call_args.args[1]['expected_window_id'], '3')
            response = {'shown': True, 'tab_id': '12', 'window_id': '3', 'serial': '12345',
                        'verification_only': True, 'code_read': False}
            with patch('sys.platform', platform), patch.object(bridge, 'status', return_value=HEALTH), patch.object(queue, 'request', return_value=response) as send:
                result = bridge.show_verification('12', tab_id_namespace=NS, expected_window_id='3', serial='12345', expected_userid='fixture01')
                self.assertFalse(result['code_read'])
                self.assertEqual(send.call_args.args[0], 'show-verification')
                self.assertNotIn('code', send.call_args.args[1])

    def test_windows_transport_change_cannot_recover_old_marker(self):
        with patch('sys.platform', 'win32'), patch.dict(os.environ, {'REIMBURSEMENT_EDGE_BACKEND': 'edge_native_messaging', 'REIMBURSEMENT_EDGE_TAB_NAMESPACE': NS, 'REIMBURSEMENT_EDGE_WINDOW_ID': '3'}):
            identity = transport_identity()
            self.assertEqual(identity['tab_id_namespace'], NS)
            self.assertEqual(identity['window_id'], '3')
            with patch.dict(os.environ, {'REIMBURSEMENT_EDGE_WINDOW_ID': '4'}), self.assertRaises(ValueError):
                assert_original_transport({'browser_transport': identity})

    def test_repeated_native_request_only_reads_original_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            key = 'c' * 32
            packet = {'schema': 'ustc-native-edge-v1', 'id': key, 'epoch': HEALTH['epoch'], 'operation': 'create-finance-window', 'arguments': {'expected_epoch': HEALTH['epoch']}, 'expires_at': 0}
            queue.atomic_private(root / 'requests' / (key + '.json'), packet)
            original = (root / 'requests' / (key + '.json')).read_bytes()
            queue.atomic_private(root / 'responses' / (key + '.json'), {'id': key, 'epoch': HEALTH['epoch'], 'ok': True, 'result': {'window_id': '3'}})
            with patch.object(queue, 'status', return_value=HEALTH):
                self.assertEqual(queue.request('create-finance-window', packet['arguments'], state=root, request_id=key)['window_id'], '3')
                with self.assertRaises(queue.BridgeUnavailable):
                    queue.request('create-finance-window', {}, state=root, request_id=key)
            self.assertEqual((root / 'requests' / (key + '.json')).read_bytes(), original)
            self.assertEqual(len(list((root / 'requests').glob('*.json'))), 1)

    def test_unknown_window_creation_reuses_id_until_session_is_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            index = root / 'index.json'
            atomic_json(index, {'documents': [{'case_id': 'one'}]})
            session = RegisteredSession(SimpleNamespace(root=root), 'batch', {}, index, root / 'auth.json')
            reply = {'tab_id': '12', 'window_id': '3', 'tab_id_namespace': NS}
            with patch.object(bridge, 'new_finance_window', side_effect=[TimeoutError('unknown'), reply]) as create:
                with self.assertRaises(TimeoutError):
                    session.open_window('one', HEALTH)
                opened = session.open_window('one', HEALTH)
            self.assertEqual(create.call_args_list[0].kwargs['request_id'], create.call_args_list[1].kwargs['request_id'])
            self.assertTrue((session.folder / 'window-request-one.json').exists())
            atomic_json(session.state_path, {'creation_receipt': opened})
            session.finish_window_request('one', opened)
            self.assertFalse((session.folder / 'window-request-one.json').exists())
            self.assertTrue(list(session.folder.glob('window-receipt-*.json')))
