"""原生流程分节点留证；合成Next、承诺及本人动作边界。"""
import copy,json,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).resolve().parents[1])]
from assistant_api import AssistantService,atomic_json,read_json
from submission import view_plan
from reimburse import acknowledge
import finance_native_workflow as n

class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.s=AssistantService(self.root,[self.root]);self.s.new_case('test_flow');self.bundle,self.paths=self.s.resolve('test_flow');self.pending=self.paths['state'].parent/'pending_write.json';self.jobs={};self.count=0;self.started=False;self.writes=[]
        form=read_json(self.paths['form']);form.update(serial='123',amount='10.00',contract={'exists':False,'source':'synthetic case fact'},expected_invoice_count=1,expected_userid='TEST001');atomic_json(self.paths['form'],form)
        for key in ['case','invoices','state']:
            value=read_json(self.paths[key]);value['serial']='123'
            if key=='invoices':value.update(expected_count=1,expected_amount='10.00',selected_invoices=[{'guid':'A'*32,'amount':'10.00'}])
            atomic_json(self.paths[key],value)
        self.plan=view_plan(form,'TEST001','synthetic authorized lifecycle');self.plan['promise_sha256']='fixed-promise'
    def body(self,c):
        bill={'t.serial_no':'123','t.total_amt':'10.00','t.REQ_INFO':'TEST001-合成','t.status':'1'if self.started else'-99','t.lc_finish':'F','t.wf_name':'YBX_LC'if self.started else'','t.uni_key':'U123'if self.started else'','t.node_id':'508'if self.started else''}
        return{'ok':True,'complete':True,'action':'workflow-inspect','case_id':'test_flow','serial':'123','expected_userid':'TEST001','business_transport':'native_http','system_write':False,'save_dispatched':False,'definition_sha256':c['workflow_contract']['definition_sha256'],
               'original':{'bill':bill,'basic':[{'fixed':'same'}],'invoices':[{'t.tk_guid':'A'*32}],'salary':None,'options':{'print':'N','proofs':[]},'payments':[],'budget':[],'pre_review':[]},'native_error_present':False,'environment':{'fixed':'same'},'promise_sha256':'fixed-promise','final_applicant_approval_executed':False,'sms_sent':False}
    def runtime(self,c,method,output,extra=None):
        self.count+=1;output=Path(output);script=output.with_suffix('.js');script.write_text(n.script(c,method,extra),encoding='utf-8',newline='\n');now=datetime(2028,1,1,tzinfo=timezone.utc)+timedelta(seconds=self.count*10)
        if method=='reimbursementWorkflowSave':
            self.assertTrue(self.pending.exists());self.writes.append(c['action']);self.started=c['action']!='next'
            body={'ok':True,'action':'workflow-save','operation':c['action'],'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'state':'submitted_independent_readback_required'if self.started else'next_staged_independent_readback_required','workflow_started_by_action':self.started,'next_context':{'61-LC_ABST':'原本案摘要'},'messages':{},'final_applicant_approval_executed':False,'sms_sent':False}
        else:body=self.body(c)
        envelope={'phase':'script_completed','tab_id':'7','script':str(script),'source_sha256':n.sha(script),'result':body,'dispatch_intent_at_utc':now.isoformat(),'executed_at_utc':(now+timedelta(seconds=1)).isoformat()};atomic_json(output,envelope);return envelope
    def typed(self,case,job,artifact,op,actions):
        folder=self.jobs[job];r=read_json(folder/'request.json');self.assertEqual(op,'submission');self.assertIn(r['parameters']['action'],actions);return folder/'output.json',read_json(folder/'output.json'),r
    def job(self,name,action,**p):
        folder=self.root/name;folder.mkdir();self.jobs[name]=folder;r={'case_id':'test_flow','operation':'submission','parameters':{'action':action,'plan':self.plan,'tab_id':'7',**p},'request_id':name,'execute':action in n.WRITES};atomic_json(folder/'request.json',r)
        with patch.object(self.s,'_typed_case_artifact',side_effect=self.typed),patch.object(self.s,'job_folder',side_effect=lambda case,job:self.jobs[job]):n.command(self.s,r,folder)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(folder/'native-workflow-input.json',folder/'output.json')
    def next_read(self):
        self.job('next','next');pending=read_json(self.pending);self.job('next_read','workflow-readback',job_id='next',artifact_id='output');return pending,read_json(self.jobs['next_read']/'output.json')
    def test_next_only_staged_then_promise_requires_original_next_and_real_node(self):
        pending,observed=self.next_read();self.assertFalse(n.verify_ack(pending,observed,'staged')['workflow_started'])
        with self.assertRaises(ValueError):n.verify_ack(pending,observed,'saved')
        acknowledge(self.paths,self.jobs['next_read']/'output.json','合成Next后完整字段未变','staged');self.assertFalse(self.pending.exists())
        self.job('promise','promise',job_id='next',artifact_id='output');pending=read_json(self.pending);self.job('submitted','workflow-readback',job_id='promise',artifact_id='output');r=read_json(self.jobs['submitted']/'output.json');self.assertTrue(n.verify_ack(pending,r,'saved')['workflow_started'])
        acknowledge(self.paths,self.jobs['submitted']/'output.json','真实合成流程实例、节点、未改金额完整匹配','saved');self.assertFalse(self.pending.exists());self.assertEqual(self.writes,['next','promise'])
        current=self.s.overview('test_flow');self.assertTrue(current['workflow_started']);self.assertEqual(current['known_node'],'508');self.assertIsNone(current['recommended_stage'])
        with self.assertRaisesRegex(Exception,'已有流程'):self.s.update_case('test_flow','form',{'amount':'10.00'},current['revision'])
        with self.assertRaisesRegex(Exception,'已有流程'):self.s.set_explanation('test_flow','Test',current['revision'],False)
    def test_stale_read_modified_amount_and_wrong_instance_cannot_clear_pending(self):
        pending,observed=self.next_read()
        for mode in ['time','amount','source','sms']:
            bad=copy.deepcopy(observed)
            if mode=='time':bad['dispatch_intent_at_utc']='2020-01-01T00:00:00+00:00'
            if mode=='amount':bad['result']['original']['bill']['t.total_amt']='11.00'
            if mode=='source':bad['result']['original']['invoices']=[]
            if mode=='sms':bad['result']['sms_sent']=True
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,'staged')
        self.assertTrue(self.pending.exists())
    def test_promise_without_typed_original_next_and_final_approval_are_not_executable(self):
        with self.assertRaises(Exception):self.job('bad','promise')
        with self.assertRaises(ValueError):self.job('final','applicant-agree')
        self.assertEqual(self.writes,[]);self.assertFalse(self.pending.exists())
    def test_business_correction_blocks_submission_but_keeps_readback(self):
        case=read_json(self.paths['case']);case['decisions']=[{'status':'requires_business_correction','kind':'user_grouping_correction'}];atomic_json(self.paths['case'],case)
        from operator_support import check
        self.assertTrue(any(e['field']=='case.decisions'for e in check(self.bundle)['errors']))
        self.assertFalse(any(e['field']=='case.decisions'for e in check(self.bundle,validation_stage='basic')['errors']))
        with self.assertRaisesRegex(ValueError,'业务范围仍待纠正'):self.job('blocked','next')
        self.assertEqual(self.writes,[]);self.assertFalse(self.pending.exists())
        self.job('inspect','workflow-inspect');self.assertEqual(self.writes,[])
        case['decisions'][0]['status']='resolved';atomic_json(self.paths['case'],case)
        self.job('fixed','next');self.assertEqual(self.writes,['next'])

    def test_pre_dispatch_options_rejection_needs_unchanged_independent_readback(self):
        runtime=self.runtime
        def rejected_runtime(c,method,output,extra=None):
            result=runtime(c,method,output,extra)
            if method=='reimbursementWorkflowSave':
                result['result'].update(ok=False,system_write=False,save_dispatched=False,
                    state='rejected_before_dispatch',error='WORKFLOW_API_OPTIONS_NOT_SAVED',steps=[],requests=[])
                atomic_json(output,result)
            return result
        self.runtime=rejected_runtime
        pending,observed=self.next_read()
        self.assertEqual(n.verify_ack(pending,observed,'not_applied')['outcome'],'not_applied')
        for mode in ['saved','staged','changed_amount','dispatched','unknown','requests']:
            bad=copy.deepcopy(observed);saved_path=self.jobs['next']/'output.json';original=read_json(saved_path);saved=copy.deepcopy(original)
            outcome=mode if mode in ['saved','staged'] else 'not_applied'
            if mode=='changed_amount':bad['result']['original']['bill']['t.total_amt']='11.00'
            if mode=='dispatched':saved['result']['save_dispatched']=True
            if mode=='unknown':saved['result']['state']='unknown_original_workflow_recovery_required'
            if mode=='requests':saved['result']['requests']=[{'endpoint':'write','status':'received'}]
            atomic_json(saved_path,saved)
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,outcome)
            atomic_json(saved_path,original)
        acknowledge(self.paths,self.jobs['next_read']/'output.json','完整原单未变，原生选项检查派发前拒绝','not_applied')
        self.assertFalse(self.pending.exists())

    def test_completed_next_parse_failure_is_not_submission_and_needs_full_readback(self):
        runtime=self.runtime
        def failed_runtime(c,method,output,extra=None):
            result=runtime(c,method,output,extra)
            if method=='reimbursementWorkflowSave':
                result['result'].update(ok=False,system_write=True,save_dispatched=True,
                    state='unknown_original_workflow_recovery_required',error='NATIVE_PROTOCOL_LOCAL_SYNTAX',steps=[],requests=[])
                atomic_json(output,result)
            return result
        self.runtime=failed_runtime
        pending,observed=self.next_read()
        result=n.verify_ack(pending,observed,'not_applied')
        self.assertTrue(result['completed_validation_response_unparsed']);self.assertFalse(result['workflow_started'])
        for mode in ['changed_payment','changed_state','transport_unknown','promise','saved']:
            bad=copy.deepcopy(observed);saved_path=self.jobs['next']/'output.json';original=read_json(saved_path);saved=copy.deepcopy(original)
            if mode=='changed_payment':bad['result']['original']['payments']=[{'amount':'20'}]
            if mode=='changed_state':bad['result']['original']['bill']['t.status']='1'
            if mode=='transport_unknown':saved['phase']='result_unknown'
            if mode=='promise':saved['result']['operation']='promise'
            atomic_json(saved_path,saved)
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved' if mode=='saved' else 'not_applied')
            atomic_json(saved_path,original)

    def test_readback_retains_original_adapter_after_tool_upgrade(self):
        self.job('next','next');pending=read_json(self.pending)
        with patch('finance_http.source',return_value='CHANGED CURRENT ADAPTER MUST NOT REPLACE ORIGINAL'):
            self.job('read_old','workflow-readback',job_id='next',artifact_id='output')
        observed=read_json(self.jobs['read_old']/'output.json')
        self.assertEqual(n.verify_ack(pending,observed,'staged')['outcome'],'staged')

    def test_captured_concatenated_next_response_recovers_without_replay(self):
        runtime=self.runtime
        def captured_runtime(c,method,output,extra=None):
            result=runtime(c,method,output,extra)
            if method=='reimbursementWorkflowSave' and c['action']=='next':
                result['result'].update(ok=False,system_write=True,save_dispatched=True,
                    state='unknown_original_workflow_recovery_required',error='NATIVE_PROTOCOL_LOCAL_SYNTAX',steps=[],requests=[],
                    native_response='passlocal(61-jhinfo:;0-jihe_type:;0-jihe_tip_msg:)passlocal(61-Lc_IsSignCa:F)passlocal(61-tipinfo:)')
                atomic_json(output,result)
            return result
        self.runtime=captured_runtime
        pending,observed=self.next_read();self.assertEqual(n.verify_ack(pending,observed,'staged')['outcome'],'staged')
        acknowledge(self.paths,self.jobs['next_read']/'output.json','原响应纯数据解析与独立回读一致','staged')
        self.job('promise','promise',job_id='next',artifact_id='output')
        self.assertEqual(self.writes,['next','promise'])

    def test_missing_saved_options_stops_before_registering_write(self):
        body=self.body
        def missing(c):
            result=body(c);result['original']['options']['print']='Y';return result
        self.body=missing
        with self.assertRaisesRegex(ValueError,'打印选项'):self.job('no_options','next')
        self.assertFalse(self.pending.exists());self.assertEqual(self.writes,[])

    def test_captured_department_callback_continues_original_promise(self):
        pending,observed=self.next_read();acknowledge(self.paths,self.jobs['next_read']/'output.json','next verified','staged')
        runtime=self.runtime
        def callback(c,method,output,extra=None):
            result=runtime(c,method,output,extra)
            if method=='reimbursementWorkflowSave' and c['action']=='promise':
                self.started=False
                result['result'].update(ok=False,system_write=True,save_dispatched=True,workflow_started_by_action=False,
                    state='unknown_original_workflow_recovery_required',error='NATIVE_PROTOCOL_RESPONSE_UNKNOWN',steps=[],requests=[],
                    native_response='afterjs:local(0-buss_var_list:TEST_DEPARTMENT_ROLE)')
                atomic_json(output,result)
            return result
        self.runtime=callback
        self.job('promise','promise',job_id='next',artifact_id='output');pending=read_json(self.pending)
        self.job('department_read','workflow-readback',job_id='promise',artifact_id='output');observed=read_json(self.jobs['department_read']/'output.json')
        self.assertFalse(n.verify_ack(pending,observed,'staged')['workflow_started'])
        acknowledge(self.paths,self.jobs['department_read']/'output.json','原响应要求单位选择；未启动流程','staged')
        self.job('department','department',job_id='promise',artifact_id='output')
        c=read_json(self.jobs['department']/'native-workflow-input.json');self.assertEqual(c['next_context']['0-BUSS_VAR_LIST'],'TEST_DEPARTMENT_ROLE')
        self.assertEqual(self.writes,['next','promise','department'])

    def native_instance_readback(self,captured=False):
        body=self.body;runtime=self.runtime
        def native_body(c):
            r=body(c);r['original']['bill']['t.basetable_key']='123' if self.started else ''
            r['original']['bill']['t.req_date']='2028-01-01' if self.started else '2027-12-31'
            if self.started:r['original']['bill']['t.uni_key']='4567'
            r['original']['invoices'][0]['t.status_name']='申请人' if self.started else '草稿'
            return r
        def native_runtime(c,method,output,extra=None):
            r=runtime(c,method,output,extra)
            if method=='reimbursementWorkflowSave' and c['action']=='promise':
                r['result']['workflow_instance']={'node_id':'508','uni_key':'4567','base_key':'123','wf_name':'YBX_LC'};atomic_json(output,r)
                if captured:
                    r['result'].update(ok=False,workflow_started_by_action=False,save_dispatched=True,system_write=True,
                        state='unknown_original_workflow_recovery_required',error='WORKFLOW_API_CONTEXT_CHANGED:109-wf_inst.NODE_ID',
                        native_response='passlocal(61-node_id:508;61-uni_key:4567;61-wf_name:YBX_LC;61-spinfo_serialno:123;109-wf_inst.NODE_ID:508;109-wf_inst.UNI_KEY:4567;109-wf_inst.BASETABLE_KEY:123)')
                    atomic_json(output,r)
            return r
        self.body=native_body;self.runtime=native_runtime
        pending,observed=self.next_read();acknowledge(self.paths,self.jobs['next_read']/'output.json','next verified','staged')
        self.job('promise','promise',job_id='next',artifact_id='output');pending=read_json(self.pending)
        self.job('submitted','workflow-readback',job_id='promise',artifact_id='output');observed=read_json(self.jobs['submitted']/'output.json')
        self.assertTrue(n.verify_ack(pending,observed,'saved')['workflow_started'])
        for mode in ['wrong_instance','wrong_base','changed_invoice','wrong_invoice_status','wrong_request_date']:
            bad=copy.deepcopy(observed)
            if mode=='wrong_instance':bad['result']['original']['bill']['t.uni_key']='999'
            if mode=='wrong_base':bad['result']['original']['bill']['t.basetable_key']='999'
            if mode=='changed_invoice':bad['result']['original']['invoices'][0]['t.tk_guid']='B'*32
            if mode=='wrong_invoice_status':bad['result']['original']['invoices'][0]['t.status_name']='已付款'
            if mode=='wrong_request_date':bad['result']['original']['bill']['t.req_date']='2029-01-01'
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')

    def test_native_numeric_instance_and_only_invoice_status_change_are_verified(self):
        self.native_instance_readback()

    def test_captured_submission_only_completes_with_exact_independent_instance(self):
        self.native_instance_readback(captured=True)

    def test_captured_missing_supplement_is_a_blocked_validation_not_submission(self):
        value={'phase':'script_completed','result':{'ok':False,'error':'NATIVE_PROTOCOL_RESPONSE_UNKNOWN',
            'state':'unknown_original_workflow_recovery_required','system_write':True,'save_dispatched':True,'steps':[],
            'native_response':'#left#font class="cont_tipword_NORMAL"#right#还有1个补充说明未填写！#left#/font#right#'}}
        context,messages=n.captured_validation_context(value,{'action':'next'})
        self.assertEqual(context,{});self.assertEqual(messages['61-tipinfo'],'还有1个补充说明未填写！')
        for mode in ['unknown','different','script','undispatched','promise']:
            bad=copy.deepcopy(value);action='next'
            if mode=='unknown':bad['phase']='result_unknown'
            if mode=='different':bad['result']['native_response']='其他错误'
            if mode=='script':bad['result']['native_response']+=';evil()'
            if mode=='undispatched':bad['result']['save_dispatched']=False
            if mode=='promise':action='promise'
            self.assertIsNone(n.captured_validation_context(bad,{'action':action}))

    def test_payroll_only_allows_native_summary_default(self):
        before={'original':{'dd.remark':'','dd.amount':'100'},'rows':[{'id':'TEST','amount':'100'}]};after=copy.deepcopy(before);after['original']['dd.remark']='已核活动'
        c={'system_mode':'remuneration','plan':{'summary':'已核活动'}}
        n.verify_salary_fields(before,after,c,True)
        for mode in ['daily','not_started','amount','person','summary']:
            config=copy.deepcopy(c);bad=copy.deepcopy(after)
            if mode=='daily':config['system_mode']='daily'
            if mode=='amount':bad['rows'][0]['amount']='101'
            if mode=='person':bad['rows'][0]['id']='OTHER'
            if mode=='summary':bad['original']['dd.remark']='其他活动'
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_salary_fields(before,bad,config,mode!='not_started')

if __name__=='__main__':unittest.main()
