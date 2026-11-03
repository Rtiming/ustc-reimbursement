"""仅合成哨兵凭据；确认复用、授权、无磁盘秘密和实际Windows私有管道。"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
import threading
import struct
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import windows_auth_protocol as protocol
import windows_finance_auth as auth
import windows_auth_pipe as pipes
import windows_edge_bridge as bridge
import edge_native_host

SENTINEL='FAKE_only_secret_书包_+$(not-a-shell-command)_2026'


def packet():
    return {'schema':'ustc-cas-memory-login-v1','case_id':'fixture_case','request_id':'login-01','tab_id':'123',
            'expected_username':'TESTER','username':'TESTER','password':SENTINEL,'authorized':True,'expires_at':time.time()+15}


class AuthProtocolTests(unittest.TestCase):
    def test_canonical_pipe_acl_keeps_exact_private_principals(self):
        user = 'S-1-5-21-123-1001'
        rows = [(0, 0, 0x10000000, sid) for sid in (user, 'S-1-5-18', 'S-1-5-32-544')]
        self.assertTrue(pipes.private_pipe_acl_matches(0x1000, rows, user))
        mapped = [(kind, flags, 0x1f01ff, sid) for kind, flags, mask, sid in rows]
        self.assertTrue(pipes.private_pipe_acl_matches(0x1000, mapped, user))
        for bad in [rows + [rows[0]], rows[:2], [(0, 0, 0x10000000, 'S-1-1-0')] + rows[1:],
                    [(1, 0, 0x10000000, user)] + rows[1:], [(0, 2, 0x10000000, user)] + rows[1:],
                    [(0, 0, 0x12019f, user)] + rows[1:]]:
            self.assertFalse(pipes.private_pipe_acl_matches(0x1000, bad, user))
        self.assertFalse(pipes.private_pipe_acl_matches(0, rows, user))

    def test_only_authorized_fixed_request_and_secret_free_intent(self):
        value=protocol.validate(packet())
        self.assertNotIn(SENTINEL,json.dumps(protocol.intent(value),ensure_ascii=False))
        for changes in [{'authorized':False},{'username':'OTHER'},{'script':'arbitrary'},{'tab_id':'elsewhere'}]:
            with self.assertRaises(ValueError):protocol.validate({**packet(),**changes})

    def test_response_whitelist_never_returns_page_or_error_secrets(self):
        for response in [{'ok':False,'error':SENTINEL}, {'ok':True,'result':{'state':'login_submitted','login_submitted':True,'password':SENTINEL}},
                         {'ok':True,'result':{'state':SENTINEL,'login_submitted':False}}]:
            value=protocol.sanitize(response)
            self.assertNotIn(SENTINEL,json.dumps(value,ensure_ascii=False))
        self.assertIsNone(protocol.sanitize({'ok':False})['login_submitted'])

    def test_file_bridge_cannot_accept_the_credential_action(self):
        with patch.object(bridge,'atomic_private') as persist:
            with self.assertRaises(ValueError):bridge.request('cas-login',packet())
            persist.assert_not_called()

    def test_cancelled_or_expired_queued_auth_never_gets_a_fresh_dispatch_budget(self):
        for expired in [False,True]:
            value=packet()
            if expired:value['expires_at']=time.time()-.01
            pending=protocol.MemoryRequest(value)
            original_deadline=pending.expires_at
            if not expired:pending.cancel()
            self.assertIsNone(pending.take_for_dispatch())
            self.assertEqual(pending.packet,{})
            self.assertFalse(pending.dispatched)
            self.assertEqual(pending.expires_at,original_deadline)

    @unittest.skipUnless(os.name=='nt','Windows原生管道')
    def test_real_pipe_acl_identity_and_memory_roundtrip(self):
        received=[]
        def consume(value):
            received.append(value['password']==SENTINEL)
            return {'ok':True,'state':'fixture_only'}
        name=r'\\.\pipe\ustc-reimbursement-auth-test-'+uuid.uuid4().hex
        server=pipes.AuthPipeServer(name,consume)
        try:
            self.assertTrue(server.acl_is_private())
            with self.assertRaises(pipes.PipeError):pipes.call(name,os.getpid()+1,packet())
            deadline=time.monotonic()+2
            while not server.ready and time.monotonic()<deadline:time.sleep(.01)
            value=pipes.call(name,os.getpid(),packet())
            self.assertEqual(value,{'ok':True,'state':'fixture_only'})
            self.assertEqual(received,[True])
        finally:server.close()
        self.assertFalse(server.thread.is_alive())

    @unittest.skipUnless(os.name=='nt','Windows原生管道')
    def test_reply_failure_after_callback_never_claims_not_submitted(self):
        name=r'\\.\pipe\ustc-reimbursement-auth-test-'+uuid.uuid4().hex
        original=pipes.write_message;called=[]
        def write(api,handle,value):
            if value.get('state')=='login_submitted':
                raise pipes.PipeError('synthetic_reply_failure')
            return original(api,handle,value)
        def callback(value):
            called.append(True)
            return {'state':'login_submitted','login_submitted':True}
        with patch.object(pipes,'write_message',side_effect=write):
            server=pipes.AuthPipeServer(name,callback)
            try:result=pipes.call(name,os.getpid(),packet())
            finally:server.close()
        self.assertEqual(called,[True]);self.assertEqual(result['state'],'auth_result_unknown')
        self.assertIsNone(result['login_submitted'])

    @unittest.skipUnless(os.name=='nt','Windows命名管道到Native Messaging内存合同')
    def test_native_host_auth_dispatch_is_memory_only_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(edge_native_host,'STATE',Path(directory)):
            root=Path(directory)
            bridge.atomic_private(root/'installation.json',{'extension_id':'fixture','build_sha256':'build'})
            read_fd,write_fd=os.pipe();source=os.fdopen(read_fd,'rb');writer=os.fdopen(write_fd,'wb');destination=io.BytesIO()
            errors=[]
            def serve():
                try:edge_native_host.serve(source,destination)
                except Exception as error:errors.append(type(error).__name__)
            worker=threading.Thread(target=serve,daemon=True);worker.start()
            try:
                edge_native_host.write_frame(writer,{'type':'hello','protocol':1,'extension_id':'fixture','build_sha256':'build','synthetic_profile':False})
                deadline=time.monotonic()+5
                while not (root/'status.json').exists() and time.monotonic()<deadline:time.sleep(.01)
                health=json.loads((root/'status.json').read_text());self.assertIn('auth_pipe_name',health)
                ordinary={'schema':'ustc-native-edge-v1','id':'b'*32,'epoch':health['epoch'],'operation':'inventory','arguments':{},'expires_at':time.time()+10}
                bridge.atomic_private(root/'requests'/('b'*32+'.json'),ordinary)
                deadline=time.monotonic()+5
                while not destination.getvalue() and time.monotonic()<deadline:time.sleep(.01)
                first=edge_native_host.read_frame(io.BytesIO(destination.getvalue()))
                self.assertEqual(first['operation'],'inventory')
                refused=pipes.call(health['auth_pipe_name'],os.getpid(),{**packet(),'request_id':'busy-auth-01'})
                self.assertEqual(refused['state'],'bridge_busy')
                first_length=len(destination.getvalue())
                edge_native_host.write_frame(writer,{'id':first['id'],'ok':True,'result':{'fixture':True}})
                deadline=time.monotonic()+5
                while json.loads((root/'status.json').read_text()).get('active_request_id') and time.monotonic()<deadline:time.sleep(.01)
                time.sleep(.05)
                self.assertEqual(len(destination.getvalue()),first_length,'busy auth must not be queued for later dispatch')
                returned=[]
                auth_request=packet()
                client=threading.Thread(target=lambda:returned.append(pipes.call(health['auth_pipe_name'],os.getpid(),auth_request)),daemon=True);client.start()
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    data=destination.getvalue()[first_length:]
                    if len(data)>=4 and len(data)>=4+struct.unpack('<I',data[:4])[0]:break
                    time.sleep(.01)
                native=edge_native_host.read_frame(io.BytesIO(destination.getvalue()[first_length:]))
                self.assertEqual(native['operation'],'cas-login');self.assertEqual(native['arguments']['password'],SENTINEL)
                self.assertEqual(native['expires_at'],auth_request['expires_at'])
                edge_native_host.write_frame(writer,{'id':native['id'],'ok':True,'result':{'state':'login_submitted','login_submitted':True,'unexpected':SENTINEL}})
                client.join(timeout=5);self.assertFalse(client.is_alive());self.assertEqual(returned[0]['state'],'login_submitted')
                prior_bytes=destination.getvalue()
                repeated=pipes.call(health['auth_pipe_name'],os.getpid(),packet())
                self.assertEqual(repeated['state'],'login_submitted');self.assertEqual(destination.getvalue(),prior_bytes)
                self.assertEqual([p.name for p in (root/'requests').glob('*')],['b'*32+'.json'])
            finally:
                writer.close();worker.join(timeout=5);source.close()
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            for path in root.rglob('*'):
                if path.is_file():
                    self.assertNotIn(SENTINEL.encode('utf-8'),path.read_bytes())
                    self.assertNotIn(json.dumps(SENTINEL).strip('"').encode('ascii'),path.read_bytes())


class AuthConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'data').mkdir()
        self.conf={'username':'TESTER','credential_reference':'fixture.reference','credential_backend':'rtime-credentials','secret_values_in_project':False}
        (self.root/'data/finance_access.json').write_text(json.dumps(self.conf),encoding='utf-8')
        self.output=self.root/'auth.json'
        self.patch=patch.object(auth,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        _,self.digest=auth.configuration()
        self.base={**auth.context('fixture_case','login-01','123'),'phase':'credential_use_intent','config_sha256':self.digest,'login_submitted':None}
        self.bridge={'ready':True,'synthetic_profile':False,'host_pid':os.getpid(),'auth_pipe_name':r'\\.\pipe\ustc-reimbursement-auth-fixture',
                     'auth_transport_alive':True,'auth_pipe_acl_verified':True}

    def no_secret_files(self):
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(SENTINEL.encode('utf-8'),path.read_bytes(),path.name)
                self.assertNotIn(json.dumps(SENTINEL).strip('"').encode('ascii'),path.read_bytes(),path.name)

    def test_existing_session_never_reads_configuration_or_uses_vault(self):
        for state in ['ready','finance_home']:
            with patch('finance_access.snapshot',return_value={'state':state}),patch('edge_runtime.browser_lock',side_effect=contextlib.nullcontext), \
                 patch.object(auth,'configuration',side_effect=AssertionError('must not read vault configuration')),patch.object(auth.subprocess,'run') as child:
                result=auth.use_credentials('fixture_case','login-01','123',self.output)
            self.assertEqual(result['state'],'session_reused');self.assertFalse(result['credential_accessed'])
            child.assert_not_called();self.assertFalse(self.output.exists())

    def test_reference_probe_returns_only_booleans_and_does_not_touch_browser(self):
        with patch.dict(os.environ,{'FINANCE_CAS_USERNAME':'TESTER','FINANCE_CAS_PASSWORD':SENTINEL}),patch.object(auth,'status') as browser:
            result=auth.verify_reference('fixture_case')
        self.assertTrue(result['ok']);self.assertFalse(result['login_submitted']);browser.assert_not_called()
        self.assertNotIn(SENTINEL,json.dumps(result,ensure_ascii=False));self.no_secret_files()

    def test_consumer_sends_secret_only_to_pipe_and_strips_extra_reply_fields(self):
        auth.atomic_private(self.output,self.base)
        def transport(name,pid,value):
            self.assertEqual(value['password'],SENTINEL)
            return {'state':'login_submitted','login_submitted':True,'debug':SENTINEL}
        with patch.dict(os.environ,{'FINANCE_CAS_USERNAME':'TESTER','FINANCE_CAS_PASSWORD':SENTINEL}), \
             patch('edge_runtime.browser_lock',side_effect=contextlib.nullcontext),patch('finance_access.snapshot',return_value={'state':'login_required'}), \
             patch.object(auth,'status',return_value=self.bridge),patch.object(pipes,'call',side_effect=transport):
            result=auth.consume('fixture_case','login-01','123',self.output)
            self.assertNotIn('FINANCE_CAS_PASSWORD',os.environ)
        self.assertEqual(result['state'],'login_submitted');self.no_secret_files()

    def test_unknown_pipe_result_is_not_replayed(self):
        auth.atomic_private(self.output,self.base)
        with patch.dict(os.environ,{'FINANCE_CAS_USERNAME':'TESTER','FINANCE_CAS_PASSWORD':SENTINEL}), \
             patch('edge_runtime.browser_lock',side_effect=contextlib.nullcontext),patch('finance_access.snapshot',return_value={'state':'login_required'}), \
             patch.object(auth,'status',return_value=self.bridge),patch.object(pipes,'call',side_effect=RuntimeError(SENTINEL)):
            result=auth.consume('fixture_case','login-01','123',self.output)
        self.assertEqual(result['state'],'auth_result_unknown');self.assertIsNone(result['login_submitted'])
        with patch('finance_access.snapshot',return_value={'state':'login_required'}),patch('edge_runtime.browser_lock',side_effect=contextlib.nullcontext),patch.object(auth.subprocess,'run') as run:
            repeated=auth.use_credentials('fixture_case','login-01','123',self.output)
        self.assertTrue(repeated['reused_existing_auth_intent']);run.assert_not_called();self.no_secret_files()

    def test_vault_output_and_environment_sentinel_are_not_logged_or_forwarded(self):
        cli=self.root/'.ai-skills/rtime-credentials/scripts/cli.py';cli.parent.mkdir(parents=True);cli.write_text('# fixture')
        def tool(command,**kwargs):
            self.assertNotIn(SENTINEL,str(command)+kwargs['input'])
            self.assertNotIn('FINANCE_CAS_PASSWORD',kwargs['env'])
            return SimpleNamespace(returncode=1,stdout=json.dumps({'error':SENTINEL}),stderr=SENTINEL)
        with patch.object(Path,'home',return_value=self.root),patch.dict(os.environ,{'FINANCE_CAS_PASSWORD':SENTINEL}), \
             patch('finance_access.snapshot',return_value={'state':'login_required'}),patch('edge_runtime.browser_lock',side_effect=contextlib.nullcontext), \
             patch.object(auth,'status',return_value=self.bridge),patch.object(auth.subprocess,'run',side_effect=tool):
            result=auth.use_credentials('fixture_case','login-01','123',self.output)
        self.assertEqual(result['state'],'credential_use_failed');self.no_secret_files()

    def test_consumer_exception_never_prints_secret_exception_text(self):
        args=['consume','--case-id','fixture_case','--request-id','login-01','--tab-id','123','--output',str(self.output)]
        with patch.object(sys,'argv',['auth',*args]),patch.object(auth,'consume',side_effect=RuntimeError(SENTINEL)),contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(auth.main(),2)
        self.assertNotIn(SENTINEL,stdout.getvalue())


if __name__=='__main__':unittest.main()
