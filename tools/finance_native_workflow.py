"""下一步、承诺和部门选择的原意图合同；最终本人同意及短信保留本人。"""
from __future__ import annotations
import argparse,json,re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from finance_native_create import ref,checked,checked_text,frozen,read,sha
from finance_native_amount import script,browser
ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-workflow-v1'
WRITES={'next','promise','department'}

def rejected_before_dispatch(saved):
    b=saved.get('result',{})
    return (saved.get('phase')=='script_completed' and b.get('ok') is False
            and b.get('state')=='rejected_before_dispatch'
            and b.get('error') in {'WORKFLOW_API_OPTIONS_NOT_SAVED','WORKFLOW_API_REQUIRED_FIELDS_NOT_SAVED_OR_SPECIAL_PROOF_REQUIRED','WORKFLOW_API_NATIVE_WORKFLOW_COMMENT_MISSING','WORKFLOW_API_DEPARTMENT_CANDIDATE_NOT_UNIQUE'}
            and b.get('system_write') is False and b.get('save_dispatched') is False
            and b.get('steps')==[] and b.get('requests')==[])

def completed_next_parse_failure(saved,c):
    """Only the completed next-step check; never infer submit/approval success."""
    b=saved.get('result',{})
    code=checked_text(c['adapter'])
    return (c['action']=='next' and saved.get('phase')=='script_completed' and b.get('ok') is False
            and b.get('state')=='unknown_original_workflow_recovery_required'
            and b.get('error')=='NATIVE_PROTOCOL_LOCAL_SYNTAX'
            and b.get('system_write') is True and b.get('save_dispatched') is True
            and b.get('steps')==[] and b.get('requests')==[]
            and "const raw=await b.http.post('commonUpdate_responseButtonEvent.action'" in code
            and ('const parsed=reimbursementWorkflowApply(raw,b.context);steps.push' in code or 'const parsed=reimbursementWorkflowApply(raw,b.context,c);steps.push' in code)
            and "if(c.action==='next'){" in code)

def captured_next_context(saved,c):
    """Recover captured check response as data without repeating any request."""
    if not completed_next_parse_failure(saved,c):return None
    raw=saved['result'].get('native_response')
    if not isinstance(raw,str) or len(raw)>32768 or not re.fullmatch(r'(?:passlocal\([^()]*\))+',raw):return None
    allowed={'61-JHINFO','0-JIHE_TYPE','0-JIHE_TIP_MSG','61-LC_ISSIGNCA','61-TIPINFO','61-TIPINFO_WITH_TICK'}
    updates={}
    for group in re.findall(r'passlocal\(([^()]*)\)',raw):
        for pair in filter(None,group.split(';')):
            key,sep,value=pair.partition(':');key=key.upper()
            if not sep or key not in allowed or key in updates or ':' in value:return None
            updates[key]=value
    if '61-LC_ISSIGNCA' not in updates or updates['61-LC_ISSIGNCA'] not in {'T','F'}:return None
    messages={k:updates.get(k.upper(),'') for k in ['61-tipinfo','61-jhinfo','61-tipinfo_with_tick']}
    return updates,messages

def department_context(saved,c):
    b=saved.get('result',{})
    if (c['action']!='promise' or saved.get('phase')!='script_completed' or b.get('operation')!='promise'
            or b.get('save_dispatched')is not True or b.get('system_write')is not True
            or b.get('final_applicant_approval_executed')is not False or b.get('sms_sent')is not False):return None
    if b.get('ok')is True and b.get('state')=='department_selection_required':
        updates=b.get('next_context',{})
        if set(updates)=={'0-BUSS_VAR_LIST'} and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',str(updates['0-BUSS_VAR_LIST'])):return updates
    if b.get('ok')is False and b.get('state')=='unknown_original_workflow_recovery_required' and b.get('error')=='NATIVE_PROTOCOL_RESPONSE_UNKNOWN' and b.get('steps')==[] and b.get('requests')==[]:
        match=re.fullmatch(r'afterjs:local\(0-buss_var_list:([A-Z][A-Z0-9_]{0,79})\)',str(b.get('native_response','')))
        if match:return {'0-BUSS_VAR_LIST':match[1]}
    return None

