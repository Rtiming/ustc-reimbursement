"""医疗HTTP保存的原pending和独立回读；不接触真实财务。"""
import copy
from datetime import datetime,timezone
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
import finance_native_invoice_edit as native


class NativeMedicalTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.service=AssistantService(self.root,[self.root]);self.service.new_case('medical_fixture');self.bundle,self.paths=self.service.resolve('medical_fixture')
        source=self.root/'original.pdf';source.write_bytes(b'synthetic original')
        self.edit={'schema':'ustc-invoice-medical-edit-v1','case_id':'medical_fixture','expected_userid':'TEST001','tab_id':'99',
          'invoice_code':'12345678','invoice_number':'1234567890','native_guid':'a'*32,'native_status':'NORMAL','amount':'10.00',
          'source_path':str(source),'source_sha256':native.sha(source),'changes':{'insurance_type':'自费'},
          'evidence':[{'path':str(source),'sha256':native.sha(source),'basis':'synthetic explicit self-pay confirmation'}]}
        self.original={'t.medical_paytype':'','t.medical_invamount':'10.00','t.medical_invno':'1234567890'}
        self.target={**self.original,'t.medical_paytype':'自费'};self.writes=0
        manifest=self.root/'edit-manifest.json';atomic_json(manifest,{'case_id':'medical_fixture','entries':[self.edit]})
        case=read_json(self.paths['case']);case['material_context']['invoice_edit_manifest']=native.ref(manifest);atomic_json(self.paths['case'],case)

    def freeze(self,folder,action='medical-run'):
        folder.mkdir();native.command(self.service,'medical_fixture',self.edit,action,action in {'medical-run','run'},folder.name,folder)
        return folder/('native-medical-input.json'if self.edit['schema']=='ustc-invoice-medical-edit-v1'else'native-travel-input.json')

    def runtime(self,c,method,output,extra=None):
        output=Path(output);script=output.with_suffix('.js');script.write_text(native.script(c,method,extra),encoding='utf-8',newline='\n')
        domain=native.kind(c);save=method==native.method(c,'Save')
        if save:
            self.writes+=1;self.assertTrue((self.paths['state'].parent/'pending_write.json').exists())
            body={'action':domain+'-save','save_dispatched':True};at='2026-10-29T13:01:00+00:00'
        else:
            body={'action':domain+'-prepare','save_dispatched':False,'system_write':False,'complete':True,
              'original':self.target if c['action']=='inspect'else self.original,'target':self.target,
              'definition_sha256':c[domain+'_contract']['definition_sha256']};at='2026-10-29T13:02:00+00:00'if c['action']=='inspect'else'2026-10-29T13:00:00+00:00'
        body.update(ok=True,case_id=c['case_id'],expected_userid=c['expected_userid'],native_guid=self.edit['native_guid'],business_transport='native_http')
        envelope={'phase':'script_completed','tab_id':'99','result':copy.deepcopy(body),'script':str(script),'source_sha256':native.sha(script),
           'executed_at_utc':at,'dispatch_intent_at_utc':at};atomic_json(output,envelope);return envelope

    def save_and_read(self):
        folder=self.root/'save';inp=self.freeze(folder)
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(inp,folder/'output.json'),0)
        pending=read_json(self.paths['state'].parent/'pending_write.json');reader=self.root/'read';inp=self.freeze(reader,'inspect')
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(inp,reader/'output.json'),0)
        return pending,read_json(reader/'output.json')

    def test_original_save_and_later_readback_release_pending(self):
        p,r=self.save_and_read();self.assertTrue(native.verify_ack(p,r,'saved')['protected_fields_verified'])
        from reimburse import acknowledge
        self.assertTrue(acknowledge(self.paths,self.root/'read/output.json','核对医疗字段及原支付金额','saved')['pending_cleared'])
        self.assertEqual(self.writes,1)

    def test_changed_payment_or_earlier_readback_or_foreign_identity_is_rejected(self):
        p,r=self.save_and_read()
        for mode in ['amount','identity','earlier','unknown']:
            bad=copy.deepcopy(r)
            if mode=='amount':bad['result']['original']['t.medical_invamount']='9.00'
            if mode=='identity':bad['result']['expected_userid']='OTHER'
            if mode=='earlier':bad['dispatch_intent_at_utc']='2026-10-29T13:00:00+00:00'
            if mode=='unknown':
                old=read_json(p['verification']['save_receipt']);old['phase']='dispatched';atomic_json(p['verification']['save_receipt'],old)
            with self.subTest(mode=mode),self.assertRaises(ValueError):native.verify_ack(p,bad,'saved')

    def test_source_drift_stops_before_network_and_original_pending_cannot_repeat(self):
        folder=self.root/'save';inp=self.freeze(folder);source=Path(self.edit['source_path']);source.write_bytes(b'changed')
        with patch.object(native,'browser')as browser:
            with self.assertRaises(ValueError):native.run(inp,folder/'output.json')
            browser.assert_not_called()

    def test_repeated_preparation_never_overwrites_frozen_adapter(self):
        folder=self.root/'freeze';inp=self.freeze(folder);c=read_json(inp);adapter=Path(c['adapter']['path']);before=adapter.read_bytes()
        with patch('finance_http.source',return_value='changed source'):
            with self.assertRaises(ValueError):native.command(self.service,'medical_fixture',self.edit,c['action'],True,'original',folder)
        self.assertEqual(adapter.read_bytes(),before)

    def test_unknown_save_recovers_only_original_marker_then_reads_independently(self):
        pending,_=self.save_and_read();path=Path(pending['verification']['save_receipt']);old=read_json(path);body=old['result']
        old.update(phase='await_original_marker',result_marker='reimbursement-result-'+'a'*32);atomic_json(path,old);original=path.read_bytes()
        folder=self.root/'recover';folder.mkdir()
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2026,10,29,13,1,30,tzinfo=timezone.utc)
        with patch('finance_backend.assert_original_transport'),patch('edge_runtime.browser_lock',nullcontext),patch('edge_runtime.read_marker',return_value=body)as marker,patch.object(native,'datetime',Clock),patch.object(native,'browser',side_effect=self.runtime):
            self.assertEqual(native.run(Path(pending['verification']['input']['path']),folder/'output.json',recover=True),0)
        result=read_json(folder/'output.json');self.assertEqual(path.read_bytes(),original);self.assertEqual(self.writes,1);marker.assert_called_once()
        self.assertTrue(native.verify_ack(pending,result,'saved')['independent_http_readback'])


class NativeTravelTests(NativeMedicalTests):
    def setUp(self):
        super().setUp()
        self.edit.update(schema='ustc-invoice-travel-edit-v1',invoice_number='9'*20,changes={'traveler':'合成人','person_id':'TEST002'})
        self.edit.pop('invoice_code')
        self.original={'t.passenger_sname':'','t.passenger_sno':'','t.tour_startdate':'2026-10-01'}
        self.target={**self.original,'t.passenger_sname':'合成人','t.passenger_sno':'TEST002'}
        manifest=self.root/'edit-manifest.json';atomic_json(manifest,{'case_id':'medical_fixture','entries':[self.edit]})
        case=read_json(self.paths['case']);case['material_context']['invoice_edit_manifest']=native.ref(manifest);atomic_json(self.paths['case'],case)

    def freeze(self,folder,action='medical-run'):
        return super().freeze(folder,'run'if action=='medical-run'else action)
