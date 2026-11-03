"""固定HTTP候选+本案冻结选择，所有浏览器和材料均为合成夹具。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
import invoice_selection as old
import finance_native_selection as native

class NativeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.service=AssistantService(self.root,[self.root]);self.service.new_case('selection_fixture')
        self.bundle,self.paths=self.service.resolve('selection_fixture')
        source=self.root/'invoice.pdf';source.write_bytes(b'original synthetic invoice');sha=old.sha(source);number='99000000000000001234'
        self.manifest={'case_id':'selection_fixture','expected_userid':'TEST001','original_files':[{'path':str(source),'sha256':sha,'invoice_number_candidates':[number]}]}
        self.audits={'reports':[{'path':str(source),'sha256':sha,'pages':[{'invoice_number_candidates':[number],'total_amount':'1.00',
                     'validation':{'title_matches_expected':True,'tax_id_matches_expected':True}}]}]}
        m=read_json(self.paths['invoices']);m.update(expected_count=1,original_files=self.manifest['original_files']);atomic_json(self.paths['invoices'],m)
        f=read_json(self.paths['form']);f.update(amount='1.00',expected_invoice_count=1);atomic_json(self.paths['form'],f)
        self.row={'t.tk_guid':'a'*32,'t.tk_code_abst':'普/全电票/'+number,'t.tk_total_amt':'1.00','t.tk_type':'DZ',
                  't.tk_display_abst':'材料','t.tk_mod_flag':'NORMAL','t.auth_rst_desc':'正常(已认证)','t.serial_no':''}
        self.request={'case_id':'selection_fixture','tab_id':'99','grid':'53','mode':'replace','selectors':[{'invoice_number':number}],
                      'expected_before_guids':[],'expected_userid':'TEST001'}
        self.calls=[]
    def browser(self,source,request,output):
        self.calls.append(source)
        self.assertIn('reimbursementBusinessApi',source);self.assertNotIn('.click()',source)
        record={'phase':'script_completed','tab_id':'99','result':{'ok':True,'case_id':'selection_fixture','projid':'WF_YBX',
            'identity':{'userid':'TEST001'},'business_transport':'native_http','mode':'native_http_full_grid','complete':True,
            'scopeComplete':True,'page_context_modified':False,'observed_at':'2026-10-29T12:00:00Z',
            'grids':[{'funcno':'53','rows':[copy.deepcopy(self.row)],'complete':True,'records':1,'rowCount':1,
                      'allRecordsVisible':False,'loading':False,'display_evidence':False}]}}
        atomic_json(output,record);return record
    def plan(self):
        snap=self.root/'snapshot.json'
        with patch.object(old,'run_browser',side_effect=self.browser):snapshot=native.snapshot(self.request,self.paths,snap)
        p=old.preview(self.request,self.manifest,snapshot,[self.audits]);refs={}
        for name,value in [('request',self.request),('case_manifest',self.manifest)]:
            path=self.root/(name+'.json');atomic_json(path,value);refs[name]={'path':str(path),'sha256':old.sha(path)}
        path=self.root/'audit.json';atomic_json(path,self.audits)
        p['evidence']={**refs,'snapshot':{'path':str(snap),'sha256':old.sha(snap)},'audit_receipts':[{'path':str(path),'sha256':old.sha(path)}]}
        path=self.root/'plan.json';atomic_json(path,p);return p,path
    def test_plan_distinguishes_local_guid_set_from_browser_selection(self):
        p,_=self.plan();self.assertTrue(p['apply_ready']);self.assertFalse(p['selection_available']);self.assertFalse(p['context_available'])
        self.assertTrue(p['local_selection_available']);self.assertEqual(p['scope']['rendered_guids'],[])
        native.verify_plan(p);self.assertEqual(read_json(self.paths['invoices']).get('selected_invoices',[]),[])
    def test_apply_persists_guid_and_audit_provenance_without_financial_write(self):
        p,path=self.plan();out=self.root/'apply'/'output.json'
        with patch.object(old,'run_browser',side_effect=self.browser):result=native.apply(p,path,self.bundle,out,'request-one')
        self.assertTrue(result['ok']);self.assertTrue(result['local_selection_write']);self.assertFalse(result['system_write']);self.assertFalse(result['selection_write'])
        self.assertFalse(result['draft_created']);m=read_json(self.paths['invoices']);self.assertEqual(m['selected_invoices'][0]['guid'],'a'*32)
        self.assertEqual(m['expected_count'],1);self.assertEqual(m['expected_amount'],'1.00');self.assertFalse(m['serial'])
        self.assertEqual(m['native_selection']['plan_sha256'],old.sha(path));self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())
        with patch.object(old,'run_browser',side_effect=self.browser):
            with self.assertRaises(ValueError):native.apply(p,path,self.bundle,self.root/'other'/'output.json','changed-request')
    def test_deferred_originals_do_not_inflate_expected_claim_count(self):
        manifest=read_json(self.paths['invoices']);manifest['expected_count']=2
        manifest['original_files'].append({'path':'/preserved/deferred.pdf','sha256':'b'*64})
        atomic_json(self.paths['invoices'],manifest);p,path=self.plan()
        with patch.object(old,'run_browser',side_effect=self.browser):native.apply(p,path,self.bundle,self.root/'apply'/'output.json','scoped-count')
        saved=read_json(self.paths['invoices']);self.assertEqual(saved['expected_count'],1);self.assertEqual(len(saved['original_files']),2)
    def test_interrupted_after_local_save_recovers_original_without_reapplying(self):
        p,path=self.plan();out=self.root/'apply'/'output.json';calls=0
        def runtime(source,request,output):
            nonlocal calls
            calls+=1
            if calls==2:raise TimeoutError('read-only after snapshot lost')
            return self.browser(source,request,output)
        with patch.object(old,'run_browser',side_effect=runtime):
            with self.assertRaises(TimeoutError):native.apply(p,path,self.bundle,out,'original-request')
        original=self.paths['invoices'].read_bytes();intent=out.parent/'selection-intent.json';intent_bytes=intent.read_bytes()
        with patch.object(old,'run_browser',side_effect=self.browser):result=native.apply(p,path,self.bundle,self.root/'resume.json','original-request',recover=intent)
        self.assertTrue(result['ok']);self.assertTrue(result['recovered_original_local_intent']);self.assertFalse(result['local_selection_write'])
        self.assertEqual(self.paths['invoices'].read_bytes(),original);self.assertEqual(intent.read_bytes(),intent_bytes)
    def test_drift_and_existing_claim_are_rejected_before_local_mutation(self):
        p,path=self.plan();before=self.paths['invoices'].read_bytes();self.row['t.serial_no']='12345'
        with patch.object(old,'run_browser',side_effect=self.browser):
            with self.assertRaises(ValueError):native.apply(p,path,self.bundle,self.root/'apply'/'output.json','request-one')
        self.assertEqual(self.paths['invoices'].read_bytes(),before)
        f=read_json(self.paths['form']);f['serial']='12345';atomic_json(self.paths['form'],f)
        with patch.object(old,'run_browser')as browser:
            with self.assertRaises(ValueError):native.snapshot(self.request,self.paths,self.root/'bound.json')
        browser.assert_not_called()
    def test_wrong_buyer_audit_still_blocks_new_frozen_selection(self):
        self.audits['reports'][0]['pages'][0]['validation']['tax_id_matches_expected']=False
        with self.assertRaises(ValueError):self.plan()

    def test_selected_formal_sources_append_without_replacing_existing_sources(self):
        m=read_json(self.paths['invoices']);m['original_files']=[];atomic_json(self.paths['invoices'],m)
        p,path=self.plan()
        with patch.object(old,'run_browser',side_effect=self.browser):native.apply(p,path,self.bundle,self.root/'apply'/'output.json','request-one')
        m=read_json(self.paths['invoices']);self.assertEqual(m['original_files'],self.manifest['original_files'])
        self.assertEqual(m['expected_count'],1)
