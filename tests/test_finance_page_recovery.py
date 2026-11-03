"""合成页状态验收；不启动浏览器、不访问学校。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_page_recovery import recover_native_page

class PageRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.before={'tab_id_namespace':'ns','tabs':[{'tab_id':'11','window_id':'1','path':'https://cwzh.ustc.edu.cn/YBX/main.jsp','active':False,'frozen':True,'discarded':False}],
                     'windows':[{'window_id':'1','active_tab_id':'10'}]}
        self.after={**self.before,'tabs':[{**self.before['tabs'][0],'window_id':'2','frozen':False}]}
        self.result={'window_id':'2','woken':True}
        self.addCleanup(patch.stopall)
        patch('finance_session.SESSIONS',self.root/'sessions').start()
        patch('finance_backend.mac_target',return_value={'tab_id_namespace':'ns','expected_window_id':'1'}).start()
        self.inventory=patch('mac_edge_bridge.inventory',side_effect=[self.before,self.after]).start()
        self.move=patch('mac_edge_bridge.relocate_finance_tab',return_value=self.result).start()
        self.wake=patch('mac_edge_bridge.wake_finance_tab',return_value=self.result).start()

    def run_recovery(self,name='out.json'):
        return recover_native_page('fixture','stable-request','11',self.root/name)

    def test_mixed_page_moves_once_and_duplicate_intent_does_not_mutate(self):
        value=self.run_recovery();self.assertTrue(value['ok']);self.assertEqual(value['state'],'original_page_recovered')
        self.assertEqual(self.move.call_args.kwargs['expected_active_tab_id'],'10');self.wake.assert_not_called()
        repeated=self.run_recovery('repeated.json');self.assertTrue(repeated['reused_original_intent'])
        self.move.assert_called_once();self.assertEqual(self.inventory.call_count,2)

    def test_unknown_move_preserves_intent_and_does_not_retry(self):
        self.move.side_effect=TimeoutError('unknown')
        self.assertEqual(self.run_recovery()['state'],'recovery_unknown')
        self.assertEqual(self.run_recovery('again.json')['state'],'recovery_unknown');self.move.assert_called_once()

    def test_moved_window_persisted_when_followup_read_fails(self):
        self.inventory.side_effect=[self.before,TimeoutError('read failed')]
        value=self.run_recovery();self.assertEqual(value['window_id'],'2');self.assertFalse(value['ok'])
        cache=next((self.root/'sessions'/'fixture').glob('wake-native-*'))
        self.assertEqual(json.loads(cache.read_text())['window_id'],'2')

    def test_discarded_page_is_not_reloaded(self):
        self.before['tabs'][0]['discarded']=True
        self.assertEqual(self.run_recovery()['state'],'document_discarded');self.move.assert_not_called();self.wake.assert_not_called()

    def test_awake_page_is_noop(self):
        self.before['tabs'][0]['frozen']=False
        self.assertEqual(self.run_recovery()['state'],'already_awake');self.move.assert_not_called();self.wake.assert_not_called()

    def test_namespace_change_prevents_action(self):
        self.before['tab_id_namespace']='other'
        with self.assertRaises(ValueError):self.run_recovery()
        self.move.assert_not_called();self.wake.assert_not_called()

    def test_active_page_wakes_without_relocation(self):
        self.before['tabs'][0]['active']=True
        self.assertTrue(self.run_recovery()['ok']);self.wake.assert_called_once();self.move.assert_not_called()

    def test_completed_wake_failure_rechecks_then_relocates_original_once(self):
        self.before['tabs'][0]['active']=True
        self.wake.side_effect=RuntimeError('Mac bridge: frozen_tab_not_woken')
        self.inventory.side_effect=[self.before,self.before,self.after]
        value=self.run_recovery();self.assertTrue(value['ok']);self.move.assert_called_once()
        self.assertIn('completed_wake_failure',value);self.assertFalse(value['business_replayed'])
        self.run_recovery('again.json');self.move.assert_called_once()

    def test_unknown_wake_or_changed_document_never_falls_back(self):
        self.before['tabs'][0]['active']=True;self.wake.side_effect=TimeoutError('unknown wake')
        self.assertFalse(self.run_recovery()['ok']);self.move.assert_not_called()

if __name__=='__main__':unittest.main()
