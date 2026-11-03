"""新界面/失效会话恢复：只用合成原生桥，拒绝重复创建和未知写入迁移。"""
import copy
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import APIError,atomic_json,read_json
from finance_page_open import open_page
from registered_session import RegisteredSession


def inventory(new=False,namespace='namespace'):
    result={'tab_id_namespace':namespace,'frontmost_bundle_id':None,'frontmost_process_id':None,
        'windows':[{'window_id':'8','active_tab_id':'7'}],
        'tabs':[{'tab_id':'7','window_id':'8','path':'https://cwzh.ustc.edu.cn/YBX/main.jsp'}]}
    if new:
        result['windows'].append({'window_id':'10','active_tab_id':'9'})
        result['tabs'].append({'tab_id':'9','window_id':'10','path':'https://id.ustc.edu.cn/cas/login'})
    return result


class NativeInterfaceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        lock=patch('edge_runtime.LOCK_PATH',self.root/'fixture-browser.lock');lock.start();self.addCleanup(lock.stop)
        p=patch('finance_session.SESSIONS',self.root/'sessions');p.start();self.addCleanup(p.stop)
        self.transport={'backend':'edge_native_messaging','tab_id_namespace':'namespace','window_id':'8','platform':'darwin'}
        self.opened={'tab_id':'9','window_id':'10','window_created':True,'window_focus_requested':False}

    def open(self,name,**kwargs):
        return open_page('fixture','stable','7',self.root/name,wait=0,silent=True,separate_window=True,**kwargs)

    def test_public_native_entry_preserves_old_page_and_recovers_without_reopening(self):
        with patch('finance_backend.mac_native_enabled',return_value=True),patch('finance_backend.transport_identity',return_value=self.transport), \
                patch('finance_backend.assert_original_transport'),patch('registered_bridge.inventory',side_effect=[inventory(),inventory(True),inventory(True)]), \
                patch('registered_bridge.new_finance_window',return_value=self.opened)as create:
            r=self.open('created.json');self.assertTrue(r['ok']);self.assertEqual(r['new_tab_id'],'9')
            self.assertIsNone(r['frontmost_preserved']);self.assertIsNone(r['quiet_verified_at_observation_points'])
            self.assertTrue(r['old_finance_tabs_present']);self.assertFalse(r['business_replayed'])
            r=self.open('recovered.json');self.assertTrue(r['ok']);self.assertFalse(r['reopened'])
            self.assertEqual(create.call_count,1)

    def test_unknown_creation_keeps_original_intent_and_never_adopts_user_tab(self):
        with patch('finance_backend.mac_native_enabled',return_value=True),patch('finance_backend.transport_identity',return_value=self.transport), \
                patch('finance_backend.assert_original_transport'),patch('registered_bridge.inventory',side_effect=[inventory(),inventory(True)]), \
                patch('registered_bridge.new_finance_window',side_effect=TimeoutError('unknown'))as create:
            self.assertFalse(self.open('unknown.json')['ok'])
            r=self.open('read-original.json');self.assertFalse(r['page_opened']);self.assertEqual(create.call_count,1)

    def test_wrong_namespace_or_window_cannot_bind_new_page(self):
        for mode in ['epoch','window']:
            observed=inventory(True)
            if mode=='epoch':observed['tab_id_namespace']='other'
            else:observed['tabs'][-1]['window_id']='11'
            with tempfile.TemporaryDirectory()as tmp,patch('finance_session.SESSIONS',Path(tmp)/'sessions'), \
                    patch('finance_backend.mac_native_enabled',return_value=True),patch('finance_backend.transport_identity',return_value=self.transport), \
                    patch('registered_bridge.inventory',side_effect=[inventory(),observed]),patch('registered_bridge.new_finance_window',return_value=self.opened):
                self.assertFalse(self.open(mode+'.json')['ok'])

    def test_same_window_entry_never_closes_old_tab(self):
        after=inventory()
        after['tabs'].append({'tab_id':'9','window_id':'8','path':'https://id.ustc.edu.cn/cas/login'})
        with patch('finance_backend.mac_native_enabled',return_value=True),patch('finance_backend.transport_identity',return_value=self.transport), \
                patch('registered_bridge.inventory',side_effect=[inventory(),after]), \
                patch('registered_bridge.reopen_page',return_value={'new_tab_id':'9','silent':True})as create:
            r=open_page('fixture','same-window','7',self.root/'same-window.json',wait=0,silent=True)
        self.assertTrue(r['ok']);create.assert_called_once_with(window_id='8',silent=True)


