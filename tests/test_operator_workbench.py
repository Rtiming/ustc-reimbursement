from pathlib import Path
import hashlib
import http.client
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from assistant_api import AssistantService
from operator_workbench import Workbench, Server
from workbench_catalog import catalog, json_request


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        (self.root/'mcp').mkdir();shutil.copyfile(ROOT/'mcp/tool_catalog.json',self.root/'mcp/tool_catalog.json')
        self.material=self.root/'materials';self.material.mkdir()
        self.service=AssistantService(self.root,[self.material])
        self.app=Workbench(self.service)

    def test_catalog_covers_every_public_tool_and_no_arbitrary_dispatch(self):
        rows=catalog(self.root)['tools']
        original=json.loads((ROOT/'mcp/tool_catalog.json').read_text(encoding='utf-8'))['tools']
        self.assertEqual({t['name']for t in rows},{t['name']for t in original})
        with self.assertRaises(ValueError):self.app.prepare('__getattribute__',{})
        with self.assertRaises(ValueError):self.app.read('reimbursement_new_case',{'case_id':'fixture'})
        tool=next(t for t in rows if t['name']=='reimbursement_stage')
        req=json_request(tool,{'case_id':'fixture','stage':'basic','request_id':'once','execute':False})
        self.assertEqual(req['method'],'enqueue');self.assertEqual(req['params']['parameters'],{'stage':'basic'})

    def test_preview_does_not_create_and_duplicate_confirmation_is_idempotent(self):
        p=self.app.prepare('reimbursement_new_case',{'case_id':'fixture','profile':'student_activity','title':'合成活动'})
        self.assertFalse((self.root/'cases/fixture').exists())
        first=self.app.execute(p['intent_id']);second=self.app.execute(p['intent_id'])
        self.assertTrue(first['ok']);self.assertTrue(second['replayed'])
        self.assertEqual(self.service.cases()['cases'][0]['title'],'合成活动')
        self.assertEqual(len(self.service.cases()['cases']),1)

    def test_revision_change_blocks_original_confirmation(self):
        self.service.new_case('fixture')
        revision=self.service.revision('fixture')
        p=self.app.prepare('reimbursement_update_case',{'case_id':'fixture','document':'case','changes':{'description':'原计划'},'expected_revision':revision})
        self.service.update_case('fixture','case',{'description':'另一个已确认更正'},revision)
        with self.assertRaisesRegex(ValueError,'资料已更新'):self.app.execute(p['intent_id'])

    def test_upload_preserves_bytes_and_rejects_traversal_and_conflict(self):
        self.service.new_case('fixture');raw=b'original synthetic material';sha=hashlib.sha256(raw).hexdigest()
        args={'case_id':'fixture','batch_id':'a'*32,'name':'合成材料.txt','sha256':sha}
        r=self.app.upload(io.BytesIO(raw),len(raw),**args)
        self.assertEqual(Path(r['path']).read_bytes(),raw)
        self.assertTrue(self.app.upload(io.BytesIO(raw),len(raw),**args)['reused'])
        for name in ['../outside.txt','/outside.txt','C:/outside.txt','bad\\outside.txt']:
            with self.assertRaises(ValueError):self.app.upload(io.BytesIO(raw),len(raw),**{**args,'name':name})
        with self.assertRaises(ValueError):self.app.upload(io.BytesIO(raw),len(raw),**{**args,'sha256':'0'*64})
        self.assertEqual(Path(r['path']).read_bytes(),raw)

    def test_http_blocks_unauthenticated_and_cross_origin_calls(self):
        server=Server(self.service)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def call(path,body,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            encoded=json.dumps(body)
            connection.request('POST',path,body=encoded,headers={'Content-Type':'application/json',**(headers or {})})
            response=connection.getresponse();code=response.status;data=json.loads(response.read());cookie=response.getheader('Set-Cookie');connection.close();return code,data,cookie
        for _ in range(20):
            code,_,_=call('/api/read',{'tool':'reimbursement_cases'},{'Origin':'http://untrusted.invalid'})
            self.assertEqual(code,400)
        code,_,cookie=call('/api/session',{'token':server.token},{'Origin':server.origin})
        self.assertEqual(code,200)
        headers={'Origin':server.origin,'Cookie':cookie.split(';')[0],'X-Workbench-CSRF':server.csrf}
        code,data,_=call('/api/read',{'tool':'reimbursement_cases','arguments':{}},headers)
        self.assertEqual(code,200);self.assertEqual(data['cases'],[])
        code,_,_=call('/api/execute',{'intent_id':'a'*32,'arguments':{}},headers)
        self.assertEqual(code,400)


if __name__=='__main__':unittest.main()
