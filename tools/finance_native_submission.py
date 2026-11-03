"""提交选项原生HTTP合同：原pending、只读准备和独立全量选项回读。"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT)not in sys.path:sys.path.insert(0,str(ROOT))
from finance_native_create import sha,ref,checked,checked_text,frozen,read
SCHEMA='ustc-native-submission-options-v1'


def script(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or{})},ensure_ascii=False)+');})()'


def command(service,request,folder):
    from submission import validate_plan
    from finance_http import source
    p=request['parameters'];bundle,paths=service.resolve(request['case_id']);form=read(paths['form']);plan=p.get('plan')
    if p.get('action')not in {'inspect','options','options-recover'}or set(p)-{'action','plan','tab_id','job_id'}:raise ValueError('原生提交选项仅inspect/options/options-recover')
    validate_plan(plan,form,require_submit_authority=p.get('action')!='inspect')
    if p['action']=='options-recover'and not p.get('job_id'):raise ValueError('选项恢复须引用原保存任务')
    if p.get('job_id'):
        if p['action']not in {'inspect','options-recover'}or request['execute']:raise ValueError('原选项源码引用仅用于独立只读检查')
        origin=service.job_folder(request['case_id'],p['job_id']);prior=read(origin/'request.json');c=read(origin/'native-submission-input.json')
        if(prior.get('operation')!='submission'or prior['parameters'].get('action')!='options'or service.result(request['case_id'],p['job_id'])['status']in {'queued','running'}or
           c['case_id']!=request['case_id']or c['plan']!=plan or c['tab_id']!=p['tab_id']or c['form']!=ref(paths['form'])):raise ValueError('原选项计划、源码或当前单据不同')
        if p['action']=='options-recover':return[service.python,str(ROOT/'tools/finance_native_submission.py'),'--input',str(origin/'native-submission-input.json'),'--output',str(folder/'output.json'),'--recover']
        c={**c,'action':'inspect','execute':False};path=folder/'native-submission-input.json';frozen(path,c)
        return[service.python,str(ROOT/'tools/finance_native_submission.py'),'--input',str(path),'--output',str(folder/'output.json')]
    who=plan.get('expected_userid')or form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID');tab=p.get('tab_id')
    if not isinstance(who,str)or not re.fullmatch(r'[A-Za-z0-9]{2,40}',who)or form.get('expected_userid')not in (None,'',who)or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')not in (None,who):raise ValueError('须明确本案提交账号')
    if (request['execute']or p['action']=='inspect')and(not isinstance(tab,str)or not re.fullmatch(r'\d+',tab)):raise ValueError('提交选项检查须明确当前tab')
    if p['action']=='inspect'and request['execute']:raise ValueError('inspect不写入')
    if form.get('system_mode','daily')not in {'daily','remuneration'}:raise ValueError('未适配业务类型')
    c={'schema':SCHEMA,'case_id':request['case_id'],'serial':form['serial'],'expected_amount':form['amount'],'expected_userid':who,
       'plan':plan,'action':p['action'],'execute':request['execute'],'tab_id':tab,'request_id':request['request_id'],'bundle':str(bundle),
       'form':ref(paths['form']),'basic_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['basic'],
       'submission_contract':read(ROOT/'data/finance_submission_contract.json')}
    if form.get('system_mode')=='remuneration':
        from finance_native_remuneration import verify_bound_case
        verify_bound_case(form);p0=form['remuneration']['creation_plan'];original=checked({'path':p0['plan'],'sha256':p0['sha256']})
        c.update(system_mode='remuneration',activity=original['activity'],remuneration_contract=original['remuneration_contract'],payment_contracts=read(ROOT/'data/finance_business_contracts.json')['domains']['payments'])
    adapter=folder/'native-submission-adapter.js';code=source('submission')
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原提交选项源码漂移')
    else:
        with adapter.open('x',encoding='utf-8',newline='\n')as stream:stream.write(code)
        adapter.chmod(0o600)
    c['adapter']=ref(adapter);path=folder/'native-submission-input.json';frozen(path,c)
    return [service.python,str(ROOT/'tools/finance_native_submission.py'),'--input',str(path),'--output',str(folder/'output.json')]


def browser(c,method,output,extra=None):
    from registered_page_owner import RegisteredPageOwner
    path=Path(output).with_suffix('.js')
    with path.open('x',encoding='utf-8',newline='\n')as stream:stream.write(script(c,method,extra))
    path.chmod(0o600);RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(path),'--output',str(output),'--tab-id',c['tab_id'],
                   '--operation-kind','write'if method=='reimbursementSubmissionOptions'else'read','--quiet'],check=False)
    if not Path(output).exists():raise ValueError('原提交选项子任务未留下回执，保留pending')
    return read(output)


def validate_prepare(c,envelope):
    p=envelope.get('result',{})
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or p.get('ok')is not True or p.get('complete')is not True or p.get('action')!='submission-options-prepare'or p.get('business_transport')!='native_http'or p.get('system_write')is not False or p.get('save_dispatched')is not False or any(p.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or p.get('definition_sha256')!=c['submission_contract']['definition_sha256']:raise ValueError('须原生完整同单提交选项准备')
    return p


def run(input_path,output,recover=False):
    from operator_support import bundle
    from reimburse import begin_write
    c=read(input_path);_,paths=bundle(c['bundle']);checked(c['form']);checked_text(c['adapter']);output=Path(output)
    if recover:
        from edge_runtime import read_marker,browser_lock
        from finance_backend import assert_original_transport
        from assistant_api import atomic_json
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if v.get('schema')!=SCHEMA or v.get('input')!=ref(input_path):raise ValueError('只能恢复原打印选项保存意图')
        plan=validate_prepare(c,checked(v['prepare']));old=read(v['save_receipt'])
        if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=script(c,'reimbursementSubmissionOptions',{'expected_plan':plan,'pending_registered':True}):raise ValueError('原选项源码已改变')
        if old.get('phase')=='script_completed':saved=old
        else:
            if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原选项尚未派发或缺marker')
            assert_original_transport(old)
            with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
            if not isinstance(body,dict):raise ValueError('原选项marker尚未返回，继续保留原意图')
            saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now().astimezone().isoformat(),'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
        target=output.parent/'original-options-result.json';frozen(target,saved)
        observed=browser({**c,'action':'inspect','execute':False},'reimbursementSubmissionPrepare',output)
        validate_prepare(c,observed);observed['original_save_recovery']=ref(target);atomic_json(output,observed);return 0
    if c['action']=='options'and not c['execute']:
        frozen(output,{'ok':True,'schema':SCHEMA,'state':'preview','business_transport':'native_http','system_write':False,'finalApproval':False});return 0
    if c['action']=='inspect':
        observed=browser(c,'reimbursementSubmissionPrepare',output);validate_prepare(c,observed);return 0
    prepared_path=output.parent/'native-submission-prepare.json';observed=browser(c,'reimbursementSubmissionPrepare',prepared_path);plan=validate_prepare(c,observed)
    checked(c['form']);v={'schema':SCHEMA,'input':ref(input_path),'prepare':ref(prepared_path),'save_receipt':str(output)}
    begin_write(paths,read(paths['form']),'submission:options',output.parent,v)
    result=browser(c,'reimbursementSubmissionOptions',output,{'expected_plan':plan,'pending_registered':True})
    return 0 if result.get('phase')=='script_completed'and result.get('result',{}).get('ok')is True else 2


def verify_ack(paths,previous,envelope,outcome):
    v=previous['verification'];c=checked(v['input']);prepare=checked(v['prepare']);p=validate_prepare(c,prepare);saved=read(v['save_receipt']);s=saved.get('result',{})
    if envelope.get('original_save_recovery'):
        recovered=checked(envelope['original_save_recovery']);binding=recovered.get('original_marker_recovery',{})
        if saved.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('选项恢复未绑定原marker')
        if any(recovered.get(k)!=saved.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('选项恢复身份不同')
        saved=recovered;s=saved.get('result',{})
    if c.get('schema')!=SCHEMA or previous.get('operation')!='submission:options'or any(previous.get(k)!=c[k]for k in ['case_id','serial'])or Path(v['save_receipt']).resolve()!=(Path(previous['output_directory'])/'output.json').resolve():raise ValueError('原提交选项意图不同')
    checked(c['form'])
    if(saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script(c,'reimbursementSubmissionOptions',{'expected_plan':p,'pending_registered':True})or s.get('action')!='submission-options-save'or any(s.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])):raise ValueError('原提交选项尚未取得同任务完整停止回执，先恢复原marker')
    observed=validate_prepare(c,envelope)
    if not envelope.get('dispatch_intent_at_utc')or datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('独立选项回读须晚于原保存结束')
    # Parse only the exact fixed reader invocation and compare its owning inputs.
    code=Path(envelope['script']).read_text();prefix='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementSubmissionPrepare('
    if sha(envelope['script'])!=envelope.get('source_sha256')or not code.startswith(prefix)or not code.endswith(');})()'):raise ValueError('不是原固定选项读取适配器')
    reader=json.loads(code[len(prefix):-len(');})()')])
    for key in ['case_id','serial','expected_amount','expected_userid','plan','tab_id','form','basic_contracts','submission_contract','system_mode','activity','remuneration_contract','payment_contracts']:
        if reader.get(key)!=c.get(key):raise ValueError('独立读取输入与原选项意图不同：'+key)
    if outcome=='saved':target=p['target']
    elif outcome=='staged':
        from copy import deepcopy
        target=deepcopy(p['original']);steps=s.get('steps',[])
        if(not steps or len(steps)>len(p['changes'])or s.get('save_dispatched')is not True or
           s.get('error')not in {'NATIVE_PROTOCOL_LOCAL_KEY','SUBMISSION_API_OPTION_CONTEXT'}or
           len(s.get('requests',[]))!=len(steps)or any(r.get('status')!='received'for r in s['requests'])):raise ValueError('选项部分保存须原脚本结束且每次HTTP响应完整，无传输未知')
        for step,change in zip(steps,p['changes']):
            if any(step.get(k)!=v for k,v in change.items())or step.get('state')not in {'accepted','dispatched'}:raise ValueError('选项部分回读不属于原顺序步骤')
            if change['kind']=='print':target['print']=change['value']
            else:
                matches=[row for row in target['proofs']if all(row[k]==change[k]for k in ['ord','type','guid'])]
                if len(matches)!=1:raise ValueError('辅助证据部分保存身份不唯一')
                matches[0]['selected']=change['value']
    elif outcome=='not_applied'and s.get('save_dispatched')is False and s.get('state')=='rejected_before_save':target=p['original']
    else:raise ValueError('只接受完整saved或明确派发前not_applied')
    from copy import deepcopy
    target=deepcopy(target)
    for row in target['proofs']:
        if row['contract']and row['selected']=='N':row['cells'][2]=''
    if observed['original']!=target:raise ValueError('合同/打印选择或未修改辅助证据不符')
    return {'schema':SCHEMA,'independent_http_readback':True,'proof_groups':len(target['proofs']),'delegate_finance_print':target['print']=='Y','outcome':outcome,'workflow_started_by_action':False,'finalApproval':False}


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--recover',action='store_true');a=ap.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
