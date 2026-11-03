"""Windows登记会话的连接身份、窗口和未知结果合同；不访问财务。"""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import registered_bridge as bridge
import finance_backend
import windows_edge_bridge


class RegisteredBridgeTests(unittest.TestCase):
    def setUp(self):
        self.health = {'ready': True, 'extension_id': 'a'*32, 'epoch': '1'*32}
        self.namespace = 'edge-native-messaging:'+'a'*32+':'+'1'*32
        self.platform = patch('sys.platform', 'win32'); self.platform.start(); self.addCleanup(self.platform.stop)
        self.environment = patch.dict(os.environ, {}, clear=True); self.environment.start(); self.addCleanup(self.environment.stop)
        self.health_patch = patch.object(bridge.queue, 'status', return_value=self.health)
        self.health_patch.start(); self.addCleanup(self.health_patch.stop)

    def inventory(self):
        return {'all_tab_ids': ['7'], 'all_tab_count': 1, 'tab_inventory_complete': True,
                'tab_inventory_scope': 'extension_visible_profile_tabs', 'window_inventory_scope': 'normal',
                'tabs': [{'tab_id': '7', 'window_id': '8', 'path': 'https://cwzh.ustc.edu.cn/YBX/main.jsp'}],
                'windows': [{'window_id': '8', 'active_tab_id': '7'}]}

    def test_complete_inventory_and_synthetic_rejection(self):
        with patch.object(bridge.queue, 'request', return_value=self.inventory()) as request:
            result = bridge.inventory()
            self.assertEqual(result['tab_id_namespace'], self.namespace)
            self.assertEqual(request.call_args.args[1]['expected_epoch'], '1'*32)
        with patch.object(bridge.queue, 'status', return_value={**self.health, 'synthetic_profile': True}):
            with self.assertRaises(bridge.BridgeUnavailable):bridge.inventory()

    def test_partial_inventory_and_changed_epoch_never_used(self):
        with patch.object(bridge.queue, 'request', return_value={**self.inventory(), 'all_tab_count': 2}):
            with self.assertRaises(bridge.BridgeUnavailable):bridge.inventory()
        with patch.object(bridge.queue, 'request') as request:
            with self.assertRaises(bridge.BridgeUnavailable):
                bridge.close_tab('7', tab_id_namespace=self.namespace+'x', expected_window_id='8')
            request.assert_not_called()

    def test_window_creation_and_timeout_are_single_dispatch(self):
        with patch.object(bridge.queue, 'request', return_value={'window_created': True,
                'window_focus_requested': False, 'tab_id': '7', 'window_id': '8'}) as request:
            self.assertEqual(bridge.new_finance_window()['window_id'], '8')
            self.assertEqual(request.call_count, 1)
        with patch.object(bridge.queue, 'request', side_effect=TimeoutError('unknown')) as request:
            with self.assertRaises(TimeoutError):bridge.new_finance_window()
            self.assertEqual(request.call_count, 1)

    def test_registered_evaluate_binds_epoch_window_without_weakening_legacy(self):
        with patch.object(windows_edge_bridge, 'request', return_value='{}') as request:
            windows_edge_bridge.evaluate('fixed-source', '7')
            self.assertNotIn('expected_window_id', request.call_args.args[1])
            os.environ['REIMBURSEMENT_EDGE_TAB_NAMESPACE'] = self.namespace
            os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = '8'
            windows_edge_bridge.evaluate('fixed-source', '7')
            self.assertEqual(request.call_args.args[1]['expected_window_id'], '8')
            self.assertEqual(request.call_args.args[1]['expected_epoch'], '1'*32)
            identity = finance_backend.transport_identity()
            self.assertEqual(identity['window_id'], '8')
            self.assertEqual(identity['tab_id_namespace'], self.namespace)

    def test_window_and_user_selection_drift_rejected(self):
        reply = {'tab_id': '7', 'old_window_id': '8', 'window_id': '9', 'relocated': True,
                 'foreground_requested': False, 'navigation_requested': False, 'business_replayed': False,
                 'source_active_tab_id': '6', 'source_active_tab_preserved': True}
        with patch.object(bridge.queue, 'request', return_value=reply):
            value = bridge.relocate_finance_tab('7', tab_id_namespace=self.namespace, expected_window_id='8', expected_active_tab_id='6')
            self.assertEqual(value['window_id'], '9')
        with patch.object(bridge.queue, 'request', return_value={**reply, 'source_active_tab_preserved': False}):
            with self.assertRaises(bridge.BridgeUnavailable):
                bridge.relocate_finance_tab('7', tab_id_namespace=self.namespace, expected_window_id='8', expected_active_tab_id='6')

    def test_show_verification_requires_exact_bill_and_does_not_read_code(self):
        value = {'shown': True, 'tab_id': '7', 'window_id': '8', 'serial': '123', 'verification_only': True, 'code_read': False}
        with patch.object(bridge.queue, 'request', return_value=value) as request:
            bridge.show_verification('7', tab_id_namespace=self.namespace, expected_window_id='8', serial='123', expected_userid='TESTER')
            self.assertEqual(request.call_args.args[0], 'show-verification')
        with patch.object(bridge.queue, 'request', return_value={**value, 'code_read': True}):
            with self.assertRaises(bridge.BridgeUnavailable):
                bridge.show_verification('7', tab_id_namespace=self.namespace, expected_window_id='8', serial='123', expected_userid='TESTER')

    def test_mac_delegates_existing_owner(self):
        with patch('sys.platform', 'darwin'), patch('mac_edge_bridge.status', return_value={'ready': 'fixture'}) as status:
            self.assertEqual(bridge.status(), {'ready': 'fixture'})
            status.assert_called_once_with()

    def test_teacher_verification_requires_original_node(self):
        value={'shown':True,'tab_id':'7','window_id':'8','serial':'123','node_id':'449','verification_only':True,'code_read':False}
        args={'tab_id_namespace':self.namespace,'expected_window_id':'8','serial':'123','expected_userid':'TESTER','node_id':'449'}
        with patch.object(bridge.queue,'request',return_value=value)as request:
            bridge.show_approver_verification('7',**args)
            self.assertEqual(request.call_args.args[0],'show-approver-verification')
            self.assertEqual(request.call_args.args[1]['node_id'],'449')
        with patch.object(bridge.queue,'request',return_value={**value,'node_id':'508'}):
            with self.assertRaises(bridge.BridgeUnavailable):bridge.show_approver_verification('7',**args)


if __name__ == '__main__':unittest.main()
