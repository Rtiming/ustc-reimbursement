"""经费/预算的正式任务与持久HTTP回读；只使用虚构案件。"""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'tools')]
from tests import test_finance_native_stage as basic_tests
from finance_native_stage import command, run, verify_ack, stage_contract
from assistant_api import atomic_json, read_json
from reimburse import acknowledge


class AllocationStageTests(unittest.TestCase):
    def setUp(self):
        basic_tests.NativeStageTests.setUp(self)
        self.form.update(budget_allocation_mode='manual',funding=[{'code':'F1','amount':'10.00'}],payments=[{
            'mode':'bank_transfer','person_id':'STUDENT001','name':'Student','amount':'10.00'}],
            expense_allocations=[{'project':'F1','subject':'30201','budget':'B1','amount':'10.00'}])
        atomic_json(self.paths['form'],self.form)
        self.rows=[{'t.ord':'A','t.amt':'10.00','t.tlevel':'2','t.serial_no':'123','t.uni_prj_code':'F1','t.t_desc':'Calculated'}]

    def execute(self, stage):
        self.stage=stage;self.request['parameters']['stage']=stage
        self.prepare.update(action=stage+'-prepare',definition_sha256=stage_contract(stage)['definition_sha256'])
        self.prepare.pop('teacher')
        if stage == 'payments':
            self.prepare.update(original={'19':[{'BX_AMT':'10.00','YT_AMT':'0.00','WT_AMT':'10.00','CLR_AMT':'0.00'}],
                '22':[{'SNO':'STUDENT001','SNAME':'Student','BANKACNT':'12345678','KEY':'BOUND','AMOUNT':'0.00'}]},
                changes={'19':[{'BX_AMT':'10.00','YT_AMT':'10.00','WT_AMT':'0.00','CLR_AMT':'0.00'}],
                '22':[{'SNO':'STUDENT001','SNAME':'Student','BANKACNT':'12345678','KEY':'BOUND','AMOUNT':'10.00'}]},
                bank_bindings=[{'person_id':'STUDENT001','key':'BOUND'}],bank_check_flag='F')
        elif stage == 'expenses':
            self.prepare.update(original={'A':{**self.rows[0],'t.amt':'9.00'}},changes={'A':{'t.amt':'10.00'}})
        else:self.prepare.update(original={'budget_rows':[]},changes={'projects':['F1']},candidates=[{'code':'F1','grant_id':'G1'}])
        command(self.service,self.request,self.folder)
        with patch('operator_support.check',return_value={'ok':True}):return run(self.input,self.output,runtime=self.runtime)

    def runtime(self,args):
        script=Path(args[2]).read_text();self.calls.append(script)
        prepare='reimbursement'+self.stage.title()+'Prepare('
        if script.rsplit('return await ',1)[-1].startswith(prepare):
            self.assertFalse(self.pending.exists());body=self.prepare
        else:
            self.assertTrue(self.pending.exists());body={'action':self.stage+'-save','state':'unknown_save_readback_required',
                'save_dispatched':True,'case_id':'fixture','serial':'123','expected_userid':'TEST001'}
        atomic_json(Path(args[args.index('--output')+1]),{'phase':'script_completed',
            'executed_at_utc':datetime.now(timezone.utc).isoformat(),'result':body})
        return SimpleNamespace(returncode=0)

    def readback(self):
        r=basic_tests.NativeStageTests.readback(self);r['result']['domain']='categories'
        r['result']['sections']=[{'funcno':'307','kind':'grid','complete':True,'rows':copy.deepcopy(self.rows)}]
        if self.stage == 'payments':
            r['result']['domain']='payments'
            r['result']['sections']=[{'funcno':no,'kind':'form','rows':copy.deepcopy(rows)} for no,rows in self.prepare['changes'].items()]
        return r

    def test_expenses_formal_route_and_complete_persisted_readback(self):
        self.assertEqual(self.execute('expenses'),0)
        self.assertEqual(self.service.overview('fixture')['next_action']['domain'],'categories')
        read=self.folder/'read.json';atomic_json(read,self.readback());acknowledge(self.paths,read,'Full tree compared','saved')
        self.assertFalse(self.pending.exists());self.assertIn('expenses',read_json(self.paths['state'])['operator_verified_stages'])

    def test_funding_uses_persisted_budget_not_candidate_flags(self):
        self.assertEqual(self.execute('funding'),0)
        self.assertEqual(self.service.overview('fixture')['next_action']['domain'],'categories')
        r=self.readback();self.assertEqual(verify_ack(self.paths,read_json(self.pending),r,'saved')['selected_projects'],['F1'])
        r['result']['domain']='funding';r['result']['sections']=[{'funcno':'242','kind':'grid','complete':True,'rows':[{'t.select_flag':'已选'}]}]
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')

    def test_expenses_rejects_missing_extra_duplicate_wrong_and_changed_rows(self):
        self.execute('expenses')
        for mutation in ['missing','extra','duplicate','amount','project','partial','field','comma']:
            r=self.readback();rows=r['result']['sections'][0]['rows']
            if mutation=='missing':rows.clear()
            if mutation=='extra':rows.append({**rows[0],'t.ord':'OTHER'})
            if mutation=='duplicate':rows.append(copy.deepcopy(rows[0]))
            if mutation=='amount':rows[0]['t.amt']='9.00'
            if mutation=='project':rows[0]['t.uni_prj_code']='OTHER'
            if mutation=='partial':r['result']['sections'][0]['complete']=False
            if mutation=='field':del rows[0]['t.serial_no']
            if mutation=='comma':rows[0]['t.amt']='1,0.00'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')
            self.assertTrue(self.pending.exists())

    def test_funding_rejects_unpersisted_partial_foreign_or_duplicate_projects(self):
        self.execute('funding')
        for mutation in ['missing','extra','duplicate','amount','foreign']:
            r=self.readback();rows=r['result']['sections'][0]['rows']
            if mutation=='missing':rows.clear()
            if mutation=='extra':rows.append({**rows[0],'t.ord':'B','t.uni_prj_code':'OTHER','t.amt':'0.00'})
            if mutation=='duplicate':rows.append(copy.deepcopy(rows[0]))
            if mutation=='amount':rows[0]['t.amt']='9.00'
            if mutation=='foreign':rows[0]['t.serial_no']='999'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')

    def test_changed_input_and_new_request_do_not_bypass_pending(self):
        self.execute('expenses')
        second=self.root/'second';second.mkdir();command(self.service,{**self.request,'request_id':'new'},second)
        with self.assertRaisesRegex(ValueError,'原写入'):run(second/'native-stage-input.json',second/'output.json',runtime=self.runtime)
        self.form['expense_allocations'][0]['budget']='CHANGED';atomic_json(self.paths['form'],self.form)
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),self.readback(),'saved')

    def test_unknown_initialization_not_proof_of_not_applied(self):
        self.execute('funding');r=self.readback();r['result']['sections'][0]['rows']=[]
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'not_applied')

    def test_payments_formal_route_and_all_fields_readback(self):
        self.execute('payments');r=self.readback()
        self.assertEqual(self.service.overview('fixture')['next_action']['domain'],'payments')
        self.assertEqual(verify_ack(self.paths,read_json(self.pending),r,'saved')['verified_recipients'],1)
        read=self.folder/'read.json';atomic_json(read,r);acknowledge(self.paths,read,'Bank binding and all fields checked','saved')
        self.assertFalse(self.pending.exists());self.assertIn('payments',read_json(self.paths['state'])['operator_verified_stages'])

    def test_payments_rejects_changed_identity_account_key_and_incomplete_readback(self):
        self.execute('payments')
        for mutation in ['SNO','SNAME','BANKACNT','KEY','AMOUNT','partial','duplicate','summary']:
            r=self.readback()
            if mutation=='partial':r['result']['sections'].pop()
            elif mutation=='duplicate':r['result']['sections'][1]['rows']*=2
            elif mutation=='summary':r['result']['sections'][0]['rows'][0]['WT_AMT']='1.00'
            else:r['result']['sections'][1]['rows'][0][mutation]='OTHER'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')


if __name__=='__main__':unittest.main()
