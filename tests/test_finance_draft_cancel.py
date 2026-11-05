import copy
import hashlib
import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_draft_cancel import CANCEL_SCRIPT
from finance_draft_readback import classify_draft_readback


class DraftCancelTests(unittest.TestCase):
    def native(self,**options):
        warning='提示信息 Close 发票中存在行程重复的情况; 行程[合成旅客：合成甲-合成乙] 若确定没有改签情况,则继续。 继 续 取 消'
        plan={'case_id':'test_case','userid':'TEST01','warning_text':warning,'guids':['G1'],'creation_sha256':'a'*64,
            'invoices':[{'guid':'G1','type':'PLANE','amount':'100.00'}]}
        script=r'''const vm=require('node:vm'),p=__PLAN__,o=__OPTIONS__;let clicks=0;
const vis={getClientRects:()=>[1]},cancel={...vis,innerText:'取 消',disabled:false,click:()=>clicks++},proceed={...vis,innerText:'继 续',disabled:false};
const dialog={...vis,innerText:o.other?'未知提示':p.warning_text,querySelectorAll:()=>[cancel,proceed]};
const element={...vis,grid:{hDiv:{loading:false}}};const row={'t.tk_guid':'G1','t.tk_type':'PLANE','t.serial_no':o.bound?'123':'','t.tk_mod_flag':'NORMAL','t.tk_total_amt':'100.00'};
const $=()=>({jqGrid:(cmd)=>cmd==='getDataIDs'?['row1']:cmd==='getRowData'?row:cmd==='getGridParam'?(o.otherSelection?[]:['row1']):null});
$.getProjID=()=> 'WF_YBX';$.UC={bindData:k=>k.includes('userid')?(o.wrongUser?'OTHER':'TEST01'):''};$.page={currPage:'6'};
const context={$,window:{},location:{origin:'https://cwzh.ustc.edu.cn'},document:{querySelectorAll:()=>[dialog],getElementById:()=>element}};
let result,error,replay;try{result=vm.runInNewContext(__SOURCE__,context)(p);try{vm.runInNewContext(__SOURCE__,context)(p);}catch(e){replay=e.message}}catch(e){error=e.message}
console.log(JSON.stringify({result,error,replay,clicks}));'''.replace('__PLAN__',json.dumps(plan,ensure_ascii=False)).replace('__OPTIONS__',json.dumps(options)).replace('__SOURCE__',json.dumps('('+CANCEL_SCRIPT+')',ensure_ascii=False))
        if shutil.which('node') is None:
            self.skipTest('Node开发测试运行时未安装；三系统CI继续执行此合同')
        result=subprocess.run(['node'],input=script,text=True,capture_output=True,check=True)
        return json.loads(result.stdout)

    def test_only_cancel_is_clicked_once_and_context_drift_stops(self):
        good=self.native();self.assertTrue(good['result']['ok']);self.assertEqual(good['clicks'],1);self.assertTrue(good['replay'])
        for option in ['other','bound','wrongUser','otherSelection']:
            bad=self.native(**{option:True});self.assertEqual(bad['clicks'],0);self.assertTrue(bad['error'])

    def test_cancellation_needs_later_full_unbound_readback_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);creation=root/'creation.json';creation.write_text('{"original":true}')
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            manifest={'case_id':'test_case','serial':None,'expected_count':1,'expected_amount':'100.00','original_files':[],
                'selected_invoices':[{'guid':'G1','amount':'100.00','type':'PLANE'}],'creation_receipt':str(creation),'draft_creation_requested':True}
            context={'case_id':'test_case','userid':'TEST01','creation_sha256':digest(creation),'invoices':manifest['selected_invoices'],'guids':['G1']}
            plan={'manifest_before':copy.deepcopy(manifest),'tab_id':'99','context':context,'creation':{'path':str(creation),'sha256':digest(creation)}}
            plan_path=root/'plan.json';plan_path.write_text(json.dumps(plan));script=root/'cancel.js';script.write_text('('+CANCEL_SCRIPT+')('+json.dumps(context,ensure_ascii=False)+')')
            transport={'backend':'test'};native_path=root/'native.json';native_path.write_text(json.dumps({'script':str(script),'source_sha256':digest(script),'phase':'script_completed','browser_transport':transport,'executed_at_utc':'2026-01-01T00:01:00+00:00','result':{'ok':True,'action':'cancel_duplicate_trip_creation_prompt','case_id':'test_case','creation_sha256':digest(creation),'count':1}}))
            manifest['creation_cancel_request']={'plan_path':str(plan_path),'plan_sha256':digest(plan_path),'native_path':str(native_path),'native_sha256':digest(native_path)}
            body={'ok':True,'projid':'WF_YBX','serial':'','visibleDialogs':[],'identity':{'userid':'TEST01'},'grids':[{'funcno':'53','records':1,'loading':False,'rows':[{'t.tk_guid':'G1','t.tk_total_amt':'100.00','t.tk_mod_flag':'NORMAL','t.serial_no':''}]}]}
            envelope={'tab_id':'99','browser_transport':transport,'executed_at_utc':'2026-01-01T00:02:00+00:00'}
            result=classify_draft_readback(body,manifest,envelope);self.assertEqual(result['state'],'cancelled_before_creation');self.assertFalse(result['claim_created'])
            with self.assertRaises(ValueError):classify_draft_readback(body,manifest,{**envelope,'executed_at_utc':'2026-01-01T00:00:00+00:00'})
            bad=copy.deepcopy(body);bad['grids'][0]['rows'][0]['t.serial_no']='123'
            with self.assertRaises(ValueError):classify_draft_readback(bad,manifest,envelope)
            native=json.loads(native_path.read_text());action_result=native['result'];native['phase']='unknown';native['result']={'ok':False,'execution_status':'unknown_browser_local_recover_original_marker'};native_path.write_text(json.dumps(native));manifest['creation_cancel_request']['native_sha256']=digest(native_path)
            recovered=root/'recovered.json';recovered.write_text(json.dumps({'receipt':str(native_path),'tab_id':'99','reinjected':False,'marker_state':'completed','observed_at_utc':'2026-01-01T00:01:30+00:00','result':action_result}))
            manifest['creation_cancel_request'].update(recovery_path=str(recovered),recovery_sha256=digest(recovered))
            self.assertEqual(classify_draft_readback(body,manifest,envelope)['state'],'cancelled_before_creation')
            with self.assertRaises(ValueError):classify_draft_readback(body,manifest,{**envelope,'executed_at_utc':'2026-01-01T00:01:20+00:00'})
            creation.write_text('{}')
            with self.assertRaises(ValueError):classify_draft_readback(body,manifest,envelope)


if __name__=='__main__':unittest.main()
