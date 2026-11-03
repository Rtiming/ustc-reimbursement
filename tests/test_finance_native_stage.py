"""Synthetic write lifecycle: no browser and no financial requests."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from assistant_api import AssistantService, atomic_json, read_json
from finance_native_stage import command, digest, run, verify_ack
from finance_transport_policy import check_dispatch
from reimburse import acknowledge


class NativeStageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.service = AssistantService(self.root, [self.root])
        self.service.new_case('fixture')
        self.bundle, self.paths = self.service.resolve('fixture')
        self.form = read_json(self.paths['form'])
        self.form.update(serial='123', amount='10.00', expected_userid='TEST001',
            basic={'teacher_id':'100','teacher_name':'Teacher','fields':{'t-abst':'New',
                't-req_mobile':'13800000000','t-is_have_changefee':'N','t-td_point':'退单地点'}})
        atomic_json(self.paths['form'], self.form)
        self.folder = self.root/'job'; self.folder.mkdir()
        self.request = {'case_id':'fixture','operation':'stage','parameters':{'stage':'basic','tab_id':'7'},
                        'execute':True,'request_id':'fixture-request'}
        self.calls = []
        self.input = self.folder/'native-stage-input.json'; self.output = self.folder/'output.json'
        self.pending = self.paths['state'].parent/'pending_write.json'
        self.prepare = {'ok':True,'complete':True,'action':'basic-prepare','case_id':'fixture','serial':'123',
            'expected_userid':'TEST001','business_transport':'native_http','system_write':False,'save_dispatched':False,
            'definition_sha256':read_json(ROOT/'data/finance_basic_contract.json')['definition_sha256'],
            'bill':{'t.status':'-99','t.lc_finish':'F'},'original':{'t.abst':'Old','t.untouched':'Keep'},
            'changes':{'t.abst':'New'},'teacher':{'id':'100','name':'Teacher','department':'Unit'}}

    def runtime(self, args):
        script = Path(args[2]).read_text(); self.calls.append(script)
        if script.rsplit('return await ',1)[-1].startswith('reimbursementBasicPrepare('):
            self.assertFalse(self.pending.exists())
            body = self.prepare
        else:
            self.assertTrue(self.pending.exists())
            previous = read_json(self.pending)
            self.assertEqual(previous['verification']['prepare_sha256'],digest(self.folder/'native-stage-prepare.json'))
            body = {'action':'basic-save','state':'unknown_save_readback_required','save_dispatched':True,
                    'case_id':'fixture','serial':'123','expected_userid':'TEST001'}
        atomic_json(Path(args[args.index('--output')+1]), {'phase':'script_completed',
            'executed_at_utc':datetime.now(timezone.utc).isoformat(),'result':body})
        return SimpleNamespace(returncode=0)

    def execute(self):
        command(self.service,self.request,self.folder)
        with patch('operator_support.check',return_value={'ok':True}):
            return run(self.input,self.output,runtime=self.runtime)

    def readback(self):
        return {'phase':'script_completed','executed_at_utc':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),
            'dispatch_intent_at_utc':datetime.now(timezone.utc).isoformat(),
            'result':{'ok':True,'complete':True,'business_transport':'native_http','action':'bill-read','domain':'basic',
                'system_write':False,'case_id':'fixture','serial':'123','expected_userid':'TEST001',
                'bill':{'t.serial_no':'123','t.REQ_INFO':'TEST001-Fixture','t.total_amt':'10.00'},
                'sections':[{'funcno':'60','kind':'form','rows':[{'ABST':'New','UNTOUCHED':'Keep'}]}]}}

    def test_formal_command_and_policy_only_allow_migrated_stage(self):
        args = self.service.command(self.request,self.folder)
        self.assertEqual(args[1],str(ROOT/'tools/finance_native_stage.py'))
        self.assertNotIn('run_case.py',' '.join(args)); check_dispatch(self.request)
        check_dispatch({**self.request,'parameters':{'stage':'notes'}})
        with self.assertRaises(ValueError):check_dispatch({**self.request,'parameters':{'stage':'review'}})

    def test_pending_overview_uses_http_domain_readback(self):
        self.execute()
        action=self.service.overview('fixture')['next_action']
        self.assertEqual((action['tool'],action['action'],action['domain']),('reimbursement_business','bill-read','basic'))

    def test_backend_check_is_fixed_readonly_and_not_arbitrary_transport(self):
        request={**self.request,'operation':'access','execute':False,
                 'parameters':{'action':'check-backend','tab_id':'7','expected_userid':'TEST001'}}
        args=self.service.command(request,self.folder)
        self.assertEqual(args[1],str(ROOT/'tools/finance_backend_health.py'))
        for change in [{'execute':True},{'parameters':{**request['parameters'],'query':'anything'}}]:
            with self.assertRaises(ValueError):self.service.command({**request,**change},self.folder)

    def test_preview_has_no_runtime_or_pending(self):
        command(self.service,{**self.request,'execute':False},self.folder)
        self.assertEqual(run(self.input,self.output,runtime=lambda args:self.fail('runtime called')),0)
        self.assertFalse(self.pending.exists()); self.assertFalse(read_json(self.output)['system_write'])

    def test_formal_worker_command_runs_from_unrelated_directory(self):
        args=command(self.service,{**self.request,'execute':False},self.folder)
        result=subprocess.run(args,cwd=self.root,text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(read_json(self.output)['business_transport'],'native_http')
        self.assertFalse(self.pending.exists())

    def test_offline_preview_reports_missing_identity_but_execution_rejects(self):
        self.form.pop('expected_userid');atomic_json(self.paths['form'],self.form)
        with patch.dict('os.environ',{},clear=True):
            command(self.service,{**self.request,'execute':False,'parameters':{'stage':'basic'}},self.folder)
            run(self.input,self.output,runtime=lambda args:self.fail('preview must not access browser'))
            self.assertEqual(read_json(self.output)['needed_inputs'],['expected_userid','tab_id'])
            other=self.root/'execute';other.mkdir()
            with self.assertRaisesRegex(ValueError,'expected_userid'):command(self.service,self.request,other)

    def test_pending_precedes_single_save_and_new_request_cannot_replay(self):
        self.assertEqual(self.execute(),0); self.assertEqual(len(self.calls),2)
        with self.assertRaisesRegex(ValueError,'原写入'):run(self.input,self.output,runtime=self.runtime)
        self.assertEqual(len(self.calls),2)
        second = self.root/'second'; second.mkdir()
        command(self.service,{**self.request,'request_id':'new-request'},second)
        with self.assertRaisesRegex(ValueError,'原写入'):run(second/'native-stage-input.json',second/'output.json',runtime=self.runtime)

    def test_after_dispatch_exception_keeps_original_intent(self):
        def interrupted(args):
            if self.pending.exists():raise RuntimeError('connection lost')
            return self.runtime(args)
        command(self.service,self.request,self.folder)
        with patch('operator_support.check',return_value={'ok':True}), self.assertRaises(RuntimeError):
            run(self.input,self.output,runtime=interrupted)
        self.assertTrue(self.pending.exists())

    def test_frozen_adapter_cannot_be_replaced_before_execution_or_ack(self):
        command(self.service,self.request,self.folder)
        adapter=self.folder/'native-stage-adapter.js';original=adapter.read_text()
        adapter.write_text(original+'\n// changed\n')
        with self.assertRaisesRegex(ValueError,'适配器源码'):run(self.input,self.output,runtime=self.runtime)
        self.assertFalse(self.pending.exists());self.assertEqual(self.calls,[])
        adapter.write_text(original,encoding='utf-8',newline='\n')
        with patch('operator_support.check',return_value={'ok':True}):run(self.input,self.output,runtime=self.runtime)
        adapter.write_text(original+'\n// changed after save\n')
        with self.assertRaisesRegex(ValueError,'源码快照'):verify_ack(self.paths,read_json(self.pending),self.readback(),'saved')
        self.assertTrue(self.pending.exists())

    def test_approved_and_changed_form_fail_before_intent(self):
        self.prepare['bill']={'t.status':'10','t.lc_finish':'T'}
        with self.assertRaises(ValueError):self.execute()
        self.assertFalse(self.pending.exists()); self.assertEqual(len(self.calls),1)
        self.form['amount']='20.00'; atomic_json(self.paths['form'],self.form)
        with self.assertRaises(ValueError):run(self.input,self.output,runtime=self.runtime)
        self.assertEqual(len(self.calls),1)

    def test_independent_readback_acknowledges_unknown_save_without_replay(self):
        self.execute(); read=self.folder/'read.json'; atomic_json(read,self.readback())
        acknowledge(self.paths,read,'All fields independently verified','saved')
        self.assertFalse(self.pending.exists())
        self.assertIn('basic',read_json(self.paths['state'])['operator_verified_stages'])
        proof=read_json(self.folder/'operator_readback_ack.json')['native_stage_readback']
        self.assertEqual(proof['verified_fields'],2); self.assertEqual(len(self.calls),2)

    def test_partial_foreign_old_or_changed_readback_never_clears_pending(self):
        self.execute()
        for kind in ['partial','foreign','amount','comma','untouched','zero','changed','old','save','form_sha','case']:
            with self.subTest(kind=kind):
                r=self.readback(); previous=read_json(self.pending)
                if kind=='partial':r['result']['complete']=False
                if kind=='foreign':r['result']['expected_userid']='OTHER'
                if kind=='amount':r['result']['bill']['t.total_amt']='11.00'
                if kind=='comma':r['result']['bill']['t.total_amt']='1,0.00'
                if kind=='untouched':r['result']['sections'][0]['rows'][0]['UNTOUCHED']='Changed'
                if kind=='zero':r['result']['sections'][0]['rows'][0]['UNTOUCHED']=0
                if kind=='changed':r['result']['sections'][0]['rows'][0]['ABST']='Old'
                if kind=='old':r['executed_at_utc']='2020-01-01T00:00:00+00:00'
                if kind=='save':r['result']['action']='basic-save'
                if kind=='form_sha':previous['verification']['form_sha256']='0'*64
                if kind=='case':previous['case_id']='other'
                atomic_json(self.pending,previous); read=self.folder/'read.json';atomic_json(read,r)
                with self.assertRaises(ValueError):acknowledge(self.paths,read,'Checked','saved')
                self.assertTrue(self.pending.exists())
                previous['case_id']='fixture';previous['verification']['form_sha256']=digest(self.paths['form']);atomic_json(self.pending,previous)

    def test_unchanged_data_does_not_prove_unknown_write_not_applied(self):
        self.execute(); r=self.readback();r['result']['sections'][0]['rows'][0]['ABST']='Old'
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'not_applied')
        rejected={'phase':'script_completed','executed_at_utc':read_json(self.output)['executed_at_utc'],
            'result':{'action':'basic-save','case_id':'fixture','serial':'123',
            'expected_userid':'TEST001','state':'rejected_before_save','save_dispatched':False}}
        atomic_json(self.output,rejected)
        self.assertEqual(verify_ack(self.paths,read_json(self.pending),r,'not_applied')['outcome'],'not_applied')
        rejected['result']['case_id']='other';atomic_json(self.output,rejected)
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'not_applied')

    def test_original_script_must_finish_before_readback_starts(self):
        self.execute();r=self.readback();saved=read_json(self.output)
        for phase in ['dispatch_intent','injected','marker_timeout']:
            atomic_json(self.output,{**saved,'phase':phase})
            with self.assertRaisesRegex(ValueError,'尚未结束'):verify_ack(self.paths,read_json(self.pending),r,'saved')
        atomic_json(self.output,saved)
        r['dispatch_intent_at_utc']=saved['executed_at_utc']
        with self.assertRaisesRegex(ValueError,'结束后'):verify_ack(self.paths,read_json(self.pending),r,'saved')


    def test_mixed_corporate_native_save_drops_only_unchanged_zero_suggestions(self):
        from finance_native_stage import verify_payments
        paid={'BANKSNAME':'Synthetic Company','BANKACNT':'123456789012','TAXPAYER_ORD':'synthetic-tax','AMOUNT':'10.00','REMARK':''}
        unused={'BANKSNAME':'Unused Issuer','BANKACNT':'999999999999','TAXPAYER_ORD':'unused-tax','AMOUNT':'0.00','REMARK':''}
        target={'22':[],'24':[paid,unused]};plan={'original':{'24':[dict(paid,AMOUNT='0.00'),unused]}}
        read={'sections':[{'funcno':'22','kind':'form','rows':[]},{'funcno':'24','kind':'form','rows':[paid]}]}
        self.assertEqual(verify_payments(target,read,plan,'saved')['verified_sections'],2)
        for mode in ['lost_paid','changed_account','formerly_paid','duplicate']:
            actual=copy.deepcopy(read);original=copy.deepcopy(plan)
            if mode=='lost_paid':actual['sections'][1]['rows']=[]
            if mode=='changed_account':actual['sections'][1]['rows'][0]['BANKACNT']='555555555555'
            if mode=='formerly_paid':original['original']['24'][1]['AMOUNT']='2.00'
            if mode=='duplicate':actual['sections'][1]['rows']=[paid,paid]
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_payments(target,actual,original,'saved')

    def test_default_funding_known_pre_dispatch_rejection_requires_unchanged_complete_plan(self):
        self.execute();c=read_json(self.input);c['stage']='funding';atomic_json(self.input,c)
        plan=copy.deepcopy(self.prepare);plan.update(action='funding-prepare',original={'budget_rows':[],'implicit_default':{'synthetic':'same'}},changes={})
        atomic_json(self.folder/'native-stage-prepare.json',{'result':plan})
        pending=read_json(self.pending);pending['operation']='step:funding';v=pending['verification'];v.update(stage='funding',input_sha256=digest(self.input),prepare_sha256=digest(self.folder/'native-stage-prepare.json'))
        saved=read_json(self.output);saved['result'].update(action='funding-save',ok=False,system_write=False,save_dispatched=False,state='rejected_before_save',steps=[]);atomic_json(self.output,saved)
        observed=self.readback();observed['result']=plan
        with patch('finance_native_stage.validate_plan'):
            self.assertTrue(verify_ack(self.paths,pending,observed,'not_applied')['rejected_before_dispatch'])
            for mode in ['changed','dispatched','unknown']:
                bad=copy.deepcopy(observed);receipt=copy.deepcopy(saved)
                if mode=='changed':bad['result']['original']['implicit_default']['synthetic']='changed'
                if mode=='dispatched':receipt['result']['save_dispatched']=True
                if mode=='unknown':receipt['result']['state']='unknown_save_readback_required'
                atomic_json(self.output,receipt)
                with self.subTest(mode=mode),self.assertRaises(ValueError):verify_ack(self.paths,pending,bad,'not_applied')



    def test_payroll_default_funding_requires_exact_people_budget_period_and_binding(self):
        from finance_native_stage import verify_remuneration_default
        c={'system_mode':'remuneration','serial':'123','expected_amount':'10.00','funding':[{'code':'F1'}],'remuneration_native_source':{'sha256':'bound'},'activity':{'period_start':'2026-05-01','period_end':'2026-05-31','approved_people':{'S1':{'name':'合成人员','amount':'10.00'}}}}
        proof={'kind':'system_default_remuneration_funding','source_binding':c['remuneration_native_source'],'amounts':[],'bill':{'t.serial_no':'123','t.uni_prj_code':'F1','t.bx_type':'8'},'categories':[{'group':'学生-学生其他劳务','project':'F1','budget':'B1','amount':'10.00'}],'salary':{'original':{'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':'2026-05-01','dd.ff_enddate':'2026-05-31','dd.hastax':'T'},'rows':[{'t.serial_no':'123','t.unino':'S1','t.name':'合成人员','t.AMT':'10.00'}],'preview_people':[{'id':'S1','name':'合成人员','amount':'1000'}]}}
        verify_remuneration_default(c,proof)
        for mode in ['source','project','amount','identity','serial','period','duplicate','preview']:
            bad=copy.deepcopy(proof)
            if mode=='source':bad['source_binding']={}
            if mode=='project':bad['categories'][0]['project']='F2'
            if mode=='amount':bad['categories'][0]['amount']='9.00'
            if mode=='identity':bad['salary']['rows'][0]['t.unino']='S2'
            if mode=='serial':bad['salary']['rows'][0]['t.serial_no']='124'
            if mode=='period':bad['salary']['original']['dd.ff_enddate']='2026-06-01'
            if mode=='duplicate':bad['salary']['rows']*=2
            if mode=='preview':bad['salary']['preview_people'][0]['amount']='900'
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_remuneration_default(c,bad)

class NewCorporateBindingTests(unittest.TestCase):
    def test_native_identity_must_match_frozen_company_account_and_branch(self):
        from finance_native_stage import verify_corporate_binding_plan
        binding={'row_index':0,'name':'Synthetic Company','account':'123456789012','bank_name':'123456789012/Synthetic Branch','resolve_required':True}
        plan={'corporate_bindings':[binding],'changes':{'24':[{'BANKSNAME':binding['name'],'BANKACNT':binding['account'],'BANKNAME':binding['bank_name'],'AMOUNT':'10.00','TAXPAYER_ORD':''}]}}
        proof={**binding,'state':'accepted','taxpayer_ord':'native-taxpayer'};save={'corporate_resolution':[proof]}
        target=verify_corporate_binding_plan(plan,save);self.assertEqual(target['changes']['24'][0]['TAXPAYER_ORD'],'native-taxpayer');self.assertEqual(plan['changes']['24'][0]['TAXPAYER_ORD'],'')
        import copy
        for key,value in [('state','dispatched'),('name','Other Company'),('account','999999999999'),('bank_name','Other Branch'),('row_index',1),('taxpayer_ord','')]:
            bad=copy.deepcopy(save);bad['corporate_resolution'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):verify_corporate_binding_plan(plan,bad)


class NewCardBindingTests(unittest.TestCase):
    def test_native_generated_order_is_accepted_only_for_exact_planned_transaction(self):
        from finance_native_stage import verify_card_binding_plan
        c={'stage':'payments','case_id':'fixture','serial':'123','expected_userid':'TEST001','payments_contract':{'definition_sha256':'fixed'},'payments':[{'mode':'corporate_card'}]}
        transaction={'t.seq_no':'exact-sequence','t.account':'1234567890123456'}
        original={'19':[{'BX_AMT':'10.00','YT_AMT':'0.00','WT_AMT':'10.00','CLR_AMT':'0'}],'22':[],'285':[]}
        before={'original':original,'card_bindings':[{'transaction':transaction,'selection_required':True,'selected_amount':'10.00'}]}
        after={'ok':True,'complete':True,'action':'payments-prepare','business_transport':'native_http','save_dispatched':False,
          'case_id':'fixture','serial':'123','expected_userid':'TEST001','definition_sha256':'fixed','bill':{'t.status':'-99','t.lc_finish':'F'},
          'original':{'19':[{'BX_AMT':'10.00','YT_AMT':'10.00','WT_AMT':'0.00','CLR_AMT':'0'}],'22':[],
            '285':[{'GWK_SEQ_NO':'exact-sequence','GWK_BANKACNT':'1234567890123456','GWK_ORD':'native-generated','AMOUNT':'10.00'}]},
          'changes':{},'card_bindings':[{'transaction':transaction}]}
        save={'card_selection':{'state':'accepted','transaction':transaction,'selected_amount':'10.00','post_binding_plan':after}}
        self.assertEqual(verify_card_binding_plan(c,before,save),after)
        import copy
        for mode in ['other_section','summary','account','amount','duplicate','unknown']:
            bad=copy.deepcopy(save);plan=bad['card_selection']['post_binding_plan']
            if mode=='other_section':plan['original']['22']=[{'AMOUNT':'1.00'}]
            if mode=='summary':plan['original']['19'][0]['BX_AMT']='11.00'
            if mode=='account':plan['original']['285'][0]['GWK_BANKACNT']='9999999999'
            if mode=='amount':plan['original']['285'][0]['AMOUNT']='9.00'
            if mode=='duplicate':plan['original']['285'].append(plan['original']['285'][0])
            if mode=='unknown':bad['card_selection']['state']='dispatched'
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_card_binding_plan(c,before,bad)



if __name__=='__main__':unittest.main()