def captured_validation_context(saved,c):
    b=saved.get('result',{})
    if (c['action']!='next' or saved.get('phase')!='script_completed' or b.get('ok')is not False
            or b.get('error')!='NATIVE_PROTOCOL_RESPONSE_UNKNOWN' or b.get('state')!='unknown_original_workflow_recovery_required'
            or b.get('system_write')is not True or b.get('save_dispatched')is not True or b.get('steps')!=[]):return None
    match=re.fullmatch(r'#left#font class="cont_tipword_NORMAL"#right#(还有[1-9][0-9]*个补充说明未填写！)#left#/font#right#',str(b.get('native_response','')))
    return ({},{'61-tipinfo':match[1],'61-jhinfo':'','61-tipinfo_with_tick':''}) if match else None

def captured_submission_instance(saved,c):
    """Read captured native instance identifiers; financial fields still need a full independent read."""
    b=saved.get('result',{})
    if (c['action'] not in {'promise','department'} or saved.get('phase')!='script_completed'
            or b.get('ok')is not False or b.get('state')!='unknown_original_workflow_recovery_required'
            or not str(b.get('error','')).startswith('WORKFLOW_API_CONTEXT_CHANGED:')
            or b.get('system_write')is not True or b.get('save_dispatched')is not True):return None
    raw=b.get('native_response')
    if not isinstance(raw,str) or not raw.startswith('passlocal(') or len(raw)>32768:return None
    fields={}
    names=['61-node_id','61-uni_key','61-wf_name','61-spinfo_serialno','109-wf_inst.NODE_ID','109-wf_inst.UNI_KEY','109-wf_inst.BASETABLE_KEY']
    for name in names:
        hits=re.findall(r'(?:local\(|;)'+re.escape(name)+r':([^;)]*)',raw)
        if len(hits)!=1:return None
        fields[name]=hits[0]
    if (fields['61-node_id']!='508' or not re.fullmatch(r'\d+',fields['61-uni_key']) or fields['61-wf_name']!='YBX_LC'
            or fields['61-spinfo_serialno']!=c['serial'] or fields['109-wf_inst.BASETABLE_KEY']!=c['serial']
            or fields['109-wf_inst.NODE_ID']!='508' or fields['109-wf_inst.UNI_KEY']!=fields['61-uni_key']):return None
    return {'node_id':'508','uni_key':fields['61-uni_key'],'base_key':c['serial'],'wf_name':'YBX_LC'}

def verify_salary_fields(before,after,c,started):
    if before==after:return
    if not started or c.get('system_mode')!='remuneration' or not isinstance(before,dict) or not isinstance(after,dict):raise ValueError('流程改变薪酬字段')
    expected=json.loads(json.dumps(before))
    if expected.get('original',{}).get('dd.remark')=='':expected['original']['dd.remark']=c['plan']['summary']
    if expected!=after:raise ValueError('流程改变薪酬人员、金额或其他原始字段')

