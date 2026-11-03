"""人和agent共用的日常填报信息表单；金额、身份及保存校验仍由原owner负责。"""
from __future__ import annotations
import copy
import hashlib
import re

from assistant_contract import APIError
from validate_config import validate
from validate_config import money


def field(title, kind='string', **options):
    return {'title':title,'type':kind,**options}


FORM_SCHEMA = {'type':'object','properties':{
    'summary':field('报销摘要'),'amount':field('核定申请金额（元）'),'phone':field('经办手机'),'landline':field('经办座机（可选）'),
    'teacher_name':field('证明人姓名'),'teacher_id':field('证明人工号'),'remarks':field('问题联系人与备注'),
    'leader_expenses':field('是否含项目负责人费用',enum=['unknown','no','yes']),
    'return_point':field('学校退单地点选项（按本案实际选择）',enum=['unknown','退单地点','退单地点2']),
    'funding':field('经费项目','array',items={'type':'object','properties':{
        'code':field('经费代码'),'amount':field('本经费承担金额（元）'),
        'department_approver':field('本经费最终单位审批人','object',properties={
            'id':field('审批人工号'),'name':field('审批人姓名'),'source':field('本次核对依据')})},'required':['code','amount']}),
    'payments':field('收款安排','array',items={'type':'object','properties':{
        'mode':field('支付方式',enum=['bank_transfer','corporate_transfer','corporate_card']),
        'name':field('实际收款人或单位'),'person_id':field('个人学工号'),
        'amount':field('收款金额（元）'),'bank_name':field('对公开户行'),
        'bank_account':field('对公银行账号'),'source_refs':field('对公信息依据','array',items={'type':'string'}),
        'remark':field('付款附言'),'transaction':field('公务卡消费匹配信息','object',properties={
            'date':field('消费日期（YYYY-MM-DD）'),'merchant':field('消费商户'),'card_suffix':field('卡号后四位'),
            'search_amount':field('消费金额（元）')})},'required':['mode','name','amount']}),
    'allocation_mode':field('预算分配方式',enum=['system_default_single_funding','manual','unconfirmed']),
    'allocations':field('多经费预算分配（使用学校已核选项）','array',items={'type':'object','properties':{
        'project':field('经费代码'),'subject':field('预算科目'),'budget':field('预算项目'),'amount':field('分配金额（元）')},'required':['project','subject','budget','amount']}),
    'contract':field('是否有合同',enum=['unknown','no','yes']),
    'delegate_print':field('是否委托财务打印',enum=['unknown','no','yes']),
    'paper_originals':field('是否需要提交纸质原件',enum=['unknown','no','yes'])
},'additionalProperties':False}


def tri(value):
    return 'yes' if value is True else 'no' if value is False else 'unknown'


