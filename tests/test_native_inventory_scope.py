import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_invoice_reconcile import grid_scope

class NativeInventoryScopeTests(unittest.TestCase):
    def receipt(self):
        return {'phase':'script_completed','result':{'ok':True,'business_transport':'native_http',
            'mode':'native_http_full_grid','complete':True,'scopeComplete':True,'page_context_modified':False,
            'projid':'WF_YBX','identity':{'userid':'TEST001'},'observed_at':'2026-10-29T00:00:00Z',
            'grids':[{'funcno':'113','rows':[],'records':0,'rowCount':0,'complete':True,
                      'allRecordsVisible':False,'display_evidence':False,'loading':False}]}}
    def test_http_data_is_complete_without_fabricating_display_evidence(self):
        r=self.receipt();_,g,scope=grid_scope(r,'113','TEST001')
        self.assertTrue(scope['complete']);self.assertFalse(g['allRecordsVisible'])
        self.assertEqual(scope['observed_at'],'2026-10-29T00:00:00Z')
    def test_missing_completion_or_unknown_mode_cannot_bypass_legacy_contract(self):
        for change in ({'complete':False},{'scopeComplete':False},{'page_context_modified':True},{'mode':'invented'}):
            r=self.receipt();r['result'].update(change)
            self.assertFalse(grid_scope(r,'113','TEST001')[2]['complete'])
        r=self.receipt();r['result']['grids'][0]['records']=1
        self.assertFalse(grid_scope(r,'113','TEST001')[2]['complete'])
    def test_actor_mismatch_and_duplicate_grid_are_rejected(self):
        with self.assertRaises(ValueError):grid_scope(self.receipt(),'113','OTHER')
        r=self.receipt();r['result']['grids']*=2
        with self.assertRaises(ValueError):grid_scope(r,'113','TEST001')
