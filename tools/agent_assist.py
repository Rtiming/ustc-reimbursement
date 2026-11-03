"""精简代理入口：按需发现固定工具，复用工作台意图与原任务，不另写财务流程。"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from assistant_contract import APIError

ACTIONS = ('discover', 'schema', 'call', 'prepare', 'confirm', 'status')


def props_for_action(schema):
    return schema.get('properties',{}).get('action',{}).get('enum',[])


def next_call(**arguments):
    return {'tool': 'reimbursement_assist', 'arguments': arguments}


def compact_routes(value):
    if isinstance(value,list):return [compact_routes(row)for row in value]
    if not isinstance(value,dict):return value
    result={k:compact_routes(v)for k,v in value.items()}
    tool=result.get('tool')
    if isinstance(tool,str)and tool.startswith('reimbursement_')and tool not in {'reimbursement_assist','reimbursement_task_view','reimbursement_capabilities'}:
        arguments=result.get('arguments',{})
        action='prepare'if arguments.get('execute')is True or result.get('requires_user_confirmation')else'call'
        return {**result,**next_call(action=action,operation=tool.removeprefix('reimbursement_'),inputs=arguments)}
    return result


def compact(value, *, case_id=None):
    """小模型默认取得下一步和引用；完整原回执保留在原任务或确认意图中。"""
    if not isinstance(value, dict):
        return {'ok': True, 'result': value}
    case_id=case_id or value.get('case_id')
    if isinstance(value.get('rows'),list) and len(value['rows'])>20 and 'has_more'in value and value.get('source',{}).get('intake_job_id'):
        value=copy.deepcopy(value);value['rows']=value['rows'][:20];value['has_more']=value.get('offset',0)+20<value['total_materials']
        value['next_page_action']=next_call(action='call',operation='intake_review',inputs={'case_id':case_id,
            'intake_job_id':value['source']['intake_job_id'],'offset':value.get('offset',0)+20,'limit':20})if value['has_more']else None
    if isinstance(value.get('result'), dict) and 'intent_id' in value:
        result = compact(value['result'], case_id=case_id)
        result.update(intent_id=value['intent_id'], intent_status=value['status'], replayed=value.get('replayed', False))
        return result
    job_id = value.get('job_id')
    if job_id and case_id and value.get('schema') not in {'ustc-task-view-v1','ustc-case-fill-v1','ustc-case-ready-v1','ustc-case-delivery-v1'}:
        if value.get('status') in {'failed','unknown'}:
            return {key:value[key]for key in ('ok','job_id','status','error','error_code','messages','artifacts','next')if key in value} | {
                'case_id':case_id,'safe_to_retry':False,'next_action':{'kind':'human_review','reason':'原任务未明确完成，请核对原回执并选择其支持的恢复方式；不循环重发。'}}
        return {key: value[key] for key in ('ok', 'job_id', 'status', 'next', 'error', 'error_code') if key in value} | {
            'case_id': case_id, 'safe_to_retry': False,
            'next_action': next_call(action='status', case_id=case_id, job_id=job_id),
        }
    if value.get('schema') == 'ustc-materialize-preview-v1':
        return compact_routes({k: value[k] for k in ('ok','schema','case_id','plan_sha256','revision','held_forms','system_write','next_action') if k in value} |
            {'forms': [{k: row.get(k) for k in ('summary','invoice_count','claim_amount','invoice_numbers','expense_ids')} for row in value.get('forms',[])]})
    if value.get('local_materials_only') and isinstance(value.get('plan'),dict):
        plan=value['plan']
        return compact_routes({'ok':value.get('ok'),'case_id':value.get('case_id'),'system_write':False,
            'status':value.get('status'),'saved_revision':value.get('saved_revision'),
            'documents':[{'document_id':d['document_id'],'status':d['status'],
                'forms':[{k:f.get(k)for k in ('form_index','invoice_count','claim_amount','candidate_claim_total','summary','remarks','status')}for f in d.get('forms',[])]}for d in plan.get('documents',[])],
            'readiness':{k:plan.get(k)for k in ('structure_readiness','amount_readiness','export_readiness','finance_readiness')},
            'needed_inputs':value.get('needed_inputs',[]),'next_action':value.get('next_action')})
    if len(json.dumps(value, ensure_ascii=False)) <= 10000:
        return compact_routes(value)
    keys = ('ok', 'schema', 'case_id', 'revision', 'title', 'summary', 'status', 'needed_inputs', 'next_action',
            'error', 'error_code', 'safe_to_retry', 'system_write', 'input_validation', 'source', 'note', 'saved',
            'cases', 'batches', 'rows', 'row_count', 'count', 'files', 'warnings',
            'items','plans','plan_id','state','decision','opinion','expected_userid','connection_id','source_job_id',
            'serial','originals','selected','connections','connection','jobs','fill_plans','pending_write','active_jobs',
            'stages','index','phase','authorized','job_id','pause_requested')
    keys += ('next_page_action','group_action','preview_action','review_action','has_more','offset','total_materials')
    keys += ('materials','document','expenses','issues','rules_selected','total_expenses')
    keys += ('artifacts','delivery_id','can_build_pdf','scope','official_information_page_included','dispositions','generated_cases','plan_sha256')
    keys += ('ready_plans','promise_text','promise_sha256','form','step','system_result_verified','whole_claim_completed')
    result = {key: value[key] for key in keys if key in value}
    for key in ('rows', 'cases', 'batches'):
        if isinstance(result.get(key), list) and len(result[key]) > 20:
            result[key+'_total'] = len(result[key]);result[key] = result[key][:20]
    result['response_compacted'] = True
    return compact_routes(result)


def run(service, action='discover', group=None, operation=None, inputs=None, case_id=None,
        intent_id=None, job_id=None, confirmation=None):
    from operator_workbench import Workbench
    if action not in ACTIONS:
        raise APIError('assist_action', '请选择discover、schema、call、prepare、confirm或status')
    app = Workbench(service)
    if action == 'discover':
        if group is None:
            return {'ok': True, 'groups': app.catalog['groups'], 'cases': service.cases()['cases'],
                    'next_action': next_call(action='discover', group='activities'),
                    'instructions': '选group查看操作；schema取准确字段。只读/预览用call；改动先prepare，明确获准后confirm。只照next_action继续，不重放未知结果。'}
        if group not in {row['id'] for row in app.catalog['groups']}:
            raise APIError('assist_group', 'group须从discover返回的分组选择')
        return {'ok': True, 'operations': [
            {'operation': row['name'].removeprefix('reimbursement_'), 'title': row['title'],
             'read_only': row.get('annotations', {}).get('readOnlyHint') is True}
            for row in app.catalog['tools'] if row['group'] == group and row['name'] != 'reimbursement_assist'],
                'next': '选择operation后用schema查看所需输入。'}
    if action == 'status':
        if intent_id:
            _, value = app.intent(intent_id)
            return compact({'intent_id': intent_id, **value}, case_id=value['request']['params'].get('case_id'))
        if case_id and job_id:
            return compact(service.task_view(case_id, job_id, wait_seconds=5),case_id=case_id)
        raise APIError('assist_status', '状态查询需要原intent_id，或case_id和原job_id')
    if action == 'confirm':
        if not isinstance(confirmation, str) or not confirmation.strip():
            raise APIError('assist_confirmation', '需要记录用户对原计划的明确授权；不能把发现工具当作授权')
        _, value = app.intent(intent_id)
        # 确认仅接受既有意图；不接受替换范围、金额或任务参数。
        if inputs is not None or operation is not None:
            raise APIError('assist_confirmation', '确认只使用intent_id，不能替换原计划')
        result=compact(app.execute(intent_id, confirmation_note=confirmation.strip()), case_id=value['request']['params'].get('case_id'))
        if value['tool']=='reimbursement_new_case'and result.get('ok'):
            result['case_id']=value['request']['params']['case_id']
            result['next_action']=next_call(action='call',operation='case_journey',case_id=result['case_id'])
        return result
    name = 'reimbursement_' + str(operation).removeprefix('reimbursement_')
    if name not in app.tools or name == 'reimbursement_assist':
        raise APIError('assist_operation', '请从discover选择已登记操作；不接受任意方法或递归调用')
    tool = app.tools[name]
    if action == 'schema':
        schema=copy.deepcopy(tool['inputSchema'])
        if name=='reimbursement_new_case':schema['required']=[k for k in schema.get('required',[])if k!='case_id']
        if name=='reimbursement_case_materials':
            from case_materials_schema import value_schemas
            choices=value_schemas()
            selected_action=(inputs or {}).get('action')
            if selected_action:
                if selected_action not in props_for_action(schema):
                    raise APIError('assist_action_schema','请从当前材料操作选择action')
                schema['properties']['action']={'const':selected_action,'type':'string'}
                if selected_action in choices:schema['properties']['values']=choices[selected_action]
                seed_action=selected_action
            else:
                schema['x-values-by-action']=choices
                seed_action=None
        props=schema.get('properties',{});seed={}
        if name=='reimbursement_case_materials' and seed_action:seed['action']=seed_action
        if case_id and 'case_id'in props:seed['case_id']=case_id
        if 'request_id'in props:seed['request_id']='assist-'+uuid.uuid4().hex
        next_mode='call'if tool.get('annotations',{}).get('readOnlyHint')is True or 'execute'in props else'prepare'
        return {'ok': True, 'operation': operation, 'title': tool['title'], 'description': tool.get('description'),
                'input_schema': schema, 'annotations': tool.get('annotations'),
                'suggested_inputs':seed,'next_action':next_call(action=next_mode,operation=operation,inputs=seed),
                'needed_inputs':[k for k in schema.get('required',[])if k not in seed and k not in {'expected_revision'}]+(['title']if name=='reimbursement_new_case'else[]),
                'next': '把真实输入放入inputs。新建活动给title即可生成内部case_id；已有活动使用discover返回的case_id。expected_revision和稳定request_id可自动填写。'}
    if inputs is not None and not isinstance(inputs, dict):
        raise APIError('assist_inputs', 'inputs须为字段对象')
    args = copy.deepcopy(inputs or {})
    if name in {'reimbursement_case_materials','reimbursement_intake_review'}and args.get('action','inspect')=='inspect':args.setdefault('limit',20)
    props = tool['inputSchema'].get('properties', {})
    if name=='reimbursement_new_case'and not args.get('case_id')and not case_id:
        title=args.get('title')
        if not isinstance(title,str)or not title.strip():
            return {'ok':False,'error_code':'missing_inputs','needed_inputs':[{'field':'title','label':'本次活动名称'}],'safe_to_retry':True}
        seed={'title':title.strip(),'profile':args.get('profile','student_activity')}
        args['case_id']='activity_'+hashlib.sha256(json.dumps(seed,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        existing=next((c for c in service.cases()['cases']if c['case_id']==args['case_id']),None)
        if existing:
            return {'ok':True,'case_id':args['case_id'],'existing_case':True,'title':existing['title'],
                    'next_action':next_call(action='call',operation='case_journey',case_id=args['case_id']),
                    'note':'同名同场景活动已存在，继续原活动；确为另一个同名活动时可显式提供新的内部case_id。'}
    if case_id and 'case_id' in props:
        if args.get('case_id', case_id) != case_id:
            raise APIError('assist_case', '本次case_id与输入不一致')
        args['case_id'] = case_id
    actual_case = args.get('case_id')
    if 'expected_revision' in props and 'expected_revision' not in args and actual_case:
        args['expected_revision'] = service.revision(actual_case)
    if 'execute' in props:
        args.setdefault('execute', action == 'prepare')
    if action == 'call' and args.get('execute') is True:
        raise APIError('assist_prepare_required', '执行须先prepare核对原计划，再confirm；call只读或预览')
    if 'request_id' in props and 'request_id' not in args:
        # 相同操作、范围、执行模式和输入版本复用同一任务；不能自动改号重试。
        identity = {'tool': name, 'inputs': args, 'revision': service.revision(actual_case) if actual_case else None}
        from workbench_catalog import read_allowed
        readonly=read_allowed(tool,args)or(name=='reimbursement_finance_connection'and args.get('action')in {'verify','bills','approvals','history'})or(name=='reimbursement_approver_batch'and args.get('action')in {'recover','reconcile'})
        args['request_id'] = 'assist-' + (uuid.uuid4().hex if readonly else hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:40])
    missing = [key for key in tool['inputSchema'].get('required', []) if key not in args]
    if missing:
        return {'ok': False, 'error_code': 'missing_inputs', 'needed_inputs': [
            {'field': key, 'schema': props[key]} for key in missing],
            'next_action': next_call(action='schema', operation=operation), 'safe_to_retry': True}
    app.validate(name, args)
    if action == 'prepare':
        if name == 'reimbursement_standards_change' and args.get('execute') is True and not args.get('expected_sha256'):
            # 规范owner只接受原预览SHA；缺版本依据时先给可执行预览，不创建必然失败的确认意图。
            preview_args = {**args, 'execute': False}
            preview_args.pop('expected_sha256', None)
            return {'ok': False, 'error_code': 'missing_inputs', 'system_write': False,
                    'needed_inputs': [{'field': 'expected_sha256', 'label': '同一变更预览返回的before_sha256',
                                       'schema': props['expected_sha256']}],
                    'next_action': next_call(action='call', operation='standards_change', inputs=preview_args),
                    'safe_to_retry': True,
                    'note': '先执行此只读预览，将before_sha256原样填入相同变更的expected_sha256，再prepare并确认。'}
        plan = app.prepare(name, args)
        plan['next_action'] = next_call(action='confirm', intent_id=plan['intent_id'])
        plan['needed_inputs'] = ['用户对以上具体范围的授权说明confirmation']
        return plan
    from workbench_catalog import read_allowed
    if read_allowed(tool,args):
        return compact(app.read(name, args), case_id=actual_case)
    # 只有显式execute=false的预览可走call；无预览参数的本地修改也须prepare。
    if 'execute' not in props:
        raise APIError('assist_prepare_required', '此操作会保存本地资料，请先prepare核对范围')
    plan = app.prepare(name, args)
    return compact(app.execute(plan['intent_id']), case_id=actual_case)
