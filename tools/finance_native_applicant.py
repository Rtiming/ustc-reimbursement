"""Original applicant approval: explicit authority, school SMS gate, durable intent and independent HTTP readback."""
import argparse,json,re
from datetime import datetime,timezone
from pathlib import Path
from finance_native_create import ref,checked,checked_text,frozen,read,sha,browser

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-applicant-v1'
ACTIONS={'applicant-inspect','applicant-agree','applicant-readback','applicant-recover','applicant-refresh','applicant-documents'}

def script(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or{})},ensure_ascii=False)+');})()'

def authority(service,plan,form):
    m=re.fullmatch(r'(.+)#sha256=([a-f0-9]{64})',plan.get('source',''))
    if not m:raise ValueError('申请人同意须逐单SHA授权来源')
    path=Path(m[1]);reference={'path':str(path),'sha256':m[2]}
    if not path.is_absolute()or path.suffix!='.json':raise ValueError('授权须材料根内JSON文件')
    service.material_path(str(path.resolve().parent));a=checked(reference)
    entries=[x for x in a.get('entries',[])if x.get('case_id')==form['case_id']]
    if(a.get('schema')!='ustc-applicant-submission-authority-v1'or not a.get('user_text')or a.get('expected_userid')!=plan['expected_userid']or a.get('approval_opinion')!='同意'or a.get('applicant_agreement_authorized')is not True or a.get('preserve_sms_verification')is not True or a.get('create_new_claims')is not False or len(entries)!=1 or entries[0].get('serial')!=form['serial']or entries[0].get('current_amount')!=form['amount']or plan.get('applicant_agreement_authorized')is not True or plan.get('approval_opinion')!='同意'):raise ValueError('本次同意授权与原单、金额或账号不同')
    return reference

def command(service,request,folder):
    from submission import validate_plan
    from finance_http import source
    p=request['parameters'];action=p['action'];bundle,paths=service.resolve(request['case_id']);form=read(paths['form']);plan=p['plan'];validate_plan(plan,form)
    if action not in ACTIONS or request['execute']!=(action in {'applicant-agree','applicant-refresh','applicant-documents'})or set(p)-{'action','plan','tab_id','job_id','artifact_id'}:raise ValueError('申请人动作及execute不符')
    if not re.fullmatch(r'\d+',str(p.get('tab_id'))):raise ValueError('申请人原单须当前tab')
    c={'schema':SCHEMA,'case_id':request['case_id'],'serial':form['serial'],'expected_amount':form['amount'],'expected_userid':plan['expected_userid'],'tab_id':p['tab_id'],'action':action,'plan':plan,'bundle':str(bundle),'request_id':request['request_id'],'form':ref(paths['form']),'case':ref(paths['case']),'invoices':ref(paths['invoices']),'authority':authority(service,plan,form),'applicant_contract':read(ROOT/'data/finance_applicant_contract.json'),'submission_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['submission']}
    if action in {'applicant-readback','applicant-recover','applicant-refresh','applicant-documents'}:
        old=service.job_folder(request['case_id'],p['job_id']);original=read(old/'native-applicant-input.json')
        same=['case_id','serial','expected_amount','expected_userid','tab_id','plan','authority']+([]if action=='applicant-documents'else['form','case','invoices'])
        if original['action']!='applicant-agree'or any(original[k]!=c[k]for k in same):raise ValueError('只回读原单原意图，不更换输入')
        if action=='applicant-recover':return[service.python,str(ROOT/'tools/finance_native_applicant.py'),'--input',str(old/'native-applicant-input.json'),'--output',str(folder/'output.json'),'--recover']
        c['readback_source']={'input':ref(old/'native-applicant-input.json'),'prepare':ref(old/'native-applicant-prepare.json'),'save':ref(old/'output.json')};c['readback']=True
        code=source('applicant')
    else:code=source('applicant')
    adapter=folder/'native-applicant-adapter.js'
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原申请人任务源码变化')
    else:adapter.write_text(code,encoding='utf-8');adapter.chmod(0o600)
    c['adapter']=ref(adapter);path=folder/'native-applicant-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_native_applicant.py'),'--input',str(path),'--output',str(folder/'output.json')]