def prepare(c,envelope):
    p=envelope.get('result',{})
    if(envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or p.get('ok')is not True or p.get('complete')is not True or p.get('action')!='workflow-inspect'or p.get('business_transport')!='native_http'or p.get('system_write')is not False or p.get('save_dispatched')is not False or any(p.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or p.get('definition_sha256')!=c['workflow_contract']['definition_sha256']or p.get('final_applicant_approval_executed')is not False or p.get('sms_sent')is not False):raise ValueError('须完整同单原生流程检查')
    return p

def next_context(proof,c):
    original=checked(proof['input']);p=prepare(original,checked(proof['prepare']));saved=checked(proof['receipt']);b=saved.get('result',{})
    for k in ['case_id','serial','expected_userid','tab_id','case','form','invoices','workflow_contract','system_mode']:
        if original.get(k)!=c.get(k):raise ValueError('原下一步不是本案同号来源：'+k)
    department=department_context(saved,original)
    if department:
        if (original.get('schema')!=SCHEMA or not original.get('next_proof') or saved.get('tab_id')!=c['tab_id'] or b.get('action')!='workflow-save' or any(b.get(k)!=c[k] for k in ['case_id','serial','expected_userid']) or sha(saved['script'])!=saved.get('source_sha256') or Path(saved['script']).read_text()!=script(original,'reimbursementWorkflowSave',{'pending_registered':True,'expected_plan':p})):raise ValueError('单位审批人接续未绑定原提交意图')
        updates,messages=next_context(original['next_proof'],c)
        return {**updates,**department},messages
    recovered=captured_next_context(saved,original) or captured_validation_context(saved,original)
    if(original.get('schema')!=SCHEMA or original.get('action')!='next'or saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or (b.get('ok')is not True and not recovered) or b.get('operation')!='next'or b.get('action')!='workflow-save'or any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script(original,'reimbursementWorkflowSave',{'pending_registered':True,'expected_plan':p})or (not recovered and (not isinstance(b.get('next_context'),dict)or not isinstance(b.get('messages'),dict)))):raise ValueError('原下一步回执不完整或源码漂移，只恢复原marker')
    return recovered or (b['next_context'],b['messages'])

def command(service,request,folder):
    from submission import validate_plan
    from finance_http import source
    p=request['parameters'];bundle,paths=service.resolve(request['case_id']);form=read(paths['form']);case=read(paths['case']);plan=p['plan'];validate_plan(plan,form);action=p['action']
    if action not in WRITES|{'workflow-inspect','applicant-prepare','workflow-readback','workflow-recover'}or request['execute']!=(action in WRITES)or set(p)-{'action','plan','tab_id','job_id','artifact_id'}:raise ValueError('流程动作及execute不符；最终本人同意/SMS不由本接口执行')
    if action in WRITES:
        from operator_support import unresolved_business_corrections
        if unresolved_business_corrections(case):raise ValueError('本案分单或业务范围仍待纠正，不能把保存回读当作可提交流程')
    who=plan.get('expected_userid')or form.get('expected_userid');tab=p.get('tab_id')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',str(who))or not re.fullmatch(r'\d+',str(tab))or form.get('expected_userid')not in {None,'',who}:raise ValueError('须本案本人及标签')
    if action=='workflow-recover':
        old=service.job_folder(request['case_id'],p.get('job_id'));c=read(old/'native-workflow-input.json')
        if c.get('case_id')!=request['case_id']or c.get('tab_id')!=tab or c.get('expected_userid')!=who or c.get('plan')!=plan:raise ValueError('原流程恢复身份不同')
        return[service.python,str(ROOT/'tools/finance_native_workflow.py'),'--input',str(old/'native-workflow-input.json'),'--output',str(folder/'output.json'),'--recover']
    c={'schema':SCHEMA,'case_id':request['case_id'],'serial':form['serial'],'expected_amount':form['amount'],'expected_userid':who,'tab_id':tab,'action':action,'plan':plan,'execute':request['execute'],'request_id':request['request_id'],'bundle':str(bundle),
       'case':ref(paths['case']),'form':ref(paths['form']),'invoices':ref(paths['invoices']),'system_mode':form.get('system_mode','daily'),'funding':form['funding'],
       'workflow_contract':read(ROOT/'data/finance_workflow_contract.json'),'submission_contract':read(ROOT/'data/finance_submission_contract.json')}
    domains=read(ROOT/'data/finance_business_contracts.json')['domains'];c.update(basic_contracts=domains['basic'],used_contracts=domains['used_invoices'],payment_contracts=domains['payments'],budget_contracts=domains['categories'],pre_review_contracts=domains['pre_review'])
    from finance_native_stage import stage_values,stage_contract
    c.update({**stage_values(form,'payments'),**stage_values(form,'expenses')});c.update(payments_contract=stage_contract('payments'),expenses_contract=stage_contract('expenses'),pre_review=form.get('pre_review',[]),pre_review_contract=stage_contract('pre-review'))
    if c['system_mode']=='remuneration':
        from finance_remuneration_entry import activity_context
        from finance_remuneration_save import validate_bound_case
        validate_bound_case(form);c.update(activity=activity_context(case),remuneration_contract=read(ROOT/'data/finance_remuneration_contract.json'))
    elif c['system_mode']=='daily':
        from validate_config import money
        inv=read(paths['invoices']);rows=inv.get('selected_invoices',[])
        allowance=False
        if inv.get('native_allowance_source'):
            from finance_allowance import funding_context
            c['allowance_native_source']=funding_context(inv,form);allowance=True
        if inv.get('native_domestic_allowance_source'):
            from finance_domestic_allowance import funding_context
            c['domestic_allowance_source']=funding_context(inv,form);allowance=True
        if inv.get('serial')!=c['serial']or inv.get('case_id')!=c['case_id']or len(rows)!=form['expected_invoice_count']or len({r['guid']for r in rows})!=len(rows)or (not allowance and sum((money(r['amount'])for r in rows),money('0'))<money(c['expected_amount'])):raise ValueError('原票据清单、GUID或票面合计未核')
        c['invoice_guids']=[row['guid']for row in rows]
    else:raise ValueError('未登记流程业务类型')
    if action in {'promise','department'}or(action in {'workflow-inspect','applicant-prepare'}and p.get('job_id')):
        receipt,_,origin=service._typed_case_artifact(request['case_id'],p.get('job_id'),p.get('artifact_id'),'submission',['next','promise','workflow-recover']);old=service.job_folder(request['case_id'],p['job_id'])
        if origin['parameters']['action']=='workflow-recover':
            old=service.job_folder(request['case_id'],origin['parameters']['job_id'])
            recovered=read(receipt);saved_ref=recovered.get('original_save_recovery')
            checked(saved_ref)
            original=read(old/'output.json');saved=checked(saved_ref);binding=saved.get('original_marker_recovery',{})
            if original.get('phase')!='script_completed'and (binding.get('path')!=str((old/'output.json').resolve())or binding.get('sha256')!=sha(old/'output.json')or binding.get('script_reexecuted')is not False):raise ValueError('接续恢复必须绑定原marker，不能使用其他保存回执')
            if any(saved.get(k)!=original.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('接续恢复原脚本身份不同')
        else:saved_ref=ref(receipt)
        proof={'input':ref(old/'native-workflow-input.json'),'prepare':ref(old/'native-workflow-prepare.json'),'receipt':saved_ref};updates,messages=next_context(proof,c)
        c.update(next_proof=proof,next_context=updates,next_messages=messages,next_verified=True)
    elif action=='workflow-readback':
        receipt,_,_=service._typed_case_artifact(request['case_id'],p.get('job_id'),p.get('artifact_id'),'submission',sorted(WRITES));old=service.job_folder(request['case_id'],p['job_id']);original=read(old/'native-workflow-input.json')
        if any(original.get(k)!=c.get(k)for k in ['case_id','serial','expected_userid','tab_id','case','form','invoices','plan']):raise ValueError('原流程回读身份或计划变化')
        c['readback_proof']={'input':ref(old/'native-workflow-input.json'),'prepare':ref(old/'native-workflow-prepare.json'),'receipt':ref(receipt)}
        if original['action']=='next' and (captured_next_context(read(receipt),original) or captured_validation_context(read(receipt),original) or not (rejected_before_dispatch(read(receipt)) or completed_next_parse_failure(read(receipt),original))):
            updates,messages=next_context(c['readback_proof'],c);c.update(next_context=updates,next_messages=messages,next_verified=True)
        elif department_context(read(receipt),original):
            updates,messages=next_context(c['readback_proof'],c);c.update(next_context=updates,next_messages=messages,next_verified=True)
        elif original.get('next_proof'):
            updates,messages=next_context(original['next_proof'],c);c.update(next_proof=original['next_proof'],next_context=updates,next_messages=messages,next_verified=True)
    adapter=folder/'native-workflow-adapter.js'
    code=checked_text(original['adapter']) if action=='workflow-readback' else source('workflow')
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原流程适配器源码漂移')
    else:adapter.write_text(code,encoding='utf-8',newline='\n');adapter.chmod(0o600)
    c['adapter']=ref(adapter);path=folder/'native-workflow-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_native_workflow.py'),'--input',str(path),'--output',str(folder/'output.json')]

