from pathlib import Path
import json
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(ROOT/'tests'))
import test_intake_review as fixture
from assistant_contract import dispatch_request,json_routes
from workbench_catalog import json_request
from assistant_api import read_json,atomic_json


class TaskViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.IntakeReviewTests('test_inspect_preview_and_register_use_same_frozen_source')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.service=self.fixture.service;self.case=self.fixture.case
        saved=self.service.intake_review(self.case,self.fixture.job,action='register',records=self.fixture.records(),
            expected_revision=self.service.revision(self.case),confirmation_note='合成原件核对',execute=True)
        self.manifest=self.invoke(saved['next_action'])

    def wait(self,result):
        deadline=time.monotonic()+30
        while result.get('status')in {'queued','running'}and time.monotonic()<deadline:
            time.sleep(.12);result=self.service.result(self.case,result['job_id'])
        self.assertEqual(result['status'],'completed',result)
        return result

    def invoke(self,action):
        request=json_request({'route':json_routes()[action['tool']]},action['arguments'])
        return self.wait(dispatch_request(self.service,request))

    def test_manifest_next_action_registers_exact_originals_and_leads_to_form(self):
        view=self.service.task_view(self.case,self.manifest['job_id'])
        self.assertEqual(view['title'],'原件来源已核对');self.assertEqual(view['row_count'],1)
        self.assertIn('尚未审核票面',view['summary'])
        recorded=self.invoke(view['next_action'])
        following=self.service.task_view(self.case,recorded['job_id'])
        self.assertEqual(following['title'],'原件已登记')
        self.assertEqual(following['next_action']['tool'],'reimbursement_case_details')
        _,paths=self.service.resolve(self.case)
        self.assertEqual(read_json(paths['form'])['expected_invoice_count'],1)
        self.assertIsNone(read_json(paths['form'])['serial'])
        self.assertEqual(len(read_json(paths['invoices'])['original_files']),1)

    def test_unknown_result_never_offers_replay(self):
        path=self.service.job_folder(self.case,self.manifest['job_id'])/'status.json'
        status=read_json(path);status.update(status='unknown',error='synthetic interrupted result')
        atomic_json(path,status)
        view=self.service.task_view(self.case,self.manifest['job_id'])
        self.assertFalse(view['safe_to_retry'])
        self.assertEqual(view['next_action']['tool'],'reimbursement_result')
        self.assertEqual(view['next_action']['arguments']['job_id'],self.manifest['job_id'])

    def test_changed_artifact_cannot_be_reported_as_verified_source(self):
        artifact=next(a for a in self.manifest['artifacts']if a['name']=='output.json')
        Path(artifact['path']).write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.service.task_view(self.case,self.manifest['job_id'])


if __name__=='__main__':unittest.main()