def validate(c):
    from submission import validate_plan
    form=checked(c['form']);case=checked(c['case']);checked(c['invoices']);checked(c['authority']);checked_text(c['adapter']);validate_plan(c['plan'],form)
    current=read(ROOT/'data/finance_applicant_contract.json')
    if c['schema']!=SCHEMA or any(current.get(k)!=v for k,v in c['applicant_contract'].items()):raise ValueError('申请人原合同内容变化')
    if c['action']=='applicant-agree':
        from operator_support import unresolved_business_corrections
        if unresolved_business_corrections(case):raise ValueError('本案仍有未完成业务纠正')

def inspect(c,envelope):
    r=envelope.get('result',{})
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'action':'applicant-http-inspect','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'business_transport':'native_http','system_write':False,'save_dispatched':False}.items()):raise ValueError('须同号完整申请人HTTP回读')
    return r

def recover_saved(c,path):
    from edge_runtime import browser_lock,read_marker
    from finance_backend import assert_original_transport
    saved=read(path)
    if saved.get('phase')=='script_completed':return saved
    assert_original_transport(saved)
    if saved.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(saved.get('result_marker',''))):raise ValueError('缺少原派发marker，不能重发')
    with browser_lock():body=read_marker(saved['result_marker'],c['tab_id'],timeout=45)
    if not isinstance(body,dict):raise ValueError('原结果仍未知，保留pending')
    return{**saved,'phase':'script_completed','result':body,'executed_at_utc':datetime.now(timezone.utc).isoformat(),'marker_recovered_without_replay':True}

def run(path,output,recover=False):
    from operator_support import bundle
    from reimburse import begin_write
    from assistant_api import atomic_json
    c=read(path);validate(c);_,paths=bundle(c['bundle']);output=Path(output)
    if c['action']=='applicant-documents':
        source=checked(c['readback_source']['save'])
        if source.get('phase')!='script_completed'or source.get('result',{}).get('approval_dispatched')is not True:raise ValueError('仅补齐已派发原同意后的文件，不再次审批')
        observed=browser(c,'reimbursementApplicantDocuments',output,write=True)
        return 0 if observed.get('result',{}).get('ok')is True else 2
    if c['action']=='applicant-refresh':
        pending=read(paths['state'].parent/'pending_write.json')
        if pending.get('verification',{}).get('input')!=c['readback_source']['input']:raise ValueError('缓存恢复仅绑定原pending')
        observed=browser(c,'reimbursementApplicantRefresh',output,write=True)
        return 0 if observed.get('result',{}).get('ok')is True else 2
    if recover:
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if v.get('schema')!=SCHEMA or v.get('input')!=ref(path):raise ValueError('恢复只允许本案原pending')
        saved=recover_saved(c,v['save']);target=output.parent/'original-applicant-save.json';frozen(target,saved)
        from finance_http import source as adapter_source
        reader_adapter=output.parent/'native-applicant-read-adapter.js';reader_adapter.write_text(adapter_source('applicant'),encoding='utf-8')
        reader={**c,'adapter':ref(reader_adapter),'action':'applicant-readback','readback':True,'readback_source':{'input':ref(path),'prepare':v['prepare'],'save':ref(target)}}
        observed=browser(reader,'reimbursementApplicantInspect',output);inspect(reader,observed);return 0
    if c['action']!='applicant-agree':inspect(c,browser(c,'reimbursementApplicantInspect',output));return 0
    if(paths['state'].parent/'pending_write.json').exists():raise ValueError('旧写入未回读，不重复同意')
    target=output.parent/'native-applicant-prepare.json';prepared=inspect(c,browser(c,'reimbursementApplicantInspect',target));validate(c)
    if prepared.get('completed')or prepared.get('verification_required'):
        atomic_json(output,read(target));return 0
    begin_write(paths,read(paths['form']),'submission:http-applicant-agree',output.parent,{'schema':SCHEMA,'input':ref(path),'prepare':ref(target),'save':str(output)})
    saved=browser(c,'reimbursementApplicantAgree',output,{'pending_registered':True,'prepared':prepared},write=True)
    return 0 if saved.get('result',{}).get('ok')is True else 2