def validate(c):
    from submission import validate_plan
    case=checked(c['case']);form=checked(c['form']);checked(c['invoices']);checked_text(c['adapter']);validate_plan(c['plan'],form)
    if c['action']in WRITES:
        from operator_support import unresolved_business_corrections
        if unresolved_business_corrections(case):raise ValueError('本案分单或业务范围仍待纠正，不发起流程')
    if c.get('schema')!=SCHEMA or c['workflow_contract']!=read(ROOT/'data/finance_workflow_contract.json'):raise ValueError('流程固定合同漂移')
    if c.get('next_proof'):
        updates,messages=next_context(c['next_proof'],c)
        if updates!=c['next_context']or messages!=c['next_messages']or c.get('next_verified')is not True:raise ValueError('原下一步上下文变化')
    if c['system_mode']=='remuneration':
        from finance_remuneration_entry import activity_context
        if c['activity']!=activity_context(case):raise ValueError('劳务来源变化')

def run(input_path,output,recover=False):
    from operator_support import bundle
    from reimburse import begin_write
    c=read(input_path);validate(c);_,paths=bundle(c['bundle']);output=Path(output)
    if recover:
        from edge_runtime import read_marker,browser_lock
        from finance_backend import assert_original_transport
        from assistant_api import atomic_json
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if v.get('schema')!=SCHEMA or v.get('input')!=ref(input_path):raise ValueError('只恢复原流程意图')
        p=prepare(c,checked(v['prepare']));old=read(v['save_receipt'])
        if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=script(c,'reimbursementWorkflowSave',{'pending_registered':True,'expected_plan':p}):raise ValueError('原流程源码漂移')
        if old.get('phase')=='script_completed':saved=old
        else:
            if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原流程未派发或缺marker')
            assert_original_transport(old)
            with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
            if not isinstance(body,dict):raise ValueError('原流程marker尚未返回')
            saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now(timezone.utc).isoformat(),'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
        target=output.parent/'original-workflow-result.json';frozen(target,saved);proof={'input':ref(input_path),'prepare':v['prepare'],'receipt':ref(target)};reader={**c,'action':'workflow-readback','execute':False,'readback_proof':proof}
        if c['action']=='next' or department_context(saved,c):updates,messages=next_context(proof,c);reader.update(next_context=updates,next_messages=messages,next_verified=True)
        value=browser(reader,'reimbursementWorkflowInspect',output);prepare(c,value);value['original_save_recovery']=ref(target);atomic_json(output,value);return 0
    if c['action']not in WRITES:prepare(c,browser(c,'reimbursementWorkflowInspect',output));return 0
    if(paths['state'].parent/'pending_write.json').exists():raise ValueError('原财务写入未核，不重发流程')
    target=output.parent/'native-workflow-prepare.json';p=prepare(c,browser(c,'reimbursementWorkflowInspect',target));validate(c)
    if p['original']['bill']['t.status']!='-99'or p['native_error_present']:raise ValueError('原单不是无错误草稿，先处理材料及当前流程状态')
    if p.get('required_changes'):raise ValueError('原单仍有未保存业务输入，先完成对应保存和独立回读')
    if p['original']['options'].get('print')!=('Y'if c['plan']['delegate_finance_print'] else'N') or any(row.get('contract') and row.get('selected')!='N' for row in p['original']['options'].get('proofs',[])):raise ValueError('先保存本案无合同及已确认打印选项并独立回读，再进入下一步')
    begin_write(paths,read(paths['form']),'submission:http-'+c['action'],output.parent,{'schema':SCHEMA,'input':ref(input_path),'prepare':ref(target),'save_receipt':str(output)})
    saved=browser(c,'reimbursementWorkflowSave',output,{'pending_registered':True,'expected_plan':p});return 0 if saved.get('result',{}).get('ok')is True else 2

