import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from smoke_reimbursement_mcp import synthetic_activity_values
from activity_form import inspect_input

class PublicMcpFixtureTests(unittest.TestCase):
    def test_inline_activity_is_complete_and_synthetic(self):
        values=synthetic_activity_values()
        self.assertTrue(inspect_input(values)['ok'],inspect_input(values))
        self.assertTrue(values['leader_email'].endswith('@example.invalid'))
