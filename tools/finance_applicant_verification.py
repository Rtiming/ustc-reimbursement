"""Open only the native human SMS gate on one authorized existing applicant bill."""
import json,re
from pathlib import Path
from finance_native_create import checked,ref,frozen,read

ROOT=Path(__file__).resolve().parents[1]

def command(service,request,folder):
    from finance_http import source
    from submission import validate_plan
    p=request['parameters']
    if request['execute']is not True or set(p)!={'action','tab_id','plan','authority'} or p['action']!='applicant-verification-open':raise ValueError('短信界面须明确同案计划、授权和标签')
    _,paths=service.resolve(request['case_id']);form=read(paths['form']);plan=p['plan'];validate_plan(plan,form)
    authority=p['authority'];path=Path(authority['path'])
    if not path.is_absolute() or path.suffix.lower()!='.json':raise ValueError('授权须材料根内的绝对JSON路径')
    service.material_path(str(path.resolve().parent));auth=checked(authority)
    entries=[e for e in auth.get('entries',[])if e.get('case_id')==request['case_id']]
    if(auth.get('schema')!='ustc-applicant-submission-authority-v1'or auth.get('approval_opinion')!='同意'or len(entries)!=1 or entries[0].get('serial')!=form['serial']or entries[0].get('current_amount')!=form['amount']or auth.get('applicant_agreement_authorized')is not True or auth.get('preserve_sms_verification')is not True or auth.get('create_new_claims')is not False or not auth.get('user_text')or auth.get('expected_userid')!=plan.get('expected_userid')or form.get('expected_userid')not in(None,plan.get('expected_userid'))or plan.get('approval_opinion')!='同意'or plan.get('applicant_agreement_authorized')is not True):raise ValueError('须明确本次原单本人同意和短信保留授权')
    if not re.fullmatch(r'\d+',str(p['tab_id'])):raise ValueError('须明确当前标签')
    c={'case_id':request['case_id'],'serial':form['serial'],'expected_amount':form['amount'],'summary':plan['summary'],'expected_userid':plan['expected_userid'],'authorized':True,'approval_opinion':'同意','contract':read(ROOT/'data/finance_applicant_contract.json'),'submission_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['submission']}
    frozen(folder/'verification-input.json',{'form':ref(paths['form']),'authority':authority,'tab_id':p['tab_id'],'config':c})
    code='(async()=>{'+source('business')+'\n'+(ROOT/'tools/finance_native_form.js').read_text()+'\n'+(ROOT/'tools/finance_applicant_verification.js').read_text()+'\nreturn await reimbursementApplicantVerification('+json.dumps(c,ensure_ascii=False)+');})()'
    target=folder/'verification-open.js'
    if target.exists():
        if target.read_text()!=code:raise ValueError('原验证界面任务源码变化，只读原任务')
    else:target.write_text(code,encoding='utf-8');target.chmod(0o600)
    return[service.python,str(ROOT/'tools/edge_runtime.py'),str(target),'--output',str(folder/'output.json'),'--tab-id',p['tab_id'],'--operation-kind','read','--quiet']
