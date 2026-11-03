from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from assistant_api import AssistantService

class MaterialRequirementsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.service=AssistantService(self.root,[self.root]);self.service.new_case('fixture')

    def test_acknowledgement_preserves_every_gap_and_invalidates_after_input_change(self):
        before=self.service.material_requirements('fixture')
        self.assertTrue(before['rows']);self.assertFalse(before['reviewed_current']);self.assertFalse(before['ready_for_material_delivery'])
        after=self.service.material_requirements('fixture','acknowledge',expected_revision=before['revision'],execute=True)
        self.assertTrue(after['reviewed_current']);self.assertFalse(after['ready_for_material_delivery'])
        self.assertEqual(before['rows'],after['rows'])
        self.service.update_case('fixture','case',{'material_context':{'has_hotel':True}},after['revision'])
        self.assertFalse(self.service.material_requirements('fixture')['reviewed_current'])

    def test_context_requires_explicit_known_boolean_basis_and_current_version(self):
        before=self.service.material_requirements('fixture')
        for values in [{'has_hotel':None},{'has_hotel':'false'},{'invented':False}]:
            with self.assertRaises(ValueError):self.service.material_requirements('fixture','context',values,before['revision'],'synthetic source',True)
        with self.assertRaises(ValueError):self.service.material_requirements('fixture','context',{'has_hotel':False},before['revision'],None,True)
        after=self.service.material_requirements('fixture','context',{'has_hotel':False},before['revision'],'synthetic case has no hotel',True)
        self.assertFalse(next(r for r in after['conditions']if r['key']=='has_hotel')['value'])
        with self.assertRaises(ValueError):self.service.material_requirements('fixture','acknowledge',expected_revision=before['revision'],execute=True)

if __name__=='__main__':unittest.main()
