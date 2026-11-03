"""日常建单：固定HTTP、原pending、原marker恢复及独立GUID归属核验。"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT)not in sys.path:sys.path.insert(0,str(ROOT))
SCHEMA='ustc-native-create-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def frozen(path,value):
    from finance_native_stage import frozen_json
    frozen_json(Path(path),value)


def ref(path):
    return {'path':str(Path(path).resolve()),'sha256':sha(path)}


def checked(reference):
    if sha(reference['path'])!=reference['sha256']:raise ValueError('原建单证据SHA变化')
    return read(reference['path'])


def selection(paths,case,who,*,inspect_only=False):
    from finance_native_selection import verify_plan
    from invoice_selection import binding,flatten_audits,money
    from finance_draft_readback import validate_creation_selection
    f,m=read(paths['form']),read(paths['invoices'])
    if f['case_id']!=case or m['case_id']!=case or any(v.get('expected_userid')not in (None,'',who)for v in [f,m]):raise ValueError('建单案件或账号不同')
    eligible=not(f.get('serial')or m.get('serial')or m.get('draft_creation_requested')or f.get('review',{}).get('workflow_started'))
    if f.get('system_mode','daily')!='daily':raise ValueError('此建单适配只处理日常票据，劳务及医疗须独立合同')
    if inspect_only:return f,m,eligible,[]
    if not eligible:raise ValueError('已有单号或创建请求，只恢复原任务，不再次建单')
    if m.get('manual_nonvat_selection'):
        from finance_supplement import creation_selection
        return f,m,eligible,creation_selection(paths,case,who)
    validate_creation_selection(m)
    if m.get('selection_requires_review')or len(m['selected_invoices'])!=f.get('expected_invoice_count')or money(m['expected_amount'])!=money(f['amount']):raise ValueError('本案票数、金额或选择审核未闭合')
    native=m.get('native_selection',{})
    if native.get('schema')!='ustc-native-case-selection-v1' or sha(native['plan'])!=native.get('plan_sha256'):raise ValueError('先按正式HTTP选择计划冻结本案GUID集')
    p=verify_plan(read(native['plan']))
    if p['case_id']!=case or p['request']['expected_userid']!=who or sorted(t['guid']for t in m['selected_invoices'])!=p['after_guids']:raise ValueError('创建选择与同案原冻结计划不同')
    refs=p['evidence'];sources=checked(refs['case_manifest']);audits=flatten_audits([checked(r)for r in refs['audit_receipts']])
    selected=[]
    for ticket in m['selected_invoices']:
        b=binding(p['bindings'][ticket['guid']]['native_row'],sources,audits,require_eligible=True)
        if b!=p['bindings'][ticket['guid']]or b['source_sha256']!=ticket.get('source_sha256')or b['invoice_number']!=ticket['number']or money(b['native_row']['t.tk_total_amt'])!=money(ticket['amount']):raise ValueError('原件、票号、金额或票面审核与冻结选择不同')
        if ticket.get('type')=='MEDICAL':raise ValueError('医疗建单须原生医疗信息分支，不能按普通日常跳过')
        selected.append({**deepcopy(ticket),'native_row':b['native_row']})
    return f,m,eligible,selected


def command(service,request,folder,*,inspect_only=False):
    from finance_http import source
    p=request['parameters'];action=p.get('action');bundle,paths=service.resolve(request['case_id'])
    allowed={'action','tab_id','expected_userid'} if inspect_only else {'action','tab_id','job_id'}
    if set(p)-allowed:raise ValueError('建单仅接受本案动作、明确tab及原恢复job')
    if action=='resume-draft':
        original=service.job_folder(request['case_id'],p.get('job_id'));prior=read(original/'request.json')
        status=service.result(request['case_id'],p['job_id'])
        reconciliation_source=None
        if prior.get('operation')=='invoice' and prior.get('parameters',{}).get('action')=='resume-draft' and prior.get('execute')is True and status['status']not in {'queued','running'}:
            reconciliation_source=original
            original=service.job_folder(request['case_id'],prior['parameters']['job_id']);prior=read(original/'request.json')
            status=service.result(request['case_id'],original.name)
        if prior['operation']!='invoice'or prior['parameters'].get('action')!='create-draft'or prior.get('execute')is not True or status['status']in {'queued','running'}:raise ValueError('只恢复本案已停止的原HTTP建单job')
        input_path=original/'native-create-input.json';c=read(input_path)
        if c.get('schema')!=SCHEMA or c['case_id']!=request['case_id']or c['tab_id']!=p.get('tab_id'):raise ValueError('原建单输入或tab不同')
        if not request['execute']:raise ValueError('resume-draft须execute=true；只读取原marker及独立归属，不重新创建')
        frozen(folder/'create-recovery-source.json',{'input':ref(input_path),'original_job_id':p['job_id'],'no_replay':True})
        return [service.python,str(ROOT/'tools/finance_native_create.py'),'--input',str(input_path),'--output',str(folder/'output.json'),'--resume',
                *(['--reconciliation-source',str(reconciliation_source)]if reconciliation_source else[])]
    if action not in {'create-draft','create-prepare'}or(action=='create-prepare')!=inspect_only:raise ValueError('未知原生建单动作')
    form=read(paths['form']);who=p.get('expected_userid')or form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    if not isinstance(who,str)or not re.fullmatch(r'[A-Za-z0-9]{2,40}',who)or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')not in (None,who):raise ValueError('建单须明确同案财务账号')
    f,m,eligible,selected=selection(paths,request['case_id'],who,inspect_only=inspect_only)
    for ticket in selected:
        source_row=next(s for s in m['original_files']if s['sha256']==ticket['source_sha256']);service.material_path(source_row['path'])
    tab=p.get('tab_id')
    if (request['execute']or inspect_only)and(not isinstance(tab,str)or not re.fullmatch(r'\d+',tab)):raise ValueError('建单及原生预检须当前tab')
    domains=read(ROOT/'data/finance_business_contracts.json')['domains']
    c={'schema':SCHEMA,'case_id':request['case_id'],'bundle':str(bundle),'request_id':request['request_id'],
       'expected_userid':who,'expected_amount':f['amount'],'bill_type':f.get('native_bill_type','1'),'creation_eligible':eligible,'selected':selected,
       'tab_id':tab,'execute':request['execute'],'inspect_only':inspect_only,
       'form':ref(paths['form']),'form_before':f,'manifest':ref(paths['invoices']),'manifest_before':m,
       'pool_contracts':domains['invoices'],'used_contracts':domains['used_invoices'],
       'create_contract':read(ROOT/'data/finance_create_contract.json'),
       'token':hashlib.sha256((request['case_id']+':'+request['request_id']).encode()).hexdigest()}
    adapter=folder/'native-create-adapter.js'
    code=source('create')
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原建单适配器源码漂移，不覆盖')
    else:
        with adapter.open('x',encoding='utf-8',newline='\n')as out:out.write(code)
        adapter.chmod(0o600)
    c['adapter']=ref(adapter);input_path=folder/'native-create-input.json';frozen(input_path,c)
    return [service.python,str(ROOT/'tools/finance_native_create.py'),'--input',str(input_path),'--output',str(folder/'output.json')]


def browser(c,method,output,extra=None,*,write=False):
    from registered_page_owner import RegisteredPageOwner
    adapter=checked_text(c['adapter']);config={**c,**(extra or {})}
    script=Path(output).with_suffix('.js');code='(async()=>{'+adapter+';return await '+method+'('+json.dumps(config,ensure_ascii=False)+');})()'
    with script.open('x',encoding='utf-8',newline='\n')as out:out.write(code)
    script.chmod(0o600);RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(output),'--tab-id',c['tab_id'],
                    '--quiet','--timeout','60','--operation-kind','write'if write else'read'],check=False)
    if not Path(output).exists():raise ValueError('原建单子任务未留下回执，保留原pending')
    return read(output)


def checked_text(reference):
    if sha(reference['path'])!=reference['sha256']:raise ValueError('原建单适配器源码SHA变化')
    return Path(reference['path']).read_text()


def validate_prepare(c,envelope):
    p=envelope.get('result',{})
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or p.get('ok')is not True or p.get('complete')is not True or p.get('financial_write')is not False or any(p.get(k)!=c[k]for k in ['case_id','expected_userid','creation_eligible']):raise ValueError('原生建单预检未闭合')
    if p.get('definition_sha256')!=c['create_contract']['definition_sha256']or p.get('template_sha256')!=c['create_contract']['template_sha256']or p.get('targets')!=[t['native_row']for t in c['selected']]:raise ValueError('原生预检定义或选择不符')
    return p


def original_result(c,runtime_path,output,*,resume=False):
    envelope=read(runtime_path);script=Path(envelope['script'])
    if sha(script)!=envelope.get('source_sha256')or envelope.get('tab_id')!=c['tab_id']:raise ValueError('原建单源码或tab变化')
    expected='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementCreateExecute('+json.dumps({**c,'prepared':checked(c['prepared'])['result'],'pending_registered':True},ensure_ascii=False)+');})()'
    if script.read_text()!=expected:raise ValueError('原建单不是冻结输入的固定创建脚本')
    if envelope.get('phase')=='script_completed':return envelope['result'],ref(runtime_path),envelope['executed_at_utc']
    if not resume:raise ValueError('建单响应未知，保留原marker，只恢复原任务')
    from finance_backend import assert_original_transport
    from edge_runtime import read_marker,browser_lock
    assert_original_transport(envelope)
    if envelope.get('phase')in {'not_dispatched','intent_prepared'}:raise ValueError('原创建未派发；保留原意图，不自动换任务')
    marker=envelope.get('result_marker')
    if not isinstance(marker,str)or not re.fullmatch('reimbursement-result-[a-f0-9]{32}',marker):raise ValueError('原结果marker缺失')
    with browser_lock():raw=read_marker(marker,c['tab_id'],timeout=45)
    if not isinstance(raw,dict):raise ValueError('原创建marker未返回结构化结果，不重发')
    body=raw;recovered={'schema':SCHEMA,'original_runtime':ref(runtime_path),'tab_id':c['tab_id'],'result':body,
       'script_reexecuted':False,'marker_state':'result_found','observed_at_utc':datetime.now(timezone.utc).isoformat()}
    path=Path(output).with_name('native-create-recovered.json');frozen(path,recovered)
    return body,ref(path),recovered['observed_at_utc']


def verify_used(c,created,envelope,created_at,*,reconciled_serial=None):
    from validate_config import money
    r=envelope.get('result',{});serial=reconciled_serial or created.get('serial')
    unknown_ok=(reconciled_serial is not None and created.get('ok')is False and created.get('state')=='create_result_unknown'
        and created.get('marker',{}).get('case_id')==c['case_id']
        and created.get('marker',{}).get('userid')==c['expected_userid'] and created.get('marker',{}).get('token')==c['token'])
    if(created.get('case_id')!=c['case_id']or created.get('expected_userid')!=c['expected_userid']or created.get('token')!=c['token']or created.get('create_dispatched')is not True or not re.fullmatch(r'\d{1,20}',serial or'')or
       not unknown_ok and (created.get('ok')is not True or created.get('state')!='created_pending_readback'or created.get('server_accepted')is not True or created.get('marker',{}).get('serial')!=serial)):
        raise ValueError('原创建未返回可核的新单号')
    script=Path(envelope['script']);config={**c,'action':'bill-read','domain':'used_invoices','serial':serial,'contracts':c['used_contracts']}
    expected='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementBusinessApi('+json.dumps(config,ensure_ascii=False)+');})()'
    if sha(script)!=envelope.get('source_sha256')or script.read_text()!=expected:raise ValueError('独立回读不是原创建输入绑定的固定归属查询')
    if(envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or datetime.fromisoformat(envelope['executed_at_utc'])<=datetime.fromisoformat(created_at)or r.get('ok')is not True or r.get('complete')is not True or r.get('domain')!='used_invoices'or r.get('business_transport')!='native_http'or r.get('serial')!=serial or r.get('case_id')!=c['case_id']or r.get('expected_userid')!=c['expected_userid']or r.get('system_write')is not False or r.get('page_context_modified')is not False):raise ValueError('须原创建之后独立同账号完整归属回读')
    b=r['bill']
    if b.get('t.serial_no')!=serial or not b.get('t.REQ_INFO','').startswith(c['expected_userid']+'-')or b.get('t.status')!='-99'or b.get('t.lc_finish')!='F'or money(b['t.total_amt'].replace(',',''))!=money(c['expected_amount']):raise ValueError('独立回读单号、草稿状态或金额不符')
    sections=r.get('sections',[])
    if len(sections)!=1 or sections[0].get('funcno')!='533'or sections[0].get('complete')is not True:raise ValueError('须完整已报销票据查询，不接受临时选择集')
    rows=sections[0]['rows'];by_id={row['t.tk_guid']:row for row in rows}
    if len(by_id)!=len(rows)or set(by_id)!={t['guid']for t in c['selected']}:raise ValueError('新单完整GUID集合不等于原选择集')
    for ticket in c['selected']:
        row=by_id[ticket['guid']]
        if row.get('t.serial_no')!=serial or money(row['t.tk_total_amt'].replace(',',''))!=money(ticket['amount'])or row['t.tk_code_abst']!=ticket['native_row']['t.tk_code_abst']:raise ValueError('新单票号、归属或逐票金额不符')
    return {'serial':serial,'verified_guids':sorted(by_id),'invoice_count':len(rows),'amount':c['expected_amount'],'independent_http_readback':True}


def verify_reconciled_list(c,envelope,created_at):
    from validate_config import money
    script=Path(envelope['script']);config={**c,'action':'bills-list'}
    expected='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementBusinessApi('+json.dumps(config,ensure_ascii=False)+');})()'
    r=envelope.get('result',{})
    if(envelope.get('phase')!='script_completed' or envelope.get('tab_id')!=c['tab_id'] or sha(script)!=envelope.get('source_sha256') or script.read_text()!=expected
       or datetime.fromisoformat(envelope['executed_at_utc'])<=datetime.fromisoformat(created_at) or r.get('ok')is not True or r.get('complete')is not True
       or r.get('case_id')!=c['case_id']or r.get('expected_userid')!=c['expected_userid']or r.get('action')!='bills-list'or r.get('account_scope')!='current_applicant'
       or r.get('business_transport')!='native_http'or r.get('system_write')is not False or not isinstance(r.get('rows'),list) or len(r['rows'])!=r.get('total_records')):raise ValueError('须原创建之后的完整本人清单，不接任意单号')
    serials=[row.get('t.serial_no')for row in r['rows']]
    if len(set(serials))!=len(serials)or any(not re.fullmatch(r'\d+',str(x))for x in serials)or any(not row.get('t.REQ_INFO','').startswith(c['expected_userid']+'-')for row in r['rows']):raise ValueError('本人清单单号或身份不完整')
    candidates=[row['t.serial_no']for row in r['rows']if row.get('t.status')=='-99'and row.get('t.lc_finish')=='F'and money(row['t.total_amt'].replace(',',''))==money(c['expected_amount'])]
    if not 1<=len(candidates)<=20:raise ValueError('未找到有界候选草稿，保留原创建结果未知，不重建')
    return candidates


def verify_reconciliation(c,created,created_at,proof):
    listing=checked(proof['bills']);candidates=verify_reconciled_list(c,listing,created_at)
    refs=proof['candidate_readbacks']
    if sorted(refs)!=sorted(candidates):raise ValueError('候选草稿未逐一完整核对')
    matches=[]
    for serial in candidates:
        try:verified=verify_used(c,created,checked(refs[serial]),created_at,reconciled_serial=serial)
        except ValueError as error:
            if str(error)!='新单完整GUID集合不等于原选择集':raise
        else:matches.append(verified)
    if len(matches)!=1 or proof.get('serial')!=matches[0]['serial']:raise ValueError('原选择的全部GUID未唯一归属同一草稿；不绑定、不重建')
    return matches[0]


def reconcile_unknown(c,created,created_at,folder):
    listing_path=folder/'native-create-reconcile-bills.json'
    listing=read(listing_path)if listing_path.exists()else browser(c,'reimbursementBusinessApi',listing_path,{'action':'bills-list'})
    candidates=verify_reconciled_list(c,listing,created_at);refs={};matches=[]
    for serial in candidates:
        path=folder/('native-create-reconcile-used-'+serial+'.json')
        observed=read(path)if path.exists()else browser(c,'reimbursementBusinessApi',path,{'action':'bill-read','domain':'used_invoices','serial':serial,'contracts':c['used_contracts']});refs[serial]=ref(path)
        try:verify_used(c,created,observed,created_at,reconciled_serial=serial)
        except ValueError as error:
            if str(error)!='新单完整GUID集合不等于原选择集':raise
        else:matches.append(serial)
    if len(matches)!=1:raise ValueError('未知创建未唯一找到原GUID集合，保留原pending')
    proof={'schema':'ustc-native-create-reconciliation-v1','bills':ref(listing_path),'candidate_readbacks':refs,'serial':matches[0],
        'original_creation_outcome':'unknown_preserved','creation_replayed':False}
    verified=verify_reconciliation(c,created,created_at,proof)
    return proof,verified,refs[matches[0]]


def run(input_path,output,*,resume=False,reconciliation_source=None):
    from operator_support import bundle
    from reimburse import begin_write
    from assistant_api import atomic_json
    c=read(input_path);_,paths=bundle(c['bundle']);output=Path(output)
    if c.get('schema')!=SCHEMA:raise ValueError('不是原HTTP建单输入')
    checked_text(c['adapter'])
    if not resume:
        checked(c['form']);checked(c['manifest'])
        if not c['inspect_only']:selection(paths,c['case_id'],c['expected_userid'])
        if not c['execute']and not c['inspect_only']:
            frozen(output,{'ok':True,'schema':SCHEMA,'state':'preview','business_transport':'native_http','system_write':False,
                'selected_count':len(c['selected']),'expected_amount':c['expected_amount'],'requires_independent_readback':True,'finalApproval':False});return 0
        prepared_path=output.parent/'native-create-prepare.json'
        prepared=browser(c,'reimbursementCreatePrepare',prepared_path);plan=validate_prepare(c,prepared)
        if c['inspect_only']:frozen(output,{**plan,'schema':SCHEMA,'actual_create_tested':False});return 0
        checked(c['form']);checked(c['manifest']);selection(paths,c['case_id'],c['expected_userid'])
        c['prepared']=ref(prepared_path)
        # Final immutable execution input is separate: the preflight input is never rewritten.
        execution_path=output.parent/'native-create-execution.json';frozen(execution_path,c)
        verification={'schema':SCHEMA,'input':ref(execution_path),'runtime':str(output.parent/'native-create.json')}
        begin_write(paths,read(paths['form']),'invoice:create-draft',output.parent,verification)
        m=read(paths['invoices']);m.update(draft_creation_requested=True,creation_receipt=verification['runtime'],native_creation_intent=ref(execution_path));atomic_json(paths['invoices'],m)
        browser(c,'reimbursementCreateExecute',Path(verification['runtime']),{'prepared':plan,'pending_registered':True},write=True)
    else:
        execution_path=Path(input_path).parent/'native-create-execution.json';c=read(execution_path)
        pending=read(paths['state'].parent/'pending_write.json')
        if pending.get('operation')!='invoice:create-draft'or pending.get('case_id')!=c['case_id']or pending.get('verification',{}).get('input')!=ref(execution_path):raise ValueError('恢复不属于本案原创建pending')
    m=read(paths['invoices']);expected=deepcopy(c['manifest_before']);expected.update(draft_creation_requested=True,creation_receipt=str(Path(execution_path).parent/'native-create.json'),native_creation_intent=ref(execution_path))
    if m!=expected or sha(paths['form'])!=c['form']['sha256']:raise ValueError('原创建期间本案manifest或表单变化，保留原任务')
    runtime_path=Path(execution_path).parent/'native-create.json'
    created,origin,created_at=original_result(c,runtime_path,output,resume=resume)
    unknown_dispatched=not created.get('ok')and created.get('state')=='create_result_unknown'and created.get('create_dispatched')is True
    # 类型2的学校原生创建可自动生成补助，成功响应也必须读取实际初始总额。
    domestic_dispatched=c.get('bill_type')=='2'and created.get('create_dispatched')is True and (created.get('ok')is True or unknown_dispatched)
    if unknown_dispatched or domestic_dispatched:
        source_folder=output.parent
        if reconciliation_source is not None:
            source_folder=Path(reconciliation_source);prior=read(source_folder/'request.json')
            if not resume or source_folder.parent!=Path(input_path).parent.parent or prior.get('case_id')!=c['case_id']or prior.get('operation')!='invoice' or prior.get('execute')is not True or prior.get('parameters',{}).get('action')!='resume-draft' or prior['parameters'].get('job_id')!=Path(input_path).parent.name or prior['parameters'].get('tab_id')!=c['tab_id']:
                raise ValueError('只复用同案原创建的已停止只读恢复任务')
            if read(runtime_path).get('phase')!='script_completed':raise ValueError('原创建仍在执行，不能跳过原marker')
        if c.get('bill_type')=='2':
            from finance_domestic_initial import reconcile
            proof,verified,observed=reconcile(c,created,created_at,source_folder)
        else:proof,verified,observed=reconcile_unknown(c,created,created_at,source_folder)
        frozen(output,{'ok':True,'schema':SCHEMA,'case_id':c['case_id'],'state':'created_pending_local_binding','business_transport':'native_http',
          'input':ref(execution_path),'original_creation':origin,'readback':observed,'verification':verified,'reconciliation':proof,
          'system_write':True,'draft_created':True,'local_bound':False,'submitted':False,'finalApproval':False});return 0
    if not created.get('ok'):
        frozen(output,{'ok':False,'schema':SCHEMA,'state':created.get('state'),'original_creation':origin,'result':created,'pending_preserved':True,'system_write':created.get('system_write',False)});return 2
    read_path=output.parent/'native-create-readback.json'
    observed=browser(c,'reimbursementBusinessApi',read_path,{'action':'bill-read','domain':'used_invoices','serial':created['serial'],'contracts':c['used_contracts']})
    verified=verify_used(c,created,observed,created_at)
    frozen(output,{'ok':True,'schema':SCHEMA,'case_id':c['case_id'],'state':'created_pending_local_binding','business_transport':'native_http',
      'input':ref(execution_path),'original_creation':origin,'readback':ref(read_path),'verification':verified,
      'system_write':True,'draft_created':True,'local_bound':False,'submitted':False,'finalApproval':False});return 0


def bind_created(paths,receipt,serial):
    """只消费正式原创建及独立完整归属证明，不发出财务请求。"""
    from assistant_api import atomic_json
    value=read(receipt);c=checked(value['input']);pending_path=paths['state'].parent/'pending_write.json';pending=read(pending_path)
    if value.get('schema')!=SCHEMA or value.get('ok')is not True or pending.get('case_id')!=c['case_id']or pending.get('operation')!='invoice:create-draft'or pending.get('verification',{}).get('input')!=value['input']or Path(value['input']['path']).resolve()!=(Path(pending['output_directory'])/'native-create-execution.json').resolve():raise ValueError('须本案原HTTP建单和原pending')
    expected_manifest=deepcopy(c['manifest_before']);expected_manifest.update(draft_creation_requested=True,creation_receipt=pending['verification']['runtime'],native_creation_intent=value['input'])
    m=read(paths['invoices']);actual=deepcopy(m);actual.pop('serial',None);expected_manifest.pop('serial',None)
    if actual!=expected_manifest:raise ValueError('原建单选择或材料在绑定前改变')
    form=read(paths['form']);before=c['form_before']
    if {k:v for k,v in form.items()if k!='serial'}!={k:v for k,v in before.items()if k!='serial'}:raise ValueError('原建单表单事实变化')
    original=checked(value['original_creation'])
    if original.get('schema')==SCHEMA:
        if original.get('script_reexecuted')is not False or original.get('marker_state')!='result_found'or original.get('original_runtime')!=ref(pending['verification']['runtime']):raise ValueError('恢复证明未绑定原创建marker')
        created=original['result'];created_at=original['observed_at_utc']
        # Revalidate original fixed script even when the marker supplied its result.
        envelope=read(pending['verification']['runtime']);script=Path(envelope['script'])
        expected='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementCreateExecute('+json.dumps({**c,'prepared':checked(c['prepared'])['result'],'pending_registered':True},ensure_ascii=False)+');})()'
        if sha(script)!=envelope['source_sha256']or script.read_text()!=expected:raise ValueError('原创建源码变化')
    else:
        if value['original_creation']!=ref(pending['verification']['runtime']):raise ValueError('创建回执不属于原pending')
        created,_,created_at=original_result(c,pending['verification']['runtime'],receipt)
    if value.get('reconciliation'):
        if value['reconciliation'].get('kind')=='native_domestic_initial_with_automatic_allowance':
            from finance_domestic_initial import verify
            verified=verify(c,created,created_at,value['reconciliation'])
            if value['readback']!=value['reconciliation']['used']:raise ValueError('国内差旅绑定未引用原票完整回读')
        else:
            verified=verify_reconciliation(c,created,created_at,value['reconciliation'])
            if value['readback']!=value['reconciliation']['candidate_readbacks'][verified['serial']]:raise ValueError('绑定未引用唯一归属回读')
    else:verified=verify_used(c,created,checked(value['readback']),created_at)
    if verified!=value['verification']or serial!=verified['serial']:raise ValueError('绑定单号或完整归属核验不同')
    for key in ['case','form','invoices','state']:
        record=read(paths[key])
        if record.get('case_id')!=c['case_id']or record.get('serial')not in (None,serial):raise ValueError('本地已有单号或案件不符')
    for key in ['case','form','invoices','state']:
        record=read(paths[key]);record['serial']=serial
        if verified.get('requires_domestic_allowance_correction'):
            if key=='form':record['amount']=verified['amount']
            if key=='invoices':record.update(expected_amount=verified['amount'],invoice_amount=verified['invoice_amount'],native_domestic_initial=ref(receipt))
        if key=='state':record.update(status='draft_created_readback',creation_readback=ref(receipt),last_native_creation=verified)
        atomic_json(paths[key],record)
    ack={**pending,'status':'draft_bound_readback_recorded','outcome':'saved','bound_serial':serial,'readback':ref(receipt),'verification_result':verified}
    frozen(Path(pending['output_directory'])/'operator_readback_ack.json',ack);pending_path.unlink()
    return {'ok':True,'serial':serial,'verified_invoice_count':verified['invoice_count'],'system_write':False,'submitted':False,'finalApproval':False}


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--resume',action='store_true');ap.add_argument('--reconciliation-source',type=Path);a=ap.parse_args()
    raise SystemExit(run(a.input,a.output,resume=a.resume,reconciliation_source=a.reconciliation_source))
