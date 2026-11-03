"""原阶段marker恢复不改旧回执、不重发保存，仅运行独立HTTP读取。"""
from contextlib import nullcontext
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_finance_native_stage as fixture
import finance_native_stage as n
from assistant_api import atomic_json,read_json
from finance_native_create import sha

class StageRecoveryTests(unittest.TestCase):
    def test_original_marker_and_independent_read_only(self):
        t=fixture.NativeStageTests('test_pending_precedes_single_save_and_new_request_cannot_replay');t.setUp();self.addCleanup(t.doCleanups);t.execute();pending=read_json(t.pending)
        old=read_json(t.output);old.update(phase='launch_returned',script=str(t.output.with_suffix('.js')),source_sha256=sha(t.output.with_suffix('.js')),tab_id='7',result_marker='reimbursement-result-'+'a'*32);atomic_json(t.output,old);raw=t.output.read_bytes()
        target=t.root/'recovery'/'output.json';target.parent.mkdir();saved={'ok':True,'action':'basic-save','case_id':'fixture','serial':'123','expected_userid':'TEST001'}
        def runtime(args):
            self.assertEqual(args[args.index('--operation-kind')+1],'read');self.assertIn('reimbursementBusinessApi',Path(args[2]).read_text());value=t.readback();value.update(tab_id='7',script=args[2],source_sha256=sha(args[2]),dispatch_intent_at_utc=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat());atomic_json(target,value);return SimpleNamespace(returncode=0)
        with patch('edge_runtime.read_marker',return_value=saved)as marker,patch('edge_runtime.browser_lock',return_value=nullcontext()),patch('finance_backend.assert_original_transport'),patch('registered_page_owner.RegisteredPageOwner.before_child_dispatch'),patch.object(n.subprocess,'run',side_effect=runtime):
            self.assertEqual(n.run(t.input,target,recover=True),0)
        self.assertEqual(t.output.read_bytes(),raw);marker.assert_called_once();self.assertTrue(t.pending.exists());self.assertTrue(n.verify_ack(t.paths,pending,read_json(target),'saved')['independent_http_readback'])

if __name__=='__main__':unittest.main()
