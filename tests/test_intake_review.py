from pathlib import Path
import hashlib
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from assistant_api import AssistantService, read_json
from material_intake_service import configure_bundled_archive
from case_invoice_sources import CANDIDATE_STATUS, build_manifest


class IntakeReviewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.material=self.root/'materials';self.material.mkdir()
        import fitz
        self.pdf=self.material/'synthetic invoice.pdf'
        with fitz.open() as doc:
            page=doc.new_page();page.insert_text((40,50),'Synthetic invoice '+ '9'*20);doc.save(self.pdf)
        self.service=AssistantService(self.root,[self.material])
        self.case='fixture_review';self.service.new_case(self.case)
        configure_bundled_archive(self.root)
        request={'schema':'reimbursement.material-intake.v1','case_id':self.case,
                 'sources':[{'id':'original','path':str(self.pdf),'group_strip_dirs':None}],
                 'output_dir':str(self.material/'received')}
        result=self.service.material_intake(self.case,request,'review-fixture-intake',True)
        self.job=result['job_id'];deadline=time.monotonic()+30
        while True:
            result=self.service.result(self.case,self.job)
            if result['status'] not in {'queued','running'} or time.monotonic()>deadline:break
            time.sleep(.15)
        self.assertEqual(result['status'],'completed',result)
        self.inspected=self.service.intake_review(self.case,self.job)
        self.mid=self.inspected['rows'][0]['material_id']

    def records(self):
        return [{'material_id':self.mid,'role':'invoice','invoice_records':[{'number':'9'*20,'page':1}]}]

    def test_inspect_preview_and_register_use_same_frozen_source(self):
        self.assertEqual(self.inspected['total_materials'],1)
        self.assertEqual(self.inspected['rows'][0]['invoice_candidates'],[{'number':'9'*20,'page':1}])
        image=self.service.intake_review(self.case,self.job,action='preview',material_id=self.mid)
        self.assertTrue(Path(image['preview_path']).read_bytes().startswith(b'\xff\xd8'))
        revision=self.service.revision(self.case)
        preview=self.service.intake_review(self.case,self.job,action='register',records=self.records(),expected_revision=revision,confirmation_note='合成测试对照票号与页码')
        self.assertTrue(preview['preview']);self.assertEqual(self.service.revision(self.case),revision)
        result=self.service.intake_review(**preview['next_action']['arguments'])
        self.assertTrue(result['saved']);self.assertFalse(result['system_write'])
        self.assertIn('request_id',result['next_action']['arguments'])
        _,paths=self.service.resolve(self.case);case=read_json(paths['case'])
        self.assertEqual(case['materials'][0]['status'],CANDIDATE_STATUS)
        manifest=build_manifest(case,self.case,[self.material])
        self.assertEqual(manifest['expected_count'],1)
        self.assertFalse(manifest['selection_write'])
        self.assertEqual(read_json(paths['form'])['amount'],'')

    def test_stale_revision_cross_case_page_and_source_identity_rejected(self):
        with self.assertRaises(ValueError):self.service.intake_review(self.case,self.job,action='preview',material_id='mat-'+'0'*16)
        with self.assertRaises(ValueError):self.service.intake_review(self.case,self.job,action='preview',material_id=self.mid,page=2)
        self.service.new_case('other_case')
        with self.assertRaises(ValueError):self.service.intake_review('other_case',self.job)
        with self.assertRaises(ValueError):self.service.intake_review(self.case,self.job,action='register',records=self.records(),expected_revision='stale',confirmation_note='合成核对',execute=True)

    def test_invalid_fields_and_missing_confirmation_do_not_change_case(self):
        revision=self.service.revision(self.case)
        for row in [dict(self.records()[0],path='/outside.pdf'),dict(self.records()[0],invoice_records=[{'number':'123','page':1}]),dict(self.records()[0],invoice_records=[{'number':'9'*20,'page':99}])]:
            with self.assertRaises(ValueError):self.service.intake_review(self.case,self.job,action='register',records=[row],expected_revision=revision,confirmation_note='合成核对',execute=True)
        with self.assertRaises(ValueError):self.service.intake_review(self.case,self.job,action='register',records=self.records(),expected_revision=revision,execute=True)
        self.assertEqual(self.service.revision(self.case),revision)


if __name__=='__main__':unittest.main()
