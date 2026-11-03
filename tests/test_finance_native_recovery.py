"""金额、拆分、劳务和流程未知回执均只读取原marker，保留旧字节。"""
from contextlib import nullcontext
from pathlib import Path
import unittest,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from unittest.mock import patch
from assistant_api import atomic_json,read_json

class OriginalNativeRecoveryTests(unittest.TestCase):
    def test_four_owners_preserve_original_receipt_and_never_replay(self):
        import test_finance_native_amount as amount
        import test_finance_native_split as split
        import test_finance_native_remuneration as remuneration
        import test_finance_native_workflow as workflow
        specs=[(amount.NativeAmountTests,amount.n,'amount','save','saved'),(split.NativeSplitTests,split.n,'split','apply','saved'),(remuneration.NativeRemunerationTests,remuneration.n,'remuneration','add','saved'),(workflow.NativeWorkflowTests,workflow.n,'workflow','next','staged')]
        for cls,owner,prefix,job_name,outcome in specs:
            with self.subTest(owner=prefix):
                t=cls();t.setUp();self.addCleanup(t.doCleanups)
                if prefix=='amount':t.save_read()
                elif prefix=='split':t.job('apply','apply')
                elif prefix=='remuneration':t.added()
                else:t.next_read()
                pending=read_json(t.pending);v=pending['verification'];input_path=Path(v['input']['path']);original=Path(v['save_receipt']);envelope=read_json(original);body=envelope['result'];envelope.update(phase='launch_returned',result_marker='reimbursement-result-'+'a'*32);atomic_json(original,envelope);raw=original.read_bytes();folder=t.root/('recovered-'+prefix);folder.mkdir();target=folder/'output.json'
                original_writes=len(t.steps)if prefix=='amount'else len(t.calls)if prefix=='split'else len(t.writes)
                with patch('edge_runtime.read_marker',return_value=body)as marker,patch('edge_runtime.browser_lock',return_value=nullcontext()),patch('finance_backend.assert_original_transport'),patch.object(owner,'browser',side_effect=t.runtime):
                    self.assertEqual(owner.run(input_path,target,recover=True),0)
                self.assertEqual(original.read_bytes(),raw);marker.assert_called_once();self.assertTrue(t.pending.exists());self.assertTrue(owner.verify_ack(pending,read_json(target),outcome)['independent_http_readback'])
                if prefix in {'remuneration','workflow'}:self.assertEqual(len(t.writes),original_writes)
                elif prefix=='amount':self.assertEqual(t.steps.count('reimbursementAmountSave'),1)
                else:self.assertEqual(t.calls.count('reimbursementSplitApply'),1)

if __name__=='__main__':unittest.main()