def inspect(service, case_id):
    from assistant_api import read_json
    _, paths=service.resolve(case_id)
    form=read_json(paths['form']);case=read_json(paths['case']);invoices=read_json(paths['invoices'])
    if form.get('system_mode','daily')!='daily':
        raise APIError('case_details_mode','此案请使用对应薪酬或其他专项表单，不转换为普通报销')
    basic=form.get('basic',{});fields=basic.get('fields',{})
    values={'summary':fields.get('t-abst',''),'amount':form.get('amount',''),'phone':fields.get('t-req_mobile',''),'landline':fields.get('t-req_tele',''),
        'teacher_name':basic.get('teacher_name',''),'teacher_id':basic.get('teacher_id',''),'remarks':fields.get('t-cl_abst',''),
        'leader_expenses':{'Y':'yes','N':'no'}.get(fields.get('t-is_have_changefee'),'unknown'),
        'return_point':fields.get('t-td_point')or'unknown',
        'funding':[{k:v for k,v in row.items()if k in FORM_SCHEMA['properties']['funding']['items']['properties']}for row in form.get('funding',[])],
        'payments':[{k:v for k,v in row.items()if k in FORM_SCHEMA['properties']['payments']['items']['properties']}for row in form.get('payments',[])],
        'allocation_mode':form.get('budget_allocation_mode','unconfirmed'),'allocations':form.get('expense_allocations',[]),
        'contract':tri(form.get('contract',{}).get('exists')),'delegate_print':tri(form.get('delivery',{}).get('delegate_finance_print')),
        'paper_originals':tri(form.get('delivery',{}).get('originals_required'))}
    missing=[]
    for key in ['summary','amount','teacher_name','teacher_id','funding','payments']:
        if not values[key]:missing.append({'field':key,'label':FORM_SCHEMA['properties'][key]['title']})
    if not values['phone']and not values['landline']:missing.append({'field':'phone','label':'经办手机或座机至少一项'})
    for key in ['contract','delegate_print','paper_originals','leader_expenses','return_point']:
        if values[key]=='unknown':missing.append({'field':key,'label':FORM_SCHEMA['properties'][key]['title']})
    if values['allocation_mode']=='unconfirmed':missing.append({'field':'allocation_mode','label':'预算分配方式'})
    check=copy.deepcopy(form);check['serial']=check.get('serial')or'0'
    try:validate(check);validation={'ok':True,'input_check_only':True}
    except (ValueError,KeyError,TypeError)as error:validation={'ok':False,'input_check_only':True,'error':str(error)}
    draft=case.get('material_context',{}).get('workbench_details_draft')
    form_sha=hashlib.sha256(paths['form'].read_bytes()).hexdigest()
    return {'ok':True,'schema':'ustc-case-details-v1','case_id':case_id,'revision':service.revision(case_id),'form_sha256':form_sha,
        'title':case.get('description')or case_id,'form_schema':FORM_SCHEMA,'values':values,
        'draft':draft,'draft_stale':bool(draft and draft.get('base_form_sha256')!=form_sha),
        'needed_inputs':missing,'input_validation':validation,'registered_original_count':len(invoices.get('original_files',[])),
        'serial':form.get('serial'),'system_write':False,
        'note':'本页保存本地填报资料。票面金额、实际支付和核定申请额分别核对；保存不等于已写入学校系统。'}


def patch_values(form, values, note):
    if not isinstance(values,dict)or set(values)-set(FORM_SCHEMA['properties']):
        raise APIError('case_details_fields','填报信息包含不支持的字段')
    patch={};basic=copy.deepcopy(form.get('basic',{}));fields=basic.setdefault('fields',{})
    for key, native in [('summary','t-abst'),('phone','t-req_mobile'),('landline','t-req_tele'),('remarks','t-cl_abst')]:
        if key in values:
            if not isinstance(values[key],str):raise APIError('case_details_fields','文字字段不能用其他类型代替')
            fields[native]=values[key]
    for key in ['teacher_name','teacher_id']:
        if key in values:basic[key]=values[key]
    patch['basic']=basic
    if 'leader_expenses'in values:
        if values['leader_expenses']=='unknown':fields.pop('t-is_have_changefee',None)
        else:fields['t-is_have_changefee']={'yes':'Y','no':'N'}[values['leader_expenses']]
    if 'return_point'in values:
        if values['return_point']=='unknown':fields.pop('t-td_point',None)
        else:fields['t-td_point']=values['return_point']
    if 'amount'in values:
        if values['amount']=='':
            if form.get('amount'):raise APIError('case_details_amount_missing','不能把清空的金额当作原金额已确认；请核定金额，或先暂存填写')
        else:patch['amount']=values['amount']
    for key,native,identity in [('funding','funding',('code',)),('payments','payments',('mode','person_id','name'))]:
        if key not in values:continue
        rows=values[key]
        if not isinstance(rows,list):raise APIError('case_details_fields','经费和收款须按行填写')
        allowed=set(FORM_SCHEMA['properties'][key]['items']['properties'])
        result=[]
        for row in rows:
            if not isinstance(row,dict)or set(row)-allowed:raise APIError('case_details_fields','经费或收款行包含不支持的字段')
            previous=next((p for p in form.get(native,[])if all(p.get(k)==row.get(k)for k in identity)),{})
            item={**previous,**row}
            if key=='payments':
                if item.get('mode')=='corporate_transfer':
                    item.pop('person_id',None)
                    if form.get('expected_userid'):item['expected_userid']=form['expected_userid']
                else:
                    for name in ['bank_account','bank_name','source_refs']:
                        item.pop(name,None)
                if item.get('mode')!='corporate_card':item.pop('transaction',None)
            result.append(item)
        patch[native]=result
    if values.get('allocation_mode') in {'manual','system_default_single_funding'}:
        patch['budget_allocation_mode']=values['allocation_mode']
    if 'allocations'in values:patch['expense_allocations']=values['allocations']
    if patch.get('budget_allocation_mode')=='system_default_single_funding'and not values.get('allocations'):
        patch['expense_allocations']=[]
    for key, section, name in [('contract','contract','exists'),('delegate_print','delivery','delegate_finance_print'),('paper_originals','delivery','originals_required')]:
        if key in values:
            if values[key]not in {'yes','no','unknown'}:raise APIError('case_details_choice','请选择是、否或尚未核对')
            patch.setdefault(section,{})[name]={'yes':True,'no':False,'unknown':None}[values[key]]
            if section=='contract':patch[section]['source']=note
    return patch


