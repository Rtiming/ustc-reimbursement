"""冻结原页迁移只改变窗口绑定，不恢复/重放业务写入。"""

import os
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import APIError, atomic_json, read_json
from registered_workflow import RegisteredSession, run_registered_workflow
from registered_page_owner import verify_marker_relocations


class PageRecoveryTests(unittest.TestCase):
    def test_inactive_original_page_is_recovered_with_user_selection_bound(self):
        self.inventory['tabs'][0]['active']=False
        self.inventory['windows']=[{'window_id':'8','active_tab_id':'6'}]
        with patch('registered_bridge.inventory',return_value=self.inventory),patch('registered_bridge.relocate_finance_tab',return_value=self.moved) as move:
            self.session.recover_frozen()
        self.assertEqual(move.call_args.kwargs['expected_active_tab_id'],'6')
        self.assertEqual(read_json(self.session.state_path)['window_id'],'9')

    def test_structured_ack_without_output_file_is_returned_without_replay(self):
        from unittest.mock import Mock
        message={'ok':True,'pending_cleared':True,'system_write':False}
        service=Mock()
        service.enqueue.return_value={'job_id':'original-job'}
        service.result.return_value={'job_id':'original-job','status':'completed','exit_code':0,'artifacts':[],'messages':[message]}
        self.session.service=service
        job,body=self.session._job_result('one','record_result',{},'original-request',False,lambda job:None)
        self.assertEqual(body,message);service.enqueue.assert_called_once();service.result.assert_called_once()
        service.artifact.assert_not_called()

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
            patch('registered_bridge.inventory', return_value=self.inventory),
            patch('registered_bridge.relocate_finance_tab', return_value=self.moved) as move,
        ):
            self.session.recover_frozen()
            self.assertEqual(os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'], '9')
        move.assert_called_once_with('7', tab_id_namespace='namespace', expected_window_id='8')
        self.assertEqual(read_json(self.session.state_path)['tab_id'], '7')
        self.assertEqual(read_json(self.session.state_path)['window_id'], '9')
        self.assertFalse((self.session.folder / 'page-relocation-pending.json').exists())

    def test_unknown_move_keeps_intent_and_refuses_repeat(self):
        with (
            patch('registered_bridge.inventory', return_value=self.inventory),
            patch(
                'registered_bridge.relocate_finance_tab', side_effect=TimeoutError('unknown')
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
            patch('registered_bridge.inventory', return_value=self.inventory),
            patch('registered_bridge.relocate_finance_tab') as move,
        ):
            self.session.recover_frozen()
        move.assert_not_called()
        self.assertEqual(self.session.state_path.read_bytes(), before)

    def test_child_relocation_adoption_requires_same_tab_epoch_and_proof(self):
        self.session.state['namespace'] = 'namespace'
        path = self.session.folder / 'page-relocation-child.json'
        proof = {'old_window_id':'8','tab_id':'7','namespace':'namespace','result':self.moved}
        atomic_json(path, proof)
        fresh = {**self.session.state,'window_id':'9','last_relocation_receipt':str(path)}
        atomic_json(self.session.state_path, fresh)
        self.session.refresh_owned_state()
        self.assertEqual(self.session.state['window_id'], '9')
        atomic_json(self.session.state_path, {**fresh,'window_id':'10','tab_id':'OTHER'})
        with self.assertRaises(APIError): self.session.refresh_owned_state()

    def test_child_dispatch_accepts_only_proved_relocation_of_owned_tab_epoch(self):
        from registered_page_owner import RegisteredPageOwner
        namespace='namespace'
        self.session.state.update(namespace=namespace,owned_tab_ids=['7'])
        proof=self.session.folder/'page-relocation-child.json'
        atomic_json(proof,{'old_window_id':'8','tab_id':'7','namespace':namespace,'result':self.moved})
        atomic_json(self.session.state_path,{**self.session.state,'window_id':'9','last_relocation_receipt':str(proof.resolve())})
        transport={'backend':'edge_native_messaging','tab_id_namespace':namespace,'window_id':'8'}
        with patch.dict(os.environ,{'REIMBURSEMENT_REGISTERED_SESSION':str(self.session.state_path.resolve())}), \
                patch('registered_page_owner.REGISTERED_ROOT',self.session.folder.parent.resolve()), \
                patch('finance_backend.transport_identity',return_value=transport), \
                patch.object(RegisteredPageOwner,'recover_frozen')as recover:
            RegisteredPageOwner.before_child_dispatch('7');recover.assert_called_once()
            self.assertEqual(os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'],'9')
            for change in [{'tab_id_namespace':'other'},{'window_id':'10'}]:
                with patch('finance_backend.transport_identity',return_value={**transport,**change}):
                    with self.assertRaises(APIError):RegisteredPageOwner.before_child_dispatch('7')
            with self.assertRaises(APIError):RegisteredPageOwner.before_child_dispatch('OTHER')

    def test_later_original_marker_recovery_requires_sha_and_window_chain(self):
        path = self.session.folder / 'page-relocation-test.json'
        atomic_json(path, {'old_window_id':'8','tab_id':'7','namespace':'namespace','result':self.moved})
        original = {'backend':'edge_native_messaging','tab_id_namespace':'namespace','window_id':'8'}
        current = {**original,'window_id':'9'}
        row = {'old_window_id':'8','window_id':'9','receipt':str(path.resolve()),
               'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
               'tab_id':'7','original_marker':'original','script_reexecuted':False}
        receipt = {'tab_id':'7','result_marker':'original','marker_recoveries':[row]}
        with patch('registered_page_owner.REGISTERED_ROOT', self.session.folder.parent.resolve()):
            self.assertTrue(verify_marker_relocations(receipt, original, current))
            for changed in ({**current,'tab_id_namespace':'changed'},{**current,'window_id':'10'}):
                self.assertFalse(verify_marker_relocations(receipt, original, changed))
            row['script_reexecuted'] = True
            self.assertFalse(verify_marker_relocations(receipt, original, current))
            row['script_reexecuted'] = False
            path.write_text('{}')
            self.assertFalse(verify_marker_relocations(receipt, original, current))

    def test_later_owner_relocation_can_bind_only_original_marker_of_same_owned_document(self):
        from registered_page_owner import current_registered_marker_binding
        state={**self.session.state,'namespace':'namespace','window_id':'9','owned_tab_ids':['7']}
        proof=self.session.folder/'page-relocation-later.json'
        atomic_json(proof,{'old_window_id':'8','tab_id':'7','namespace':'namespace','result':self.moved})
        state['last_relocation_receipt']=str(proof.resolve());atomic_json(self.session.state_path,state)
        original={'backend':'edge_native_messaging','tab_id_namespace':'namespace','window_id':'8'}
        current={**original,'window_id':'9'}
        receipt={'tab_id':'7','browser_transport':original,'result_marker':'reimbursement-result-'+'a'*32}
        with patch.dict(os.environ,{'REIMBURSEMENT_REGISTERED_SESSION':str(self.session.state_path.resolve())}), \
                patch('registered_page_owner.REGISTERED_ROOT',self.session.folder.parent.resolve()), \
                patch('finance_backend.transport_identity',return_value=current):
            self.assertTrue(current_registered_marker_binding(receipt,current))
            self.assertFalse(current_registered_marker_binding({**receipt,'tab_id':'other'},current))
            self.assertFalse(current_registered_marker_binding({**receipt,'result_marker':'arbitrary'},current))
            self.assertFalse(current_registered_marker_binding(receipt,{**current,'tab_id_namespace':'changed'}))
            atomic_json(proof,{'old_window_id':'other','tab_id':'7','namespace':'namespace','result':self.moved})
            self.assertFalse(current_registered_marker_binding(receipt,current))

    def test_access_page_recovery_requires_explicit_scope(self):
        self.inventory['tabs'][0]['path'] = 'https://id.ustc.edu.cn/cas/login'
        with patch('registered_bridge.inventory', return_value=self.inventory), patch('registered_bridge.relocate_finance_tab', return_value=self.moved) as move:
            with self.assertRaises(APIError):
                self.session.recover_frozen()
            move.assert_not_called()
            self.session.recover_frozen(allow_access=True)
        move.assert_called_once_with('7', tab_id_namespace='namespace', expected_window_id='8', allow_access=True)

    def test_only_registered_initial_portal_is_closed(self):
        self.session.state.update(creation_receipt={'window_created': True, 'tab_id': '6', 'window_id': '8'}, owned_tab_ids=['6', '7'])
        self.inventory['tabs'].append({'tab_id': '6', 'window_id': '8', 'path': 'https://cwzh.ustc.edu.cn/WFManager/home2.jsp'})
        with patch('registered_bridge.close_tab', return_value={'closed': True}) as close:
            self.session.release_initial_portal(self.inventory)
            self.session.release_initial_portal(self.inventory)
        close.assert_called_once_with('6', tab_id_namespace='namespace', expected_window_id='8')

    def test_unowned_or_changed_portal_is_preserved(self):
        self.session.state.update(creation_receipt={'window_created': True, 'tab_id': '6', 'window_id': '8'}, owned_tab_ids=['7'])
        self.inventory['tabs'].append({'tab_id': '6', 'window_id': '8', 'path': 'https://cwzh.ustc.edu.cn/WFManager/home2.jsp'})
        with patch('registered_bridge.close_tab') as close:
            self.session.release_initial_portal(self.inventory)
            self.session.state['owned_tab_ids'].append('6')
            self.inventory['tabs'][-1]['path'] = 'https://cwzh.ustc.edu.cn/YBX/main.jsp'
            self.session.release_initial_portal(self.inventory)
        close.assert_not_called()

    def test_only_exact_pre_mutation_rejection_settles_pending(self):
        state = self.session.folder / 'synthetic-bridge'
        (state / 'requests').mkdir(parents=True)
        (state / 'responses').mkdir()
        pending = self.session.folder / 'page-relocation-pending.json'
        intent = {'requested_at_ns': 2000000000, 'tab_id': '7', 'old_window_id': '8', 'namespace': 'prefix:epoch'}
        atomic_json(pending, intent)
        atomic_json(state / 'requests/r.json', {'id': 'r', 'epoch': 'epoch', 'created_at': 2,
            'operation': 'relocate-finance-tab', 'arguments': {'tab_id': '7', 'expected_window_id': '8'}})
        reply = {'id': 'r', 'epoch': 'epoch', 'ok': False, 'error_code': 'relocation_result_unknown'}
        atomic_json(state / 'responses/r.json', reply)
        with patch('registered_bridge.STATE', state):
            self.session.settle_rejected_relocation(pending)
            self.assertTrue(pending.exists())
            reply['error_code'] = 'relocation_target_not_isolated_frozen'
            atomic_json(state / 'responses/r.json', reply)
            self.session.settle_rejected_relocation(pending)
        self.assertFalse(pending.exists())
        self.assertEqual(read_json(self.session.folder / 'page-relocation-original-2000000000.json'), intent)

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
                patch('registered_bridge.status', return_value=bridge),
                patch('registered_bridge.inventory', return_value={'tabs': []}),
                patch('registered_bridge.new_finance_window') as opened,
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
                'registered_bridge.status', return_value={'ready': True, 'tab_id_namespace': 'new'}
            ),
            patch('registered_bridge.inventory') as inventory,
        ):
            with self.assertRaisesRegex(APIError, '连接身份变化'):
                self.session.ensure(preserve_original=True)
        inventory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
