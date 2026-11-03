"""整单暂缓必须阻止局部费用继续做单，原事实读取和撤销仍可执行。"""
import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,APIError,atomic_json,read_json
from operator_support import active_claim_holds,claim_hold_blocks,check

class WholeClaimHoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.service=AssistantService(self.root,[self.root]);self.service.new_case('held_case')
        self.bundle,self.paths=self.service.resolve('held_case');case=read_json(self.paths['case'])
        case['decisions']=[{'status':'held_by_user','user_text':'问题材料所在整单先不做','source':'synthetic current instruction'}];atomic_json(self.paths['case'],case);self.case=case
    def request(self,op,action,execute=True):
        return{'case_id':'held_case','operation':op,'parameters':{'action':action,'tab_id':'7'},'execute':execute,'request_id':'fixture'}
    def test_every_financial_write_is_blocked_at_command_owner(self):
        for op,action in [('stage','basic'),('invoice','create-draft'),('invoice','domestic-allowance-edit-save'),('invoice','nonvat-save'),('submission','next'),('invoice_selection','apply'),('expense_amount','save'),('correction','apply'),('access','remuneration-create-claim'),('print_batch','print')]:
            with self.subTest(op=op,action=action),self.assertRaises(APIError)as caught:self.service.command(self.request(op,action),self.root/'unused')
            self.assertEqual(caught.exception.code,'claim_on_hold')
        self.assertFalse((self.root/'unused').exists())
    def test_readback_recovery_materials_and_explicit_cancel_are_available(self):
        for op,action,execute in [('business','bills-list',False),('invoice','draft-cancel-prepare',False),('invoice','draft-cancel',True),('invoice','draft-cancel-recover',False),('read_system','recover',False),('record_result','ack-write',False),('material_intake','prepare',True),('access','new-interface',True)]:
            with self.subTest(op=op,action=action):self.assertFalse(claim_hold_blocks(self.request(op,action,execute),self.case))
        with patch('finance_claim_cancel.command',return_value=['fixed-cancel'])as cancel:
            self.assertEqual(self.service.command(self.request('invoice','draft-cancel'),self.root/'cancel'),['fixed-cancel']);cancel.assert_called_once()
    def test_saved_readback_and_good_invoice_subset_do_not_remove_hold(self):
        self.assertTrue(active_claim_holds(self.case))
        result=check(self.bundle)
        self.assertFalse(result['ok']);self.assertTrue(any('整单暂缓'in x['error']for x in result['errors']))
        self.assertTrue(claim_hold_blocks(self.request('invoice','create-draft'),self.case))
        self.case['decisions'][0]['status']='released_by_user'
        self.assertFalse(claim_hold_blocks(self.request('invoice','create-draft'),self.case))
        self.assertTrue(claim_hold_blocks(self.request('invoice','create-draft'),{'decisions':{'status':'held_by_user'}}))

if __name__=='__main__':unittest.main()
