"""展示原材料规则的具体差项和适用条件；确认查看不等于材料齐全。"""
import hashlib
import json
from pathlib import Path

from assistant_contract import APIError

LABELS={'activity_approval_required':'本次是否需要活动审批表','online_purchase':'是否涉及网购',
 'requires_non_card_support':'是否已确认需要未使用公务卡的支付及说明材料','has_travel':'是否含差旅费用',
 'has_hotel':'是否含住宿费用','boarding_pass_originals_required':'本次是否要求登机牌原件',
 'fare_comparison_required':'是否需要比价或核定票价依据','contract_required':'本次是否需要合同',
 'asset_registration_required':'是否已确认需要固定资产登记','signed_receipt_required':'是否需要签领表','chartered_vehicle':'是否涉及包车或租车'}
ROLES={'invoice':'发票','activity_approval':'活动审批表','payment_proof':'实际付款凭证','order_detail':'订单或消费明细',
 'purchase_details':'采购清单','non_card_explanation':'未使用公务卡说明','travel_evidence':'行程材料','hotel_details':'住宿明细',
 'boarding_pass_originals':'登机牌原件','fare_comparison':'比价或核定票价依据','contract':'合同','asset_registration':'资产登记',
 'signed_receipt':'签领表','vehicle_list':'包车/租车清单'}
ISSUES={'missing':'尚未登记','condition_unknown':'适用条件尚未核定','not_verified_or_not_ready':'已收材料尚待核对',
 'original_claim_number_missing':'原件在其他单据，需要原单号','invoice_scope_unknown':'待核定发票范围',
 'payment_coverage_incomplete':'付款凭证尚未完整覆盖','invoice_amount_scope_unknown':'待核定逐票金额与范围'}


def inspect(service,case_id):
    from assistant_api import read_json,PROJECT
    _,paths=service.resolve(case_id);case=read_json(paths['case'])
    from workspace_config import material_rules_path
    rules_path=material_rules_path(service.root if(service.root/'.local/installation.json').exists()else PROJECT);rules=read_json(rules_path)
    conditions={k for row in rules.get('rules',[])for k in row.get('when_all',{})}
    context=case.get('material_context',{})
    checked=service.check(case_id)
    audit=checked.get('material_audit')
    if audit is None:
        errors=checked.get('errors',[])
        error=next((e['error']for e in errors if e['field']=='case.material_context'),'材料规则尚未核实')
        audit={'checks':[],'ready_for_material_delivery':False,'configuration_error':error}
    identity={'materials':case.get('materials',[]),'invoice_scope':case.get('invoices',{}),
              'context':{k:v for k,v in context.items()if k!='material_check_review'},
              'rules_sha256':hashlib.sha256(rules_path.read_bytes()).hexdigest(),'audit':audit}
    fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    review=context.get('material_check_review',{})
    return {'ok':True,'schema':'ustc-material-requirements-v1','case_id':case_id,'revision':service.revision(case_id),
            'fingerprint':fingerprint,'reviewed_current':review.get('fingerprint')==fingerprint,
            'ready_for_material_delivery':audit.get('ready_for_material_delivery')is True,
            'configuration_error':audit.get('configuration_error'),
            'conditions':[{'key':k,'label':LABELS.get(k,k),'value':context.get(k)}for k in sorted(conditions)],
            'rows':[{'role':r['role'],'label':ROLES.get(r['role'],r['role']),'state':ISSUES.get(r.get('issue'),r.get('issue')or'按已登记材料核对通过'),
                     'issue':r.get('issue'),'level':r['level'],'instruction':r.get('instruction'),
                     'source':r.get('source'),'unknown_conditions':r.get('unknown_conditions',[]),
                     'observed_statuses':r.get('observed_statuses',[])}for r in audit.get('checks',[])],
            'system_write':False,'note':'这里显示本案材料要求。查看差项不会把缺件改成齐全；付款截图、订单和发票各自保留真实类别。'}


def run(service,case_id,action='inspect',values=None,expected_revision=None,note=None,execute=False):
    current=inspect(service,case_id)
    if action=='inspect':
        if execute or values is not None:raise APIError('material_requirements_action','查看不接受修改内容')
        return current
    if action not in {'context','acknowledge'}or type(execute)is not bool:raise APIError('material_requirements_action','请选择查看、适用条件或记录已查看差项')
    if expected_revision!=current['revision']:raise APIError('revision_conflict','材料或填报已改变，请重新查看本次差项')
    if action=='context':
        keys={row['key']for row in current['conditions']}
        if not isinstance(values,dict)or not values or set(values)-keys or any(type(v)is not bool for v in values.values()):
            raise APIError('material_context_fields','只填写已明确的本案适用条件；不确定的保留未核定')
        if not isinstance(note,str)or not note.strip():raise APIError('material_context_basis','请记录核定这些适用条件的依据')
        changes={**values,'workbench_conditions_basis':note.strip()}
    else:
        if values is not None:raise APIError('material_requirements_action','记录查看不接收替换材料或规则')
        changes={'material_check_review':{'fingerprint':current['fingerprint'],'missing_preserved':True,
                 'meaning':'已查看当前差项，可继续其他准备；不代表材料齐全'}}
    if not execute:return {'ok':True,'preview':True,'changes':changes,'system_write':False}
    service.update_case(case_id,'case',{'material_context':changes},expected_revision)
    return {**inspect(service,case_id),'saved':True,'next_action':{'tool':'reimbursement_case_journey','arguments':{'case_id':case_id},'label':'继续本案下一步'}}
