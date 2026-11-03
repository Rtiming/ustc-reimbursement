"""原生XLS导出的冻结来源、逐行逐列核对及拒绝伪完整；不访问浏览器。"""
import copy
import base64
import hashlib
from unittest.mock import patch
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_grid_export import FIELDS,command,validate_rows,run
from finance_business_api import validate
from assistant_api import atomic_json,read_json


class GridExportTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(zip(FIELDS,['10001','2026-01-01','F1<br />F2','Test','Approved','10.00','Person','12345678901']))]
        self.rows[0]['t.REQ_INFO']='TEST001-Person'
        self.cells=[['10001','2026-01-01','F1<br>F2','Test','Approved','10.00','Person','12345678901']]

    def test_exact_all_cells_and_native_project_linebreak_representation(self):
        r=validate_rows(self.cells,self.rows);self.assertEqual(r['verified_cells'],8);self.assertTrue(r['all_serials_and_values_match'])

    def test_missing_extra_duplicate_wrong_serial_amount_or_text_are_rejected(self):
        for mode in ['empty','extra','duplicate','width','serial','amount','summary']:
            actual=copy.deepcopy(self.cells)
            if mode=='empty':actual.clear()
            if mode in ['extra','duplicate']:actual*=2
            if mode=='width':actual[0].pop()
            if mode=='serial':actual[0][0]='999'
            if mode=='amount':actual[0][5]='9.99'
            if mode=='summary':actual[0][3]='Other'
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate_rows(actual,self.rows)

    def test_public_export_requires_original_full_source_and_rejects_paths_or_procedures(self):
        p={'action':'bills-export','tab_id':'7','expected_userid':'TEST001','source_job_id':'j_'+'a'*24};validate(p)
        for extra in [{'source_job_id':None},{'print_job_id':'j_'+'b'*24},{'output':'arbitrary'},{'sql':'arbitrary'},{'format':'EXCEL'}]:
            with self.assertRaises(ValueError):validate({**p,**extra})

    def test_formal_command_freezes_only_same_case_native_source(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);prior=root/'prior';prior.mkdir();target=root/'job';target.mkdir();case=root/'case.json'
            atomic_json(case,{'case_id':'fixture','material_context':{}})
            atomic_json(prior/'request.json',{'operation':'business','parameters':{'action':'bills-list'}})
            body={'phase':'script_completed','result':{'ok':True,'complete':True,'action':'bills-list',
                'expected_userid':'TEST001','business_transport':'native_http','rows':self.rows,'total_records':1}}
            atomic_json(prior/'output.json',body)
            service=SimpleNamespace(python=sys.executable,job_folder=lambda c,j:prior,
                result=lambda c,j:{'status':'completed','artifacts':[{'name':'output.json','artifact_id':'a'}]},
                artifact=lambda c,j,a:prior/'output.json',resolve=lambda c:(None,{'case':case}))
            request={'case_id':'fixture','request_id':'stable','parameters':{'action':'bills-export','tab_id':'7',
                'expected_userid':'TEST001','source_job_id':'j_'+'a'*24}}
            args=command(service,request,target);self.assertEqual(Path(args[1]).name,'finance_grid_export.py')
            frozen=read_json(target/'native-grid-export-input.json');self.assertEqual(frozen['scope'],self.rows)
            self.assertEqual(args,command(service,request,target))
            body['result']['rows'][0]['t.total_amt']='9.00';atomic_json(prior/'output.json',body)
            with self.assertRaises(ValueError):command(service,request,target)
            body['result']['complete']=False;atomic_json(prior/'output.json',body)
            with self.assertRaises(ValueError):command(service,request,target)


    def test_recovery_collects_original_bytes_without_regeneration_and_unknown_never_replays(self):
        from finance_http import source
        for unknown in [False,True]:
            with self.subTest(unknown=unknown),tempfile.TemporaryDirectory()as tmp:
                folder=Path(tmp);data=b'%PDF-'+b'x'*507;sha=hashlib.sha256(data).hexdigest()
                config={'schema':'ustc-native-grid-export-v1','case_id':'fixture','expected_userid':'TEST001',
                    'tab_id':'7','session_token':'a'*32,'export_format':'PDF','scope':self.rows,'sources':{}}
                code=source('grid-export');(folder/'native-grid-export-adapter.js').write_text(code)
                atomic_json(folder/'input.json',config)
                def text(action,**kw):
                    return '(async()=>{\n'+code+'\nreturn await reimbursementGridExportApi('+json.dumps({**config,'action':action,**kw},ensure_ascii=False)+');})()'
                metadata={'ok':True,'size':512,'sha256':sha}
                for suffix,action,kw,result in [('begin','begin',{},metadata),('chunk-0','chunk',{'offset':0,'length':512},None)]:
                    content=text(action,**kw);(folder/('grid-'+suffix+'.js')).write_text(content)
                    atomic_json(folder/('grid-'+suffix+'.json'),{'phase':'unknown'if unknown and action=='chunk'else'not_dispatched'if action=='chunk'else'script_completed',
                        'operation_kind':'read','source_sha256':hashlib.sha256(content.encode()).hexdigest(),
                        'result':result or {'ok':False,'injection_acknowledged':False,'error':'Mac财务桥拒绝/失败：tab_window_mismatch'}})
                calls=[]
                def runtime(args):
                    script=Path(args[2]);receipt=Path(args[args.index('--output')+1]);calls.append(receipt.name)
                    self.assertNotEqual(receipt.name,'grid-begin.json')
                    r={**metadata,'offset':0,'chunk_size':512,'chunk_sha256':sha,'base64':base64.b64encode(data).decode()}if 'chunk'in receipt.name else metadata
                    atomic_json(receipt,{'phase':'script_completed','source_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'result':r})
                    return SimpleNamespace(returncode=0)
                with patch('registered_page_owner.RegisteredPageOwner.before_child_dispatch'),patch('finance_grid_export.validate_pdf',return_value={'records':1,'verified_cells':8,'all_serials_and_values_match':True}):
                    if unknown:
                        with self.assertRaisesRegex(ValueError,'原导出子任务未知'):run(folder/'input.json',folder/'output.json',runtime)
                        self.assertEqual(calls,[])
                    else:
                        run(folder/'input.json',folder/'output.json',runtime)
                        self.assertEqual(calls,['grid-chunk-0-after-binding.json','grid-finish.json'])
                        self.assertEqual((folder/'bills.pdf').read_bytes(),data)
                        self.assertTrue(read_json(folder/'output.json')['original_bytes_preserved'])


if __name__=='__main__':unittest.main()
