"""无变更实机验收意图不能变成真实金额或人员字段保存。"""
import unittest
import test_finance_native_stage as fixture
from assistant_api import read_json,atomic_json
import finance_native_stage as n

class NoopValidationTests(unittest.TestCase):
    def fixture(self):
        t=fixture.NativeStageTests('test_pending_precedes_single_save_and_new_request_cannot_replay');t.setUp();self.addCleanup(t.doCleanups);t.request['parameters']['noop_only']=True;return t
    def test_required_change_rejected_before_pending_and_save(self):
        t=self.fixture()
        with self.assertRaisesRegex(ValueError,'仅核无变更'):t.execute()
        self.assertFalse(t.pending.exists());self.assertEqual(len(t.calls),1)
    def test_non_boolean_rejected(self):
        t=self.fixture();t.request['parameters']['noop_only']='true'
        with self.assertRaises(ValueError):t.execute()
        self.assertFalse(t.pending.exists());self.assertEqual(t.calls,[])
    def test_owner_reported_real_dispatch_cannot_be_acknowledged_as_noop(self):
        t=self.fixture();t.prepare['original']['t.abst']='New';t.prepare['changes']={};t.execute();p=read_json(t.pending)
        with self.assertRaisesRegex(ValueError,'无变更验收'):n.verify_ack(t.paths,p,t.readback(),'saved')
        self.assertTrue(t.pending.exists());save=read_json(t.output);save['result'].update(save_dispatched=False,state='already_matches_readback_required');atomic_json(t.output,save)
        self.assertTrue(n.verify_ack(t.paths,p,t.readback(),'saved')['independent_http_readback'])

if __name__=='__main__':unittest.main()
