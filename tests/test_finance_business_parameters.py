"""Fixed business input and composition contracts; no browser or real account."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from finance_business_api import validate, command
from finance_http import source


class BusinessParameterTests(unittest.TestCase):
    def setUp(self):
        self.parameters = {'action':'basic-prepare','tab_id':'7','expected_userid':'TEST001'}

    def test_invoice_diagnostics_only_capture_sources_and_read_fixed_saved_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);state=folder/'state.json';state.write_text('{}')
            service=SimpleNamespace(python=sys.executable,resolve=lambda case:(None,{'state':state}))
            request={'case_id':'fixture','parameters':{'action':'definitions','domain':'invoices','tab_id':'7','expected_userid':'TEST001'},'execute':False}
            command(service,request,folder)
            text=(folder/'business-api.js').read_text()
            self.assertIn('const uploaded_source_readback=[];for(const f of [])',text)
            self.assertIn('fileSystem_getAction.action',text)
            self.assertIn('source_bytes_match:response.ok&&sha===f.sha256',text)
            self.assertNotIn('return await reimbursementUploadRecognition(',text)
            self.assertIn('String(name===',text)

    def test_preparation_has_no_caller_procedure_fields_or_save_option(self):
        validate(self.parameters)
        for extra in [{'domain':'basic'}, {'save':True}, {'fields':{}}, {'procedure':'arbitrary'},
                      {'source_job_id':'j_'+'a'*24}]:
            with self.assertRaises(ValueError): validate({**self.parameters, **extra})
        for key in ['expected_userid','tab_id']:
            with self.assertRaises(ValueError): validate({**self.parameters,key:''})

    def test_script_uses_registered_form_only_and_never_dispatches_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); form = folder / 'form.json'
            form.write_text('{}')
            service = SimpleNamespace(python=sys.executable, resolve=lambda case:(None,{'form':form}))
            config = {'serial':'123','amount':'10.00','expected_userid':'TEST001',
                      'basic':{'teacher_id':'100','teacher_name':'Fixture','fields':{'t-abst':'Fixture'}}}
            request = {'case_id':'fixture','parameters':self.parameters,'execute':False}
            with patch('validate_config.load_context',return_value=config), patch('validate_config.load_basic',return_value=config):
                args = command(service,request,folder)
                script = (folder/'business-api.js').read_text()
                self.assertIn('return await reimbursementBasicPrepare(',script)
                self.assertNotIn('"execute": true',script)
                self.assertIn('"teacher_name": "Fixture"',script)
                self.assertEqual(args[-2:],['read','--quiet'])
                self.assertEqual(args,command(service,request,folder))
                config['basic']['teacher_name']='Changed'
                with self.assertRaisesRegex(ValueError,'脚本发生变化'): command(service,request,folder)
                with self.assertRaises(ValueError): command(service,{**request,'execute':True},folder)

    def test_composition_contains_one_shared_transport_and_closed_modules(self):
        for name in ['business','print','basic','funding','expenses','payments','notes','pre-review','grid-export','health']:
            self.assertEqual(source(name).count('function createReimbursementHttp('),1)
        with self.assertRaises(ValueError): source('../arbitrary')

    def test_recognition_inventory_has_fixed_contract_and_no_bill_or_caller_filters(self):
        p={**self.parameters,'action':'recognition-list'};validate(p)
        for extra in [{'domain':'recognition'},{'sql':'arbitrary'},{'filecode':'arbitrary'}]:
            with self.assertRaises(ValueError):validate({**p,**extra})
        with tempfile.TemporaryDirectory()as tmp:
            folder=Path(tmp);request={'case_id':'fixture','parameters':p,'execute':False}
            command(SimpleNamespace(python=sys.executable),request,folder)
            text=(folder/'business-api.js').read_text()
            self.assertIn('"funcno": "113"',text);self.assertIn('"winno": "79"',text)
        from finance_invoice_verification import full_grid_http_script
        for grid in ['53','113','101']:
            text=full_grid_http_script(grid,'TEST001','fixture')
            self.assertNotRegex(text,r'\.click\s*\(|\.trigger\s*\(|dispatchEvent|runInstance')
            self.assertIn('display_evidence:false',text);self.assertIn('allRecordsVisible:false',text)
        for grid,user in [('533','TEST001'),('113','bad id')]:
            with self.assertRaises(ValueError):full_grid_http_script(grid,user)

    def test_scoped_notes_reads_bind_original_serial_and_reject_command_text(self):
        from finance_business_api import valid_notes_xtype
        p={**self.parameters,'action':'bill-read','domain':'notes','notes_xtype':'维修费&维修费'}
        validate(p)
        for x in ['维修费&维修费','33&33','CHECK_DW|123&CHECK_DW']:self.assertTrue(valid_notes_xtype(x,'123'))
        for x in ['CHECK_DW|124&CHECK_DW','a&b','a;a&a;a','x#&x#','x|1&x|1']:
            self.assertFalse(valid_notes_xtype(x,'123'))
        for bad in [{'action':'notes-prepare','domain':None},{'domain':'basic'},{'notes_xtype':'bad;cmd&bad;cmd'}]:
            with self.assertRaises(ValueError):validate({**p,**bad})
        with tempfile.TemporaryDirectory()as tmp:
            folder=Path(tmp);form=folder/'form.json';form.write_text('{}')
            service=SimpleNamespace(python=sys.executable,resolve=lambda case:(None,{'form':form}))
            config={'serial':'123','amount':'10.00','expected_userid':'TEST001'}
            req={'case_id':'fixture','parameters':{**p,'notes_xtype':'CHECK_DW|124&CHECK_DW'},'execute':False}
            with patch('validate_config.load_context',return_value=config):
                with self.assertRaisesRegex(ValueError,'本案原单'):command(service,req,folder)

    def test_notes_and_pre_review_definitions_are_fixed_readonly_domains(self):
        for domain in ['notes','pre_review']:
            validate({**self.parameters,'action':'definitions','domain':domain})
            validate({**self.parameters,'action':'bill-read','domain':domain})
        with self.assertRaises(ValueError):validate({**self.parameters,'action':'bill-read','domain':'unknown'})


if __name__ == '__main__': unittest.main()
