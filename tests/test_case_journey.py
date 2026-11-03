from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from assistant_api import AssistantService,atomic_json
from case_journey import preview


class JourneyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.service=AssistantService(self.root,[self.root]);self.service.new_case('fixture')

    def test_new_case_next_step_is_materials_not_inventing_serial(self):
        result=self.service.case_journey('fixture')
        self.assertEqual(result['next_action']['ui_group'],'materials')
        self.assertIn('不需要手填',result['steps'][-1]['detail'])
        self.assertFalse(result['system_write'])

    def test_original_preview_review_and_sha_drift(self):
        import fitz
        path=self.root/'原票.pdf'
        with fitz.open()as pdf:
            pdf.new_page().insert_text((20,30),'SYNTHETIC INVOICE 99999999999999999999');pdf.save(path)
        _,paths=self.service.resolve('fixture')
        atomic_json(paths['invoices'],{'case_id':'fixture','serial':None,'original_files':[{
            'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'invoice_number_candidates':['9'*20],'visual_check':'not_reviewed'}]})
        result=self.service.case_journey('fixture')
        self.assertEqual(result['next_action']['arguments']['action'],'invoice')
        view=self.service.case_journey('fixture','invoice',0)
        self.assertEqual(view['page_count'],1)
        self.assertTrue(preview(self.service,'fixture',0,1).startswith(b'\xff\xd8'))
        with self.assertRaises(ValueError):preview(self.service,'fixture',0,2)
        self.service.verify_invoice('fixture',0,['9'*20],'synthetic visual check',view['revision'])
        self.assertEqual(self.service.case_journey('fixture')['steps'][1]['state'],'complete')
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.service.case_journey('fixture','invoice',0)
        self.assertFalse(self.service.case_journey('fixture')['originals'][0]['source_ok'])

    def test_running_and_unknown_operations_take_priority(self):
        with patch.object(self.service,'overview',return_value={'active_jobs':[{'job_id':'j_'+'1'*24}]}):
            result=self.service.case_journey('fixture')
            self.assertEqual(result['next_action']['tool'],'reimbursement_task_view')
        with patch.object(self.service,'overview',return_value={'unverified_write':{'phase':'unknown'}}):
            result=self.service.case_journey('fixture')
            self.assertEqual(result['next_action']['tool'],'reimbursement_overview')


if __name__=='__main__':unittest.main()
