"""参考单只读来源合同；使用虚构身份，不访问财务系统。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from finance_business_api import command, reference_context, validate
from assistant_api import AssistantService


class ReferenceReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'source.json'
        self.path.write_text('{}')
        self.params = dict(action='reference-bill-read', domain='funding',
            tab_id='7', expected_userid='TEST001', source_job_id='j_'+'a'*24, reference_serial='123')
        self.request = dict(case_id='new_claim', parameters=self.params, execute=False)
        self.prior = dict(case_id='new_claim', operation='business', execute=False,
                          parameters=dict(action='bills-list', expected_userid='TEST001'))
        self.envelope = dict(phase='script_completed', result=dict(ok=True, action='bills-list',
            complete=True, case_id='new_claim', expected_userid='TEST001', system_write=False,
            business_transport='native_http', account_scope='current_applicant', total_records=1,
            rows=[{'t.serial_no':'123', 't.REQ_INFO':'TEST001-Fixture', 't.total_amt':'1,234.56',
                   't.abst':'Fixture', 't.req_date':'2026-01-02', 't.bx_type':'1'}]))
        self.status = dict(status='completed', exit_code=0,
                           artifacts=[dict(name='output.json', artifact_id='a0')])
        self.service = SimpleNamespace(python=sys.executable,
            result=lambda *args: self.status,
            _typed_case_artifact=lambda *args: (self.path, self.envelope, self.prior))

    def test_parameter_scope_is_closed(self):
        validate(self.params)
        for extra in [{'reference_serial':'bad'}, {'domain':'arbitrary'}, {'source_job_id':None},
                      {'execute':True}, {'serial':'456'}, {'url':'https://example.invalid'},
                      {'print_job_id':'j_'+'b'*24}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError): validate({**self.params, **extra})
        for action in ['bills-list','bill-read','basic-prepare']:
            with self.assertRaises(ValueError):validate({**self.params,'action':action})
        validate({**self.params,'domain':'archive-summary'})
        with self.assertRaises(ValueError):validate({'action':'bill-read','domain':'archive-summary','tab_id':'7','expected_userid':'TEST001'})

    def test_staff_lookup_does_not_require_or_inherit_a_bill(self):
        params=dict(action='staff-lookup',staff_query='T100',tab_id='7',expected_userid='TEST001')
        validate(params)
        for extra in [{'domain':'basic'},{'source_job_id':'j_'+'a'*24},{'staff_query':'*'},{'staff_query':'PB00'},{'staff_query':'杨小鹏'}]:
            with self.assertRaises(ValueError):validate({**params,**extra})
        args=command(self.service,{**self.request,'parameters':params},self.root)
        code=(self.root/'business-api.js').read_text()
        self.assertIn('return await reimbursementStaffLookup(',code)
        self.assertNotIn('"serial":',code.rsplit('\nreturn await ',1)[1])
        self.assertEqual(args[-2:],['read','--quiet'])

    def test_uses_exact_source_serial_and_amount_without_resolving_new_case_form(self):
        result = reference_context(self.service, self.request)
        self.assertEqual(result['serial'], '123')
        self.assertEqual(result['expected_amount'], '1234.56')
        self.assertTrue(result['reference_only'])
        args = command(self.service, self.request, self.root)
        code = (self.root/'business-api.js').read_text()
        self.assertIn('return await reimbursementBusinessApi(', code)
        self.assertIn('"action": "bill-read"', code)
        self.assertIn('reference_only:true', code)
        self.assertEqual(args[-2:], ['read','--quiet'])
        self.assertFalse((self.root/'form.json').exists())
        with self.assertRaises(ValueError): command(self.service,{**self.request,'execute':True},self.root)

    def test_rejects_partial_wrong_account_or_non_native_source(self):
        baseline=copy.deepcopy(self.envelope)
        for field,value in [('complete',False),('expected_userid','OTHER'),('case_id','other_case'),
                            ('system_write',True),('business_transport','ui'),('total_records',2),
                            ('total_records',True),('account_scope','other'),('action','bill-read')]:
            self.envelope=copy.deepcopy(baseline);self.envelope['result'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):reference_context(self.service,self.request)
        self.envelope=baseline
        self.prior['parameters']['expected_userid']='OTHER'
        with self.assertRaises(ValueError):reference_context(self.service,self.request)

    def test_rejects_missing_target_duplicate_foreign_row_and_bad_amount(self):
        baseline=copy.deepcopy(self.envelope)
        for field,value in [('t.serial_no','456'),('t.REQ_INFO','OTHER-Fixture'),
                            ('t.total_amt','12,34.56'),('t.total_amt','NaN'),('t.total_amt',1234.56)]:
            self.envelope=copy.deepcopy(baseline);self.envelope['result']['rows'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):reference_context(self.service,self.request)
        self.envelope=baseline;self.envelope['result']['rows']*=2;self.envelope['result']['total_records']=2
        with self.assertRaises(ValueError):reference_context(self.service,self.request)

    def test_rejects_failed_or_ambiguous_source_artifacts(self):
        for status in [dict(status='running',exit_code=0),dict(status='completed',exit_code=1),
                       dict(status='completed',exit_code=0,artifacts=[])]:
            self.status=status
            with self.assertRaises(ValueError):reference_context(self.service,self.request)

    def test_public_service_preserves_reference_action_for_readonly_provenance(self):
        service=object.__new__(AssistantService)
        with patch.object(service,'enqueue',return_value={'queued':True}) as enqueue:
            result=service.business('new_claim','reference-bill-read','7','TEST001','fixture',
                domain='funding',source_job_id='j_'+'a'*24,reference_serial='123')
            self.assertEqual(result,{'queued':True})
            self.assertEqual(enqueue.call_args.args[2],self.params)
            self.assertIs(enqueue.call_args.args[-1],False)

    def test_unbound_case_command_accepts_reference_without_binding_or_changing_form(self):
        service=AssistantService(self.root,[self.root]);service.new_case('new_claim')
        _,paths=service.resolve('new_claim');before=paths['form'].read_bytes()
        context=reference_context(self.service,self.request)
        with patch('finance_business_api.reference_context',return_value=context):
            args=service.command({**self.request,'operation':'business','request_id':'fixture'},self.root)
        self.assertEqual(args[-2:],['read','--quiet'])
        self.assertEqual(paths['form'].read_bytes(),before)
        self.assertIsNone(json.loads(before)['serial'])

    def test_reference_pdf_requires_exact_archive_metadata_and_keeps_case_unbound(self):
        from finance_reference_export import reference_export_case
        params=dict(export_plan=dict(schema='ustc-finance-pdf-export-plan-v1',case_id='new_claim',
            serial='123',summary='Fixture',business_date='2026-01-02',sequence=1,amount='1234.56',
            status='confirmed',view_kind='formal_print_api',include_invoice_attachments=True,
            source_refs=['fixture']),tab_id='7',expected_userid='TEST001',
            source_job_id='j_'+'a'*24,export_root=str(self.root),silent=True)
        request=dict(case_id='new_claim',operation='pdf_export',parameters=params,execute=False,request_id='fixture-pdf')
        case=reference_export_case(self.service,request)
        self.assertEqual(case['serial'],'123');self.assertTrue(case['reference_only'])
        for key,value in [('summary','Changed'),('business_date','2026-01-03'),('sequence',2),
                          ('amount','999.00'),('view_kind','print_preview'),('serial','456')]:
            bad=copy.deepcopy(request);bad['parameters']['export_plan'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):reference_export_case(self.service,bad)
        service=AssistantService(self.root,[self.root]);service.new_case('new_claim')
        _,paths=service.resolve('new_claim');before=paths['form'].read_bytes()
        with patch('finance_reference_export.reference_context',return_value=reference_context(self.service,self.request)):
            args=service.command(request,self.root)
        self.assertIn('finance_pdf_export.py',' '.join(args))
        self.assertNotIn('--execute',args)
        self.assertEqual(paths['form'].read_bytes(),before)

    def test_archive_summary_executes_all_seven_fixed_domains_and_rejects_partial(self):
        from finance_business_api import REFERENCE_SUMMARY_DOMAINS
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable')
        request={**self.request,'parameters':{**self.params,'domain':'archive-summary'}}
        command(self.service,request,self.root)
        code=(self.root/'business-api.js').read_text()
        expression=code.rsplit('\nreturn await ',1)[1].removesuffix(';\n})()')
        script='''const assert=require('node:assert/strict');
        let calls=[];let partial=false;
        async function reimbursementBusinessApi(c){calls.push(c.domain);assert(Array.isArray(c.contracts));
          return{ok:true,complete:!(partial&&c.domain==='payments'),serial:c.serial,expected_userid:c.expected_userid,system_write:false};}
        (async()=>{const result=await EXPRESSION;assert.deepEqual(calls,DOMAINS);
          assert.equal(result.domains.length,7);assert.equal(result.reference_only,true);assert.equal(result.system_write,false);
          partial=true;calls=[];await assert.rejects(async()=>await EXPRESSION,/REFERENCE_SUMMARY_INCOMPLETE/);
          assert.deepEqual(calls,['basic','funding','payments']);
        })().catch(e=>{console.error(e);process.exitCode=1;});'''.replace('EXPRESSION',expression).replace('DOMAINS',json.dumps(REFERENCE_SUMMARY_DOMAINS))
        result=subprocess.run([node,'-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