class BackendReentryTests(unittest.TestCase):
    def credential_failure(self):
        proof=self.root/'credential-reentry.json'
        atomic_json(proof,{'phase':'reentry_incomplete','new_tab_id':'7','new_window_id':'8','namespace':'namespace','login_request_id':'original-login','business_replayed':False})
        self.session.state['backend_reentry_receipt']=str(proof);atomic_json(self.session.state_path,self.session.state)
        folder=self.root/'.local/assistant_jobs/fixture/j_original';folder.mkdir(parents=True)
        atomic_json(folder/'request.json',{'case_id':'fixture','operation':'access','parameters':{'tab_id':'7'},'execute':True,'request_id':'original-login'})
        atomic_json(folder/'output.auth.json',{'schema':'ustc-mac-credential-use-v1','phase':'credential_use_unconfirmed','state':'credential_use_failed',
            'case_id':'fixture','request_id':'original-login','tab_id':'7','login_submitted':None,'config_sha256':'fixed-config','browser_transport':{}})
        self.session.service.job_folder=Mock(return_value=folder);self.session.service.result=Mock(return_value={'status':'failed','exit_code':2})
        return proof,folder

    def test_provider_failure_resumes_once_on_same_page_and_retains_original(self):
        proof,folder=self.credential_failure();original=(folder/'output.auth.json').read_bytes()
        page={'state':'login_required','path':'https://id.ustc.edu.cn/cas/login','password_ready':False}
        with patch('finance_backend.assert_original_transport'),patch('windows_finance_auth.configuration',return_value=({},'fixed-config')), \
                patch('finance_access.snapshot',return_value=page),patch.object(self.session,'ensure',return_value=self.session.state)as ensure:
            state=self.session.resume_backend_login('j_original')
        self.assertEqual(state['tab_id'],'7');self.assertFalse(ensure.call_args.kwargs['_allow_reentry'])
        self.assertTrue(ensure.call_args.kwargs['_access_request_id'].startswith('credential-login-resume-'))
        self.assertEqual((folder/'output.auth.json').read_bytes(),original);record=read_json(proof)
        self.assertEqual(record['phase'],'verified');recovery=read_json(Path(record['credential_login_recovery']['path']))
        self.assertFalse(recovery['new_interface_created']);self.assertFalse(recovery['financial_business_replayed'])

    def test_claimed_unknown_or_still_running_auth_cannot_resume(self):
        _,folder=self.credential_failure()
        for mode in ['claim','submitted','running','financial_pending']:
            auth=read_json(folder/'output.auth.json');auth['login_submitted']=True if mode=='submitted'else None;atomic_json(folder/'output.auth.json',auth)
            claim=folder/'output.auth.json.claim'
            if mode=='claim':atomic_json(claim,{'consumer_pid':123})
            else:claim.unlink(missing_ok=True)
            self.session.service.result.return_value={'status':'running'if mode=='running'else'failed'}
            self.session.service.overview.return_value={'unverified_write':{'operation':'step:payments'}}if mode=='financial_pending'else{}
            with patch('finance_backend.assert_original_transport'),patch('windows_finance_auth.configuration',return_value=({},'fixed-config')),patch.object(self.session,'ensure')as ensure:
                with self.subTest(mode=mode),self.assertRaises(APIError):self.session.resume_backend_login('j_original')
                ensure.assert_not_called()

    def test_provider_failure_accepts_only_proved_owned_view_relocation(self):
        _,folder=self.credential_failure();auth=read_json(folder/'output.auth.json')
        auth['browser_transport']={'backend':'edge_native_messaging','tab_id_namespace':'namespace','window_id':'8','platform':'darwin'};atomic_json(folder/'output.auth.json',auth)
        moved=self.session.folder/'page-relocation-1.json';atomic_json(moved,{'old_window_id':'8','tab_id':'7','namespace':'namespace','result':{'window_id':'10','woken':True}})
        self.session.state.update(window_id='10',last_relocation_receipt=str(moved));atomic_json(self.session.state_path,self.session.state)
        page={'state':'login_required','path':'https://id.ustc.edu.cn/cas/login','password_ready':False}
        with patch('finance_backend.transport_identity',return_value={**auth['browser_transport'],'window_id':'10'}), \
                patch('windows_finance_auth.configuration',return_value=({},'fixed-config')),patch('finance_access.snapshot',return_value=page), \
                patch.object(self.session,'ensure',return_value=self.session.state):
            result=self.session.resume_backend_login('j_original')
        self.assertEqual(result['window_id'],'10')

    def test_credential_recovery_checks_every_hop_of_original_owned_page(self):
        _,folder=self.credential_failure();auth=read_json(folder/'output.auth.json')
        auth['browser_transport']={'backend':'edge_native_messaging','tab_id_namespace':'namespace','window_id':'8'};atomic_json(folder/'output.auth.json',auth)
        first=self.session.folder/'page-relocation-1.json';last=self.session.folder/'page-relocation-2.json'
        atomic_json(first,{'old_window_id':'8','tab_id':'7','namespace':'namespace','result':{'window_id':'10','woken':True}})
        atomic_json(last,{'old_window_id':'10','tab_id':'7','namespace':'namespace','result':{'window_id':'12','woken':True}})
        self.session.state.update(window_id='12',last_relocation_receipt=str(last))
        page={'state':'login_required','path':'https://id.ustc.edu.cn/cas/login','password_ready':False}
        with patch('finance_backend.transport_identity',return_value={**auth['browser_transport'],'window_id':'12'}),patch('windows_finance_auth.configuration',return_value=({},'fixed-config')),patch('finance_access.snapshot',return_value=page),patch.object(self.session,'ensure',return_value=self.session.state):
            first.unlink()
            with self.assertRaises(APIError):self.session.resume_backend_login('j_original')
            atomic_json(first,{'old_window_id':'8','tab_id':'7','namespace':'namespace','result':{'window_id':'10','woken':True}})
            self.assertEqual(self.session.resume_backend_login('j_original')['window_id'],'12')

    def test_failed_credential_recovery_retains_intent_and_never_loops(self):
        _,folder=self.credential_failure();page={'state':'login_required','path':'https://id.ustc.edu.cn/cas/login','password_ready':False}
        with patch('finance_backend.assert_original_transport'),patch('windows_finance_auth.configuration',return_value=({},'fixed-config')),patch('finance_access.snapshot',return_value=page), \
                patch.object(self.session,'ensure',side_effect=APIError('credential_unavailable','provider unavailable'))as ensure:
            with self.assertRaises(APIError):self.session.resume_backend_login('j_original')
            with self.assertRaisesRegex(APIError,'只查原任务'):self.session.resume_backend_login('j_original')
        self.assertEqual(ensure.call_count,1);self.assertEqual(read_json(Path(self.session.state['backend_reentry_receipt']))['phase'],'reentry_incomplete')

    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        lock=patch('edge_runtime.LOCK_PATH',self.root/'fixture-browser.lock');lock.start();self.addCleanup(lock.stop)
        index=self.root/'index.json';atomic_json(index,{'documents':[{'case_id':'fixture'}]})
        service=SimpleNamespace(root=self.root,overview=Mock(return_value={}))
        self.session=RegisteredSession(service,'batch',{},index,None)
        self.session.state={'case_id':'batch','tab_id':'7','window_id':'8','namespace':'namespace',
            'last_case_id':'fixture','created_by_registered_workflow':True,'owned_tab_ids':['7']}
        atomic_json(self.session.state_path,self.session.state)
        self.reply={'ok':True,'page_opened':True,'new_tab_id':'9','native_created_window_id':'10'}

    def job(self,*args,**kwargs):
        job={'job_id':'original-open'};kwargs['on_enqueued'](job);return job,self.reply

    def test_backend_reentry_uses_formal_public_entry_and_independent_verification(self):
        original=copy.deepcopy(self.session.state)
        with patch.object(self.session,'job',side_effect=self.job)as job, \
                patch.object(self.session,'ensure',side_effect=lambda **kwargs:self.session.state)as ensure:
            self.session.recover_backend_session('fixture',{'state':'native_backend_unavailable','http_status':404})
        call=job.call_args;self.assertEqual(call.args[1],'access');self.assertEqual(call.args[2],{
            'action':'new-interface','tab_id':'7','silent':True,'separate_window':True})
        self.assertTrue(call.args[4]);self.assertFalse(ensure.call_args.kwargs['_allow_reentry'])
        state=read_json(self.session.state_path);self.assertEqual(state['tab_id'],'9');self.assertEqual(state['previous_workpage'],original)
        record=read_json(Path(state['backend_reentry_receipt']));self.assertEqual(record['phase'],'verified');self.assertFalse(record['business_replayed'])

    def test_unknown_financial_write_blocks_automatic_page_replacement(self):
        self.session.service.overview.return_value={'unverified_write':{'operation':'step:payments'}}
        with patch.object(self.session,'job')as job,self.assertRaisesRegex(APIError,'未知财务写入'):
            self.session.recover_backend_session('fixture',{'http_status':404})
        job.assert_not_called();self.assertEqual(self.session.state['tab_id'],'7')

    def test_failed_new_login_retains_page_and_stable_intent_and_does_not_loop(self):
        with patch.object(self.session,'job',side_effect=self.job),patch.object(self.session,'ensure',side_effect=APIError('login_pending','pending')):
            with self.assertRaises(APIError):self.session.recover_backend_session('fixture',{'http_status':404})
        state=read_json(self.session.state_path);self.assertEqual(state['tab_id'],'9')
        self.assertEqual(read_json(Path(state['backend_reentry_receipt']))['phase'],'reentry_incomplete')
        with patch.object(self.session,'job')as job,self.assertRaisesRegex(APIError,'不循环开页'):
            self.session.recover_backend_session('fixture',{'http_status':404})
        job.assert_not_called()

    def test_ensure_reenters_on_backend_auth_status_but_preserves_original_when_required(self):
        self.session.cfg['expected_userid']='TEST001'
        bridge={'tab_id_namespace':'namespace'}
        for status in [401,403,404]:
            health={'state':'native_backend_unavailable','http_status':status}
            for preserve in [False,True]:
                with patch('sys.platform','darwin'),patch.object(self.session,'configure_backend',return_value=bridge), \
                        patch('registered_bridge.inventory',return_value=inventory()), \
                        patch.object(self.session,'job',side_effect=[({'messages':[]},{'state':'ready'}),({},health)]), \
                        patch.object(self.session,'recover_backend_session',return_value={'fresh':True})as recover:
                    if preserve:
                        with self.assertRaisesRegex(APIError,'原生YBX后端或账号未核实'):
                            self.session.ensure(preserve_original=True)
                        recover.assert_not_called()
                    else:
                        self.assertEqual(self.session.ensure(),{'fresh':True})
                        recover.assert_called_once_with('fixture',health)

    def test_verified_login_is_checked_again_and_latest_ownership_is_retained(self):
        self.session.cfg['expected_userid']='TEST001'
        form=self.root/'form.json';atomic_json(form,{'serial':'10001'})
        self.session.service.resolve=Mock(return_value=(None,{'form':form}))
        proof=self.root/'reentry.json';atomic_json(proof,{'phase':'verified','login_request_id':'old-login'})
        self.session.state['backend_reentry_receipt']=str(proof);atomic_json(self.session.state_path,self.session.state)
        def job(case,operation,parameters,*args,**kwargs):
            if parameters['action']=='ensure':return {'messages':[]},{'state':'ready'}
            self.session.state={**self.session.state,'relocation_chain':'latest-owned-proof'}
            return {},{'state':'native_backend_ready','identity_verified':True,'current_serial_hint':'10001'}
        with patch('sys.platform','darwin'),patch.object(self.session,'configure_backend',return_value={'tab_id_namespace':'namespace'}), \
                patch('registered_bridge.inventory',return_value=inventory()),patch.object(self.session,'job',side_effect=job)as calls:
            state=self.session.ensure()
        self.assertIsNone(calls.call_args_list[0].kwargs['request_id'])
        self.assertEqual(state['phase'],'ready');self.assertEqual(state['relocation_chain'],'latest-owned-proof')


if __name__=='__main__':unittest.main()
