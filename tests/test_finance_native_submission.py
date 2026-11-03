"""提交选项的合成任务/独立回读；不接触真实财务写入。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
import finance_native_submission as native
from submission import view_plan


class NativeSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.service=AssistantService(self.root,[self.root]);self.service.new_case('submission_fixture');self.bundle,self.paths=self.service.resolve('submission_fixture')
        form=read_json(self.paths['form']);form.update(serial='123',amount='1.00',expected_userid='TEST001',contract={'exists':False})
        form['basic']['fields'].update({'t-abst':'Synthetic purpose','t-req_mobile':'13000000000'});atomic_json(self.paths['form'],form)
        self.plan=view_plan(form,'TEST001','synthetic user authorization');self.folder=self.root/'options';self.folder.mkdir();self.writes=0
        self.original={'print':'Y','proofs':[{'name':'form_proof_choose_1','ord':'1','type':'合同号','guid':'合同号','cells':['合同号','共0笔','说明'],'selected':'Y','contract':True},
            {'name':'form_proof_choose_2','ord':'2','type':'材料申请','guid':'original-guid','cells':['材料申请','共1笔','说明'],'selected':'Y','contract':False}]}
        self.target=copy.deepcopy(self.original);self.target['print']='N';self.target['proofs'][0]['selected']='N'
        self.target['proofs'][0]['cells'][2]=''

    def freeze(self,execute=True,action='options',folder=None):
        folder=folder or self.folder
        native.command(self.service,{'case_id':'submission_fixture','parameters':{'action':action,'plan':self.plan,'tab_id':'99'},'request_id':action+'-original','execute':execute},folder)
        return folder/'native-submission-input.json'

    def runtime(self,c,method,output,extra=None):
        output=Path(output);output.parent.mkdir(exist_ok=True);path=output.with_suffix('.js');path.write_text(native.script(c,method,extra))
        if method=='reimbursementSubmissionOptions':
            self.writes+=1;self.assertTrue((self.paths['state'].parent/'pending_write.json').exists());at='2026-10-29T13:01:00+00:00'
            result={'ok':True,'action':'submission-options-save','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'save_dispatched':True,'state':'saved_independent_readback_required'}
        else:
            after=c['action']=='inspect';at='2026-10-29T13:02:00+00:00'if after else'2026-10-29T13:00:00+00:00'
            result={'ok':True,'complete':True,'action':'submission-options-prepare','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],
                'business_transport':'native_http','system_write':False,'save_dispatched':False,'definition_sha256':c['submission_contract']['definition_sha256'],
                'original':copy.deepcopy(self.target if after else self.original),'target':copy.deepcopy(self.target),'changes':[{'kind':'print','value':'N'}]}
        value={'phase':'script_completed','tab_id':'99','result':result,'script':str(path),'source_sha256':native.sha(path),'executed_at_utc':at,'dispatch_intent_at_utc':at}
        atomic_json(output,value);return value

    def save_and_read(self):
        inp=self.freeze()
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(inp,self.folder/'output.json'),0)
        pending=read_json(self.paths['state'].parent/'pending_write.json');out=self.root/'read';out.mkdir();inp=self.freeze(False,'inspect',out)
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(inp,out/'output.json'),0)
        return pending,read_json(out/'output.json')

    def test_preview_is_offline_and_creates_no_pending(self):
        inp=self.freeze(False)
        with patch.object(native,'browser')as browser:self.assertEqual(native.run(inp,self.folder/'output.json'),0)
        browser.assert_not_called();self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())

    def test_original_pending_full_readback_and_acknowledgement(self):
        pending,observed=self.save_and_read();verified=native.verify_ack(self.paths,pending,observed,'saved')
        self.assertEqual(verified['proof_groups'],2);self.assertFalse(verified['delegate_finance_print']);self.assertFalse(verified['workflow_started_by_action'])
        from reimburse import acknowledge
        result=acknowledge(self.paths,self.root/'read'/'output.json','核对全部合同/打印和其他辅助证据','saved')
        self.assertTrue(result['pending_cleared']);self.assertEqual(self.writes,1);self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())

    def test_independent_readback_must_preserve_other_proofs_and_be_later(self):
        pending,observed=self.save_and_read()
        for mutation in ['other_proof','print','earlier','actor']:
            bad=copy.deepcopy(observed)
            if mutation=='other_proof':bad['result']['original']['proofs'][1]['selected']='N'
            elif mutation=='print':bad['result']['original']['print']='Y'
            elif mutation=='actor':bad['result']['expected_userid']='OTHER'
            else:bad['dispatch_intent_at_utc']='2026-10-29T13:00:30+00:00'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):native.verify_ack(self.paths,pending,bad,'saved')

    def test_cannot_release_unknown_original_from_unchanged_fields(self):
        pending,observed=self.save_and_read();path=Path(pending['verification']['save_receipt']);saved=read_json(path)
        saved['phase']='dispatched';atomic_json(path,saved)
        with self.assertRaises(ValueError):native.verify_ack(self.paths,pending,observed,'saved')
        self.assertTrue((self.paths['state'].parent/'pending_write.json').exists())

    def test_changed_form_stops_before_browser(self):
        inp=self.freeze();f=read_json(self.paths['form']);f['amount']='2.00';atomic_json(self.paths['form'],f)
        with patch.object(native,'browser')as browser:
            with self.assertRaises(ValueError):native.run(inp,self.folder/'output.json')
        browser.assert_not_called()

    def test_changed_request_cannot_overwrite_original_pending(self):
        self.save_and_read();path=self.paths['state'].parent/'pending_write.json';original=path.read_bytes()
        other=self.root/'changed-request';other.mkdir();inp=self.freeze(True,'options',other)
        with patch.object(native,'browser',side_effect=self.runtime):
            with self.assertRaises(ValueError):native.run(inp,other/'output.json')
        self.assertEqual(path.read_bytes(),original);self.assertEqual(self.writes,1)

    def test_readback_source_and_prepared_receipt_sha_are_checked(self):
        pending,observed=self.save_and_read();Path(observed['script']).write_text('arbitrary source')
        with self.assertRaises(ValueError):native.verify_ack(self.paths,pending,observed,'saved')
        pending2,observed2=copy.deepcopy(pending),copy.deepcopy(observed)
        p=Path(pending2['verification']['prepare']['path']);p.write_text(p.read_text()+' ')
        with self.assertRaises(ValueError):native.verify_ack(self.paths,pending2,observed2,'saved')

    def test_recovery_reads_original_once_and_never_resaves(self):
        pending,_=self.save_and_read();output=self.root/'recover'/'output.json';output.parent.mkdir()
        original=Path(pending['verification']['save_receipt']).read_bytes()
        with patch.object(native,'browser',side_effect=self.runtime):
            self.assertEqual(native.run(self.folder/'native-submission-input.json',output,recover=True),0)
        observed=read_json(output)
        self.assertTrue(native.verify_ack(self.paths,pending,observed,'saved')['independent_http_readback'])
        self.assertEqual(self.writes,1)
        self.assertEqual(Path(pending['verification']['save_receipt']).read_bytes(),original)

    def test_recovery_different_marker_does_not_release_pending(self):
        pending,observed=self.save_and_read();saved=read_json(pending['verification']['save_receipt'])
        saved['result_marker']='reimbursement-result-'+('b'*32)
        target=self.root/'different-marker.json';atomic_json(target,saved);observed['original_save_recovery']=native.ref(target)
        with self.assertRaisesRegex(ValueError,'恢复身份'):native.verify_ack(self.paths,pending,observed,'saved')
        self.assertTrue((self.paths['state'].parent/'pending_write.json').exists())


if __name__=='__main__':unittest.main()
