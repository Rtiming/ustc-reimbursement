import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from submission import verify_next_still_draft

class NextDraftRecoveryTests(unittest.TestCase):
    def test_same_draft_is_required_and_no_workflow_or_input_drift_is_accepted(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);source=root/'native.js';source.write_text('frozen original native action')
            plan={'case_id':'test_case','serial':'123','amount':'12.00','summary':'合成活动','phone':'12345678901','submit_authorized':True,'source':'user','contract_exists':False,'delegate_finance_print':False,'expected_userid':'TEST01'}
            form={'case_id':'test_case','serial':'123','amount':'12.00','contract':{'exists':False},'basic':{'fields':{'t-abst':'合成活动','t-req_mobile':'12345678901'}}}
            original={'phase':'script_completed','script':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'executed_at_utc':'2026-01-01T00:00:00+00:00','result':{'ok':True,'action':'submission_next','serial':'123'}}
            for name,value in [('plan.json',plan),('original.json',original)]: (root/name).write_text(json.dumps(value))
            previous={'operation':'submission:next','verification':{'save_receipt':str(root/'original.json'),'submission_plan':str(root/'plan.json')}}
            row={'t.serial_no':'123','t.status':'-99','t.status_name':'草稿','t.REQ_INFO':'TEST01-合成人','t.abst':'合成活动','t.total_amt':'12.00'}
            read={'phase':'script_completed','executed_at_utc':'2026-01-01T00:01:00+00:00','result':{'ok':True,'projid':'WF_YBX','scopeComplete':True,'serial':'','visibleDialogs':[],'identity':{'userid':'TEST01'},'grids':[{'funcno':'101','records':1,'rows':[row]}]}}
            self.assertFalse(verify_next_still_draft(previous,read,form)['workflow_started'])
            for change in [{'t.status':'1'},{'t.node_id':'508'},{'t.req_no':'FLOW1'},{'t.total_amt':'13.00'},{'t.REQ_INFO':'OTHER-合成人'},{'t.abst':'别的活动'}]:
                bad=copy.deepcopy(read);bad['result']['grids'][0]['rows'][0].update(change)
                with self.assertRaises(ValueError):verify_next_still_draft(previous,bad,form)
            bad=copy.deepcopy(read);bad['result']['grids'][0]['records']=2
            with self.assertRaises(ValueError):verify_next_still_draft(previous,bad,form)
            with self.assertRaises(ValueError):verify_next_still_draft(previous,read,{**form,'review':{'workflow_started':True}})

if __name__=='__main__':unittest.main()
