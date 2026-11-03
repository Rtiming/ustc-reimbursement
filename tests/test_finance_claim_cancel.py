import json,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
from finance_native_create import ref
import finance_claim_cancel as n

class ClaimCancelLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.s=AssistantService(self.root,[self.root]);self.s.new_case('cancel_fixture');self.bundle,self.paths=self.s.resolve('cancel_fixture');self.count=0;self.writes=0
        for key,p in self.paths.items():
            v=read_json(p);v['serial']='123'
            if key=='form':v.update(amount='10.00',expected_userid='TEST001')
            if key=='invoices':v.update(selected_invoices=[],original_files=[{'path':'preserved.pdf','sha256':'a'*64}])
            atomic_json(p,v)
        p=self.root/'authority.json';atomic_json(p,{'case_id':'cancel_fixture','serial':'123','amount':'10.00','cancel_draft':True,'reopen_for_rebuild':True,'user_text':'纠正本案误拆草稿','source':'synthetic decision'})
        c=read_json(self.paths['case']);c['material_context']['draft_cancellation_authority']=ref(p);atomic_json(self.paths['case'],c)
    def runtime(self,c,method,path,extra=None,write=False):
        self.count+=1;path=Path(path);s=path.with_suffix('.js');s.write_text(n.script(c,method,extra));when=datetime(2030,1,1,tzinfo=timezone.utc)+timedelta(seconds=self.count*10)
        if method=='reimbursementClaimCancelPrepare':r={'ok':True,'complete':True,'action':'claim-cancel-prepare','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,'save_dispatched':False,'definition_sha256':c['cancel_contract']['definition_sha256']}
        elif method=='reimbursementClaimCancel':
            self.assertTrue((self.paths['state'].parent/'pending_write.json').exists());self.writes+=1;r={'ok':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'token':c['token'],'save_dispatched':True}
        else:r={'ok':True,'complete':True,'claim_absent':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,'released':[]}
        e={'phase':'script_completed','tab_id':c['tab_id'],'script':str(s),'source_sha256':ref(s)['sha256'],'result':r,'executed_at_utc':(when+timedelta(seconds=1)).isoformat(),'dispatch_intent_at_utc':when.isoformat()};atomic_json(path,e);return e
    def test_cancel_reopens_only_after_independent_receipt_and_keeps_originals(self):
        prepare=self.root/'prepare';prepare.mkdir();request={'case_id':'cancel_fixture','operation':'invoice','parameters':{'action':'draft-cancel-prepare','tab_id':'7'},'request_id':'prepare','execute':False};atomic_json(prepare/'request.json',request)
        n.command(self.s,request,prepare)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(prepare/'claim-cancel-input.json',prepare/'output.json')
        folder=self.root/'cancel';folder.mkdir();request={**request,'parameters':{'action':'draft-cancel','tab_id':'7','job_id':'prepare'},'request_id':'cancel','execute':True}
        with patch.object(self.s,'job_folder',return_value=prepare),patch.object(self.s,'result',return_value={'status':'completed'}):n.command(self.s,request,folder)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(folder/'claim-cancel-input.json',folder/'output.json')
        self.assertEqual(self.writes,1);self.assertIsNone(read_json(self.paths['form'])['serial']);self.assertEqual(read_json(self.paths['invoices'])['original_files'][0]['path'],'preserved.pdf')
        self.assertTrue((folder/'cancelled-case-before-reopen.json').exists());self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())
        with patch.object(n,'browser',side_effect=self.runtime),self.assertRaises(ValueError):n.run(folder/'claim-cancel-input.json',folder/'other.json')
        self.assertEqual(self.writes,1)
    def test_missing_or_changed_authority_stops_before_browser(self):
        c=read_json(self.paths['case']);c['material_context'].pop('draft_cancellation_authority');atomic_json(self.paths['case'],c);p=self.root/'job';p.mkdir()
        with self.assertRaisesRegex(ValueError,'明确纠正安排'):n.command(self.s,{'case_id':'cancel_fixture','operation':'invoice','parameters':{'action':'draft-cancel-prepare','tab_id':'7'},'request_id':'no-auth','execute':False},p)

if __name__=='__main__':unittest.main()
