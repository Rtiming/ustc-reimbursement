"""Mac transport contracts use local fixtures/mocks; no browser or finance UI."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import edge_native_host as host
import mac_edge_bridge as mac
import windows_edge_bridge as shared

EXT='a'*32;EPOCH='b'*32;NS='edge-native-messaging:'+EXT+':'+EPOCH


class MacBridgeTests(unittest.TestCase):
    def test_relocation_retains_new_window_even_if_renderer_stays_frozen(self):
        result={'tab_id':'11','old_window_id':'1','window_id':'2','relocated':True,'woken':False,
                'foreground_requested':False,'navigation_requested':False,'business_replayed':False}
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value=result) as send:
            self.assertEqual(mac.relocate_finance_tab('11',tab_id_namespace=NS,expected_window_id='1')['window_id'],'2')
            self.assertEqual(send.call_args.args[0],'relocate-finance-tab')
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value={**result,'tab_id':'12'}),self.assertRaises(mac.BridgeUnavailable):
            mac.relocate_finance_tab('11',tab_id_namespace=NS,expected_window_id='1')

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.addCleanup(patch.stopall);patch('sys.platform','darwin').start();patch.object(mac,'STATE',self.root).start()
        self.current={'ready':True,'epoch':EPOCH,'extension_id':EXT,'tab_id_namespace':NS}

    def install_health(self,**extra):
        shared.atomic_private(self.root/'installation.json',{'extension_id':EXT,'build_sha256':'build'})
        shared.atomic_private(self.root/'status.json',{'connected':True,'host_pid':os.getpid(),'heartbeat':time.time(),
            'extension_id':EXT,'build_sha256':'build','epoch':EPOCH,**extra})

    def test_local_status_never_creates_or_queries_browser(self):
        with patch.object(shared,'request',side_effect=AssertionError('no browser')):
            self.assertFalse(mac.status()['ready'])
        self.assertEqual(list(self.root.iterdir()),[])
        self.install_health();value=mac.status();self.assertTrue(value['ready']);self.assertEqual(value['tab_id_namespace'],NS)
        self.assertEqual(value['foreground_state'],'unknown');self.assertFalse(value['lock_screen_operation_verified'])
        self.install_health(synthetic_profile=True);self.assertFalse(mac.status()['ready']);self.assertTrue(mac.status()['synthetic_test_connected'])

    def test_namespace_window_and_backend_platform_required_before_queue(self):
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request') as send:
            for namespace,window in [(None,'1'),('edge_applescript','1'),(NS.replace(EPOCH,'c'*32),'1'),(NS,None),(NS,'window')]:
                with self.subTest(namespace=namespace,window=window),self.assertRaises((ValueError,mac.BridgeUnavailable)):
                    mac.evaluate('fixture','11',tab_id_namespace=namespace,expected_window_id=window)
            send.assert_not_called()
        with patch('sys.platform','linux'),self.assertRaises(mac.BridgeUnavailable):mac.status()

    def test_target_and_epoch_are_forwarded_without_foreground_or_fallback(self):
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value='fixture') as send:
            self.assertEqual(mac.evaluate('SOURCE','11',tab_id_namespace=NS,expected_window_id='1',timeout=.5),'fixture')
            send.assert_called_once_with('evaluate',{'tab_id':'11','expected_window_id':'1','source':'SOURCE','scope':'finance','expected_epoch':EPOCH},timeout=.5,state=self.root)
            with self.assertRaises(mac.BridgeUnavailable):mac.validate_tab('11',True,tab_id_namespace=NS,expected_window_id='1')
            self.assertEqual(send.call_count,1)

    def test_validation_checks_native_window_and_returns_namespace(self):
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value={'verified':True,'tab_id':'11','window_id':'1'}):
            self.assertEqual(mac.validate_tab('11',tab_id_namespace=NS,expected_window_id='1')['tab_id_namespace'],NS)
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value={'verified':True,'tab_id':'11','window_id':'2'}),self.assertRaises(mac.BridgeUnavailable):
            mac.validate_tab('11',tab_id_namespace=NS,expected_window_id='1')

    def inventory(self):
        return {'edge_frontmost':False,'windows':[{'window_id':'1','active_tab_id':'99'}],
                'tabs':[{'tab_id':'11','window_id':'1','path':mac.ENTRY}],
                'all_tab_ids':['11','99'],'all_tab_count':2,'tab_inventory_complete':True,
                'tab_inventory_scope':'extension_visible_profile_tabs','window_inventory_scope':'normal'}

    def test_inventory_is_complete_in_explicit_scope_and_list_tabs_has_no_target_namespace(self):
        with patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value=self.inventory()):
            value=mac.inventory();self.assertEqual(value['all_tab_ids'],['11','99']);self.assertEqual(value['tab_id_namespace'],NS)
            self.assertIsNone(value['frontmost_bundle_id']);self.assertIsNone(value['edge_frontmost'])
            self.assertEqual(value['tabs'][0]['tab_id_namespace'],NS);self.assertEqual(mac.list_tabs(access=False),['11'])
        for field,value in [('all_tab_count',1),('all_tab_ids',['11','11']),('tab_inventory_complete',False),('tab_inventory_scope','finance_filtered')]:
            reply=self.inventory();reply[field]=value
            with self.subTest(field=field),patch.object(mac,'status',return_value=self.current),patch.object(shared,'request',return_value=reply),self.assertRaises(mac.BridgeUnavailable):mac.inventory()

    def test_timeout_leaves_one_exact_request_no_appleevent_retry(self):
        self.install_health()
        with self.assertRaisesRegex(TimeoutError,'Mac.*不重新派发'):
            mac.evaluate('fixture','11',tab_id_namespace=NS,expected_window_id='1',timeout=.02)
        paths=list((self.root/'requests').glob('*.json'));self.assertEqual(len(paths),1)
        request=json.loads(paths[0].read_text());self.assertEqual(request['epoch'],EPOCH)
        self.assertEqual(request['arguments']['expected_epoch'],EPOCH);self.assertEqual(request['arguments']['expected_window_id'],'1')

    def test_darwin_host_uses_same_origin_handshake_and_binary_stream_without_msvcrt(self):
        self.install_health()
        stdin=SimpleNamespace(buffer=io.BytesIO());stdout=SimpleNamespace(buffer=io.BytesIO())
        with patch.object(host,'STATE',self.root),patch.object(sys,'argv',['host.py','chrome-extension://'+EXT+'/']), \
             patch.object(host,'os',SimpleNamespace(name='posix',getpid=os.getpid)), \
             patch.object(host,'_startup_diagnostic'), \
             patch.object(sys,'stdin',stdin),patch.object(sys,'stdout',stdout),patch.object(host,'serve') as serve:
            host.main();serve.assert_called_once_with(stdin.buffer,stdout.buffer)
        with patch.object(host,'STATE',self.root),patch.object(sys,'argv',['host.py','chrome-extension://'+'c'*32+'/']), \
             patch.object(host,'os',SimpleNamespace(name='posix',getpid=os.getpid)), \
             patch.object(host,'_startup_diagnostic'),patch.object(host,'serve') as serve:
            with self.assertRaisesRegex(RuntimeError,'unregistered_extension_origin'):host.main()
            serve.assert_not_called()


if __name__=='__main__':unittest.main()
