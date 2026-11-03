"""预审保存的槽身份、派发前pending及人员/未改字段完整HTTP回读。"""
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from tests import test_finance_native_allocations as allocation_tests
from tests import test_finance_native_stage as basic_tests
from assistant_api import atomic_json, read_json
from finance_native_stage import command, run, verify_ack, stage_contract
from reimburse import acknowledge


class PreReviewStageTests(unittest.TestCase):
    def setUp(self):
        allocation_tests.AllocationStageTests.setUp(self)
        self.form['pre_review']=[{'id':'P001','name':'Person'}]
        atomic_json(self.paths['form'],self.form);self.request['parameters']['stage']='pre-review'
        self.slots=[{'index':0,'name':'ustc_xmysr','count':'1','uuid':'A'*32,'source_table':'QT',
            'multi_choice':'N','select_or_and':'A','project_code':''}]
        self.prepare.update(action='pre-review-prepare',definition_sha256=stage_contract('pre-review')['definition_sha256'],
            original={'0':{'PARAM_KEY3':'','LC_APPROVER':'','LC_APPROVER_NAME':'','UNTOUCHED':'KEEP'}},
            changes={'0':{'PARAM_KEY3':'P001/Person','LC_APPROVER':'P001','LC_APPROVER_NAME':'Person'}},slots=self.slots)
        self.prepare.pop('teacher')

    def runtime(self,args):
        script=Path(args[2]).read_text();self.calls.append(script)
        prepare=script.rsplit('return await ',1)[-1].startswith('reimbursementPreReviewPrepare(')
        self.assertEqual(self.pending.exists(),not prepare)
        body=self.prepare if prepare else {'action':'pre-review-save','case_id':'fixture','serial':'123',
            'expected_userid':'TEST001','save_dispatched':True,'state':'unknown_save_readback_required'}
        atomic_json(Path(args[args.index('--output')+1]),{'phase':'script_completed',
            'executed_at_utc':datetime.now(timezone.utc).isoformat(),'result':body})
        return SimpleNamespace(returncode=0)

    def execute(self):
        command(self.service,self.request,self.folder)
        with patch('operator_support.check',return_value={'ok':True}):run(self.input,self.output,runtime=self.runtime)

    def readback(self):
        r=basic_tests.NativeStageTests.readback(self);r['result']['domain']='pre_review'
        r['result']['workflow_parameters']=copy.deepcopy(self.slots)
        r['result']['sections']=[{'funcno':'1297','kind':'form','slot':copy.deepcopy(self.slots[0]),
            'rows':[{**self.prepare['original']['0'],**self.prepare['changes']['0']}]}]
        return r

    def test_pending_precedes_save_and_exact_readback_can_finish(self):
        self.execute();self.assertEqual(self.service.overview('fixture')['next_action']['domain'],'pre_review')
        p=self.folder/'read.json';atomic_json(p,self.readback());acknowledge(self.paths,p,'人员及所有参数槽已独立核对','saved')
        self.assertFalse(self.pending.exists());self.assertIn('pre-review',read_json(self.paths['state'])['operator_verified_stages'])

    def test_real_precheck_recognizes_stage_and_keeps_material_errors(self):
        from operator_support import check
        report=check(self.bundle,validation_stage='pre-review')
        self.assertIsInstance(report,dict);self.assertFalse(report['ok'])
        self.assertTrue(report['errors'])

    def test_wrong_person_uuid_partial_duplicate_and_changed_parameter_rejected(self):
        self.execute()
        for mode in ['person','name','uuid','partial','duplicate','untouched']:
            r=self.readback();sections=r['result']['sections']
            if mode=='person':sections[0]['rows'][0]['LC_APPROVER']='OTHER'
            if mode=='name':sections[0]['rows'][0]['LC_APPROVER_NAME']='OTHER'
            if mode=='uuid':r['result']['workflow_parameters'][0]['uuid']='B'*32
            if mode=='partial':sections.clear()
            if mode=='duplicate':sections.append(copy.deepcopy(sections[0]))
            if mode=='untouched':sections[0]['rows'][0]['UNTOUCHED']='OTHER'
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')
        self.assertTrue(self.pending.exists())

    def test_new_request_cannot_repeat_partial_unknown_save(self):
        self.execute();other=self.root/'other';other.mkdir();command(self.service,{**self.request,'request_id':'new'},other)
        with self.assertRaisesRegex(ValueError,'原写入'):run(other/'native-stage-input.json',other/'output.json',runtime=self.runtime)


if __name__=='__main__':unittest.main()
