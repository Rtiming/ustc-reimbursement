"""说明保存沿原pending，正文与附件必须在新的HTTP回读中全部匹配。"""
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


class NotesStageTests(unittest.TestCase):
    def setUp(self):
        allocation_tests.AllocationStageTests.setUp(self)
        self.form['explanations']={'general':'New','card_notes':[]}
        atomic_json(self.paths['form'],self.form)
        self.request['parameters']['stage']='notes'
        self.prepare.update(action='notes-prepare',definition_sha256=stage_contract('notes')['definition_sha256'],
            original={'t.spec_response':'Old','t.SPEC_FILE':'KEEP'},changes={'t.spec_response':'New'},scope={'xtype':'33&33','groups':'0','record_only':False})
        self.prepare.pop('teacher')

    def runtime(self,args):
        script=Path(args[2]).read_text();self.calls.append(script)
        prepare=script.rsplit('return await ',1)[-1].startswith('reimbursementNotesPrepare(')
        self.assertEqual(self.pending.exists(),not prepare)
        body=self.prepare if prepare else {'action':'notes-save','case_id':'fixture','serial':'123',
            'expected_userid':'TEST001','save_dispatched':True,'state':'unknown_save_readback_required'}
        atomic_json(Path(args[args.index('--output')+1]),{'phase':'script_completed',
            'executed_at_utc':datetime.now(timezone.utc).isoformat(),'result':body})
        return SimpleNamespace(returncode=0)

    def execute(self):
        command(self.service,self.request,self.folder)
        with patch('operator_support.check',return_value={'ok':True}):run(self.input,self.output,runtime=self.runtime)

    def readback(self):
        r=basic_tests.NativeStageTests.readback(self);r['result']['domain']='notes'
        r['result']['sections']=[{'funcno':'76','kind':'form','rows':[{'SPEC_RESPONSE':'New','SPEC_FILE':'KEEP'}]}]
        return r

    def test_formal_route_pending_and_full_independent_readback(self):
        self.execute();self.assertEqual(self.service.overview('fixture')['next_action']['domain'],'notes')
        r=self.folder/'read.json';atomic_json(r,self.readback());acknowledge(self.paths,r,'正文及原附件完整回读','saved')
        self.assertFalse(self.pending.exists());self.assertIn('notes',read_json(self.paths['state'])['operator_verified_stages'])

    def test_changed_or_missing_attachment_and_wrong_text_are_rejected(self):
        self.execute()
        for field in ['SPEC_RESPONSE','SPEC_FILE']:
            r=self.readback();r['result']['sections'][0]['rows'][0][field]='CHANGED'
            with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')
        r=self.readback();del r['result']['sections'][0]['rows'][0]['SPEC_FILE']
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),r,'saved')
        self.assertTrue(self.pending.exists())

    def test_record_only_input_and_new_request_cannot_replay_unknown_save(self):
        self.execute();self.form['explanations']['record_only']=True;atomic_json(self.paths['form'],self.form)
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),self.readback(),'saved')
        other=self.root/'other';other.mkdir()
        with self.assertRaisesRegex(ValueError,'只留档'):command(self.service,{**self.request,'request_id':'new'},other)
        self.form['explanations'].pop('record_only');atomic_json(self.paths['form'],self.form)
        command(self.service,{**self.request,'request_id':'new'},other)
        with self.assertRaisesRegex(ValueError,'原写入'):run(other/'native-stage-input.json',other/'output.json',runtime=self.runtime)


if __name__=='__main__':unittest.main()
