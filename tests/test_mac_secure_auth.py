"""Real local Unix socket with synthetic credentials; no school login performed."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import mac_auth_socket as ipc
import windows_auth_protocol as protocol
import edge_native_host as host
from windows_edge_bridge import atomic_private

SECRET='SYNTHETIC_MAC_SECRET_没有真实密码'
def packet(epoch='a'*32):
    return {'schema':'ustc-cas-memory-login-v1','case_id':'fixture_case','request_id':'mac-login-01','tab_id':'123',
            'expected_username':'TESTER','username':'TESTER','password':SECRET,'authorized':True,'expires_at':time.time()+15,
            'expected_epoch':epoch,'expected_window_id':'99'}

@unittest.skipUnless(sys.platform=='darwin','macOS private socket')
class SocketTests(unittest.TestCase):
    def test_real_uid_pid_private_endpoint_and_cleanup(self):
        received=[]
        def callback(value):
            received.append(value['password']==SECRET)
            return {'state':'fixture_only'}
        server=ipc.AuthSocketServer(callback);endpoint=Path(server.name)
        try:
            self.assertTrue(server.acl_is_private())
            for _ in range(4):
                with self.assertRaises(ipc.AuthSocketError):ipc.call(server.name,os.getpid()+1,packet())
            self.assertEqual(ipc.call(server.name,os.getpid(),packet()),{'state':'fixture_only'})
            self.assertEqual(received,[True])
            os.chmod(endpoint,0o666)
            with self.assertRaises(ipc.AuthSocketError):ipc.call(server.name,os.getpid(),packet())
        finally:server.close()
        self.assertFalse(endpoint.exists());self.assertFalse(server.thread.is_alive())

    def test_native_host_memory_dispatch_no_file_secret_or_replay(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(host,'STATE',Path(directory)):
            root=Path(directory);atomic_private(root/'installation.json',{'extension_id':'fixture','build_sha256':'build'})
            rd,wr=os.pipe();source=os.fdopen(rd,'rb');writer=os.fdopen(wr,'wb');dest=io.BytesIO();errors=[]
            def serve():
                try:host.serve(source,dest)
                except Exception as e:errors.append(type(e).__name__)
            worker=threading.Thread(target=serve,daemon=True);worker.start()
            try:
                host.write_frame(writer,{'type':'hello','protocol':1,'extension_id':'fixture','build_sha256':'build','synthetic_profile':False})
                deadline=time.monotonic()+3
                while not(root/'status.json').exists() and time.monotonic()<deadline:time.sleep(.01)
                health=json.loads((root/'status.json').read_text());endpoint=health['auth_socket_path'];first=len(dest.getvalue())
                wrong=ipc.call(endpoint,os.getpid(),packet())
                self.assertEqual(wrong['state'],'auth_intent_mismatch')
                reply=[];req=packet(health['epoch'])
                client=threading.Thread(target=lambda:reply.append(ipc.call(endpoint,os.getpid(),req)),daemon=True);client.start()
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    raw=dest.getvalue()[first:]
                    if len(raw)>=4 and len(raw)>=4+struct.unpack('<I',raw[:4])[0]:break
                    time.sleep(.01)
                message=host.read_frame(io.BytesIO(dest.getvalue()[first:]));self.assertEqual(message['operation'],'cas-login')
                self.assertEqual(message['arguments']['password'],SECRET);self.assertEqual(message['arguments']['expected_window_id'],'99')
                host.write_frame(writer,{'id':message['id'],'ok':True,'result':{'state':'login_submitted','login_submitted':True,'secret':SECRET}})
                client.join(timeout=3);self.assertFalse(client.is_alive());self.assertEqual(reply[0]['state'],'login_submitted')
                native_before=dest.getvalue();again=ipc.call(endpoint,os.getpid(),packet(health['epoch']))
                self.assertEqual(again['state'],'login_submitted');self.assertEqual(native_before,dest.getvalue())
            finally:writer.close();worker.join(timeout=3);source.close()
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            for p in root.rglob('*'):
                if p.is_file():
                    self.assertNotIn(SECRET.encode(),p.read_bytes());self.assertNotIn(json.dumps(SECRET).strip('"').encode(),p.read_bytes())

class BindingTests(unittest.TestCase):
    def test_optional_mac_binding_is_all_or_nothing(self):
        p=packet();self.assertEqual(protocol.validate(p),p)
        for k in ['expected_epoch','expected_window_id']:
            bad=dict(p);bad.pop(k)
            with self.assertRaises(ValueError):protocol.validate(bad)
        request=protocol.MemoryRequest(p)
        self.assertEqual(request.take_for_dispatch()['expected_epoch'],p['expected_epoch'])

if __name__=='__main__':unittest.main()
