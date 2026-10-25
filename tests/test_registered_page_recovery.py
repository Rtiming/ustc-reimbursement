"""冻结原页迁移只改变窗口绑定，不恢复/重放业务写入。"""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import APIError, atomic_json, read_json
from registered_workflow import RegisteredSession, run_registered_workflow


class PageRecoveryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        index = root / 'index.json'
        atomic_json(index, {'documents': [{'case_id': 'one'}]})
        self.session = RegisteredSession(
            SimpleNamespace(root=root), 'root', {}, index, root / 'auth.json'
        )
        self.session.state = {
            'case_id': 'root',
            'created_by_registered_workflow': True,
            'last_case_id': 'one',
            'namespace': 'namespace',
            'tab_id': '7',
            'window_id': '8',
        }
        atomic_json(self.session.state_path, self.session.state)
        self.inventory = {
            'tab_id_namespace': 'namespace',
            'tabs': [
                {
                    'tab_id': '7',
                    'window_id': '8',
                    'path': 'https://cwzh.ustc.edu.cn/YBX/main.jsp',
                    'frozen': True,
                }
            ],
        }
        self.moved = {'window_id': '9', 'tab_id_namespace': 'namespace', 'woken': True}

    def test_recovery_persists_new_binding_before_next_operation(self):
        with (
            patch.dict(os.environ, {}),
            patch('mac_edge_bridge.inventory', return_value=self.inventory),
            patch('mac_edge_bridge.relocate_finance_tab', return_value=self.moved) as move,
        ):
            self.session.recover_frozen()
            self.assertEqual(os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'], '9')
        move.assert_called_once_with('7', tab_id_namespace='namespace', expected_window_id='8')
        self.assertEqual(read_json(self.session.state_path)['tab_id'], '7')
        self.assertEqual(read_json(self.session.state_path)['window_id'], '9')
        self.assertFalse((self.session.folder / 'page-relocation-pending.json').exists())

    def test_unknown_move_keeps_intent_and_refuses_repeat(self):
        with (
            patch('mac_edge_bridge.inventory', return_value=self.inventory),
            patch(
                'mac_edge_bridge.relocate_finance_tab', side_effect=TimeoutError('unknown')
            ) as move,
        ):
            with self.assertRaises(TimeoutError):
                self.session.recover_frozen()
            with self.assertRaisesRegex(APIError, '原迁移尚未'):
                self.session.recover_frozen()
        self.assertEqual(move.call_count, 1)
        self.assertTrue((self.session.folder / 'page-relocation-pending.json').exists())

    def test_unfrozen_page_has_no_mutation(self):
        self.inventory['tabs'][0]['frozen'] = False
        before = self.session.state_path.read_bytes()
        with (
            patch('mac_edge_bridge.inventory', return_value=self.inventory),
            patch('mac_edge_bridge.relocate_finance_tab') as move,
        ):
            self.session.recover_frozen()
        move.assert_not_called()
        self.assertEqual(self.session.state_path.read_bytes(), before)

    def test_material_resume_refreshes_only_attachment_status(self):
        current = self.session.folder / 'current-result.json'
        atomic_json(current, {'applicant_ready_count': 31, 'attachments': {'ok': False}})
        with (
            patch('registered_workflow.load_definition', return_value=({}, None, None, None)),
            patch(
                'material_packet_batch.run_registered_packets',
                return_value={'ok': True, 'pdf_count': 37},
            ),
        ):
            result = run_registered_workflow(
                self.session.service, case_id='root', phase='materials', execute=True
            )
        self.assertTrue(result['ok'])
        self.assertTrue(read_json(current)['attachments']['ok'])
        self.assertEqual(read_json(current)['applicant_ready_count'], 31)

    def test_missing_original_page_cannot_open_replacement(self):
        self.session.cfg['expected_userid'] = 'SYNTH001'
        self.session.state['namespace'] = 'namespace'
        atomic_json(self.session.state_path, self.session.state)
        before = self.session.state_path.read_bytes()
        bridge = {'ready': True, 'tab_id_namespace': 'namespace'}
        with patch('sys.platform', 'darwin'):
            with (
                patch('mac_edge_bridge.status', return_value=bridge),
                patch('mac_edge_bridge.inventory', return_value={'tabs': []}),
                patch('mac_edge_bridge.new_finance_window') as opened,
            ):
                with self.assertRaisesRegex(APIError, '不能用新页'):
                    self.session.ensure(preserve_original=True)
        opened.assert_not_called()
        self.assertEqual(self.session.state_path.read_bytes(), before)

    def test_original_namespace_change_stops_before_browser_mutation(self):
        self.session.cfg['expected_userid'] = 'SYNTH001'
        self.session.state['namespace'] = 'old'
        atomic_json(self.session.state_path, self.session.state)
        with (
            patch('sys.platform', 'darwin'),
            patch(
                'mac_edge_bridge.status', return_value={'ready': True, 'tab_id_namespace': 'new'}
            ),
            patch('mac_edge_bridge.inventory') as inventory,
        ):
            with self.assertRaisesRegex(APIError, '连接身份变化'):
                self.session.ensure(preserve_original=True)
        inventory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
