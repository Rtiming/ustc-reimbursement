import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_execution_mode import prepare_focus, validate_mode, restore, inherit_batch_mode, preflight_ready

class ExecutionModeTests(unittest.TestCase):
    def test_applicant_confirmation_uses_role_context_when_preview_is_empty(self):
        request={'operation':'submission','parameters':{'action':'applicant-agree','plan':{'serial':'99001','expected_userid':'TEST01'}}}
        observation={'project':'WF_YBX','serial':'99001','userid':'TEST01','node':'508','activeRequests':0,'applicantAgreementVisible':True}
        health={'state':'preview_not_loaded','observation':observation}
        self.assertTrue(preflight_ready(request,health))
        for field,value in [('serial','99002'),('userid','OTHER'),('node','449'),('activeRequests',1),('applicantAgreementVisible',False)]:
            self.assertFalse(preflight_ready(request,{**health,'observation':{**observation,field:value}}))
        for state in ['identity_mismatch','native_backend_unavailable','transport_unavailable','preview_identity_mismatch']:
            self.assertFalse(preflight_ready(request,{**health,'state':state}))
        self.assertFalse(preflight_ready({**request,'parameters':{**request['parameters'],'action':'next'}},health))

    def test_default_and_authority_scope(self):
        self.assertEqual(validate_mode(None,'test_case'),{'mode':'silent'})
        good={'schema':'ustc-execution-mode-v1','mode':'full_control','case_id':'test_case','authorized_by':'user','source':'本案用户明确授权'}
        self.assertEqual(validate_mode(good,'test_case'),good)
        for bad in [{**good,'case_id':'other_case'},{**good,'source':''},{**good,'authorized_by':'document'},{**good,'skip_readback':True}]:
            with self.assertRaises(ValueError):validate_mode(bad,'test_case')

    def test_focus_only_authorized_finance_job_and_reuse_context(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);folder=root/'.local/assistant_jobs/test_case/j_fixture';folder.mkdir(parents=True)
            mode={'schema':'ustc-execution-mode-v1','mode':'full_control','case_id':'test_case','authorized_by':'user','source':'用户授权','restore_context':True}
            case=root/'case.json';case.write_text(json.dumps({'case_id':'test_case','material_context':{'execution_mode':mode}}))
            request={'case_id':'test_case','operation':'read_system','execute':False,'parameters':{'tab_id':'123'}}
            original={'frontmost_bundle_id':'com.openai.codex','windows':[{'window_id':'9','active_tab_id':'124'}],
                      'tabs':[{'tab_id':'123','window_id':'9','path':'https://cwzh.ustc.edu.cn/YBX/main.jsp'}]}
            active={**original,'frontmost_bundle_id':'com.microsoft.edgemac','windows':[{'window_id':'9','active_tab_id':'123'}]}
            with patch('finance_backend.transport_identity',return_value={'backend':'edge_applescript'}),patch('finance_session.native_inventory',side_effect=[original,active]),patch('edge_read_probe.activate_tab')as activate:
                first=prepare_focus(request,case,folder);second=prepare_focus(request,case,folder)
            activate.assert_called_once_with('123',foreground=True)
            self.assertEqual(second['state'],'already_active')
            state=json.loads(Path(first['session_path']).read_text());self.assertEqual(state['before'],original);self.assertEqual(state['owned_tab_ids'],['123'])
            with patch('finance_backend.transport_identity',side_effect=AssertionError('本地/计划不能访问浏览器')):
                self.assertIsNone(prepare_focus({**request,'operation':'record_result'},case,folder))
                self.assertIsNone(prepare_focus({**request,'operation':'stage'},case,folder))
            foreign={**original,'tabs':[{'tab_id':'123','path':'https://unrelated.invalid/'}]}
            with patch('finance_backend.transport_identity',return_value={'backend':'edge_applescript'}),patch('finance_session.native_inventory',return_value=foreign),patch('edge_read_probe.activate_tab')as activate:
                with self.assertRaises(ValueError):prepare_focus(request,case,folder)
                activate.assert_not_called()
            with patch('finance_session.native_inventory',return_value=active),patch('finance_session.restore_active',return_value=[{'state':'restored'}]),patch('finance_session.restore_foreground',return_value={'state':'user_foreground_preserved'}):
                self.assertTrue(restore(case,root)['ok'])
                self.assertEqual(restore(case,root)['state'],'already_restored')

    def test_failed_backend_blocks_before_business_dispatch(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);folder=root/'.local/assistant_jobs/test_case/j_test';folder.mkdir(parents=True)
            mode={'schema':'ustc-execution-mode-v1','mode':'full_control','case_id':'test_case','authorized_by':'user','source':'用户授权'}
            case=root/'case.json';case.write_text(json.dumps({'case_id':'test_case','material_context':{'execution_mode':mode}}))
            inventory={'frontmost_bundle_id':'com.microsoft.edgemac','windows':[{'window_id':'9','active_tab_id':'123'}],'tabs':[{'tab_id':'123','path':'https://cwzh.ustc.edu.cn/YBX/main.jsp'}]}
            request={'case_id':'test_case','operation':'access','execute':True,'parameters':{'tab_id':'123','expected_userid':'TEST01','action':'remuneration-entry'}}
            with patch('finance_backend.transport_identity',return_value={'backend':'edge_applescript'}),patch('finance_session.native_inventory',return_value=inventory),patch('finance_session.check_page',return_value={'state':'native_backend_unavailable'}):
                with self.assertRaisesRegex(ValueError,'尚未派发'):prepare_focus(request,case,folder)

    def test_batch_inherits_only_listed_children_and_preserves_explicit_choice(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);mode={'schema':'ustc-execution-mode-v1','mode':'full_control','case_id':'parent_case','authorized_by':'user','source':'用户批次授权'}
            for cid,value in [('parent_case',mode),('child_one',None),('child_two',{'schema':'ustc-execution-mode-v1','mode':'silent','case_id':'child_two'}),('unlisted_child',None)]:
                (root/(cid+'.json')).write_text(json.dumps({'case_id':cid,'material_context':{'execution_mode':value}}))
            class Service:
                def resolve(self,cid):return None,{'case':root/(cid+'.json')}
                def revision(self,cid):return 'fixture'
                def update_case(self,cid,document,change,revision):
                    p=root/(cid+'.json');data=json.loads(p.read_text());data['material_context'].update(change['material_context']);p.write_text(json.dumps(data))
            service=Service();self.assertEqual(inherit_batch_mode(service,'parent_case',['child_one','child_two']),['child_one'])
            child=json.loads((root/'child_one.json').read_text())['material_context']['execution_mode'];self.assertEqual(child['inherited_from'],'parent_case')
            self.assertIsNone(json.loads((root/'unlisted_child.json').read_text())['material_context']['execution_mode'])
            parent=root/'parent_case.json';data=json.loads(parent.read_text());data['material_context']['execution_mode']={**mode,'mode':'silent'};parent.write_text(json.dumps(data))
            self.assertEqual(inherit_batch_mode(service,'parent_case',['child_one','child_two']),['child_one'])
            self.assertEqual(json.loads((root/'child_one.json').read_text())['material_context']['execution_mode']['mode'],'silent')

    def test_native_backend_does_not_switch_or_change_transport(self):
        with tempfile.TemporaryDirectory()as d:
            p=Path(d)/'case.json';p.write_text(json.dumps({'case_id':'test_case','material_context':{'execution_mode':{'schema':'ustc-execution-mode-v1','mode':'full_control','case_id':'test_case','authorized_by':'user','source':'用户'}}}))
            request={'case_id':'test_case','operation':'read_system','execute':False,'parameters':{'tab_id':'123'}}
            with patch('finance_backend.transport_identity',return_value={'backend':'native_messaging'}),patch('edge_read_probe.activate_tab')as activate:
                self.assertFalse(prepare_focus(request,p,Path(d))['foreground_requested']);activate.assert_not_called()

if __name__=='__main__':unittest.main()
