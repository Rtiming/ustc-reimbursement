"""固定阶段的HTTP写入及独立回读合同；复用原stage/pending/record_result入口。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
STAGES = ('basic', 'funding', 'categories', 'expenses', 'payments', 'notes', 'pre-review')
READ_DOMAINS = {'basic':'basic', 'funding':'categories', 'categories':'used_invoices', 'expenses':'categories', 'payments':'payments', 'notes':'notes', 'pre-review':'pre_review'}
SCHEMA = 'ustc-native-stage-v1'


def stage_form(path, stage):
    from validate_config import load_basic, load_funding, load
    return (load_basic if stage == 'basic' else load_funding if stage == 'funding' else load)(path)


def stage_contract(stage):
    return json.loads((ROOT / ('data/finance_' + stage.replace('-','_') + '_contract.json')).read_text())


def contract_key(stage):
    return stage.replace('-','_') + '_contract'


def stage_values(form, stage):
    implicit={}
    if stage in {'funding','expenses'}and form.get('budget_allocation_mode')=='system_default_single_funding':
        implicit={'amount_contract':stage_contract('amount'),'submission_contract':stage_contract('submission'),
                  'basic_contracts':json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['basic']}
    if stage in {'funding','expenses'} and form.get('system_mode')=='remuneration':
        from finance_native_remuneration import verify_bound_case
        from finance_native_create import checked
        if form.get('budget_allocation_mode')!='system_default_single_funding' or len(form['funding'])!=1 or form.get('expense_allocations'):raise ValueError('薪酬经费仅支持已核单经费默认分配')
        verify_bound_case(form)
        plan=form['remuneration']['creation_plan'];original=checked({'path':plan['plan'],'sha256':plan['sha256']})
        implicit.update(system_mode='remuneration',activity=original['activity'],remuneration_contract=original['remuneration_contract'],remuneration_native_source=form['remuneration']['native_acknowledgement'])
    if stage=='categories':
        return {'expense_categories':form.get('expense_categories',[]),'funding':form['funding'],'funding_policy':form.get('funding_policy',{}),
                'amount_contract':stage_contract('amount'),'submission_contract':stage_contract('submission'),
                'basic_contracts':json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['basic'],
                'used_contracts':json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['used_invoices']}
    if stage == 'basic': return {'basic':form['basic']}
    if stage == 'funding': return {**implicit,'funding':form['funding'],'budget_allocation_mode':form.get('budget_allocation_mode','manual'),'expense_allocations':form.get('expense_allocations',[]),'funding_policy':form.get('funding_policy',{})}
    if stage == 'payments': return {'payments':form['payments'],**({'corporate_contract':json.loads((ROOT/'data/finance_corporate_contract.json').read_text())}if any(p.get('mode')=='corporate_transfer'for p in form['payments'])else{}),**({'card_contract':json.loads((ROOT/'data/finance_card_contract.json').read_text())}if any(p.get('mode')=='corporate_card'for p in form['payments'])else{})}
    if stage == 'notes': return {'explanations':form.get('explanations',{})}
    if stage == 'pre-review': return {'pre_review':form.get('pre_review',[])}
    return {**implicit,**{key:form.get(key,default) for key,default in [
        ('funding',[]),('expense_allocations',[]),('funding_policy',{}),('budget_allocation_mode','manual')]}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if path.exists():
        if path.read_text() != text: raise ValueError('原阶段输入漂移，不覆盖')
    else:
        with path.open('x', encoding='utf-8', newline='\n') as stream: stream.write(text)
        path.chmod(0o600)


def command(service, request, folder):
    bundle, paths = service.resolve(request['case_id'])
    freeze_input(bundle, paths, request, folder)
    return [service.python,str(ROOT/'tools/finance_native_stage.py'),'--input',str(folder/'native-stage-input.json'),
            '--output',str(folder/'output.json')]


def freeze_input(bundle, paths, request, folder):
    from validate_config import money
    from finance_http import source
    from reimburse import guard_step
    p = request['parameters']; stage = p.get('stage')
    if stage not in STAGES or set(p) - {'stage','tab_id','noop_only'}or type(p.get('noop_only',False))is not bool: raise ValueError('未知HTTP阶段或参数')
    form = stage_form(paths['form'], stage)
    if form['case_id'] != request['case_id']: raise ValueError('本案标识不同')
    guard_step(form, stage)
    user = form.get('expected_userid') or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    if not (user is None and request['execute'] is False) and (not isinstance(user, str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', user)):
        raise ValueError('本案须登记expected_userid')
    if os.environ.get('REIMBURSEMENT_EXPECTED_USERID') not in (None,user): raise ValueError('本案与执行会话账号不同')
    tab = p.get('tab_id')
    if request['execute'] and (not isinstance(tab,str) or not re.fullmatch(r'\d+',tab)):
        raise ValueError('实际阶段执行须明确tab_id')
    domains = json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']
    contracts = domains['funding' if stage == 'funding' else READ_DOMAINS[stage]]
    if stage == 'funding' and form.get('system_mode','daily') not in {'daily','remuneration'}:
        raise ValueError('原生经费阶段当前仅适配日常报销，薪酬项目须另核原生合同')
    value = {'schema':SCHEMA,'stage':stage,'case_id':request['case_id'],'serial':form['serial'],
             'expected_userid':user,'expected_amount':str(money(form['amount']).quantize(money('0.01'))),
             contract_key(stage):stage_contract(stage),
             'contracts':contracts,'bundle':str(bundle),'form_sha256':digest(paths['form']),
             'tab_id':tab,'execute':request['execute'],'request_id':request['request_id'],'noop_only':p.get('noop_only',False)}
    value.update(stage_values(form, stage))
    if stage in {'funding','expenses'}and json.loads(paths['invoices'].read_text()).get('native_allowance_source'):
        from finance_allowance import funding_context
        value['allowance_native_source']=funding_context(json.loads(paths['invoices'].read_text()),form)
    if stage in {'funding','expenses'}and json.loads(paths['invoices'].read_text()).get('native_domestic_allowance_source'):
        from finance_domestic_allowance import funding_context
        value['domestic_allowance_source']=funding_context(json.loads(paths['invoices'].read_text()),form)
    if stage=='basic':
        state=json.loads(paths['state'].read_text());binding=state.get('basic_identity_repair_required')
        if binding:
            from finance_native_create import checked
            acknowledged=checked(binding);proof=acknowledged.get('native_stage_readback',{})
            if(acknowledged.get('case_id')!=request['case_id']or acknowledged.get('serial')!=form['serial']or
               acknowledged.get('outcome')!='needs_correction'or proof.get('identity_repair',{}).get('original_id')!=user):raise ValueError('原受保护身份修复证据不符')
            value['protected_identity_repair']=proof['identity_repair']
    if stage == 'funding': value['budget_contracts'] = domains['categories']
    adapter = folder/'native-stage-adapter.js'
    code = source(stage)
    if adapter.exists():
        if adapter.read_text() != code: raise ValueError('原阶段适配器源码漂移，不覆盖')
    else:
        with adapter.open('x',encoding='utf-8',newline='\n') as stream: stream.write(code)
        adapter.chmod(0o600)
    value['adapter_sha256'] = digest(adapter)
    frozen_json(folder/'native-stage-input.json', value)
    return folder/'native-stage-input.json'


def run(input_path, output, *, runtime=None,recover=False):
    from operator_support import bundle, check
    from reimburse import begin_write, guard_step
    from finance_http import source
    c = json.loads(input_path.read_text()); stage = c['stage']
    if c.get('schema') != SCHEMA or stage not in STAGES: raise ValueError('阶段输入合同不符')
    adapter = input_path.parent/'native-stage-adapter.js'
    if (not adapter.is_file() or digest(adapter) != c.get('adapter_sha256')
            or hashlib.sha256(source(stage).encode()).hexdigest() != c['adapter_sha256']):
        raise ValueError('原阶段适配器源码已变化，不执行新源码或覆盖原任务')
    adapter_source = adapter.read_text()
    _, paths = bundle(c['bundle']); form = stage_form(paths['form'], stage); guard_step(form, stage)
    if (form['case_id'] != c['case_id'] or form['serial'] != c['serial'] or digest(paths['form']) != c['form_sha256']
            or c[contract_key(stage)] != stage_contract(stage)
            or any(c.get(k) != value for k,value in stage_values(form,stage).items())
            or c['contracts'] != json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['funding' if stage == 'funding' else READ_DOMAINS[stage]]
            or stage == 'funding' and c['budget_contracts'] != json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['categories']):
        raise ValueError('本案输入或固定接口合同已变化')
    if recover:
        return recover_original(input_path,output,c,paths)
    if not c['execute']:
        result = {'ok':True,'state':'preview','stage':stage,'execute':False,'business_transport':'native_http',
                  'steps':['原字段及原生规则预检','冻结原计划与写入意图','同号同金额草稿重核','单次保存','独立HTTP回读后登记'],
                  'needed_inputs':([] if c['expected_userid'] else ['expected_userid']) + ([] if c['tab_id'] else ['tab_id']),
                  'system_write':False,'requires_independent_readback':True}
        frozen_json(output,result); return 0
    pending = paths['state'].parent/'pending_write.json'
    if pending.exists(): raise ValueError('原写入尚未独立回读；只恢复原意图，不重发')
    report = check(c['bundle'], validation_stage=stage)
    if not report['ok']: raise ValueError('本案基础资料预检未通过')
    runtime = runtime or subprocess.run
    def call(method, config, receipt):
        from registered_page_owner import RegisteredPageOwner
        RegisteredPageOwner.before_child_dispatch(c['tab_id'])
        script = receipt.with_suffix('.js')
        text = '(async()=>{\n'+adapter_source+'\nreturn await '+method+'('+json.dumps(config,ensure_ascii=False)+');\n})()'
        with script.open('x',encoding='utf-8',newline='\n') as f:f.write(text)
        script.chmod(0o600)
        result = runtime([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(receipt),
                          '--tab-id',c['tab_id'],'--operation-kind','write' if method.endswith('Save') else 'read','--quiet'])
        return result.returncode
    prepared_path = output.parent/'native-stage-prepare.json'
    method = 'reimbursement' + stage.title().replace('-','')
    if call(method + 'Prepare',c,prepared_path): return 1
    plan = json.loads(prepared_path.read_text()).get('result',{})
    validate_plan(c, plan)
    if c.get('noop_only'):
        if stage=='categories':
            same=all(all(next(r for r in plan['original']['categories']if r['group_key']==key)[field]==value for field,value in target.items())for key,target in plan['changes'].items())
        else:same=not plan['changes']and not any(b.get('selection_required')for b in plan.get('card_bindings',[]))and not any(b.get('resolve_required')for b in plan.get('corporate_bindings',[]))
        if not same:raise ValueError('仅核无变更生命周期；当前需要真实变更，未登记写入或派发保存')
    if digest(paths['form']) != c['form_sha256']: raise ValueError('预检期间本案输入漂移')
    verification = {'schema':SCHEMA,'stage':stage,'input':str(input_path),'input_sha256':digest(input_path),
                    'prepare_receipt':str(prepared_path),'prepare_sha256':digest(prepared_path),
                    'save_receipt':str(output),'form_sha256':c['form_sha256']}
    begin_write(paths,form,'step:'+stage,output.parent,verification)
    expected = {k:plan[k] for k in ['original','changes','definition_sha256'] + (
        ['teacher'] if stage == 'basic' else ['candidates'] if stage == 'funding' else ['bank_bindings','bank_check_flag']+(['card_bindings']if 'card_bindings'in plan else[])+(['corporate_bindings']if 'corporate_bindings'in plan else[]) if stage == 'payments' else ['scope'] if stage == 'notes' else ['slots'] if stage == 'pre-review' else [])}
    return call(method + 'Save',{**c,'expected_plan':expected},output)


def verify_remuneration_default(c,proof):
    from validate_config import money
    code=c['funding'][0]['code'];categories=proof.get('categories',[]);salary=proof.get('salary',{});rows=salary.get('rows',[])
    expected={k:(v['name'],money(v['amount']))for k,v in c['activity']['approved_people'].items()}
    observed={r['t.unino']:(r['t.name'],money(r['t.AMT'].replace(',','')))for r in rows}
    preview={r['id']:(r['name'],money(r['amount'])/100)for r in salary.get('preview_people',[])}
    if(c.get('system_mode')!='remuneration' or proof.get('source_binding')!=c.get('remuneration_native_source') or
       proof.get('kind')!='system_default_remuneration_funding' or proof.get('amounts')!=[] or
       proof.get('bill',{}).get('t.serial_no')!=c['serial'] or proof['bill'].get('t.bx_type')!='8' or proof['bill'].get('t.uni_prj_code')!=code or
       len(categories)!=1 or categories[0].get('group')!='学生-学生其他劳务' or categories[0].get('project')!=code or not categories[0].get('budget') or
       money(categories[0]['amount'].replace(',',''))!=money(c['expected_amount']) or
       len(rows)!=len(expected) or len(salary.get('preview_people',[]))!=len(expected) or observed!=expected or preview!=expected or
       any(r.get('t.serial_no')!=c['serial'] for r in rows) or sum((v[1]for v in expected.values()),money('0'))!=money(c['expected_amount'])):raise ValueError('薪酬默认经费须同单原生两侧逐人金额及预算完整闭合')
    for k,v in {'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':c['activity']['period_start'],'dd.ff_enddate':c['activity']['period_end'],'dd.hastax':'T'}.items():
        if salary.get('original',{}).get(k,'').strip()!=v:raise ValueError('薪酬经费期间或性质变化')

def validate_plan(c, plan):
    stage = c.get('stage', 'basic')
    if stage=='payments'and any(p.get('mode')=='corporate_card'for p in c.get('payments',[]))and len(plan.get('card_bindings',[]))!=1:
        raise ValueError('公务卡须有本次完整交易查询及同单原绑定证明')
    if (stage not in STAGES or plan.get('ok') is not True or plan.get('complete') is not True or plan.get('action') != stage + '-prepare'
            or plan.get('business_transport') != 'native_http' or plan.get('save_dispatched') is not False
            or any(plan.get(k) != c[k] for k in ['case_id','serial','expected_userid'])
            or plan.get('definition_sha256') != c[contract_key(stage)]['definition_sha256']
            or plan.get('bill',{}).get('t.status') != '-99' or plan.get('bill',{}).get('t.lc_finish') != 'F'
            or not isinstance(plan.get('original'),dict) or not plan['original'] or not isinstance(plan.get('changes'),dict)):
        raise ValueError('须为本案同账号草稿的完整原生预检')
    if c.get('remuneration_native_source') and plan['original'].get('implicit_default'):verify_remuneration_default(c,plan['original']['implicit_default'])


def verify_ack(paths, previous, read, outcome):
    """完整比较保存目标和未改字段；未知保存不能凭未变化回读解除意图。"""
    from validate_config import money
    v = previous['verification']
    stage = v.get('stage')
    if v.get('schema') != SCHEMA or stage not in STAGES or previous.get('operation') != 'step:' + stage:
        raise ValueError('未知固定阶段回读合同')
    for key, sha in [('input','input_sha256'),('prepare_receipt','prepare_sha256')]:
        if digest(v[key]) != v[sha]: raise ValueError('原阶段冻结证据变化')
    if digest(paths['form']) != v['form_sha256']: raise ValueError('本案参数已变，不将旧保存标为当前完成')
    c = json.loads(Path(v['input']).read_text()); plan = json.loads(Path(v['prepare_receipt']).read_text())['result']
    if c.get('adapter_sha256') and digest(Path(v['input']).parent/'native-stage-adapter.js') != c['adapter_sha256']:
        raise ValueError('原阶段适配器源码快照变化')
    validate_plan(c,plan)
    if (any(previous.get(k) != c[k] for k in ['case_id','serial']) or c['form_sha256'] != v['form_sha256']
            or Path(v['save_receipt']).resolve() != (Path(previous['output_directory'])/'output.json').resolve()):
        raise ValueError('原意图与冻结输入或保存回执不同')
    r = read.get('result',{}); bill = r.get('bill',{})
    envelope = json.loads(Path(v['save_receipt']).read_text()); save = envelope.get('result',{})
    if read.get('original_save_recovery'):
        from finance_native_create import checked,sha
        recovered=checked(read['original_save_recovery']);binding=recovered.get('original_marker_recovery',{})
        if envelope.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('阶段恢复未绑定原marker')
        if any(recovered.get(k)!=envelope.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('阶段恢复身份漂移')
        envelope=recovered;save=envelope.get('result',{})
    if (envelope.get('phase') != 'script_completed' or save.get('action') != stage + '-save'
            or any(save.get(k) != c[k] for k in ['case_id','serial','expected_userid'])):
        raise ValueError('原保存脚本尚未结束；先恢复原marker，不能边执行边解锁')
    if (not envelope.get('executed_at_utc') or not read.get('dispatch_intent_at_utc')
            or datetime.fromisoformat(read['dispatch_intent_at_utc']) <= datetime.fromisoformat(envelope['executed_at_utc'])):
        raise ValueError('独立回读须在原保存脚本结束后新派发')
    if c.get('noop_only')and save.get('save_dispatched')is not False:raise ValueError('无变更验收不接受实际保存派发，保留原意图核查')
    if stage=='categories':
        from finance_native_categories import verify_categories_ack
        return verify_categories_ack(c,plan,envelope,read,outcome)
    if stage in {'funding','expenses'}and plan['original'].get('implicit_default'):
        validate_plan(c,r)
        if(outcome=='not_applied'and save.get('ok')is False and save.get('save_dispatched')is False and save.get('system_write')is False and save.get('state')=='rejected_before_save'and not save.get('steps') and r['original']==plan['original'] and r['changes']==plan['changes']):
            return {'stage':stage,'outcome':outcome,'independent_http_readback':True,'native_default_verified':True,'save_requested':False,'rejected_before_dispatch':True}
        if(outcome!='saved'or save.get('save_dispatched')is not False or save.get('state')!='already_matches_readback_required'or r['original']!=plan['original']or r['changes']):raise ValueError('单经费默认分配须同原计划完整独立确认且未请求保存')
        return{'stage':stage,'outcome':outcome,'independent_http_readback':True,'native_default_verified':True,'save_requested':False}
    if stage=='funding' and r.get('action')=='funding-prepare' and r.get('original',{}).get('implicit_default'):
        validate_plan(c,r)
        if(outcome!='saved'or save.get('ok')is not True or save.get('save_dispatched')is not True or save.get('state')!='saved_independent_readback_required'
           or c.get('budget_allocation_mode')!='system_default_single_funding'or len(c['funding'])!=1
           or plan['changes']!={'projects':[c['funding'][0]['code']]}or r['changes']or not same_funding_candidates(plan['candidates'],r['candidates'])):raise ValueError('新选单经费须原保存成功及完整默认分配回读')
        proof=r['original']['implicit_default'];categories=proof.get('categories',[]);amounts=proof.get('amounts',[])
        code=c['funding'][0]['code']
        def native_money(value):return money(value.replace(',',''))
        if proof.get('kind')=='system_default_remuneration_funding':
            verify_remuneration_default(c,proof)
            return{'stage':'funding','outcome':outcome,'independent_http_readback':True,'native_default_verified':True,'save_requested':True,'selected_projects':[code],'allocation_stage_required':False,'remuneration_people_verified':True}
        if proof.get('kind')=='system_default_domestic_funding':
            from finance_domestic_allowance import funding_context
            source=funding_context(json.loads(paths['invoices'].read_text()),json.loads(paths['form'].read_text()))
            components=source['components'];allowance_rows=[r for r in categories if r['group']in components];invoice_rows=[r for r in categories if r['group']not in components]
            if proof.get('source_binding')!=source['binding']or proof.get('components')!=components or len(allowance_rows)!=2 or {r['group']for r in allowance_rows}!=set(components)or len(invoice_rows)!=len(amounts)or proof.get('bill',{}).get('t.serial_no')!=c['serial']or proof['bill'].get('t.uni_prj_code')!=code or any(r.get('project')!=code or not r.get('budget')for r in categories)or sum((native_money(r['amount'])for r in categories),money('0'))!=money(c['expected_amount'])or any(native_money(r['amount'])!=money(components[r['group']])for r in allowance_rows)or sum((native_money(r['amount'])for r in invoice_rows),money('0'))!=money(source['invoice_amount']):raise ValueError('有票差旅经费须原票加逐人补助的完整同单分项')
            for row in invoice_rows:
                hits=[e for e in amounts if e.get('group')==row['group']and e.get('subject')==row['subject']and e['parameters'].split('&')[2]==row['group_key']]
                if len(hits)!=1 or native_money(hits[0]['amount'])!=native_money(row['amount']):raise ValueError('有票差旅默认经费的逐票分组金额不同')
            return{'stage':'funding','outcome':outcome,'independent_http_readback':True,'native_default_verified':True,'save_requested':True,'selected_projects':[code],'allocation_stage_required':False,'domestic_components_verified':True}
        if proof.get('kind')=='system_default_allowance_funding':
            from finance_allowance import funding_context
            source=funding_context(json.loads(paths['invoices'].read_text()),json.loads(paths['form'].read_text()))
            if proof.get('source_binding')!=source['binding']or len(categories)!=2 or proof.get('bill',{}).get('t.uni_prj_code')!=code or proof['bill'].get('t.serial_no')!=c['serial']:raise ValueError('补助默认经费须同单原生分项及原创建来源')
            components={'国内出差伙食补贴':sum((native_money(g['values']['hsbt'])for g in source['prepared']['groups']),money('0')),'国内出差交通补贴':sum((native_money(g['values']['jtbt'])for g in source['prepared']['groups']),money('0'))}
            if {row['group']for row in categories}!=set(components)or any(row.get('project')!=code or not row.get('budget')or native_money(row['amount'])!=components[row['group']]for row in categories)or sum(components.values(),money('0'))!=money(c['expected_amount']):raise ValueError('补助经费伙食和交通逐项金额不闭合')
            return{'stage':'funding','outcome':outcome,'independent_http_readback':True,'native_default_verified':True,'save_requested':True,'selected_projects':[code],'allocation_stage_required':False,'allowance_components_verified':True}
        if(not categories or len(categories)!=len(amounts)or proof.get('kind')!='system_default_single_funding'
           or proof.get('bill',{}).get('t.serial_no')!=c['serial']or proof['bill'].get('t.uni_prj_code')!=code
           or any(row.get('project')!=code or not row.get('budget')for row in categories)
           or sum((native_money(row['amount'])for row in categories),money('0'))!=money(c['expected_amount'])):raise ValueError('新选经费默认分配未闭合')
        for row in categories:
            matches=[e for e in amounts if e.get('group')==row['group']and e.get('subject')==row['subject']and e['parameters'].split('&')[2]==row['group_key']]
            if len(matches)!=1 or native_money(matches[0]['amount'])!=native_money(row['amount']):raise ValueError('经费默认分配逐组金额不符')
        return{'stage':'funding','outcome':outcome,'independent_http_readback':True,'native_default_verified':True,'save_requested':True,'selected_projects':[code],'allocation_stage_required':False}
    amount = bill.get('t.total_amt')
    if (read.get('phase') != 'script_completed' or r.get('ok') is not True or r.get('complete') is not True
            or r.get('business_transport') != 'native_http' or r.get('action') != 'bill-read' or r.get('domain') != READ_DOMAINS[stage]
            or r.get('system_write') is not False or any(r.get(k) != c[k] for k in ['case_id','serial','expected_userid'])
            or bill.get('t.serial_no') != c['serial'] or not str(bill.get('t.REQ_INFO','')).startswith(c['expected_userid']+'-')
            or not isinstance(amount,str) or not re.fullmatch(r'(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?',amount)
            or money(amount.replace(',','')) != money(c['expected_amount'])):
        raise ValueError('须为本案保存后的完整独立HTTP基本信息回读')
    if stage=='payments'and outcome=='saved'and any(b.get('selection_required')for b in plan.get('card_bindings',[])):
        plan=verify_card_binding_plan(c,plan,save)
    if stage=='payments'and outcome=='saved'and any(b.get('resolve_required')for b in plan.get('corporate_bindings',[])):
        plan=verify_corporate_binding_plan(plan,save)
    target = plan['original'] if stage == 'funding' else {**plan['original'], **plan['changes']} if stage in {'basic','payments','notes'} else {
        key:{**row, **plan['changes'].get(key,{})} for key,row in plan['original'].items()}
    identity_repair=None
    if outcome=='needs_correction'and stage=='basic':
        if(save.get('ok')is not True or save.get('save_dispatched')is not True or target.get('t.real_sno')!=c['expected_userid']or
           not plan.get('bill',{}).get('t.REQ_INFO','').startswith(c['expected_userid']+'-')or not target.get('t.real_sname')or target['t.real_sname']==c['expected_userid']):raise ValueError('非本工具已证明的辅助姓名误传，不自动修复身份')
        identity_repair={'original_id':target['t.real_sno'],'original_name':target['t.real_sname'],'observed_id':target['t.real_sname']}
        target={**target,'t.real_sno':target['t.real_sname']}
    if outcome == 'not_applied':
        rejected_before=save.get('save_dispatched') is False and save.get('state')=='rejected_before_save'
        # A completed native HTTP response may be a previously unmodelled
        # validation message. This is distinct from a transport timeout: the
        # frozen BasicSave code reaches this parser only after awaiting POST.
        # Release only after every original field is independently unchanged.
        unparsed_basic=completed_unparsed_basic(c,plan,save,(Path(v['input']).parent/'native-stage-adapter.js').read_text())
        if (envelope.get('phase') != 'script_completed' or save.get('action') != stage + '-save'
                or not (rejected_before or unparsed_basic)
                or any(save.get(k) != c[k] for k in ['case_id','serial','expected_userid'])):
            raise ValueError('未证明原保存派发前拒绝，不能释放未知意图')
        target = plan['original']
    elif outcome != 'saved'and identity_repair is None: raise ValueError('原生阶段仅支持完整saved、明确未生效或已定位身份联动修复')
    if stage == 'funding':
        return verify_funding(c, r, plan, outcome)
    if stage == 'payments':
        return verify_payments(target, r, plan, outcome)
    if stage == 'expenses':
        return verify_expenses(target, r, plan, outcome)
    if stage == 'pre-review':
        return verify_pre_review(target, r, plan, outcome)
    rows = [s['rows'] for s in r.get('sections',[]) if s.get('funcno') == ('76' if stage == 'notes' else '60') and s.get('kind') == 'form']
    if len(rows) != 1 or len(rows[0]) != 1: raise ValueError('原生基本信息回读不唯一')
    actual = rows[0][0]
    for field,value in target.items():
        key = field.removeprefix('t.').upper()
        if key not in actual or ('' if actual[key] is None else actual[key]) != value:
            raise ValueError('保存目标或未改字段不一致：'+field)
    return {'stage':stage,'verified_fields':len(target),'changed_fields':list(plan['changes']),
            'independent_http_readback':True,'outcome':outcome,
            **({'identity_repair':identity_repair}if identity_repair else{}),
            'completed_response_original_unchanged':outcome=='not_applied'and completed_unparsed_basic(c,plan,save,(Path(v['input']).parent/'native-stage-adapter.js').read_text())}


def completed_unparsed_basic(c,plan,save,adapter_source):
    if(c.get('stage')!='basic'or save.get('save_dispatched')is not True or save.get('state')!='unknown_save_readback_required'
       or save.get('error')!='NATIVE_PROTOCOL_RESPONSE_UNKNOWN'or not isinstance(save.get('prepared'),dict)
       or any(save['prepared'].get(k)!=plan.get(k)for k in ['original','changes','teacher','definition_sha256'])):return False
    return(save['prepared'].get('ok')is True and save['prepared'].get('action')=='basic-prepare'
           and "const raw = await built.http.post('commonUpdate_responseButtonEvent.action',"in adapter_source
           and 'const response = ReimbursementNativeProtocol.response(raw, []);'in adapter_source)


def same_funding_candidates(before,after):
    if len(before)!=len(after):return False
    for old,new in zip(before,after):
        if old==new:continue
        if(not old.get('external')or new.get('external')or old.get('code')!=new.get('code')or
           old.get('target',{}).get('t.proj_code')!=new.get('code')or old['target'].get('t.charge_sname')!=new.get('owners')or
           not isinstance(new.get('grant_id'),str)):return False
    return True


def verify_pre_review(target, read, plan, outcome):
    if read.get('workflow_parameters') != plan['slots']:
        raise ValueError('预审槽身份或原生流程参数发生变化')
    actual = {}
    for section in read.get('sections',[]):
        slot=section.get('slot',{})
        if (section.get('funcno')!='1297' or section.get('kind')!='form' or len(section.get('rows',[]))!=1
                or str(slot.get('index')) in actual or slot not in plan['slots']):
            raise ValueError('预审完整回读缺槽、重复或身份不符')
        actual[str(slot['index'])]={k:v or '' for k,v in section['rows'][0].items()}
    if actual != target:
        raise ValueError('预审人员、显示姓名或未修改参数未完整匹配')
    return {'stage':'pre-review','verified_slots':len(actual),'changed_slots':list(plan['changes']),
            'independent_http_readback':True,'outcome':outcome}


def verify_corporate_binding_plan(plan,save):
    """只接纳原公司/账户/开户行的原生关联标识；其余目标仍由原冻结计划决定。"""
    from copy import deepcopy
    planned=[b for b in plan.get('corporate_bindings',[])if b.get('resolve_required')];proofs=save.get('corporate_resolution',[])
    if not planned or not isinstance(proofs,list) or len(proofs)!=len(planned):raise ValueError('缺完整原生对公身份关联结果')
    if len({p.get('row_index') for p in proofs})!=len(proofs):raise ValueError('对公身份关联行重复')
    result=deepcopy(plan);rows=result['changes'].get('24')
    for wanted in planned:
        matched=[p for p in proofs if p.get('row_index')==wanted['row_index']]
        if len(matched)!=1:raise ValueError('对公身份关联行缺失')
        proof=matched[0];ordinal=proof.get('taxpayer_ord')
        if(proof.get('state')!='accepted'or any(proof.get(k)!=wanted[k]for k in ['row_index','name','account','bank_name'])or
           not isinstance(ordinal,str)or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',ordinal)or ordinal=='0'):raise ValueError('对公身份关联未核实或属于其他收款人')
        index=wanted['row_index']
        if(type(index)is not int or not isinstance(rows,list)or not 0<=index<len(rows)or rows[index].get('TAXPAYER_ORD')):raise ValueError('新对公关联不是原空白目标')
        row=rows[index]
        if any(row.get(k)!=wanted[f]for k,f in [('BANKSNAME','name'),('BANKACNT','account'),('BANKNAME','bank_name')]):raise ValueError('对公冻结目标字段改变')
        row['TAXPAYER_ORD']=ordinal
    return result


def verify_card_binding_plan(config,before,save):
    """新消费绑定的服务端行序号只从同一执行链的完整再读取用。"""
    from validate_config import money
    wanted=[b for b in before.get('card_bindings',[])if b.get('selection_required')]
    proof=save.get('card_selection')or{};after=proof.get('post_binding_plan')
    if len(wanted)!=1 or proof.get('state')!='accepted'or not isinstance(after,dict):raise ValueError('缺原新消费选择成功及完整绑定回读')
    binding=wanted[0]
    if proof.get('transaction')!=binding['transaction']or money(proof.get('selected_amount','-1'))!=money(binding['selected_amount']):raise ValueError('选择了其他公务卡消费或金额')
    validate_plan(config,after)
    if any(b.get('selection_required')for b in after.get('card_bindings',[]))or len(after.get('card_bindings',[]))!=1 or after['card_bindings'][0]['transaction']!=binding['transaction']:raise ValueError('新公务卡绑定未核实')
    for key,rows in before['original'].items():
        if key not in {'19','285'}and after['original'].get(key)!=rows:raise ValueError('新公务卡选择改变其他支付方式')
    head=before['original']['19'][0];new=after['original']['19'][0]
    if head.keys()!=new.keys()or any(head[k]!=new[k]for k in head if k not in {'YT_AMT','WT_AMT'}):raise ValueError('新公务卡选择改变未计划汇总字段')
    rows=[r for r in after['original']['285']if r.get('GWK_SEQ_NO')or r.get('GWK_BANKACNT')]
    if(len(rows)!=1 or rows[0].get('GWK_SEQ_NO')!=binding['transaction']['t.seq_no']or
       rows[0].get('GWK_BANKACNT')!=binding['transaction']['t.account']or money(rows[0].get('AMOUNT','-1'))!=money(binding['selected_amount'])):raise ValueError('新公务卡独立绑定身份或实报金额不符')
    return after


def verify_expenses(target, read, plan, outcome):
    from validate_config import money
    sections = [s for s in read.get('sections',[]) if s.get('funcno') == '307' and s.get('kind') == 'grid']
    if len(sections) != 1 or sections[0].get('complete') is not True:
        raise ValueError('须完整预算树独立回读')
    actual = {}
    for row in sections[0]['rows']:
        key = row.get('t.ord')
        if not key or key in actual: raise ValueError('预算节点缺身份或重复')
        actual[key] = row
    if actual.keys() != target.keys(): raise ValueError('预算节点集合改变')
    for key, row in target.items():
        if actual[key].keys() != row.keys(): raise ValueError('预算节点字段缺失或增加')
        for field,value in row.items():
            found = actual[key][field]
            # 描述包含学校动态计算的金额提示；实际归属和全部金额分别核对。
            if field == 't.t_desc': continue
            if field == 't.amt':
                if (not isinstance(found,str) or not re.fullmatch(r'(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?',found)
                        or money(found.replace(',','')) != money(value.replace(',',''))):
                    raise ValueError('预算节点金额不符：' + key)
            elif found != value: raise ValueError('预算节点未改字段不符：' + key + '/' + field)
    return {'stage':'expenses','verified_rows':len(target),'changed_rows':list(plan['changes']),
            'independent_http_readback':True,'outcome':outcome}


def verify_funding(config, read, plan, outcome):
    from validate_config import money
    sections = [s for s in read.get('sections',[]) if s.get('funcno') == '307' and s.get('kind') == 'grid']
    if len(sections) != 1 or sections[0].get('complete') is not True:
        raise ValueError('经费须由持久预算树完整独立回读，不用临时选择标记代替')
    rows = sections[0]['rows']
    if outcome == 'not_applied':
        if rows != plan['original']['budget_rows']: raise ValueError('派发前拒绝后的原预算已变化')
    else:
        leaves = [r for r in rows if r.get('t.tlevel') == '2']
        if (not leaves or any(r.get('t.serial_no') != config['serial'] for r in rows)
                or len({r.get('t.ord') for r in rows}) != len(rows)
                or {r.get('t.uni_prj_code') for r in leaves} != {f['code'] for f in config['funding']}
                or any(not isinstance(r.get('t.amt'),str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?',r['t.amt']) for r in leaves)
                or sum((money(r['t.amt']) for r in leaves),money('0')) != money(config['expected_amount'])):
            raise ValueError('保存后的经费集合或预算总额不符')
    return {'stage':'funding','verified_rows':len(rows),'selected_projects':[f['code'] for f in config['funding']],
            'allocation_stage_required':True,'independent_http_readback':True,'outcome':outcome}


def verify_payments(target, read, plan, outcome):
    from validate_config import money
    sections = read.get('sections',[])
    derived_departments = []
    if len(sections) != len(target) or {s.get('funcno') for s in sections} != target.keys():
        raise ValueError('支付回读必须包含全部原支付栏')
    for no, rows in target.items():
        matches = [s for s in sections if s.get('funcno') == no and s.get('kind') == 'form']
        if len(matches) != 1: raise ValueError('支付回读行数不符：' + no)
        actual = matches[0]['rows']
        # Native save removes inactive invoice-issuer remittance suggestions.
        # This is accepted only for an explicitly zero-amount original and a
        # wholly empty readback, never for an actual company remittance.
        if(no=='24'and outcome=='saved'and rows and all(isinstance(row.get('AMOUNT'),str)and row['AMOUNT']and money(row['AMOUNT'])==0 for row in rows)
           and all(all(v in ('',None)for v in row.values())for row in actual)):
            rows=[{key:''for key in rows[0]}for _ in actual]
        if no=='24'and outcome=='saved'and len(actual)<len(rows):
            active=[row for row in rows if isinstance(row.get('AMOUNT'),str)and row['AMOUNT']and money(row['AMOUNT'])>0]
            inactive=[row for row in rows if row not in active]
            # Only native, unallocated suggestions may disappear. A zeroed
            # former payment or any active company must still fail verification.
            if(active and len(actual)==len(active)and all(row in plan.get('original',{}).get('24',[]) and money(row['AMOUNT'])==0 for row in inactive)):
                rows=active
        if no=='24'and rows and any(row.get('BANKSNAME') for row in rows):
            identity=lambda row:(row.get('TAXPAYER_ORD',''),row.get('BANKACNT',''))
            if len({identity(row)for row in rows})!=len(rows)or len({identity(row)for row in actual})!=len(actual):raise ValueError('对公回读身份重复')
            rows=sorted(rows,key=identity);actual=sorted(actual,key=identity)
        if len(actual)!=len(rows):raise ValueError('支付回读行数不符：'+no)
        if no == '22':
            if len({r.get('SNO') for r in actual}) != len(actual): raise ValueError('收款人回读重复')
            actual = sorted(actual,key=lambda r:r.get('SNO',''));rows = sorted(rows,key=lambda r:r['SNO'])
        for expected, found in zip(rows,actual):
            for key,value in expected.items():
                if key not in found: raise ValueError('支付回读字段缺失：' + no + '/' + key)
                observed = '' if found[key] is None else found[key]
                if key in {'DEPART','REMARK'}:
                    if isinstance(observed,str)and not observed.strip():observed=''
                    if isinstance(value,str)and not value.strip():value=''
                # A newly selected person has no prior department to preserve.
                # The school fills this display field on save. Accept it only
                # for a new, uniquely frozen bank identity; all payment fields
                # below still have to match, including account and bank key.
                if (no == '22' and key == 'DEPART' and outcome == 'saved'
                        and value == '' and isinstance(observed, str) and observed
                        and not any(r.get('SNO') == expected.get('SNO')
                                    for r in plan.get('original', {}).get('22', []))):
                    bindings = [b for b in plan.get('bank_bindings', [])
                                if b.get('person_id') == expected.get('SNO')]
                    fields = {'person_id':'SNO', 'name':'SNAME', 'account':'BANKACNT',
                              'bank':'BANKNAME', 'key':'KEY'}
                    if (len(bindings) == 1 and all(expected.get(field) == bindings[0].get(key)
                                                   and key in bindings[0] for key, field in fields.items())
                            and expected.get('CNT1') == bindings[0]['account']):
                        derived_departments.append({'person_id':expected['SNO'], 'department':observed})
                        continue
                if key == 'AMOUNT' or no == '19' and key.endswith('_AMT'):
                    if value == '' and observed == '': continue
                    if (not isinstance(observed,str) or not re.fullmatch(r'(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?',observed)
                            or money(observed.replace(',','')) != money(value)):
                        raise ValueError('支付金额不符：' + no + '/' + key)
                elif observed != value: raise ValueError('收款身份、卡绑定或未改字段不符：' + no + '/' + key)
    return {'stage':'payments','verified_sections':len(target),'verified_recipients':len(target['22']),
            'independent_http_readback':True,'outcome':outcome,
            **({'server_derived_departments':derived_departments} if derived_departments else {})}


def recover_original(input_path,output,c,paths):
    from finance_native_create import ref,checked,frozen,read,sha
    from finance_backend import assert_original_transport
    from edge_runtime import browser_lock,read_marker
    from registered_page_owner import RegisteredPageOwner
    from assistant_api import atomic_json
    stage=c['stage'];pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
    if v.get('schema')!=SCHEMA or v.get('input')!=str(input_path)or v.get('input_sha256')!=sha(input_path)or pending.get('operation')!='step:'+stage:raise ValueError('只恢复原HTTP阶段意图')
    plan=read(v['prepare_receipt'])['result'];validate_plan(c,plan)
    if sha(v['prepare_receipt'])!=v['prepare_sha256']:raise ValueError('原阶段准备来源变化')
    expected={k:plan[k]for k in ['original','changes','definition_sha256']+(['teacher']if stage=='basic'else['candidates']if stage=='funding'else['bank_bindings','bank_check_flag']+(['card_bindings']if'card_bindings'in plan else[])+(['corporate_bindings']if'corporate_bindings'in plan else[])if stage=='payments'else['scope']if stage=='notes'else['slots']if stage=='pre-review'else[])}
    adapter=(input_path.parent/'native-stage-adapter.js').read_text();old=read(v['save_receipt']);method='reimbursement'+stage.title().replace('-','')+'Save'
    text='(async()=>{\n'+adapter+'\nreturn await '+method+'('+json.dumps({**c,'expected_plan':expected},ensure_ascii=False)+');\n})()'
    if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=text:raise ValueError('原阶段保存源码漂移')
    if old.get('phase')=='script_completed':saved=old
    else:
        if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原阶段未派发或缺marker')
        assert_original_transport(old)
        with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
        if not isinstance(body,dict):raise ValueError('原阶段marker尚未返回')
        saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now().astimezone().isoformat(),'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
    recovered=Path(output).parent/'original-stage-result.json';frozen(recovered,saved)
    config={**c,'execute':False};own_reader=stage=='categories'or stage in {'funding','expenses'}and bool(plan['original'].get('implicit_default'));reader='reimbursement'+stage.title()+'Prepare'if own_reader else'reimbursementBusinessApi'
    if not own_reader:config.update(action='bill-read',domain=READ_DOMAINS[stage],contracts=c['budget_contracts']if stage=='funding'else c['contracts'])
    script=Path(output).with_suffix('.js');script.write_text('(async()=>{\n'+adapter+'\nreturn await '+reader+'('+json.dumps(config,ensure_ascii=False)+');\n})()',encoding='utf-8',newline='\n');script.chmod(0o600)
    RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    result=subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(output),'--tab-id',c['tab_id'],'--operation-kind','read','--quiet'])
    if not Path(output).is_file():raise ValueError('原阶段恢复后缺独立回读，保留pending')
    observed=read(output);observed['original_save_recovery']=ref(recovered);atomic_json(output,observed);return result.returncode


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--recover',action='store_true');a=p.parse_args();return run(a.input,a.output,recover=a.recover)


if __name__ == '__main__': raise SystemExit(main())
