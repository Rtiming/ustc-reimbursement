"""原生逐票金额合同：初始化、完整金额读取与持久金额保存分开留证。"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
from finance_native_create import ref,checked,checked_text,frozen,read,sha

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-invoice-amount-v1'


def native_plan_request(request):
    from assistant_api import AssistantService
    p=request['parameters']
    if p.get('action')not in {'open','save'}or not request.get('workspace'):return False
    service=AssistantService(request['workspace'],request.get('material_roots'),request.get('python'))
    try:
        _,data,_=service._typed_case_artifact(request['case_id'],p.get('source_job_id'),p.get('source_artifact_id'),'expense_amount',['inspect'])
        prior=read(service.job_folder(request['case_id'],p['source_job_id'])/'native-amount-input.json')
        preparation(prior,data,rows=p['action']=='save')
        return prior.get('schema')==SCHEMA
    except (ValueError,KeyError,TypeError,OSError):return False


def script(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or{})},ensure_ascii=False)+');})()'


def preparation(c,envelope,*,rows=False):
    b=envelope.get('result',{})
    if(envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or b.get('ok')is not True or
       b.get('complete')is not True or b.get('action')!='amount-inspect'or b.get('business_transport')!='native_http'or
       b.get('system_write')is not False or b.get('save_dispatched')is not False or
       any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or b.get('definition_sha256')!=c['amount_contract']['definition_sha256']):
        raise ValueError('须完整同案同号本人HTTP金额检查')
    if rows and(b.get('amount_rows_complete')is not True or not b.get('rows')or b.get('target_group_found')is not True):
        raise ValueError('金额行须在原初始化后完整独立读取')
    if rows and c.get('include_invoice_allocations')is True and(b.get('allocation_tree_complete')is not True or not isinstance(b.get('allocation_tree'),list)):
        raise ValueError('缺完整独立逐票经费树')
    return b


def initialization(c,depth=0):
    """只有原初始化完成回执可以授予F390读取，caller不能自行声明已初始化。"""
    if depth>2:raise ValueError('金额初始化引用循环或过深')
    proof=c.get('initialization')
    if not proof:
        if c.get('initialization_verified'):raise ValueError('缺原金额初始化证明')
        return None
    original=checked(proof['input']);saved=checked(proof['receipt']);p=preparation(original,checked(proof['prepare']))
    if original.get('schema')!=SCHEMA or original.get('action')not in {'open','save'}or any(original.get(k)!=c[k]for k in ['case_id','serial','group','subject','expected_userid','tab_id','form']):
        raise ValueError('金额初始化不是同案同分组原任务')
    b=saved.get('result',{});is_save=original['action']=='save'
    if is_save:
        initialization(original,depth+1)
        refresh=b.get('persistence_refresh',{})
        if b.get('branch',{}).get('allocation_required')is not False or refresh.get('initialized')is not True or refresh.get('ok')is not True:raise ValueError('原金额保存未完成持久读取刷新')
    operation='reimbursementAmountSave'if is_save else'reimbursementAmountInitialize'
    if(saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or b.get('ok')is not True or
       (not is_save and (b.get('initialized')is not True or b.get('action')!='amount-initialize'or b.get('financial_amount_save_requested')is not False))or (is_save and b.get('action')!='amount-save')or
       any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or sha(saved['script'])!=saved.get('source_sha256')or
       Path(saved['script']).read_text()!=script(original,operation,{'pending_registered':True,'expected_plan':p})):
        raise ValueError('原金额初始化未确认或源码漂移，只恢复原marker')
    return b


def command(service,request,folder):
    from finance_http import source
    from finance_expense_amount import base_request
    p=request['parameters'];action=p.get('action');bundle,paths=service.resolve(request['case_id']);form=read(paths['form'])
    import re
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',str(p.get('expected_userid',''))):raise ValueError('金额HTTP须明确当前本人账号')
    if request['execute']!=(action in {'open','save'}):raise ValueError('仅初始化/保存使用execute，其余为只读')
    if action!='save'and p.get('claims_path')is not None:raise ValueError('逐票申请仅用于save')
    base_request(form,p.get('group'),p.get('subject'),p.get('expected_userid'),p.get('tab_id'))
    if form.get('review',{}).get('workflow_started'):raise ValueError('已有流程，不迁移草稿金额')
    if action not in {'inspect','open','recover','save'}:raise ValueError('HTTP金额仅inspect/open/save/recover，无界面返回动作')
    if action=='recover':
        job=p.get('job_id');original_folder=service.job_folder(request['case_id'],job);prior=read(original_folder/'request.json');status=service.result(request['case_id'],job)
        if prior.get('operation')!='expense_amount'or prior.get('execute')is not True or status['status']in {'queued','running'}:raise ValueError('只恢复已停止原金额任务')
        path=original_folder/'native-amount-input.json';c=read(path)
        saved_path=original_folder/'output.json';saved=read(saved_path)
        if c.get('action')=='save'and saved.get('phase')=='script_completed'and saved.get('result',{}).get('error')=='AMOUNT_API_PERSISTED_GROUP_TOTAL':
            from finance_amount_recovery import command as recovery_command
            return recovery_command(service,request,folder,path,saved_path)
        if any(c.get(k)!=p.get(k)for k in ['group','subject','expected_userid','tab_id'])or c['case_id']!=request['case_id']:raise ValueError('原金额恢复身份不同')
        frozen(folder/'amount-recovery-source.json',{'input':ref(path),'original_job_id':job,'no_replay':True})
        return [service.python,str(ROOT/'tools/finance_native_amount.py'),'--input',str(path),'--output',str(folder/'output.json'),'--recover']
    c={'schema':SCHEMA,'case_id':request['case_id'],'serial':form['serial'],'expected_final_amount':form['amount'],
       **{k:p[k]for k in ['group','subject','expected_userid','tab_id']},'action':action,'execute':request['execute'],
       'request_id':request['request_id'],'bundle':str(bundle),'form':ref(paths['form']),
       'basic_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['basic'],
       'used_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['used_invoices'],
       'funding':form.get('funding',[]),'funding_policy':form.get('funding_policy',{}),
       'amount_contract':read(ROOT/'data/finance_amount_contract.json'),'submission_contract':read(ROOT/'data/finance_submission_contract.json'),
       'initialization_verified':False,'include_invoice_allocations':True}
    if action=='inspect'and(p.get('job_id')or p.get('artifact_id')):
        receipt,_,_=service._typed_case_artifact(request['case_id'],p.get('job_id'),p.get('artifact_id'),'expense_amount',['open','save'])
        original_folder=service.job_folder(request['case_id'],p['job_id']);original=read(original_folder/'native-amount-input.json')
        proof={'input':ref(original_folder/'native-amount-input.json'),'prepare':ref(original_folder/'native-amount-prepare.json'),'receipt':ref(receipt)}
        if original['action']=='save'and original.get('reduction',{}).get('change_required')is False:proof=original['initialization']
        c.update(initialization_verified=True,initialization=proof)
        initialization(c)
    elif action=='open':
        receipt,_,previous=service._typed_case_artifact(request['case_id'],p.get('source_job_id'),p.get('source_artifact_id'),'expense_amount',['inspect'])
        source_input=service.job_folder(request['case_id'],p['source_job_id'])/'native-amount-input.json';prior=read(source_input)
        if any(prior.get(k)!=c[k]for k in ['case_id','serial','group','subject','expected_userid','tab_id','form'])or prior.get('initialization_verified'):raise ValueError('初始化仅接受同案原始HTTP分组检查')
        preparation(c,read(receipt));c['source_inspection']=ref(receipt)
    elif action=='save':
        from finance_expense_amount import prepare_http_reduction
        receipt,data,_=service._typed_case_artifact(request['case_id'],p.get('source_job_id'),p.get('source_artifact_id'),'expense_amount',['inspect'])
        prior=read(service.job_folder(request['case_id'],p['source_job_id'])/'native-amount-input.json')
        if any(prior.get(k)!=c[k]for k in ['case_id','serial','group','subject','expected_userid','tab_id','form'])or prior.get('initialization_verified')is not True:raise ValueError('减报须同案初始化后的完整HTTP检查')
        initialization(prior);c.update(initialization_verified=True,initialization=prior['initialization'],source_inspection=ref(receipt))
        preparation(c,data,rows=True)
        from material_intake import reject_links
        path=Path(str(p.get('claims_path','')))
        if not path.is_absolute():raise ValueError('逐票申请须明确绝对路径')
        reject_links(path)
        if(not path.is_file()or path.suffix.lower()!='.json'or not any(path.resolve().is_relative_to(root)for root in service.material_roots)
           or path.stat().st_size>1024*1024):raise ValueError('逐票申请须授权资料根内不超过1MiB的JSON')
        document=read(path);claims=document;allocations=[]
        if isinstance(document,dict):
            if set(document)!={'schema','claims','budget_allocations'}or document['schema']!='ustc-native-amount-plan-v1':raise ValueError('未知减报及经费分配计划结构')
            claims=document['claims'];allocations=document['budget_allocations']
        if not isinstance(claims,list)or not isinstance(allocations,list):raise ValueError('逐票申请和分配须明确完整数组')
        for item in claims:
            for item_ref in item.get('source_refs',[]):service.material_path(item_ref.get('path'))
        from finance_expense_amount import _source_ref,_amount
        for allocation in allocations:
            if not isinstance(allocation,dict)or set(allocation)!={'invoice_guid','project','budget','amount','source_refs'}or not re.fullmatch(r'[a-fA-F0-9]{32}',str(allocation['invoice_guid'])):raise ValueError('逐票经费分配字段不完整')
            if any(not isinstance(allocation[k],str)or not re.fullmatch(r'[A-Za-z0-9_]+',allocation[k])for k in ['project','budget']):raise ValueError('逐票经费/预算代码须明确')
            _amount(allocation['amount'])
            if not isinstance(allocation['source_refs'],list)or not allocation['source_refs']:raise ValueError('分配须有明确已核来源')
            for item_ref in allocation['source_refs']:
                service.material_path(item_ref.get('path'));_source_ref(item_ref)
        if allocations and form.get('funding_policy',{}).get('invoice_level_required'):raise ValueError('有逐票资格或个人限额的经费须走correction的专项拆分合同')
        if prior.get('include_invoice_allocations')is not True or data['result'].get('allocation_tree_complete')is not True:raise ValueError('减报前须重新读取完整逐票经费树')
        if (len(c['funding'])>1 or data['result']['allocation_tree'])and not allocations:raise ValueError('多经费/多报销项减报须有完整逐票经费分配计划')
        c['claims']=ref(path);c['budget_allocations']=allocations;c['reduction']=prepare_http_reduction(base_request(form,c['group'],c['subject'],c['expected_userid'],c['tab_id']),data,c['source_inspection'],claims)
    elif any(p.get(k)is not None for k in ['source_job_id','source_artifact_id','claims_path']):raise ValueError('金额检查不接受保存输入')
    adapter=folder/'native-amount-adapter.js';code=source('amount')
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原金额适配器源码漂移，不覆盖')
    else:
        with adapter.open('x',encoding='utf-8',newline='\n')as f:f.write(code)
        adapter.chmod(0o600)
    c['adapter']=ref(adapter);path=folder/'native-amount-input.json';frozen(path,c)
    return [service.python,str(ROOT/'tools/finance_native_amount.py'),'--input',str(path),'--output',str(folder/'output.json')]


def browser(c,method,output,extra=None):
    from registered_page_owner import RegisteredPageOwner
    path=Path(output).with_suffix('.js')
    with path.open('x',encoding='utf-8',newline='\n')as f:f.write(script(c,method,extra))
    path.chmod(0o600);RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(path),'--output',str(output),'--tab-id',c['tab_id'],
        '--operation-kind','read'if method in {'reimbursementAmountInspect','reimbursementSplitInspect','reimbursementSplitMetadata','reimbursementRemunerationInspect','reimbursementWorkflowInspect'}else'write','--quiet'],check=False)
    if not Path(output).is_file():raise ValueError('原金额任务缺回执，保留原pending')
    return read(output)


def run(input_path,output,*,recover=False):
    from operator_support import bundle
    from reimburse import begin_write
    c=read(input_path)
    if c.get('schema')!=SCHEMA:raise ValueError('未知金额合同')
    checked(c['form']);checked_text(c['adapter']);initialization(c)
    if c['amount_contract']!=read(ROOT/'data/finance_amount_contract.json'):raise ValueError('固定金额合同已变')
    _,paths=bundle(c['bundle']);output=Path(output)
    if recover:
        from assistant_api import atomic_json
        from finance_backend import assert_original_transport
        from edge_runtime import browser_lock,read_marker
        import re
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if pending.get('operation')!='expense_amount:http-'+c['action']or v.get('input')!=ref(input_path):raise ValueError('只恢复原金额初始化pending')
        prepared=preparation(c,checked(v['prepare']));old=read(v['save_receipt']);method='reimbursementAmountSave'if c['action']=='save'else'reimbursementAmountInitialize'
        if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=script(c,method,{'pending_registered':True,'expected_plan':prepared}):raise ValueError('原初始化源码变化')
        if old.get('phase')=='script_completed':saved=old
        else:
            if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原初始化未派发或缺marker，只保留原任务')
            assert_original_transport(old)
            with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
            if not isinstance(body,dict):raise ValueError('原初始化marker未返回结构化结果')
            saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now(timezone.utc).isoformat(),
                'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
        recovered=output.parent/'original-amount-initialize-result.json';frozen(recovered,saved)
        proof={'input':ref(input_path),'prepare':v['prepare'],'receipt':ref(recovered)}
        if c['action']=='save'and c['reduction']['change_required']is False:proof=c['initialization']
        reader={**c,'action':'inspect','execute':False,'initialization_verified':True,'initialization':proof}
        initialization(reader);value=browser(reader,'reimbursementAmountInspect',output);preparation(c,value,rows=True)
        value['original_save_recovery']=ref(recovered);atomic_json(output,value);return 0
    if c['action']=='inspect':
        preparation(c,browser(c,'reimbursementAmountInspect',output),rows=c['initialization_verified']);return 0
    if c['action']not in {'open','save'}or c['execute']is not True:raise ValueError('金额执行须原open/save execute=true')
    if c['action']=='save':
        from finance_expense_amount import base_request,prepare_http_reduction
        form=checked(c['form']);document=checked(c['claims']);claims=document['claims']if isinstance(document,dict)else document
        if c.get('budget_allocations',[])!=(document.get('budget_allocations',[])if isinstance(document,dict)else[]):raise ValueError('分配来源与原意图不同')
        for allocation in c.get('budget_allocations',[]):
            from finance_expense_amount import _source_ref
            for source in allocation['source_refs']:_source_ref(source)
        expected=prepare_http_reduction(base_request(form,c['group'],c['subject'],c['expected_userid'],c['tab_id']),checked(c['source_inspection']),c['source_inspection'],claims)
        if c['reduction']!=expected:raise ValueError('逐票减报来源或准备漂移')
    if (paths['state'].parent/'pending_write.json').exists():raise ValueError('上次写入未核，不重新初始化金额')
    source=preparation(c,checked(c['source_inspection']))
    prepared=output.parent/'native-amount-prepare.json';body=preparation(c,browser(c,'reimbursementAmountInspect',prepared))
    for key in ['bill','basic_sections','entries','entry','definition_sha256']+(['rows','allocation_tree']if c['action']=='save'else[]):
        if source[key]!=body[key]:raise ValueError('原金额初始化前分组/单据已改变')
    if body.get('target_group_found')is not True:raise ValueError('原检查没有目标分组，不能初始化')
    checked(c['form']);begin_write(paths,read(paths['form']),'expense_amount:http-'+c['action'],output.parent,
        {'schema':SCHEMA,'action':c['action'],'input':ref(input_path),'prepare':ref(prepared),'save_receipt':str(output)})
    result=browser(c,'reimbursementAmountSave'if c['action']=='save'else'reimbursementAmountInitialize',output,{'pending_registered':True,'expected_plan':body})
    return 0 if result.get('phase')=='script_completed'and result.get('result',{}).get('ok')is True else 2


def allocation_target(c,rows,owned):
    """由冻结来源重新计算，不能用保存回执自报的target证明自身。"""
    from finance_expense_amount import _amount
    import re
    plans=c.get('budget_allocations',[]);funds=c.get('funding',[])
    if c.get('funding_policy',{}).get('invoice_level_required')or not plans or not rows or not funds or not owned:
        raise ValueError('经费树缺完整分配或须专项拆分')
    nodes={r['t.ord']:r for r in rows};children={};targets={k:dict(v)for k,v in nodes.items()};owned={g.upper()for g in owned}
    if len(nodes)!=len(rows)or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',k)or r['t.tleaf']not in {'true','false'}for k,r in nodes.items()):raise ValueError('经费树身份异常')
    def invoice(row):
        guids=set();visited=set()
        while row is not None:
            key=row['t.ord']
            if key in visited:raise ValueError('经费树循环')
            visited.add(key);guid=row['t.tk_guid']
            if guid:
                if not re.fullmatch(r'[a-fA-F0-9]{32}',guid):raise ValueError('经费树票据身份异常')
                guids.add(guid.upper())
            parent=row['t.tparent']
            if parent not in nodes and parent not in {'','0','null'}:raise ValueError('经费树孤立节点')
            row=nodes.get(parent)
        if len(guids)!=1 or not guids<=owned:raise ValueError('经费树票据不属于本单')
        return next(iter(guids))
    for row in rows:
        invoice(row)
        if row['t.tparent']in nodes:children.setdefault(row['t.tparent'],[]).append(row['t.ord'])
    leaves=[r for r in rows if r['t.tleaf']=='true'];roots=[r for r in rows if r['t.tparent']not in nodes]
    if len(leaves)!=len(plans)or not leaves or any((r['t.tleaf']=='true')==(r['t.ord']in children)for r in rows):raise ValueError('须完整唯一叶分配')
    if len({invoice(r)for r in roots})!=len(roots):raise ValueError('重复票据根')
    totals={};original={};updated={};seen=set()
    for row in leaves:
        guid=invoice(row);project=row['t.uni_prj_code'];budget=row['t.bu_code']
        matches=[(i,p)for i,p in enumerate(plans)if(p['invoice_guid'].upper(),p['project'],p['budget'])==(guid,project,budget)]
        if not budget or project not in {f['code']for f in funds}or len(matches)!=1 or matches[0][0]in seen:raise ValueError('经费分配目标不是原叶或不唯一')
        i,p=matches[0];seen.add(i);value=_amount(p['amount']);targets[row['t.ord']]['t.t_amt']=p['amount']
        totals[guid]=totals.get(guid,0)+value;original[project]=original.get(project,0)+_amount(row['t.t_amt']or'0',native=True);updated[project]=updated.get(project,0)+value
    claims={r['invoice_guid'].upper():r for r in c['reduction']['rows']}
    for root in roots:
        guid=invoice(root);wanted=_amount(claims[guid]['claim_amount'])if guid in claims else _amount(root['t.t_amt'],native=True)
        if totals.get(guid)!=wanted or wanted>_amount(root['t.invoice_amt'],native=True):raise ValueError('逐票分配合计不符')
    for f in funds:
        if updated.get(f['code'],0)>max(_amount(f['amount']),original.get(f['code'],0)):raise ValueError('经费分配超过原额与确认额')
    def summary(key):
        if key not in children:return _amount(targets[key]['t.t_amt'])
        value=sum(summary(k)for k in children[key]);targets[key]['t.t_amt']=f'{value:.2f}';return value
    for row in roots:summary(row['t.ord'])
    return[targets[r['t.ord']]for r in rows]


def verify_saved(c,before,saved,after):
    from decimal import Decimal
    from finance_expense_amount import _amount,base_request,prepare_http_reduction
    form=checked(c['form']);document=checked(c['claims']);claims=document['claims']if isinstance(document,dict)else document
    expected=prepare_http_reduction(base_request(form,c['group'],c['subject'],c['expected_userid'],c['tab_id']),checked(c['source_inspection']),c['source_inspection'],claims)
    if expected!=c['reduction']:raise ValueError('逐票减报来源变化')
    branch=saved.get('branch',{});allocation=saved.get('allocation')
    if branch.get('count',0):
        if not isinstance(allocation,dict)or allocation.get('state')!='saved'or allocation.get('independent_query_readback')is not True:raise ValueError('原分配保存缺独立回读')
        target=allocation_target(c,allocation['original'],allocation['owned_invoice_guids'])
        if allocation.get('target')!=target or after.get('allocation_tree')!=target:raise ValueError('持久经费分配与冻结来源不符')
    elif allocation is not None:raise ValueError('金额原分支不支持多出的分配保存')
    delta=_amount(expected['change_total']);total=_amount(before['bill']['t.total_amt'],native=True)-delta
    if _amount(after['bill']['t.total_amt'],native=True)!=total or total<_amount(c['expected_final_amount']):raise ValueError('减报后单据总额不符')
    if set(after['bill'])!=set(before['bill'])or any(after['bill'][k]!=v for k,v in before['bill'].items()if k!='t.total_amt'):raise ValueError('减报出现其他单据字段变更')
    if after['basic_sections']!=before['basic_sections']:raise ValueError('减报出现其他基本信息变更')
    if len(after['entries'])!=len(before['entries']):raise ValueError('减报分组集合改变')
    whole=before.get('editor_scope')=='whole_bill'
    if whole and (sum((_amount(e['amount'],native=True)for e in before['entries']),0)!=_amount(before['bill']['t.total_amt'],native=True)or sum((_amount(e['amount'],native=True)for e in after['entries']),0)!=total):raise ValueError('整单类别小计须完整覆盖原金额及减报后金额')
    for old in before['entries']:
        matches=[e for e in after['entries']if e['parameters']==old['parameters']]
        if len(matches)!=1:raise ValueError('减报分组归属变化')
        new=matches[0]
        if any(new[k]!=v for k,v in old.items()if k!='amount'):raise ValueError('减报原分组元数据变化')
        if whole:
            if not 0<=_amount(new['amount'],native=True)<=_amount(old['amount'],native=True):raise ValueError('整单减报不得增加或产生负类别小计')
        else:
            wanted=_amount(old['amount'],native=True)-(delta if old['parameters']==before['entry']['parameters']else 0)
            if _amount(new['amount'],native=True)!=wanted:raise ValueError('目标或未改分组金额不符')
    rows=after['rows'];targets=expected['rows']
    if len(rows)!=len(targets):raise ValueError('减报持久逐票集合不完整')
    found=set()
    for row in rows:
        matching=[p for p in targets if p['invoice_guid']==row['t.tk_guid']]
        if len(matching)!=1 or row['t.tk_guid']in found:raise ValueError('减报持久GUID重复或多出')
        old=matching[0];found.add(row['t.tk_guid']);wanted={**old['native_fields'],'t.t_amt':old['claim_amount']}
        if set(row)!=set(wanted):raise ValueError('减报持久字段集合变化')
        for key,value in wanted.items():
            if key=='t.t_amt':
                if _amount(row[key],native=True)!=_amount(value):raise ValueError('减报金额未持久化')
            elif key=='t.old_amt'and _amount(value or '0',native=True)==_amount(old['current_amount']):
                if _amount(row[key]or '0',native=True)not in {_amount(value or '0',native=True),_amount(old['claim_amount'])}:raise ValueError('重开后的原生基准金额异常')
            elif row[key]!=value:raise ValueError('减报未改票面/自付/归属字段变化：'+key)
    return {'schema':SCHEMA,'stage':'save','outcome':'saved','independent_http_readback':True,
        'amount_save_requested':saved.get('financial_amount_save_requested')is True,'financial_amount_verified':True,
        'invoice_row_persistence_verified':True,'claim_total':expected['claim_total'],'group':c['group'],'subject':c['subject'],
        'bill_total':f'{total:.2f}','final_target_reached':total==_amount(c['expected_final_amount']),
        'rows':[{k:r[k]for k in ['invoice_guid','invoice_number','face_amount','claim_amount']}for r in targets]}


def verify_ack(previous,envelope,outcome):
    if envelope.get('amount_persistence_recovery'):
        from finance_amount_recovery import verify_ack as verify_recovery
        return verify_recovery(previous,envelope,outcome)
    v=previous['verification'];c=checked(v['input']);p=preparation(c,checked(v['prepare']));saved=read(v['save_receipt'])
    if previous.get('operation')!='expense_amount:http-'+c['action']or previous.get('case_id')!=c['case_id']or v.get('schema')!=SCHEMA or v.get('action')!=c['action']:raise ValueError('原金额初始化意图不同')
    checked(c['form']);checked_text(c['adapter'])
    if envelope.get('original_save_recovery'):
        recovered=checked(envelope['original_save_recovery']);binding=recovered.get('original_marker_recovery',{})
        if saved.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('金额恢复未绑定原marker')
        if any(recovered.get(k)!=saved.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('金额恢复身份漂移')
        saved=recovered
    b=saved.get('result',{});is_save=c['action']=='save';method='reimbursementAmountSave'if is_save else'reimbursementAmountInitialize'
    if(saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or b.get('action')!=('amount-save'if is_save else'amount-initialize')or
       any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or sha(saved['script'])!=saved.get('source_sha256')or
       Path(saved['script']).read_text()!=script(c,method,{'pending_registered':True,'expected_plan':p})):
        raise ValueError('原金额初始化尚未取得同任务完整停止回执')
    initialized=b.get('ok')is True and b.get('initialized')is True and b.get('financial_amount_save_requested')is False
    if is_save:
        if outcome!='saved'or b.get('ok')is not True or b.get('branch',{}).get('allocation_required')is not False:raise ValueError('金额保存须完成原分配分支后才可saved')
    elif outcome!='staged'or not initialized:raise ValueError('原初始化只能登记staged，不是金额保存')
    observed=preparation(c,envelope,rows=is_save)
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('金额独立检查须晚于初始化结束')
    text=Path(envelope['script']).read_text();prefix='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementAmountInspect('
    if sha(envelope['script'])!=envelope.get('source_sha256')or not text.startswith(prefix)or not text.endswith(');})()'):raise ValueError('不是原固定HTTP金额读取源码')
    reader=json.loads(text[len(prefix):-len(');})()')])
    for key in ['case_id','serial','group','subject','expected_userid','tab_id','form','amount_contract','submission_contract','basic_contracts','used_contracts','funding','funding_policy','include_invoice_allocations']:
        if reader.get(key)!=c[key]:raise ValueError('金额回读源与原初始化不符：'+key)
    if not is_save and reader.get('initialization_verified') is False and not reader.get('initialization'):
        # A completed initialization is not a financial amount save. This read
        # confirms unchanged financial values, never that editor rows are ready.
        if observed.get('amount_rows_complete') is not False or observed.get('rows') is not None:
            raise ValueError('初始化范围回读不能伪称逐票读取完成')
        for key in ['bill','basic_sections','entries','entry']:
            if observed[key]!=p[key]:raise ValueError('原初始化后财务值已变化，保留原任务')
        return {'schema':SCHEMA,'stage':'open','outcome':'staged','native_group_rows':None,
                'independent_http_readback':True,'amount_save_requested':False,'financial_amount_verified':False,
                'editor_rows_verified':False,'initialization_completed_financial_values_unchanged':True}
    preparation(c,envelope,rows=True)
    expected_receipt=envelope.get('original_save_recovery')or ref(v['save_receipt'])
    proof={'input':v['input'],'prepare':v['prepare'],'receipt':expected_receipt}
    if is_save and c['reduction']['change_required']is False:proof=c['initialization']
    if reader.get('initialization')!=proof or reader.get('initialization_verified')is not True:raise ValueError('金额回读未引用原已完成初始化')
    initialization(reader)
    if is_save:return verify_saved(c,p,b,observed)
    for key in ['bill','basic_sections','entries','entry']:
        if observed[key]!=p[key]:raise ValueError('原金额初始化后单据或分组值改变')
    return {'schema':SCHEMA,'stage':'open','outcome':'staged','native_group_rows':len(observed['rows']),
            'independent_http_readback':True,'amount_save_requested':False,'financial_amount_verified':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--recover',action='store_true');a=p.parse_args();raise SystemExit(run(a.input,a.output,recover=a.recover))
