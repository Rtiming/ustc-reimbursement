"""用户缩小范围时取消尚无单号的重复行程提示；保留原创建意图，另行独立回读。"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from assistant_api import AssistantService, atomic_json, read_json

ROOT=Path(__file__).resolve().parents[1]
FIELDS=('case_id','serial','expected_count','expected_amount','selected_invoices','original_files','creation_receipt','draft_creation_requested')
CANCEL_SCRIPT=r'''p=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==p.userid||String($.UC.bindData('#61-serialno#')||'')||String($.page.currPage)!=='6')throw Error('原建单身份或空单号上下文改变');
 const vis=e=>e&&e.getClientRects().length,compact=s=>String(s||'').replace(/\s/g,'');
 const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(vis);
 if(dialogs.length!==1||compact(dialogs[0].innerText)!==compact(p.warning_text))throw Error('不是已冻结的重复行程提示');
 const buttons=[...dialogs[0].querySelectorAll('button')].filter(b=>vis(b)&&!b.disabled);
 const cancel=buttons.filter(b=>compact(b.innerText)==='取消'),proceed=buttons.filter(b=>compact(b.innerText)==='继续');
 if(cancel.length!==1||proceed.length!==1)throw Error('原生继续/取消按钮不唯一');
 const e=document.getElementById('gridWF_YBX_53');if(!e?.grid||!vis(e)||e.grid.hDiv.loading)throw Error('原票池未就绪');
 const g=$(e),rows=g.jqGrid('getDataIDs').map(id=>({id,row:g.jqGrid('getRowData',id)}));
 const ids=g.jqGrid('getGridParam','selarrrow');if(!Array.isArray(ids))throw Error('缺少真实已选状态');
 const selected=ids.map(id=>rows.find(x=>x.id===id)?.row['t.tk_guid']).sort();
 if(JSON.stringify(selected)!==JSON.stringify(p.guids))throw Error('本案已选范围改变');
 for(const expected of p.invoices){const hits=rows.filter(x=>x.row['t.tk_guid']===expected.guid);if(hits.length!==1)throw Error('原票GUID不唯一');const row=hits[0].row;
  if(row['t.serial_no']||row['t.tk_mod_flag']!=='NORMAL'||row['t.tk_type']!==expected.type||String(row['t.tk_total_amt']).replace(/,/g,'')!==expected.amount)throw Error('原票占用、金额或认证改变');}
 if(Object.prototype.hasOwnProperty.call(window,'__ustcCancelDraftTravelV1'))throw Error('已有取消意图，须读原结果');
 Object.defineProperty(window,'__ustcCancelDraftTravelV1',{value:{case_id:p.case_id,creation_sha256:p.creation_sha256,requested:true},configurable:true});
 cancel[0].click();return{ok:true,action:'cancel_duplicate_trip_creation_prompt',case_id:p.case_id,creation_sha256:p.creation_sha256,count:p.invoices.length,requiresIndependentReadback:true};
}'''


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(service,case_id,job_id,artifact_id,decision_path,tab_id):
    _,paths=service.resolve(case_id);manifest=read_json(paths['invoices']);form=read_json(paths['form']);pending=read_json(paths['state'].parent/'pending_write.json')
    if manifest.get('serial') or form.get('serial') or manifest.get('draft_creation_requested') is not True or pending.get('operation')!='invoice:create-draft' or pending.get('case_id')!=case_id:raise ValueError('仅取消本案尚无单号的原创建提示')
    creation=Path(manifest['creation_receipt']).resolve()
    if not creation.is_relative_to(paths['state'].parent/'receipts') or creation.parent!=Path(pending['output_directory']).resolve():raise ValueError('原创建意图与回执位置不一致')
    original=read_json(creation)
    if original.get('phase')!='script_completed' or original.get('result',{}).get('action')!='create_daily_draft_requested' or original['result'].get('ok') is not True:raise ValueError('原创建派发尚未明确结束')
    if original['result'].get('case_id')!=case_id or original['result'].get('count')!=manifest['expected_count'] or original['result'].get('total')!=manifest['expected_amount']:raise ValueError('原创建票数金额与当前范围不一致')
    receipt=service.artifact(case_id,job_id,artifact_id);observed=read_json(receipt);body=observed.get('result',{})
    if observed.get('phase')!='script_completed' or observed.get('tab_id')!=tab_id or observed.get('browser_transport')!=original.get('browser_transport') or body.get('ok') is not True or body.get('projid')!='WF_YBX' or body.get('serial'):raise ValueError('缺少同案同标签的独立空单号回读')
    dialogs=body.get('visibleDialogs',[])
    if len(dialogs)!=1 or '发票中存在行程重复的情况' not in dialogs[0].get('text','') or '若确定没有改签情况,则继续。' not in dialogs[0]['text']:raise ValueError('只处理已观察的重复行程提示')
    decision_path=Path(decision_path).resolve();decision=read_json(decision_path)
    numbers=decision.get('remove_invoice_numbers');selected=manifest['selected_invoices']
    if decision.get('case_id')!=case_id or not decision.get('user_text') or not isinstance(numbers,list) or not numbers or len(set(numbers))!=len(numbers) or any(n not in {x['number'] for x in selected} for n in numbers):raise ValueError('须有本案用户缩小范围决定及原范围内完整票号')
    root=paths['state'].parent/'receipts'/('travel-cancel-'+sha(creation)[:24])
    if root.exists():raise ValueError('原创建已有取消意图，不能换请求重发；先读原取消结果')
    return {'case_id':case_id,'manifest_path':str(paths['invoices']),'manifest_sha256':sha(paths['invoices']),
        'manifest_before':manifest,'pending_before':pending,'creation':{'path':str(creation),'sha256':sha(creation)},
        'readback':{'path':str(receipt),'sha256':sha(receipt)},'decision':{'path':str(decision_path),'sha256':sha(decision_path)},
        'output_root':str(root),'context':{'case_id':case_id,'userid':body['identity']['userid'],'warning_text':dialogs[0]['text'],
            'guids':sorted(x['guid'] for x in selected),'invoices':selected,'creation_sha256':sha(creation)},'tab_id':tab_id}


def run(case_id,job_id,artifact_id,decision,tab_id,output,execute=False):
    output=Path(output)
    if output.exists():raise ValueError('不覆盖原取消回执')
    service=AssistantService(ROOT,[ROOT.parent])
    with service.mutex(case_id),service.mutex(case_id,browser=True):
        plan=prepare(service,case_id,job_id,artifact_id,decision,tab_id)
        if not execute:atomic_json(output,{'ok':True,'execute':False,'plan':plan});return {'ok':True,'execute':False}
        folder=Path(plan['output_root']);folder.mkdir(exist_ok=False);plan_path=folder/'plan.json';atomic_json(plan_path,plan)
        script=folder/'cancel.js';script.write_text('('+CANCEL_SCRIPT+')('+json.dumps(plan['context'],ensure_ascii=False)+')',encoding='utf-8')
        native=folder/'native.json';result=subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--tab-id',tab_id,'--output',str(native),'--operation-kind','browser-local','--quiet'])
        receipt=read_json(native)
        ok=result.returncode==0 and receipt.get('phase')=='script_completed' and receipt.get('result',{}).get('ok') is True
        if ok:
            manifest_path=Path(plan['manifest_path'])
            if sha(manifest_path)!=plan['manifest_sha256']:raise ValueError('取消后本案参数已改变，保留原结果待核')
            manifest=read_json(manifest_path);manifest['creation_cancel_request']={'plan_path':str(plan_path),'plan_sha256':sha(plan_path),'native_path':str(native),'native_sha256':sha(native)};atomic_json(manifest_path,manifest)
        summary={'ok':ok,'case_id':case_id,'cancellation_requested':ok,'independent_readback_required':True,'native_receipt':str(native),'original_creation_preserved':True,'replay_allowed':False}
        atomic_json(output,summary);return summary


def register_recovery(case_id,recovery_path,output):
    service=AssistantService(ROOT,[ROOT.parent]);output=Path(output);recovery_path=Path(recovery_path).resolve()
    if output.exists():raise ValueError('不覆盖原恢复登记')
    with service.mutex(case_id):
        _,paths=service.resolve(case_id);recovery=read_json(recovery_path);native_path=Path(recovery['receipt']).resolve()
        if not native_path.is_relative_to(paths['state'].parent/'receipts'):raise ValueError('恢复来源不属于本案')
        plan_path=native_path.parent/'plan.json';plan=read_json(plan_path);native=read_json(native_path);manifest=read_json(paths['invoices'])
        if plan.get('case_id')!=case_id or plan.get('manifest_path')!=str(paths['invoices']) or sha(paths['invoices'])!=plan['manifest_sha256']:raise ValueError('原取消计划或案件参数变化')
        expected='('+CANCEL_SCRIPT+')('+json.dumps(plan['context'],ensure_ascii=False)+')'
        if Path(native['script']).read_text()!=expected or sha(native['script'])!=native.get('source_sha256'):raise ValueError('原取消源码不匹配')
        body=recovery.get('result',{})
        if recovery.get('reinjected') is not False or recovery.get('marker_state')!='completed' or recovery.get('tab_id')!=plan['tab_id'] or body.get('ok') is not True or body.get('action')!='cancel_duplicate_trip_creation_prompt' or body.get('case_id')!=case_id or body.get('creation_sha256')!=plan['creation']['sha256']:raise ValueError('未取得原取消标记的明确成功结果')
        manifest['creation_cancel_request']={'plan_path':str(plan_path),'plan_sha256':sha(plan_path),'native_path':str(native_path),'native_sha256':sha(native_path),'recovery_path':str(recovery_path),'recovery_sha256':sha(recovery_path)}
        atomic_json(paths['invoices'],manifest)
        result={'ok':True,'case_id':case_id,'cancellation_recovered':True,'reinjected':False,'independent_readback_required':True};atomic_json(output,result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['case-id','readback-job','artifact-id','tab-id']:p.add_argument('--'+name,required=True)
    p.add_argument('--decision',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--recover',type=Path);a=p.parse_args()
    if a.recover and a.execute:p.error('恢复只登记原结果，不再次执行')
    result=register_recovery(a.case_id,a.recover,a.output) if a.recover else run(a.case_id,a.readback_job,a.artifact_id,a.decision,a.tab_id,a.output,a.execute)
    print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 2


if __name__=='__main__':raise SystemExit(main())
