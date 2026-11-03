from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import worker_context


class WorkerContextTests(unittest.TestCase):
    def test_same_context_reuses_original_job_and_changed_context_is_rejected(self):
        from assistant_api import AssistantService, APIError
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{},clear=True):
            service=AssistantService(root,[root],runtime_context=self.context())
            service.new_case('fixture')
            process=Mock(pid=os.getpid())
            with patch.object(service,'command',return_value=[]),patch('assistant_api.launch_worker',return_value=process) as launch:
                a=service.enqueue('fixture','access',{'action':'check-backend','tab_id':'7','expected_userid':'TESTER'},'same-request')
                b=service.enqueue('fixture','access',{'action':'check-backend','tab_id':'7','expected_userid':'TESTER'},'same-request')
                self.assertEqual(a['job_id'],b['job_id']);self.assertTrue(b['reused_existing_job'])
                self.assertEqual(launch.call_count,1)
                service.runtime_context={**self.context(),'REIMBURSEMENT_EDGE_WINDOW_ID':'8'}
                with self.assertRaises(APIError):service.enqueue('fixture','access',{'action':'check-backend','tab_id':'7','expected_userid':'TESTER'},'same-request')

    def context(self):
        return {'REIMBURSEMENT_EDGE_BACKEND':'edge_native_messaging',
                'REIMBURSEMENT_EDGE_TAB_NAMESPACE':'edge-native-messaging:'+'a'*32+':'+'1'*32,
                'REIMBURSEMENT_EDGE_WINDOW_ID':'7','REIMBURSEMENT_EXPECTED_USERID':'TESTER'}

    def test_only_known_nonsecret_context_is_frozen_and_restored(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{**self.context(),'PRIVATE_PASSWORD':'synthetic-secret'},clear=True):
            with worker_context.task_context(root,'stage') as value:
                self.assertEqual(value,self.context())
                self.assertNotIn('PRIVATE_PASSWORD',value)
            self.assertEqual(os.environ['PRIVATE_PASSWORD'],'synthetic-secret')
            with worker_context.applied(root,{'REIMBURSEMENT_EXPECTED_USERID':'OTHER'}):
                self.assertEqual(os.environ['REIMBURSEMENT_EXPECTED_USERID'],'OTHER')
                self.assertNotIn('REIMBURSEMENT_EDGE_TAB_NAMESPACE',os.environ)
            self.assertEqual(os.environ['REIMBURSEMENT_EXPECTED_USERID'],'TESTER')

    def test_unknown_fields_bad_identity_and_outside_session_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for value in [{'PASSWORD':'no'}, {'REIMBURSEMENT_EXPECTED_USERID':'bad;command'},
                          {'REIMBURSEMENT_EDGE_TAB_NAMESPACE':'edge-native-messaging:'+'a'*32+':'+'1'*32},
                          {'REIMBURSEMENT_REGISTERED_SESSION':str(Path(root).parent/'outside/session.json')}]:
                with self.assertRaises(ValueError):worker_context.validate(value,root)

    def test_exception_restores_parent_and_material_tasks_do_not_capture_context(self):
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'REIMBURSEMENT_EXPECTED_USERID':'BEFORE'},clear=True):
            with self.assertRaises(RuntimeError):
                with worker_context.applied(root,self.context()):
                    raise RuntimeError('fixture')
            self.assertEqual(os.environ['REIMBURSEMENT_EXPECTED_USERID'],'BEFORE')
            with worker_context.task_context(root,'material_intake') as value:self.assertIsNone(value)


if __name__=='__main__':unittest.main()
