import json,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
from finance_native_create import ref
import finance_returned as n

class ReturnedLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.s=AssistantService(self.root,[self.root]);self.s.new_case('returned_fixture');_,self.paths=self.s.resolve('returned_fixture');self.count=0;self.writes=0;self.fail_read=False
        for key,p in self.paths.items():
            v=read_json(p);v['serial']='123'
            if key=='form':v.update(amount='3700.00',system_mode='daily',expected_userid='TEST001',review={'workflow_started':True})
            if key=='invoices':v.update(selected_invoices=[{'guid':'a'*32}],original_files=[{'path':'preserved.pdf'}])
            atomic_json(p,v)
        a=self.root/'authority.json';atomic_json(a,{'case_id':'returned_fixture','serial':'123','observed_amount':'3700.00','edit_original':True,'user_text':'原单改为1500','source':'synthetic'})
        c=read_json(self.paths['case']);c['material_context']['returned_correction_authority']=ref(a);atomic_json(self.paths['case'],c)
    def runtime(self,c,method,path,extra=None,write=False):
        self.count+=1;path=Path(path);script=path.with_suffix('.js');script.write_text(n.script(c,method,extra));when=datetime(2030,1,1,tzinfo=timezone.utc)+timedelta(seconds=self.count*10)
        common={'ok':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid']}
        bill={'t.status':'-99' if method.endswith('Readback') else '-3','t.lc_finish':'F','t.total_amt':'3,700.00'};rows=[{'t.tk_guid':'a'*32,'t.serial_no':'123','t.tk_total_amt':'3700.00'}]
        if method.endswith('Prepare'):r={**common,'complete':True,'action':'returned-prepare','system_write':False,'save_dispatched':False,'definition_sha256':c['returned_contract']['definition_sha256'],'bill':bill,'rows':rows}
        elif method.endswith('Open'):
            self.assertTrue((self.paths['state'].parent/'pending_write.json').exists());self.writes+=1;r={**common,'token':c['token'],'save_dispatched':True}
        else:
            r={**common,'complete':True,'action':'returned-readback','system_write':False,'original_serial_preserved':True,'original_invoice_set_preserved':True,'bill':bill,'rows':rows}
            if self.fail_read:r['bill']['t.total_amt']='1500.00'
        e={'phase':'script_completed','tab_id':c['tab_id'],'script':str(script),'source_sha256':ref(script)['sha256'],'result':r,'executed_at_utc':(when+timedelta(seconds=1)).isoformat(),'dispatch_intent_at_utc':when.isoformat()};atomic_json(path,e);return e
    def prepared(self):
        p=self.root/'prepare';p.mkdir();request={'case_id':'returned_fixture','operation':'correction','parameters':{'action':'returned-prepare','tab_id':'7'},'request_id':'prepare','execute':False};atomic_json(p/'request.json',request)
        n.command(self.s,request,p)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(p/'returned-input.json',p/'output.json')
        f=self.root/'open';f.mkdir();request={**request,'parameters':{'action':'returned-open','tab_id':'7','job_id':'prepare'},'request_id':'open','execute':True}
        with patch.object(self.s,'job_folder',return_value=p),patch.object(self.s,'result',return_value={'status':'completed'}):n.command(self.s,request,f)
        return f
    def test_independent_readback_preserves_original_and_only_clears_workflow(self):
        f=self.prepared()
        with patch.object(n,'browser',side_effect=self.runtime):n.run(f/'returned-input.json',f/'output.json')
        value=read_json(self.paths['form']);self.assertEqual(value['serial'],'123');self.assertEqual(value['amount'],'3700.00');self.assertFalse(value['review']['workflow_started']);self.assertEqual(self.writes,1)
        self.assertEqual(len(read_json(self.paths['invoices'])['selected_invoices']),1);self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())
        with patch.object(n,'browser',side_effect=self.runtime),self.assertRaises(ValueError):n.run(f/'returned-input.json',f/'again.json')
        self.assertEqual(self.writes,1)
    def test_bad_readback_keeps_pending_and_submitted_local_state(self):
        f=self.prepared();self.fail_read=True
        with patch.object(n,'browser',side_effect=self.runtime),self.assertRaises(ValueError):n.run(f/'returned-input.json',f/'output.json')
        self.assertTrue((self.paths['state'].parent/'pending_write.json').exists());self.assertTrue(read_json(self.paths['form'])['review']['workflow_started']);self.assertEqual(self.writes,1)
    def test_missing_authority_rejected(self):
        c=read_json(self.paths['case']);c['material_context'].pop('returned_correction_authority');atomic_json(self.paths['case'],c)
        with self.assertRaisesRegex(ValueError,'纠正安排'):n.command(self.s,{'case_id':'returned_fixture','parameters':{'action':'returned-prepare','tab_id':'7'},'request_id':'no','execute':False},self.root)
    def test_only_exact_received_confirmation_is_eligible(self):
        c={'case_id':'x','serial':'123','expected_userid':'TEST001','token':'t'}
        saved={**c,'ok':False,'save_dispatched':True,'native_response':n.CONFIRMATION,'requests':[{'status':'received'}]}
        self.assertEqual(n.explicit_confirmation(c,saved)['original_token'],'t')
        for patch in [{'native_response':'judge:其他确认'},{'requests':[]},{'ok':True},{'token':'another'}]:
            with self.assertRaises(ValueError):n.explicit_confirmation(c,{**saved,**patch})

if __name__=='__main__':unittest.main()
