"""异常清理的固定HTTP脚本与原pending核验；复用现有preview/readback owner。"""
from pathlib import Path
import json
import re

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-invoice-cleanup-v1'
REIDENTIFY_SCHEMA='ustc-native-invoice-reidentify-v1'


def script(request, action='inspect', *, plan=None, request_id=None, pending=False):
    from finance_http import source
    from finance_invoice_retry import digest
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',request['expected_userid']):
        raise ValueError('HTTP异常处理须明确当前财务账号')
    contracts=json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']
    c={**request,'action':action,'retry_contract':json.loads((ROOT/'data/finance_retry_contract.json').read_text()),
       'inventory_contracts':{'invoices':contracts['invoices'],'recognition':contracts['recognition']},
       'request_id':request_id,'execute':action=='confirm','pending_registered':pending}
    if plan is not None:c.update(plan=plan,plan_digest=digest(plan))
    return '(async()=>{'+source('retry')+';return await reimbursementRetryApi('+json.dumps(c,ensure_ascii=False)+');})()'


def native_plan_request(request):
    """派发门禁只领用同案正式native预览；完整输入重建仍由原command owner执行。"""
    from assistant_api import AssistantService
    p=request['parameters']
    if p.get('action')not in {'cleanup','reidentify','confirm','refresh'} or not request.get('workspace'):
        return False
    service=AssistantService(request['workspace'],request.get('material_roots'),request.get('python'))
    try:
        _,plan,_=service._typed_case_artifact(request['case_id'],p.get('plan_job_id'),p.get('plan_artifact_id'),'invoice_retry',['preview'])
        return plan.get('business_transport')=='native_http'and plan.get('ready')is True
    except (ValueError,KeyError,TypeError,OSError):
        return False


def confirm_evidence(plan,receipt,inspection):
    from finance_invoice_retry import body,digest,parse_observed_time,native_field
    a,o=body(receipt),body(inspection);req=plan['request']
    if (receipt.get('phase')!='script_completed' or receipt.get('tab_id')!=req['tab_id']
        or a.get('case_id')!=plan['case_id']or a.get('business_transport')!='native_http'
        or a.get('action')!=plan['action']or a.get('marker',{}).get('plan_digest')!=digest(plan)
        or a['marker'].get('phase')!='awaiting_http_confirmation'):
        raise ValueError('须引用原HTTP清理确认意图，不领用旧控件回执')
    if (o.get('business_transport')!='native_http' or o.get('identity',{}).get('userid')!=req['expected_userid']
        or o.get('definitionCaptureComplete')is not True or o.get('grid',{}).get('funcno')!=req['grid']
        or [b for b in o.get('buttonDefinitions',[]) if b.get('caption')==plan['button_definition']['caption']]!=[plan['button_definition']]
        or o['grid'].get('records')!=plan['scope']['grid_records']):
        raise ValueError('清理确认前HTTP定义或完整范围变化')
    if parse_observed_time(o['observed_at'])<=parse_observed_time(receipt['executed_at_utc']):
        raise ValueError('清理确认前须新发独立HTTP检查')
    for t in plan['targets']:
        rows=[r for r in o['grid']['rows']if native_field(r,t['native_key'])==t['native_value']]
        if rows!=[t['native_row']]:raise ValueError('清理确认前目标字段变化')
    return True


