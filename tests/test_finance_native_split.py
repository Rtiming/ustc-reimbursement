"""专项拆分一次保存后独立持久回读，保留原有字段和个人上限。"""
import copy,json,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).resolve().parent/'fixtures'),str(Path(__file__).resolve().parents[1])]
from assistant_api import AssistantService,atomic_json,read_json
from reimburse import acknowledge
from synthetic_case_helpers import correction_plan
import finance_native_split as n

class NativeSplitTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.plan=correction_plan(self.root);self.case=self.plan['case_id'];self.s=AssistantService(self.root,[self.root]);self.s.new_case(self.case);self.bundle,self.paths=self.s.resolve(self.case);self.jobs={};self.count=0;self.saved=False;self.calls=[];self.pending=self.paths['state'].parent/'pending_write.json';self.group=self.plan['expected_groups'][0]
        keys=['30211!P!WC0000000001!P!A00001','30211!P!WW0000000002!P!A00001'];self.plan['native_keys']={self.group:keys}
        for i,p in enumerate(self.plan['people']):
            file=self.root/('invoice-'+str(i)+'.pdf');file.write_bytes(Path(p['source_file']).read_bytes());p.update(source_file=str(file),source_sha256=n.sha(file),invoice_guid=str(i+1)*32)
        f=read_json(self.paths['form']);f.update(serial=self.plan['serial'],amount=self.plan['amount'],expected_userid='TEST001',funding=self.plan['funding']);atomic_json(self.paths['form'],f)
        self.native={'t.tk_display_abst':'合成交通票','t.invoice_type':'国内机票款','t.amt':'500.00','t.sj_amt':'500.00','t.split_amt':'0.00','t.sj_bf':'30211-WW0000000002-A00001-500.00','t.serial_no':f['serial'],'t.tk_guid':self.plan['people'][0]['invoice_guid'],'t.inner_ord':'1','t.select_flag':''}
    def body(self,c,rows=False):
        entry={'group':self.group,'group_key':self.group+'!P!1','parameters':self.group+'!P!1&30211&WW0000000002&A00001'}
        b={'ok':True,'complete':True,'action':'split-inspect','case_id':self.case,'serial':self.plan['serial'],'expected_userid':'TEST001','business_transport':'native_http','system_write':False,'save_dispatched':False,'definition_sha256':c['split_contract']['definition_sha256'],
           'original':{'bill':{'t.serial_no':self.plan['serial'],'t.total_amt':'1000.00'},'basic':[],'invoices':[{'t.tk_guid':p['invoice_guid']}for p in self.plan['people']],'categories':[entry],'entry':entry}}
        if rows:
            row=copy.deepcopy(self.native)
            if self.saved:row['t.sj_bf']='30211-WC0000000001-A00001-450.00;30211-WW0000000002-A00001-50.00'
            b.update(rows=[row],rows_complete=True)
        return b
    def runtime(self,c,method,output,extra=None):
        self.count+=1;self.calls.append(method);output=Path(output);script=output.with_suffix('.js');script.write_text(n.script(c,method,extra),encoding='utf-8',newline='\n');now=datetime(2028,1,1,tzinfo=timezone.utc)+timedelta(seconds=self.count*10)
        if method=='reimbursementSplitApply':
            self.assertTrue(self.pending.exists());self.saved=True;body={'ok':True,'action':'split-apply','case_id':self.case,'serial':c['serial'],'expected_userid':'TEST001','initialization_verified':True,'original_rows':[self.native]}
        else:body=self.body(c,method=='reimbursementSplitInspect')
        envelope={'phase':'script_completed','tab_id':'7','script':str(script),'source_sha256':n.sha(script),'result':body,'dispatch_intent_at_utc':now.isoformat(),'executed_at_utc':(now+timedelta(seconds=1)).isoformat()};atomic_json(output,envelope);return envelope
    def typed(self,case,job,artifact,op,actions):
        folder=self.jobs[job];r=read_json(folder/'request.json');self.assertEqual(op,'correction');self.assertIn(r['parameters']['action'],actions);return folder/'output.json',read_json(folder/'output.json'),r
    def job(self,name,action,**p):
        folder=self.root/name;folder.mkdir();self.jobs[name]=folder;r={'case_id':self.case,'operation':'correction','parameters':{'action':action,'plan':self.plan,'group':self.group,'tab_id':'7',**p},'request_id':name,'execute':action=='apply'};atomic_json(folder/'request.json',r)
        with patch.object(self.s,'_typed_case_artifact',side_effect=self.typed),patch.object(self.s,'job_folder',side_effect=lambda case,job:self.jobs[job]):n.command(self.s,r,folder)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(folder/'native-split-input.json',folder/'output.json')
    def test_one_apply_then_full_read_ack(self):
        self.job('apply','apply');pending=read_json(self.pending);self.job('verify','verify',job_id='apply',artifact_id='output');r=read_json(self.jobs['verify']/'output.json');self.assertTrue(n.verify_ack(pending,r,'saved')['independent_http_readback'])
        acknowledge(self.paths,self.jobs['verify']/'output.json','合成逐票资格与完整分担核对','saved');self.assertFalse(self.pending.exists());self.assertEqual(self.calls.count('reimbursementSplitApply'),1)
    def test_wrong_funding_face_old_read_and_third_binding_cannot_release(self):
        self.job('apply','apply');pending=read_json(self.pending);self.job('verify','verify',job_id='apply',artifact_id='output');observed=read_json(self.jobs['verify']/'output.json')
        for mode in ['third','face','ineligible','time','other_field']:
            bad=copy.deepcopy(observed)
            if mode=='third':bad['result']['rows'][0]['t.sj_bf']+=';30211-WX0000000003-A00001-1.00'
            if mode=='face':bad['result']['rows'][0]['t.amt']='501.00'
            if mode=='ineligible':bad['result']['rows'][0]['t.invoice_type']='行李费'
            if mode=='time':bad['dispatch_intent_at_utc']='2020-01-01T00:00:00+00:00'
            if mode=='other_field':bad['result']['original']['bill']['t.total_amt']='900.00'
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')
        self.assertTrue(self.pending.exists())

if __name__=='__main__':unittest.main()
