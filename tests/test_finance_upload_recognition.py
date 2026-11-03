"""The continuation retains the original upload and never replays a prepared child."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_finance_native_upload import NativeUploadTests
from finance_upload_recognition import command,run
from assistant_api import atomic_json,read_json

class RecognitionTests(NativeUploadTests):
    def prepare_recognition(self,execute=True):
        self.execute();req={**self.request,'request_id':'recognition','execute':execute,'parameters':{'action':'upload-recognize','job_id':'j_'+'1'*24,'tab_id':'8'}}
        original=self.folder;atomic_json(original/'request.json',self.request)
        folder=self.root/'recognition';folder.mkdir()
        with patch.object(self.service,'job_folder',return_value=original),patch.object(self.service,'result',return_value={'status':'completed'}),patch.dict('os.environ',{'REIMBURSEMENT_EXPECTED_USERID':'TEST001'}):
            command(self.service,req,folder)
        return folder/'recognition-input.json',folder/'output.json'

    def test_continuation_is_exclusive_and_never_reuploads_or_saves(self):
        path,out=self.prepare_recognition();pending=self.pending.read_bytes();calls=[]
        def runtime(args):
            calls.append(args);atomic_json(Path(args[args.index('--output')+1]),{'phase':'script_completed','result':{'ok':True}});return SimpleNamespace(returncode=0)
        self.assertEqual(run(path,out,runtime),0);self.assertEqual(len(calls),1);self.assertEqual(self.pending.read_bytes(),pending)
        self.assertEqual(self.actions,['prepare','start','append','upload','save','finish'])
        with self.assertRaisesRegex(ValueError,'不再派发'):run(path,out,runtime)
        self.assertEqual(len(calls),1)

    def test_unknown_continuation_preserves_original_and_cannot_repeat(self):
        path,out=self.prepare_recognition();calls=[]
        def runtime(args):calls.append(args);return SimpleNamespace(returncode=1)
        self.assertEqual(run(path,out,runtime),0)
        self.assertEqual(read_json(out)['results'][0]['state'],'unknown_requires_inventory')
        with self.assertRaises(ValueError):run(path,out,runtime)
        self.assertEqual(len(calls),1);self.assertTrue(self.pending.exists())

    def test_changed_original_source_or_unknown_save_stops_before_continuation(self):
        self.execute();v=read_json(self.folder/'upload-save.json');v['phase']='unknown';atomic_json(self.folder/'upload-save.json',v)
        from finance_upload_recognition import original
        with self.assertRaisesRegex(ValueError,'原保存'):original(self.paths,self.input)

    def test_native_recovery_requires_exact_completed_client_timeout_and_source_sha(self):
        from finance_upload_recognition import timeout_proofs
        from finance_native_upload import sha
        script=self.folder/'recognition-0.js';script.write_text('fixture original recognition script')
        receipt=script.with_suffix('.json');r={'phase':'script_completed','source_sha256':sha(script),'result':{'ok':False,'error':'UPLOAD_RECOGNITION_REQUEST_UNKNOWN:thirdPartyInterface.action:timeout:http=0'}}
        atomic_json(receipt,r);files=[{'filecode':'c'*32}]
        self.assertEqual(timeout_proofs(self.folder,files)[0]['filecode'],'c'*32)
        for change in ['unknown','other_error','source_changed']:
            altered=json.loads(json.dumps(r))
            if change=='unknown':altered['phase']='await_original_marker'
            if change=='other_error':altered['result']['error']='other failure'
            if change=='source_changed':altered['source_sha256']='d'*64
            atomic_json(receipt,altered)
            with self.assertRaises(ValueError):timeout_proofs(self.folder,files)

if __name__=='__main__':
    import unittest
    unittest.main()