def verify_ack(previous,receipt,outcome="saved"):
    """新的完整双库存+原成功confirm脚本才可解除原pending。"""
    from finance_invoice_retry import load,sha,readback,action_script,digest
    from assistant_api import read_json
    v=previous.get('verification',{});p=read_json(receipt)
    if v.get('schema') not in {SCHEMA,REIDENTIFY_SCHEMA} or p.get('schema')!='ustc-finance-invoice-retry-readback-v1':
        raise ValueError('原HTTP清理pending与独立回读类型不符')
    evidence=p.get('inputs_evidence',{})
    if set(evidence)!={'plan','pool','recognition','receipt'}:
        raise ValueError('原HTTP清理回读缺少完整输入证据')
    for ref in evidence.values():
        if sha(ref['path'])!=ref.get('sha256'):raise ValueError('原HTTP清理回读输入SHA变化')
    if (evidence['plan']['path']!=v.get('plan')or evidence['plan']['sha256']!=v.get('plan_sha256')):
        raise ValueError('回读不是原清理计划')
    plan=load(evidence['plan']['path']);native=load(evidence['receipt']['path'])
    from finance_invoice_retry import verify_plan_evidence
    verify_plan_evidence(plan)
    from finance_invoice_retry import parse_observed_time
    if plan['case_id']!=previous['case_id'] or parse_observed_time(native['executed_at_utc'])<=parse_observed_time(previous['started_at_utc']):
        raise ValueError('原清理动作须同案且晚于原pending')
    recovery=native.get('original_marker_recovery')
    if recovery:
        original=Path(previous['output_directory'])/'native.json'
        if (Path(recovery.get('path','')).resolve()!=original.resolve()or sha(original)!=recovery.get('sha256')
                or recovery.get('script_reexecuted')is not False):raise ValueError('原清理marker恢复来源不符')
        old=load(original)
        if any(old.get(k)!=native.get(k)for k in ['script','source_sha256','tab_id','result_marker','browser_transport']):
            raise ValueError('原清理marker恢复改变原动作身份')
    if Path(native.get('script','')).resolve().parent!=Path(previous['output_directory']).resolve():
        raise ValueError('回读未引用原清理confirm工作目录')
    original_script=Path(native['script']).read_bytes()
    if sha(native['script'])!=native.get('source_sha256') or original_script.decode()!=action_script(plan,native['result']['request_id'],True):
        raise ValueError('原清理confirm脚本SHA或固定源码不符')
    rebuilt=readback(plan,load(evidence['pool']['path']),load(evidence['recognition']['path']),native)
    if any(p.get(k)!=value for k,value in rebuilt.items()):raise ValueError('回读结论与原双库存证据重建不同')
    is_reidentify=plan['action']=='reidentify'
    if previous['operation']!='invoice:http-'+plan['action'] or v['schema']!=(REIDENTIFY_SCHEMA if is_reidentify else SCHEMA):
        raise ValueError('原异常处理pending动作与计划不同')
    applied=(outcome=='saved' and rebuilt.get('server_accepted')is True and rebuilt['all_target_records_absent']
             and (not is_reidentify or rebuilt['all_targets_authenticated']))
    errors=(is_reidentify and outcome=='needs_correction' and rebuilt.get('server_accepted')is True
            and rebuilt['all_targets_have_recognition_error'])
    rejected=(outcome=='not_applied' and rebuilt.get('server_accepted')is False and rebuilt.get('server_status')=='error'
              and rebuilt['targets'] and all(t['native_target_fields_unchanged'] for t in rebuilt['targets']))
    if not (applied or errors or rejected)or not rebuilt['protected_records_unchanged']:
        raise ValueError('未证明异常处理结果或明确拒绝未生效；原pending保留')
    return {'native_retry_verified':True,'native_cleanup_verified':not is_reidentify,'native_reidentify_verified':is_reidentify,
            'plan_digest':digest(plan),'target_records_absent':rebuilt['all_target_records_absent'],
            'protected_records_unchanged':True,'outcome':outcome,'claim_created':False,
            'buyer_fields_verified':False,'claim_amount_verified':False}


def recover_original(plan,request_id,action,path,expected_sha):
    """只读原marker；保留原运行时回执，既不重新注入也不重发HTTP。"""
    from datetime import datetime,timezone
    from finance_invoice_retry import load,sha,action_script
    from finance_backend import assert_original_transport
    from registered_page_owner import RegisteredPageOwner
    from edge_runtime import read_marker
    if action not in {'confirm','cleanup','refresh','reidentify'} or sha(path)!=expected_sha:
        raise ValueError('原清理恢复输入或动作不符')
    old=load(path);text=Path(old['script']).read_text()
    if (old.get('tab_id')!=plan['request']['tab_id']or sha(old['script'])!=old.get('source_sha256')
            or text!=action_script(plan,request_id,action=='confirm')):
        raise ValueError('原清理回执tab或固定脚本SHA不符')
    if old.get('phase')=='not_dispatched':raise ValueError('原清理动作明确未派发，不能当作丢失响应恢复')
    if old.get('phase')=='script_completed':return old
    RegisteredPageOwner.before_child_dispatch(old['tab_id'])
    assert_original_transport(old)
    result=read_marker(old['result_marker'],old['tab_id'],timeout=45)
    return {**old,'phase':'script_completed','result':result,'executed_at_utc':datetime.now(timezone.utc).isoformat(),
        'original_marker_recovery':{'path':str(Path(path).resolve()),'sha256':expected_sha,'script_reexecuted':False}}