def normalized_money(value):
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            if key in {'amount','search_amount'}:
                if not isinstance(item,str)or not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?',item):
                    raise APIError('case_details_money','金额请填写数值，例如1234.56，单位为元；未知金额请先暂存，不填写0代替')
                result[key]=format(money(item.replace(',','')),'.2f')
            else:result[key]=normalized_money(item)
        return result
    if isinstance(value,list):return[normalized_money(item)for item in value]
    return value


def run(service, case_id, action='inspect', values=None, expected_revision=None, confirmation_note=None, execute=False):
    if action not in {'inspect','draft','save'}or type(execute)is not bool:raise APIError('case_details_action','仅支持查看、暂存或保存填报资料')
    current=inspect(service,case_id)
    if action=='inspect':
        if values is not None or execute:raise APIError('case_details_action','查看资料不接收保存内容')
        return current
    if current['revision']!=expected_revision:raise APIError('revision_conflict','资料已有变化，请重新查看再合并本次填写')
    from assistant_api import read_json
    _,paths=service.resolve(case_id);form=read_json(paths['form'])
    if not isinstance(values,dict)or set(values)-set(FORM_SCHEMA['properties']):raise APIError('case_details_fields','字段不属于本表单')
    from jsonschema import Draft202012Validator
    schema=copy.deepcopy(FORM_SCHEMA)
    if action=='draft':
        def relax(item):
            if isinstance(item,dict):
                item.pop('required',None)
                for child in item.values():relax(child)
            elif isinstance(item,list):
                for child in item:relax(child)
        relax(schema)
    errors=list(Draft202012Validator(schema).iter_errors(values))
    if errors:
        item=errors[0];where='.'.join(map(str,item.path))or'填报信息'
        raise APIError('case_details_fields',where+'：'+item.message)
    if action=='draft':
        changes={'material_context':{'workbench_details_draft':{'values':values,'status':'draft_not_applied','base_form_sha256':current['form_sha256']}}}
        result=service.update_case(case_id,'case',changes,expected_revision)if execute else{'ok':True,'preview':True}
        return {**result,'saved_draft':execute,'system_write':False,'next_action':{'tool':'reimbursement_case_details','arguments':{'case_id':case_id},'label':'继续填写'}}
    if not isinstance(confirmation_note,str)or not confirmation_note.strip():raise APIError('case_details_confirmation','保存须记录本次确认依据')
    patch=normalized_money(patch_values(form,values,confirmation_note))
    service.validate_patch(patch,'form')
    if not execute:return {'ok':True,'preview':True,'changes':patch,'system_write':False,'revision':expected_revision}
    saved=service.update_case(case_id,'form',patch,expected_revision)
    return {**inspect(service,case_id),'saved':True,'input_receipt':saved['input_receipt'],
            'next_action':{'tool':'reimbursement_case_journey','arguments':{'case_id':case_id},'label':'查看本案下一步'}}
