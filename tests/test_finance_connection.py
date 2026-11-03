from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from assistant_api import AssistantService,atomic_json
from finance_connection import account_case,bound

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.service=AssistantService(self.root,[self.root]);self.who='TEST01';self.case=account_case(self.who)
        self.inventory={'tab_id_namespace':'edge-native-messaging:'+'a'*32+':'+'b'*32,
            'tabs':[{'tab_id':'12','window_id':'8','path':'https://cwzh.ustc.edu.cn/YBX/main.jsp'},
                    {'tab_id':'13','window_id':'8','path':'https://id.ustc.edu.cn/'}]}
        self.patcher=patch('registered_bridge.inventory',return_value=self.inventory);self.patcher.start();self.addCleanup(self.patcher.stop)

    def proof(self,*,correct=True):
        self.service.new_case(self.case)
        job='j_'+'1'*24;folder=self.service.jobs/self.case/job;folder.mkdir(parents=True)
        atomic_json(folder/'request.json',{'case_id':self.case,'operation':'access','execute':False,'parameters':{
            'action':'check-backend','tab_id':'12','expected_userid':self.who},'runtime_context':{
            'REIMBURSEMENT_EDGE_WINDOW_ID':'8','REIMBURSEMENT_EDGE_TAB_NAMESPACE':self.inventory['tab_id_namespace']}})
        atomic_json(folder/'output.json',{'schema':'ustc-finance-backend-health-v1','state':'native_backend_ready',
            'identity_verified':correct,'system_write':False,'observed_at_utc':'2026-11-03T00:00:00Z'})
        return job,folder

    def accept(self,job,folder):
        with patch.object(self.service,'result',return_value={'status':'completed','exit_code':0,'artifacts':[{'name':'output.json','artifact_id':'a0'}]}),patch.object(self.service,'artifact',return_value=folder/'output.json'):
            return self.service.finance_connection('accept',expected_userid=self.who,source_job_id=job)

    def test_discover_only_finance_page_and_verify_freezes_scope(self):
        result=self.service.finance_connection('discover');self.assertEqual(len(result['candidates']),1)
        captured=[]
        def enqueue(scoped,case,op,parameters,request_id,execute):
            captured.append((scoped.runtime_context,case,op,parameters,request_id,execute));return {'ok':True,'job_id':'j_'+'2'*24,'status':'queued'}
        with patch.object(AssistantService,'enqueue',enqueue):
            result=self.service.finance_connection('verify',expected_userid=self.who,tab_id='12',request_id='check-01')
        self.assertEqual(captured[0][0]['REIMBURSEMENT_EDGE_WINDOW_ID'],'8');self.assertFalse(captured[0][-1])
        self.assertEqual(result['next_action']['arguments']['action'],'accept')
        self.assertEqual(len(self.service.cases()['cases']),1)
        with self.assertRaises(ValueError):self.service.finance_connection('verify',expected_userid=self.who,tab_id='13',request_id='check-02')

    def test_verified_connection_binds_original_source_and_current_namespace(self):
        job,folder=self.proof();result=self.accept(job,folder)
        self.assertTrue(result['connected'])
        with patch.object(self.service,'artifact',return_value=folder/'output.json'):
            info,runtime=bound(self.service,result['connection_id']);self.assertEqual(info['expected_userid'],self.who)
            self.assertEqual(runtime['REIMBURSEMENT_EDGE_WINDOW_ID'],'8')
            folder.joinpath('output.json').write_text('{}')
            with self.assertRaises(ValueError):bound(self.service,result['connection_id'])

    def test_failed_identity_cannot_register(self):
        job,folder=self.proof(correct=False)
        with self.assertRaises(ValueError):self.accept(job,folder)
        self.assertFalse((self.root/'.local/finance-connections').exists())

    def test_identity_and_mutation_inputs_rejected(self):
        for who in [None,'', 'name;script','with password']:
            with self.assertRaises(ValueError):account_case(who)
        with self.assertRaises(ValueError):self.service.finance_connection('verify',expected_userid=self.who,tab_id='12')
        with self.assertRaises(ValueError):self.service.finance_connection('approvals',execute=True,request_id='once')

if __name__=='__main__':unittest.main()
