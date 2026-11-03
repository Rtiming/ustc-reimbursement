"""科目整组改类的持久回读；只从冻结表单推导允许变化。"""
import json
import re
from pathlib import Path
from finance_native_stage import validate_plan,digest

def verify_categories_ack(c,plan,saved,read,outcome):
    observed=read.get('result',{});validate_plan(c,observed)
    if outcome not in {'saved','not_applied'}:raise ValueError('分类只登记已核实的saved或not_applied')
    adapter=Path(saved['script']).parent/'native-stage-adapter.js';source=adapter.read_text()
    expected={k:plan[k]for k in ['original','changes','definition_sha256']}
    script='(async()=>{\n'+source+'\nreturn await reimbursementCategoriesSave('+json.dumps({**c,'expected_plan':expected},ensure_ascii=False)+');\n})()'
    if digest(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script:raise ValueError('分类原保存源码或计划漂移')
    if read.get('tab_id')!=c['tab_id']or digest(read['script'])!=read.get('source_sha256'):raise ValueError('分类独立回读来源不符')
    code=Path(read['script']).read_text();prefix='(async()=>{\n'+source+'\nreturn await reimbursementCategoriesPrepare('
    if not code.startswith(prefix)or not code.endswith(');\n})()'):raise ValueError('不是固定分类HTTP回读')
    reader=json.loads(code[len(prefix):-len(');\n})()')])
    for key in ['case_id','serial','expected_userid','expected_amount','tab_id','expense_categories','funding','funding_policy','categories_contract','amount_contract','submission_contract','basic_contracts','used_contracts']:
        if reader.get(key)!=c[key]:raise ValueError('分类回读冻结来源漂移：'+key)
    before=plan['original'];after=observed['original']
    if outcome=='not_applied':
        result=saved.get('result',{})
        # 旧适配器在首个不同分类的初始化响应解析处退出。完整独立回读
        # 必须与保存前逐字段一致，不能凭错误码或金额相等释放原pending。
        unchanged=[]
        for item in c['expense_categories']:
            row=next((r for r in before['categories'] if r['group']==item['group'] and (not item.get('group_key') or r['group_key']==item['group_key'])),None)
            if row is None:raise ValueError('原分类来源不唯一')
            if row['subject']!=item['subject'] or row['subject_name']!=item['subject_name']:break
            unchanged.append({'group_key':row['group_key'],'no_change':True})
        requests=result.get('requests',[])
        legacy=(result.get('error')=='NATIVE_PROTOCOL_LOCAL_KEY' and
                'ReimbursementNativeProtocol.response(raw,[]).accepted'in source and
                "await execute({check_func:'',procs:win.beforeLoadProcs},context);"in source)
        scoped=(result.get('error')=='CATEGORIES_API_LOCAL_SCOPE' and result.get('failed_phase')=='initialize' and
                result.get('native_responses')==[] and isinstance(result.get('native_response'),str) and
                result['native_response'].startswith('passlocal(') and
                "phase='initialize';await execute({check_func:'',procs:win.beforeLoadProcs},context);"in source and
                "throw Error('CATEGORIES_API_LOCAL_SCOPE');keys.push(key);"in source)
        if (before!=after or observed['changes']!=plan['changes'] or result.get('ok')is not False
                or result.get('state')!='unknown_save_readback_required' or not (legacy or scoped)
                or result.get('save_dispatched')is not True or result.get('system_write')is not True
                or result.get('completed')!=unchanged or len(unchanged)>=len(c['expense_categories'])
                or len(requests)!=2 or [r.get('endpoint')for r in requests]!=['loadDefinition.action','commonUpdate_responseButtonEvent.action']
                or any(r.get('status')!='received' for r in requests)):
            raise ValueError('分类未变或初始化已结束的证明不完整，保留原pending')
        return {'stage':'categories','outcome':'not_applied','independent_http_readback':True,
                'original_unchanged':True,'completed_initialization_response_unparsed':True,'financial_amount_changed':False}
    recovered_selection=False
    if saved.get('result',{}).get('ok')is not True:
        result=saved.get('result',{});requests=result.get('requests',[])
        match=re.fullmatch(r'passlocal\(61-xtype:([^;:]+)\)',str(result.get('native_response','')))
        parts=match[1].split('&') if match else []
        target=plan['changes'].get(parts[0],{}) if len(parts)==4 else {}
        origin=next((r for r in before['categories'] if len(parts)==4 and r['group_key']==parts[0]),{})
        if (result.get('failed_phase')!='select' or result.get('error')!='CATEGORIES_API_LOCAL_SCOPE' or
                result.get('state')!='unknown_save_readback_required' or result.get('save_dispatched')is not True or result.get('system_write')is not True or
                len(parts)!=4 or parts[1].strip()!=target.get('subject') or parts[2]!=origin.get('project') or parts[3] not in ('',origin.get('budget')) or
                not result.get('native_responses') or any(r.get('phase')!='initialize' or r.get('status')!='pass' for r in result['native_responses']) or
                any(r.get('status')!='received'for r in requests) or
                [r.get('endpoint')for r in requests]!=['loadDefinition.action','commonUpdate_responseButtonEvent.action','commonQuery_doQuery.action','commonUpdate_responseButtonEvent.action'] or
                "phase='select';await execute("not in source):
            raise ValueError('分类saved须原任务成功，或已结束的原生选择成功响应与完整持久回读')
        recovered_selection=True
    for key in ['bill','basic','invoices']:
        if before[key]!=after[key]:raise ValueError('分类改变其他单据、人员或票据字段：'+key)
    if len(before['categories'])!=len(after['categories']):raise ValueError('分类分组集合改变')
    for row in before['categories']:
        hits=[r for r in after['categories']if r['group_key']==row['group_key']]
        if len(hits)!=1:raise ValueError('分类缺少唯一原分组')
        target={**row,**plan['changes'].get(row['group_key'],{})}
        if {k:v for k,v in hits[0].items()if k!='parameters'}!={k:v for k,v in target.items()if k!='parameters'}:raise ValueError('分类科目、名称、经费或未改分组不符')
        parts=hits[0]['parameters'].split('&')
        if len(parts)!=4 or parts[0]!=target['group_key']or parts[1].strip()!=target['subject']or parts[2:]!=[target['project'],target['budget']]:raise ValueError('分类原生参数归属不符')
    return {'stage':'categories','outcome':outcome,'independent_http_readback':True,'groups':len(plan['changes']),'financial_amount_changed':False,
            'completed_native_selection_readback':recovered_selection}
