"""合成劳务从原查询→单次添加→独立回读→建单绑定，不访问财务系统。"""
import copy,json,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).resolve().parents[1])]
from assistant_api import AssistantService,atomic_json,read_json
from reimburse import acknowledge
import finance_native_remuneration as n

class NativeRemunerationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.s=AssistantService(self.root,[self.root]);self.s.new_case('test_labor');self.bundle,self.paths=self.s.resolve('test_labor');self.pending=self.paths['state'].parent/'pending_write.json'
        original=self.root/'original.pdf';original.write_bytes(b'only synthetic payroll source');self.original=original
        d={'id':'event_one','summary':'合成现场劳务报销','amount_status':'materials_matched_pending_native_validation','invoice_count':0,'claimed_amount':'10.00','signed_total':'10.00','budget_amount':'10.00','recipients':[{'student_id':'SA00000001','name':'合成人员','amount':'10.00'}],'sources':[{'path':str(original),'sha256':n.sha(original)}],'business_date':'2026-05-31'}
        review=self.root/'review.json';atomic_json(review,{'case_id':'test_labor','documents':[d]});case=read_json(self.paths['case']);case['material_context']={'remuneration_document_id':'event_one','labor_material_review':{'labor_review':n.ref(review)}};atomic_json(self.paths['case'],case)
        self.jobs={};self.rows=[];self.count=0;self.writes=[];self.card={'name':'合成人员','bankno':'TESTBANK','bankname':'合成银行','card_no':'000000****0000','other_information':'synthetic-source'}
    def body(self,c):
        original={'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':'2026-05-31','dd.ff_enddate':'2026-05-31','dd.hastax':'T','dd.ffcycle':'1','dd.untouched':'keep'}
        b={'ok':True,'complete':True,'action':'remuneration-inspect','case_id':'test_labor','serial':c['serial'],'expected_userid':'TEST001','business_transport':'native_http','system_write':False,'save_dispatched':False,'definition_sha256':c['remuneration_contract']['definition_sha256'],'original':original,'rows':copy.deepcopy(self.rows),'bill':None}
        if c.get('student_id'):b.update(person={'unino':'SA00000001','name':'合成人员','state':'学生','allowedFlag':'T','cardInfo':[self.card]},default_bank=self.card)
        return b
    def runtime(self,c,method,output,extra=None):
        self.count+=1;output=Path(output);script=output.with_suffix('.js');script.write_text(n.script(c,method,extra),encoding='utf-8',newline='\n');now=datetime(2028,1,1,tzinfo=timezone.utc)+timedelta(seconds=self.count*10)
        if method=='reimbursementRemunerationSave':
            self.assertTrue(self.pending.exists());self.writes.append(c['action'])
            if c['action']=='add-person':self.rows=[{'t.unino':'SA00000001','t.name':'合成人员','t.AMT':'10.00','t.premark':'合成现场劳务','t.CARDNO':self.card['card_no'],'t.persontype':'学生','t.serial_no':'','t.pno':'private','t.after_income':'10.00'}]
            if c['action']=='create-claim':self.rows[0]['t.serial_no']='123'
            b={'ok':True,'action':'remuneration-save','operation':c['action'],'case_id':c['case_id'],'serial':c['serial'],'expected_userid':'TEST001','new_serial':'123'if c['action']=='create-claim'else None}
        else:b=self.body(c)
        envelope={'phase':'script_completed','tab_id':'7','script':str(script),'source_sha256':n.sha(script),'result':b,'dispatch_intent_at_utc':now.isoformat(),'executed_at_utc':(now+timedelta(seconds=1)).isoformat()};atomic_json(output,envelope);return envelope
    def typed(self,case,job,artifact,operation,actions):
        folder=self.jobs[job];r=read_json(folder/'request.json');self.assertEqual(operation,'access');self.assertIn(r['parameters']['action'],actions);return folder/'output.json',read_json(folder/'output.json'),r
    def job(self,name,action,**p):
        folder=self.root/name;folder.mkdir();self.jobs[name]=folder;r={'case_id':'test_labor','operation':'access','parameters':{'action':'remuneration-'+action,'expected_userid':'TEST001','tab_id':'7',**p},'request_id':name,'execute':action in {'add-person','create-claim','save'}};atomic_json(folder/'request.json',r)
        with patch.object(self.s,'_typed_case_artifact',side_effect=self.typed),patch.object(self.s,'job_folder',side_effect=lambda case,job:self.jobs[job]):n.command(self.s,r,folder)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(folder/'native-remuneration-input.json',folder/'output.json')
        return folder
    def added(self):
        self.job('lookup','lookup-person',student_id='SA00000001');self.job('add','add-person',student_id='SA00000001',job_id='lookup',artifact_id='output');pending=read_json(self.pending);self.job('added','readback',job_id='add',artifact_id='output');return pending,read_json(self.jobs['added']/'output.json')
    def test_add_persistent_read_then_create_bind_and_validate_bound_case(self):
        pending,read=self.added();self.assertEqual(n.verify_ack(pending,read,'saved')['people'],1);acknowledge(self.paths,self.jobs['added']/'output.json','合成人员银行税务期间完整','saved');self.assertFalse(self.pending.exists())
        self.job('before_create','inspect');self.job('create','create-claim',job_id='before_create',artifact_id='output');self.job('created','readback',job_id='create',artifact_id='output')
        with self.assertRaisesRegex(ValueError,'bind-serial'):n.verify_ack(read_json(self.pending),read_json(self.jobs['created']/'output.json'),'saved')
        self.assertEqual(n.bind_created(self.paths,self.jobs['created']/'output.json','123')['serial'],'123');self.assertFalse(self.pending.exists());self.assertEqual(n.verify_bound_case(read_json(self.paths['form']))['people'],1);self.assertEqual(self.writes,['add-person','create-claim'])
    def test_readback_rejects_bank_tax_extra_person_period_old_and_unrelated_change(self):
        pending,observed=self.added()
        for mode in ['bank','amount','person','period','extra','time','untouched']:
            bad=copy.deepcopy(observed)
            if mode=='bank':bad['result']['rows'][0]['t.CARDNO']='999999****9999'
            if mode=='amount':bad['result']['rows'][0]['t.AMT']='11.00'
            if mode=='person':bad['result']['rows'][0]['t.name']='他人'
            if mode=='period':bad['result']['original']['dd.ff_startdate']='2026-06-01'
            if mode=='extra':bad['result']['rows'].append(dict(bad['result']['rows'][0]))
            if mode=='time':bad['dispatch_intent_at_utc']='2020-01-01T00:00:00+00:00'
            if mode=='untouched':bad['result']['original']['dd.untouched']='changed'
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')
        self.assertTrue(self.pending.exists())
    def test_changed_source_stops_before_pending_or_second_write(self):
        self.job('lookup','lookup-person',student_id='SA00000001');self.original.write_bytes(b'changed original')
        with self.assertRaises(ValueError):self.job('bad_add','add-person',student_id='SA00000001',job_id='lookup',artifact_id='output')
        self.assertEqual(self.writes,[]);self.assertFalse(self.pending.exists())

    def test_pending_readback_uses_original_adapter_after_code_update(self):
        self.job('lookup','lookup-person',student_id='SA00000001')
        self.job('add','add-person',student_id='SA00000001',job_id='lookup',artifact_id='output')
        with patch('finance_http.source',return_value='/* unrelated later update */'):
            self.job('read_original','readback',job_id='add',artifact_id='output')
        observed=read_json(self.jobs['read_original']/'output.json')
        self.assertEqual(n.verify_ack(read_json(self.pending),observed,'saved')['people'],1)

    def test_failed_setup_only_acknowledged_not_applied_with_unchanged_readback(self):
        self.job('lookup','lookup-person',student_id='SA00000001')
        self.job('add','add-person',student_id='SA00000001',job_id='lookup',artifact_id='output')
        path=self.jobs['add']/'output.json';failed=read_json(path)
        failed['result'].update(ok=False,state='unknown_original_remuneration_recovery_required',error='REMUNERATION_API_EVENT_CONTEXT_CHANGED',steps=[])
        atomic_json(path,failed);self.rows=[]
        self.job('read_failed','readback',job_id='add',artifact_id='output')
        pending=read_json(self.pending);observed=read_json(self.jobs['read_failed']/'output.json')
        self.assertEqual(n.verify_ack(pending,observed,'not_applied')['outcome'],'not_applied')
        failed['result']['error']='REMUNERATION_API_SAVE_CONTEXT_CHANGED';atomic_json(path,failed)
        self.job('read_setup_failed','readback',job_id='add',artifact_id='output')
        observed=read_json(self.jobs['read_setup_failed']/'output.json')
        self.assertEqual(n.verify_ack(pending,observed,'not_applied')['outcome'],'not_applied')
        failed['result']['steps']=['native_setup'];atomic_json(path,failed)
        self.job('read_after_setup','readback',job_id='add',artifact_id='output')
        observed=read_json(self.jobs['read_after_setup']/'output.json')
        with self.assertRaises(ValueError):n.verify_ack(pending,observed,'not_applied')
        failed['result']['error']='REMUNERATION_API_PERSON_VISIBLE_GROUPS_MISSING';atomic_json(path,failed)
        self.job('read_visible_missing','readback',job_id='add',artifact_id='output')
        observed=read_json(self.jobs['read_visible_missing']/'output.json')
        self.assertEqual(n.verify_ack(pending,observed,'not_applied')['outcome'],'not_applied')
        failed['result']['error']='REMUNERATION_API_SAVE_CONTEXT_CHANGED'
        failed['result']['steps']=[];atomic_json(path,failed)
        self.job('read_setup_reset','readback',job_id='add',artifact_id='output')
        observed=read_json(self.jobs['read_setup_reset']/'output.json')
        with self.assertRaises(ValueError):n.verify_ack(pending,observed,'saved')
        for key in ['original','rows','bill']:
            changed=copy.deepcopy(observed)
            if key=='original':changed['result'][key]['dd.untouched']='changed'
            elif key=='rows':changed['result'][key]=[{'t.unino':'SA00000001'}]
            else:changed['result'][key]={'serial':'123'}
            with self.subTest(key=key),self.assertRaises(ValueError):n.verify_ack(pending,changed,'not_applied')
        failed['result']['error']='OTHER_UNKNOWN_WRITE';atomic_json(path,failed)
        with self.assertRaises(ValueError):n.verify_ack(pending,observed,'not_applied')


    def test_completed_person_configuration_failure_before_add_can_be_closed_unchanged(self):
        self.job('lookup','lookup-person',student_id='SA00000001');self.job('add','add-person',student_id='SA00000001',job_id='lookup',artifact_id='output')
        path=self.jobs['add']/'output.json';failed=read_json(path);failed['result'].update(ok=False,state='rejected_before_save',save_dispatched=False,system_write=False,steps=[],error='REMUNERATION_API_PERSON_RULES_CHANGED');atomic_json(path,failed);self.rows=[]
        self.job('read_failed','readback',job_id='add',artifact_id='output');observed=read_json(self.jobs['read_failed']/'output.json');pending=read_json(self.pending)
        self.assertEqual(n.verify_ack(pending,observed,'not_applied')['outcome'],'not_applied')
        failed['result']['save_dispatched']=True;atomic_json(path,failed)
        with self.assertRaises(ValueError):n.verify_ack(pending,observed,'not_applied')


    def test_native_mask_width_change_needs_both_grid_columns_and_exact_branch(self):
        card={'card_no':'123456****7890','branchcode':'123456789012/Synthetic Branch'}
        row={'t.CARDNO':'1234****7890','t.cardno_show':'1234****7890','t.hq_branchname':'Synthetic Branch'}
        self.assertTrue(n.person_bank_matches(row,card))
        for key,value in [('t.CARDNO','9234****7890'),('t.CARDNO','1234****7891'),('t.cardno_show','1234****7891'),('t.hq_branchname','Other Branch')]:
            self.assertFalse(n.person_bank_matches({**row,key:value},card))
        self.assertFalse(n.person_bank_matches(row,{**card,'branchcode':''}))


    def test_legacy_create_confirmation_only_can_close_without_recreating(self):
        pending,observed=self.added();acknowledge(self.paths,self.jobs['added']/'output.json','verified','saved')
        self.job('before_create','inspect');before=copy.deepcopy(self.rows);self.job('create','create-claim',job_id='before_create',artifact_id='output')
        path=self.jobs['create']/'output.json';failed=read_json(path);failed['result'].update(ok=False,state='unknown_original_remuneration_recovery_required',steps=[],error='NATIVE_PROTOCOL_JUDGE',new_serial=None);atomic_json(path,failed);self.rows=before
        c=read_json(self.jobs['create']/'native-remuneration-input.json')
        with patch.object(n,'LEGACY_CREATE_CHECK_ADAPTER',c['adapter']['sha256']):
            self.job('read_check','readback',job_id='create',artifact_id='output');pending=read_json(self.pending);read=read_json(self.jobs['read_check']/'output.json')
            self.assertTrue(n.verify_ack(pending,read,'not_applied')['original_create_not_dispatched'])
            with self.assertRaises(ValueError):n.verify_ack(pending,read,'saved')
            changed=copy.deepcopy(read);changed['result']['rows'][0]['t.serial_no']='456'
            with self.assertRaises(ValueError):n.verify_ack(pending,changed,'not_applied')
        with self.assertRaises(ValueError):n.verify_ack(pending,read,'not_applied')

    def test_completed_create_response_recovers_serial_without_recreating(self):
        pending,observed=self.added();acknowledge(self.paths,self.jobs['added']/'output.json','verified','saved')
        self.job('before_create','inspect');self.job('create','create-claim',job_id='before_create',artifact_id='output')
        path=self.jobs['create']/'output.json';failed=read_json(path)
        failed['result'].update(ok=False,state='unknown_original_remuneration_recovery_required',steps=['native_people_total_confirmed','前往报销'],error='REMUNERATION_API_SAVE_CONTEXT_CHANGED',new_serial=None,creation_procedure_dispatched=True,save_dispatched=True,system_write=True,event_diagnostic={'button':'前往报销cj','keys':['0-g0Serialno','0-need_turor','0-zip_seq'],'response':'passlocal(0-g0Serialno:123;0-need_turor:N;0-zip_seq:)'})
        atomic_json(path,failed);c=read_json(self.jobs['create']/'native-remuneration-input.json')
        with patch.object(n,'LEGACY_CREATED_RESPONSE_ADAPTER',c['adapter']['sha256']):
            self.job('read_created','readback',job_id='create',artifact_id='output')
            observed=read_json(self.jobs['read_created']/'output.json');pending=read_json(self.pending)
            with self.assertRaises(ValueError):n.verify_ack(pending,observed,'not_applied')
            for field,value in [('creation_procedure_dispatched',False),('error','timeout')]:
                bad=copy.deepcopy(failed['result']);bad[field]=value;self.assertIsNone(n.created_response_serial(c,bad))
            bad=copy.deepcopy(failed['result']);bad['event_diagnostic']['response']+='extra';self.assertIsNone(n.created_response_serial(c,bad))
            self.assertEqual(n.bind_created(self.paths,self.jobs['read_created']/'output.json','123')['serial'],'123')
            self.assertFalse(self.pending.exists());self.assertEqual(self.writes,['add-person','create-claim'])

if __name__=='__main__':unittest.main()