def verify_ack(previous,envelope,outcome):
    v=previous['verification'];c=checked(v['input']);validate(c);p=prepare(c,checked(v['prepare']));saved=read(v['save_receipt']);receipt=envelope.get('original_save_recovery')or ref(v['save_receipt'])
    if envelope.get('original_save_recovery'):
        recovered=checked(receipt);binding=recovered.get('original_marker_recovery',{})
        if saved.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('流程恢复未引用原marker')
        if any(recovered.get(k)!=saved.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('流程恢复身份变化')
        saved=recovered
    b=saved.get('result',{})
    parse_failure=completed_next_parse_failure(saved,c)
    recovered_next=captured_next_context(saved,c) or captured_validation_context(saved,c)
    recovered_department=department_context(saved,c)
    recovered_submission=captured_submission_instance(saved,c)
    rejected=rejected_before_dispatch(saved) or (parse_failure and not recovered_next)
    if(previous.get('operation')!='submission:http-'+c['action']or previous.get('case_id')!=c['case_id']or saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or (b.get('ok')is not True and not rejected and not recovered_next and not recovered_department and not recovered_submission) or b.get('operation')!=c['action']or b.get('action')!='workflow-save'or any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script(c,'reimbursementWorkflowSave',{'pending_registered':True,'expected_plan':p})or b.get('final_applicant_approval_executed')is not False or b.get('sms_sent')is not False):raise ValueError('原流程动作尚未完整确认，只恢复原marker')
    r=prepare(c,envelope)
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('流程回读须晚于原动作完成')
    text=Path(envelope['script']).read_text();prefix='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementWorkflowInspect('
    if sha(envelope['script'])!=envelope.get('source_sha256')or not text.startswith(prefix)or not text.endswith(');})()'):raise ValueError('不是原固定流程回读源码')
    reader=json.loads(text[len(prefix):-len(');})()')]);proof={'input':v['input'],'prepare':v['prepare'],'receipt':receipt}
    if reader.get('readback_proof')!=proof:raise ValueError('流程回读未绑定原意图')
    for key in ['case_id','serial','expected_amount','expected_userid','tab_id','case','form','invoices','plan','funding','system_mode','invoice_guids','activity','workflow_contract','submission_contract','basic_contracts','used_contracts']:
        if reader.get(key)!=c.get(key):raise ValueError('流程回读来源变化：'+key)
    if rejected:
        if outcome!='not_applied' or r['original']!=p['original']:raise ValueError('派发前拒绝须完整原单未变回读，只登记not_applied')
        return {'schema':SCHEMA,'operation':c['action'],'outcome':outcome,'source_input':v['input'],'independent_http_readback':True,'workflow_started':False,'state':b['state'],'completed_validation_response_unparsed':parse_failure,'original_unchanged':True,'final_applicant_approval_executed':False,'sms_sent':False}
    started=b.get('workflow_started_by_action')is True or recovered_submission is not None
    before_invoices=p['original']['invoices'];after_invoices=r['original']['invoices']
    if before_invoices!=after_invoices:
        if not started or r['original']['bill'].get('t.node_id')!='508' or len(before_invoices)!=len(after_invoices):raise ValueError('流程票据集合或状态不符')
        for before_row,after_row in zip(before_invoices,after_invoices):
            if before_row.get('t.status_name')!='草稿' or after_row.get('t.status_name')!='申请人' or {k:v for k,v in before_row.items() if k!='t.status_name'}!={k:v for k,v in after_row.items() if k!='t.status_name'}:raise ValueError('流程改变票据身份或金额，不能仅按状态放行')
    verify_salary_fields(p['original']['salary'],r['original']['salary'],c,started)
    for key in ['basic','options','payments','budget','pre_review']:
        if r['original'][key]!=p['original'][key]:raise ValueError('流程改变其他原始字段：'+key)
    workflow_fields={'t.status','t.status_name','t.status_code_name','t.status_name_dtl','t.node_id','t.wf_name','t.uni_key','t.project_id','t.basetable_key','t.req_no','t.lc_finish','t.process_opinion','t.bxpdf_desc','t.button','t.log_id'}
    if started and c.get('system_mode')=='remuneration' and p['original']['bill'].get('t.cj_user')=='' and r['original']['bill'].get('t.cj_user')=='XC':workflow_fields.add('t.cj_user')
    if started and p['original']['bill'].get('t.req_date')!=r['original']['bill'].get('t.req_date'):
        native_date=datetime.fromisoformat(saved['executed_at_utc']).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        before_date=p['original']['bill'].get('t.req_date','');after_date=r['original']['bill'].get('t.req_date','')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',before_date) or before_date>after_date or after_date!=native_date:raise ValueError('原生申请日期并非本次提交日期')
        workflow_fields.add('t.req_date')
    if set(r['original']['bill'])!=set(p['original']['bill'])or any(r['original']['bill'][key]!=value for key,value in p['original']['bill'].items()if key not in workflow_fields):raise ValueError('流程改变单据身份、金额或其他业务字段')
    if started:
        bill=r['original']['bill']
        instance=recovered_submission or b.get('workflow_instance')
        if instance:
            if instance.get('base_key')!=c['serial'] or instance.get('wf_name')!='YBX_LC' or any(bill.get('t.'+key)!=instance.get(key) for key in ['node_id','uni_key','wf_name']) or bill.get('t.basetable_key')!=c['serial']:raise ValueError('原生流程实例及原单主键回读不一致')
        elif bill['t.uni_key']!='U'+c['serial']:raise ValueError('缺少当前原生流程实例来源')
        if outcome!='saved'or bill['t.status']=='-99'or bill['t.wf_name']!='YBX_LC'or not re.fullmatch(r'\d+',bill['t.node_id']):raise ValueError('流程尚未独立读到本单真实实例和节点')
    elif outcome!='staged'or r['original']['bill']!=p['original']['bill']:raise ValueError('仅下一步或待分工可登记staged，不能记成已提交')
    return{'schema':SCHEMA,'operation':c['action'],'outcome':outcome,'source_input':v['input'],'independent_http_readback':True,'workflow_started':started,'state':b['state'],'applicant_action_required':started,'final_applicant_approval_executed':False,'sms_sent':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--recover',action='store_true');a=p.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
