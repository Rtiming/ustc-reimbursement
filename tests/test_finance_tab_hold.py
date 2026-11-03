"""显式后台标签保持复用原守卫；默认路径和原只读marker恢复边界不变。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import finance_invoice_verification as verification
import finance_upload_batch_run as upload


class FinanceTabHoldTests(unittest.TestCase):
    def test_recovery_holds_only_requested_tab_and_reads_original_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prior=root/'prior.json';output=root/'recovered.json'
            marker='reimbursement-result-'+'a'*32
            prior.write_text(json.dumps({'tab_id':'123','result':{'result_marker':marker}}))
            active=[]
            @contextlib.contextmanager
            def hold(tab,path):
                self.assertEqual(tab,'123');active.append(True)
                try:yield
                finally:active.pop()
            def read(source,tab):
                self.assertTrue(active);self.assertEqual(tab,'123');self.assertIn(marker,source)
                self.assertNotIn('appendChild',source);return '{"ok":true}'
            with patch.object(sys,'argv',['verify','recover','--tab-id','123','--output',str(output),'--receipt',str(prior),'--keep-internal-tab']), \
                    patch('finance_session.quiet_tab_context',side_effect=hold) as held, \
                    patch('edge_runtime.browser_lock',return_value=contextlib.nullcontext()),patch('edge_read_probe.evaluate',side_effect=read),contextlib.redirect_stdout(io.StringIO()):
                verification.main()
            result=json.loads(output.read_text());self.assertEqual(result['marker_state'],'completed')
            self.assertFalse(result['reinjected']);self.assertFalse(active);held.assert_called_once()

    def test_batch_holding_is_explicit_and_existing_output_stops_before_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=root/'manifest.json';folder=root/'run';manifest.write_text('{}')
            args=['batch','--manifest',str(manifest),'--tab-id','123','--output-dir',str(folder)]
            with patch.object(sys,'argv',args),patch.object(upload,'run_batch') as run,patch('finance_session.quiet_tab_context') as hold:
                self.assertEqual(upload.main(),0);run.assert_called_once();hold.assert_not_called()
            active=[]
            @contextlib.contextmanager
            def held(tab,path):
                active.append(True)
                try:yield
                finally:active.pop()
            with patch.object(sys,'argv',args+['--keep-internal-tab']),patch('finance_session.quiet_tab_context',side_effect=held), \
                    patch.object(upload,'run_batch',side_effect=lambda *args:self.assertTrue(active)):
                self.assertEqual(upload.main(),0)
            self.assertFalse(active);folder.mkdir()
            with patch.object(sys,'argv',args+['--keep-internal-tab']),patch('finance_session.quiet_tab_context') as hold,patch.object(upload,'run_batch') as run,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(upload.main(),1);hold.assert_not_called();run.assert_not_called()


if __name__=='__main__':unittest.main()