def verify_ack(previous,envelope,outcome):
    v=previous['verification'];c=checked(v['input']);validate(c);prepared=inspect(c,checked(v['prepare']));observed=inspect(c,envelope)
    text=checked_text({'path':envelope['script'],'sha256':envelope['source_sha256']});delimiter=';return await reimbursementApplicantInspect(';at=text.rfind(delimiter)
    if at<0 or not text.endswith(');})()'):raise ValueError('回读不是固定适配器')
    reader=json.loads(text[at+len(delimiter):-len(');})()')]);proof=reader.get('readback_source',{});validate(reader)
    from finance_http import source as adapter_source
    if text!=script(reader,'reimbursementApplicantInspect')or checked_text(reader['adapter'])not in {checked_text(c['adapter']),adapter_source('applicant')}or reader.get('readback')is not True:raise ValueError('只接受原读取器或当前受控读取器，不能执行新同意')
    if proof.get('input')!=v['input']or proof.get('prepare')!=v['prepare']or any(reader.get(k)!=c.get(k)for k in ['case_id','serial','expected_userid','expected_amount','tab_id','form','case','invoices','authority','plan']):raise ValueError('回读未绑定原同意意图')
    saved=checked(proof['save']);b=saved.get('result',{})
    if saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or checked_text({'path':saved['script'],'sha256':saved['source_sha256']})!=script(c,'reimbursementApplicantAgree',{'pending_registered':True,'prepared':prepared})or any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid']):raise ValueError('原同意尚未完整返回，只恢复原marker')
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('回读须晚于原同意完成')
    old=prepared['original']['bill'];new=observed['original']['bill']
    for key in ['t.serial_no','t.REQ_INFO','t.total_amt','t.abst','t.basetable_key','t.uni_key','t.wf_name']:
        if old[key]!=new[key]:raise ValueError('原单身份、金额或流程实例变化：'+key)
    if outcome=='saved':
        if b.get('approval_dispatched')is not True or observed['completed']is not True:raise ValueError('须原同意派发且本人节点已独立核实完成')
    elif outcome=='not_applied':
        if b.get('approval_dispatched')is not False or observed['completed']or new['t.node_id']!='508':raise ValueError('未应用须明确未派发及原申请人节点保留')
    else:raise ValueError('申请人仅登记saved或not_applied')
    return{'schema':SCHEMA,'outcome':outcome,'serial':c['serial'],'node_id':observed['original'].get('workflow_node_id',new['t.node_id']),'main_list_node_stale':observed['original'].get('main_list_node_stale',False),'independent_http_readback':True,'applicant_completed':observed['completed'],'leadership_approval_completed':False,'payment_completed':False,'source_input':v['input']}

def verify_legacy_sms_unapplied(previous,envelope,outcome):
    """Reconcile a completed legacy SMS stop; never clear an unknown write."""
    if previous.get('operation')!='submission:applicant-agree'or outcome!='not_applied':raise ValueError('仅核对旧本人短信停点未应用')
    saved=read(previous['verification']['save_receipt']);plan=read(previous['verification']['submission_plan']);b=saved.get('result',{})
    c=read(Path(envelope['script']).parent/'native-applicant-input.json');validate(c);r=inspect(c,envelope)
    if checked_text({'path':envelope['script'],'sha256':envelope['source_sha256']})!=script(c,'reimbursementApplicantInspect'):raise ValueError('旧停点须固定HTTP独立回读')
    if(saved.get('phase')!='script_completed'or sha(saved['script'])!=saved.get('source_sha256')or b.get('ok')is not True or b.get('action')!='applicant_agree_requested'or b.get('verification_required')is not True or b.get('native_verification_enabled')is not True or b.get('applicant_userid')!=c['expected_userid']or b.get('serial')!=c['serial']or plan.get('serial')!=c['serial']or plan.get('case_id')!=c['case_id']or plan.get('amount')!=c['expected_amount']or plan.get('expected_userid')!=c['expected_userid']or previous['case_id']!=c['case_id']or previous['serial']!=c['serial']):raise ValueError('旧请求未明确停在本单短信验证，不清pending')
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc'])or r['completed']or r['original']['bill']['t.node_id']!='508'or 'applicant'not in r['original']:raise ValueError('未读到本案同金额仍待本人处理')
    return{'schema':'ustc-legacy-applicant-sms-reconciliation-v1','original_save':ref(previous['verification']['save_receipt']),'independent_http_readback':True,'outcome':'not_applied','financial_action_replayed':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--recover',action='store_true');a=p.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
