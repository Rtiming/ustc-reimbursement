from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,read_json


class CaseDetailsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.service=AssistantService(temp.name,[temp.name]);self.service.new_case('fixture',title='合成活动')

    def test_empty_case_returns_human_fields_and_no_fabricated_amount(self):
        result=self.service.case_details('fixture')
        self.assertEqual(result['values']['amount'],'')
        self.assertFalse(result['input_validation']['ok'])
        self.assertIn('teacher_name',[r['field']for r in result['needed_inputs']])
        self.assertFalse(result['system_write'])

    def test_draft_does_not_change_form_and_stale_draft_cannot_override_saved_values(self):
        value=self.service.case_details('fixture')
        self.service.case_details('fixture','draft',{'amount':'100.00'},value['revision'],execute=True)
        current=self.service.case_details('fixture')
        self.assertEqual(current['values']['amount'],'');self.assertFalse(current['draft_stale'])
        saved=self.service.case_details('fixture','save',{'amount':'150.00','summary':'合成摘要'},current['revision'],'按合成原件确认',True)
        self.assertEqual(saved['values']['amount'],'150.00');self.assertTrue(saved['draft_stale'])
        self.assertEqual(saved['draft']['values']['amount'],'100.00')

    def test_landline_is_not_copied_to_mobile_and_unknown_amount_not_silently_kept(self):
        value=self.service.case_details('fixture')
        saved=self.service.case_details('fixture','save',{'landline':'0551-00000000','amount':'10.00'},value['revision'],'合成核对',True)
        self.assertEqual(saved['values']['phone'],'');self.assertEqual(saved['values']['landline'],'0551-00000000')
        with self.assertRaises(ValueError):self.service.case_details('fixture','save',{'amount':''},saved['revision'],'合成核对',True)
        self.assertEqual(self.service.case_details('fixture')['values']['amount'],'10.00')

    def test_revision_protected_fields_and_invalid_types_rejected(self):
        value=self.service.case_details('fixture')
        for data in [{'serial':'123'},{'amount':10},{'teacher_name':{'invented':True}},{'payments':'bad'}]:
            with self.assertRaises(ValueError):self.service.case_details('fixture','save',data,value['revision'],'合成核对',True)
        with self.assertRaises(ValueError):self.service.case_details('fixture','save',{'amount':'10'},'old','合成核对',True)
        self.assertEqual(self.service.revision('fixture'),value['revision'])


if __name__=='__main__':unittest.main()
