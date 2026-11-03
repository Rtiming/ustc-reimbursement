"""劳务原生HTTP owner；只从已核材料事项读取学生、期间和金额。"""
from __future__ import annotations
import argparse,json,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from finance_native_create import ref,checked,checked_text,frozen,read,sha
from finance_native_amount import script,browser
ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-student-remuneration-v1'

def command(service,request,folder):
    from finance_remuneration_entry import activity_context
    from finance_http import source
    p=request['parameters'];bundle,paths=service.resolve(request['case_id']);case=read(paths['case']);form=read(paths['form']);action=p['action']
    actions={'remuneration-inspect':'inspect','remuneration-lookup-person':'lookup','remuneration-add-person':'add-person','remuneration-create-claim':'create-claim','remuneration-save':'save','remuneration-readback':'readback','remuneration-recover':'recover'}
    if action not in actions or request['execute']!=(actions[action]in {'add-person','create-claim','save'})or set(p)-{'action','tab_id','expected_userid','student_id','job_id','artifact_id'}:raise ValueError('未知劳务原生动作或execute不符')
    who=p.get('expected_userid');tab=p.get('tab_id');student=p.get('student_id')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',str(who))or not re.fullmatch(r'\d+',str(tab))or form.get('expected_userid')not in {None,'',who}:raise ValueError('须本案明确本人及标签')
    if actions[action]in {'lookup','add-person'}and not student:raise ValueError('查询/添加须材料中学号')
    if request['execute']and form.get('review',{}).get('workflow_started'):raise ValueError('已有薪酬审批流程，不重复改动原单')
    c={'schema':SCHEMA,'case_id':request['case_id'],'serial':form.get('serial')or'','expected_userid':who,'tab_id':tab,'action':actions[action],'student_id':student,'bundle':str(bundle),'execute':request['execute'],'request_id':request['request_id'],
       'case':ref(paths['case']),'form':ref(paths['form']),'activity':activity_context(case,student),'remuneration_contract':read(ROOT/'data/finance_remuneration_contract.json')}
    if actions[action]=='recover':
        old=service.job_folder(request['case_id'],p.get('job_id'));prior=read(old/'native-remuneration-input.json')
        if any(prior.get(k)!=c[k]for k in ['case_id','expected_userid','tab_id','case','form']):raise ValueError('原薪酬恢复身份不同')
        return[service.python,str(ROOT/'tools/finance_native_remuneration.py'),'--input',str(old/'native-remuneration-input.json'),'--output',str(folder/'output.json'),'--recover']
    if request['execute']:
        receipt,body,_=service._typed_case_artifact(request['case_id'],p.get('job_id'),p.get('artifact_id'),'access',['remuneration-lookup-person']if c['action']=='add-person'else['remuneration-inspect'])
        prior=read(service.job_folder(request['case_id'],p['job_id'])/'native-remuneration-input.json')
        if any(prior.get(k)!=c[k]for k in ['case_id','serial','expected_userid','tab_id','case','form','activity','student_id']):raise ValueError('劳务写入须本项同人完整原生来源')
        prepare(c,body);c['source']=ref(receipt)
        if c['action']=='create-claim'and c['serial']:raise ValueError('已有单号，不重复建薪酬单')
    elif c['action']=='readback':
        receipt,_,_=service._typed_case_artifact(request['case_id'],p.get('job_id'),p.get('artifact_id'),'access',['remuneration-add-person','remuneration-create-claim','remuneration-save'])
        old=service.job_folder(request['case_id'],p['job_id']);c['readback_proof']={'input':ref(old/'native-remuneration-input.json'),'prepare':ref(old/'native-remuneration-prepare.json'),'receipt':ref(receipt)}
        original=checked(c['readback_proof']['input']);c['student_id']=original.get('student_id');c['activity']=original['activity'];c['serial']=saved_serial(c['readback_proof'])
    # An independent read of a pending write must use that write's frozen adapter,
    # even when an unrelated HTTP module changed after dispatch.
    adapter=folder/'native-remuneration-adapter.js';code=checked_text(original['adapter']) if c['action']=='readback' else source('remuneration')
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原劳务读取源码漂移')
    else:adapter.write_text(code,encoding='utf-8',newline='\n');adapter.chmod(0o600)
    c['adapter']=ref(adapter);path=folder/'native-remuneration-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_native_remuneration.py'),'--input',str(path),'--output',str(folder/'output.json')]

def prepare(c,envelope):
    p=envelope.get('result',{})
    if(envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or p.get('ok')is not True or p.get('complete')is not True or p.get('action')!='remuneration-inspect'or p.get('business_transport')!='native_http'or p.get('system_write')is not False or any(p.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or p.get('definition_sha256')!=c['remuneration_contract']['definition_sha256']or not isinstance(p.get('rows'),list)):raise ValueError('须同案完整原生薪酬读取')
    return p

# This pre-fix adapter stops on the first native check's explicit JUDGE and
# cannot reach either the confirmed preparation or the create procedure.
LEGACY_CREATE_CHECK_ADAPTER='54b0cbede758c9afcca2d1104375e2cc4078d6115479e6c7998f0b8644d5a110'
def rejected_create_check(c,body):
    if(c.get('action')!='create-claim'or c.get('serial')or body.get('ok')is not False or body.get('state')!='unknown_original_remuneration_recovery_required'):return False
    if c.get('adapter',{}).get('sha256')==LEGACY_CREATE_CHECK_ADAPTER:return body.get('error')=='NATIVE_PROTOCOL_JUDGE'and body.get('steps')==[]
    if c.get('adapter',{}).get('sha256')=='16e663b1830105e71dd905fe1b2fcb37a24e46d29e485ce17d57cb3675207f37':return body.get('error')=='NATIVE_PROTOCOL_LOCAL_SYNTAX'and body.get('steps')==['native_people_total_confirmed']and body.get('event_diagnostic',{}).get('button')=='前往报销'
    return body.get('creation_procedure_dispatched')is False


LEGACY_CREATED_RESPONSE_ADAPTER='c58f7c864b0839828205a0834015cfd9ef4a09c73f8902609cd669072bcbbbe5'
def created_response_serial(c,b):
    """Read the completed original create response; never issue creation again."""
    d=b.get('event_diagnostic',{})
    if(c.get('adapter',{}).get('sha256')!=LEGACY_CREATED_RESPONSE_ADAPTER or c.get('action')!='create-claim' or c.get('serial') or
       b.get('ok')is not False or b.get('state')!='unknown_original_remuneration_recovery_required' or
       b.get('error')!='REMUNERATION_API_SAVE_CONTEXT_CHANGED' or b.get('creation_procedure_dispatched')is not True or
       b.get('save_dispatched')is not True or b.get('system_write')is not True or b.get('steps')!=['native_people_total_confirmed','前往报销'] or
       d.get('button')!='前往报销cj' or d.get('keys')!=['0-g0Serialno','0-need_turor','0-zip_seq']):return None
    m=re.fullmatch(r'passlocal\(0-g0Serialno:(\d+);0-need_turor:N;0-zip_seq:\)',str(d.get('response','')))
    return m.group(1) if m else None

def saved_serial(proof):
    c=checked(proof['input']);p=prepare(c,checked(proof['prepare']));saved=checked(proof['receipt']);b=saved.get('result',{})
    recovered_serial=created_response_serial(c,b)
    if(saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or (b.get('ok')is not True and not recovered_serial and not rejected_create_check(c,b) and not(c['action']=='add-person' and b.get('ok')is False and (b.get('state')=='unknown_original_remuneration_recovery_required' or b.get('state')=='rejected_before_save' and b.get('save_dispatched')is False and b.get('system_write')is False))) or b.get('action')!='remuneration-save'or b.get('operation')!=c['action']or any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script(c,'reimbursementRemunerationSave',{'pending_registered':True,'expected_plan':p})):raise ValueError('原薪酬保存尚未完整确认，只恢复原marker')
    if rejected_create_check(c,b):return ''
    serial=(recovered_serial or b.get('new_serial'))if c['action']=='create-claim'else c['serial']
    if c['action']=='create-claim'and not re.fullmatch(r'\d+',str(serial)):raise ValueError('薪酬新单号未核')
    return serial

def run(input_path,output,recover=False):
    from finance_remuneration_entry import activity_context
    from operator_support import bundle
    from reimburse import begin_write
    c=read(input_path);case=checked(c['case']);checked(c['form']);code=checked_text(c['adapter'])
    if c.get('schema')!=SCHEMA or c['activity']!=activity_context(case,c.get('student_id'))or c['remuneration_contract']!=read(ROOT/'data/finance_remuneration_contract.json'):raise ValueError('劳务材料或固定合同漂移')
    _,paths=bundle(c['bundle']);output=Path(output)
    if recover:
        from edge_runtime import read_marker,browser_lock
        from finance_backend import assert_original_transport
        from assistant_api import atomic_json
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if v.get('schema')!=SCHEMA or v.get('input')!=ref(input_path):raise ValueError('只恢复原薪酬意图')
        p=prepare(c,checked(v['prepare']));old=read(v['save_receipt'])
        if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=script(c,'reimbursementRemunerationSave',{'pending_registered':True,'expected_plan':p}):raise ValueError('原薪酬源码漂移')
        if old.get('phase')=='script_completed':saved=old
        else:
            if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原薪酬未派发或缺marker')
            assert_original_transport(old)
            with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
            if not isinstance(body,dict):raise ValueError('原薪酬marker尚未返回')
            saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now(timezone.utc).isoformat(),'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
        target=output.parent/'original-remuneration-result.json';frozen(target,saved);proof={'input':ref(input_path),'prepare':v['prepare'],'receipt':ref(target)}
        reader={**c,'action':'readback','execute':False,'serial':saved_serial(proof),'readback_proof':proof};result=browser(reader,'reimbursementRemunerationInspect',output);prepare(reader,result);result['original_save_recovery']=ref(target);atomic_json(output,result);return 0
    if c['action']in {'inspect','lookup','readback'}:
        if c.get('readback_proof')and c['serial']!=saved_serial(c['readback_proof']):raise ValueError('原薪酬回读单号不符')
        prepare(c,browser(c,'reimbursementRemunerationInspect',output));return 0
    if not c['execute']or c['action']not in {'add-person','create-claim','save'}:raise ValueError('未知薪酬写入')
    if(paths['state'].parent/'pending_write.json').exists():raise ValueError('原财务写入未核，不重发薪酬')
    original=prepare(c,checked(c['source']));path=output.parent/'native-remuneration-prepare.json';p=prepare(c,browser(c,'reimbursementRemunerationInspect',path))
    for key in ['original','rows','person','default_bank','bill','person_rules']:
        if original.get(key)!=p.get(key):raise ValueError('劳务原计划来源变化：'+key)
    checked(c['form']);checked(c['case']);begin_write(paths,read(paths['form']),'remuneration:http-'+c['action'],output.parent,{'schema':SCHEMA,'input':ref(input_path),'prepare':ref(path),'save_receipt':str(output)})
    saved=browser(c,'reimbursementRemunerationSave',output,{'pending_registered':True,'expected_plan':p});return 0 if saved.get('result',{}).get('ok')is True else 2

def person_bank_matches(row,card):
    actual=str(row.get('t.CARDNO',''));expected=card.get('card_no','')
    if actual==expected:return True
    # Native student lookup masks after six digits; saved F280 masks after
    # four. Accept only this observed pair, with the same branch and both
    # independent grid card columns agreeing. The frozen lookup also binds
    # the native bank key / private bank token used by the original save.
    branch=card.get('branchcode','')
    return(bool(re.fullmatch(r'\d{4}\*{4}\d{4}',actual))and bool(re.fullmatch(r'\d{6}\*{4}\d{4}',expected))
           and actual[:4]==expected[:4]and actual[-4:]==expected[-4:]and row.get('t.cardno_show')==actual
           and bool(re.fullmatch(r'\d{12}/.+',branch))and row.get('t.hq_branchname')==branch.split('/',1)[1])

def verify_ack(previous,envelope,outcome,*,allow_create=False,frozen_sources=None):
    from decimal import Decimal
    v=previous['verification'];c=checked(v['input']);p=prepare(c,checked(v['prepare']));saved=read(v['save_receipt'])
    for key in ['case','form']:
        if frozen_sources:
            checked(frozen_sources[key])
            if frozen_sources[key]['sha256']!=c[key]['sha256']:raise ValueError('建单原始本地快照不符')
        else:checked(c[key])
    if c['action']=='create-claim'and not allow_create and not(outcome=='not_applied'and rejected_create_check(c,saved.get('result',{}))):raise ValueError('薪酬建单必须通过bind-serial绑定，不能用普通ACK替代')
    receipt=envelope.get('original_save_recovery')or ref(v['save_receipt'])
    if envelope.get('original_save_recovery'):
        recovered=checked(receipt);binding=recovered.get('original_marker_recovery',{})
        if saved.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('薪酬恢复未引用原marker')
        if any(recovered.get(k)!=saved.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('薪酬恢复身份漂移')
        saved=recovered
    proof={'input':v['input'],'prepare':v['prepare'],'receipt':receipt};serial=saved_serial(proof)
    if previous.get('operation')!='remuneration:http-'+c['action']or previous.get('case_id')!=c['case_id']or outcome not in {'saved','not_applied'}:raise ValueError('薪酬须同案完整saved或确认未应用')
    reader={**c,'serial':serial};r=prepare(reader,envelope)
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('薪酬回读须晚于原保存')
    text=Path(envelope['script']).read_text();prefix='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementRemunerationInspect('
    if sha(envelope['script'])!=envelope.get('source_sha256')or not text.startswith(prefix)or not text.endswith(');})()'):raise ValueError('不是原固定薪酬回读源码')
    actual=json.loads(text[len(prefix):-len(');})()')])
    if actual.get('readback_proof')!=proof:raise ValueError('薪酬回读未引用原保存意图')
    for k in ['case_id','expected_userid','tab_id','case','form','activity','remuneration_contract','student_id']:
        if actual.get(k)!=c[k]:raise ValueError('薪酬冻结来源变化：'+k)
    if outcome=='not_applied':
        body=saved.get('result',{})
        before_person_save=(body.get('state')=='rejected_before_save' and body.get('save_dispatched')is False and body.get('system_write')is False and body.get('steps')==[]
                            or body.get('error') in {'REMUNERATION_API_EVENT_CONTEXT_CHANGED','REMUNERATION_API_SAVE_CONTEXT_CHANGED'} and body.get('steps')==[]
                            or body.get('error')=='REMUNERATION_API_PERSON_VISIBLE_GROUPS_MISSING' and body.get('steps')==['native_setup'])
        if(rejected_create_check(c,body)and r['rows']==p['rows']and r['original']==p['original']and r.get('bill')==p.get('bill')and all(row.get('t.serial_no')==''for row in r['rows'])):
            return {'schema':SCHEMA,'operation':c['action'],'serial':'','people':len(r['rows']),'independent_http_readback':True,'outcome':'not_applied','original_create_not_dispatched':True}
        if(c['action']!='add-person' or c.get('serial') or body.get('ok')is not False
           or not before_person_save
           or r['rows']!=p['rows'] or r['original']!=p['original'] or r.get('bill')!=p.get('bill')):
            raise ValueError('不能确认原薪酬添加未应用；保留原请求')
        return {'schema':SCHEMA,'operation':c['action'],'serial':serial,'people':len(r['rows']),
                'independent_http_readback':True,'outcome':'not_applied','original_event_failed_before_person_save':True}
    old={row['t.unino']:row for row in p['rows']};rows={row['t.unino']:row for row in r['rows']}
    if len(rows)!=len(r['rows']):raise ValueError('薪酬重复人员')
    expected=set(old)|({c['student_id']}if c['action']=='add-person'else set())
    if set(rows)!=expected:raise ValueError('薪酬完整人员集合不符')
    for key,row in old.items():
        compare={k:v for k,v in row.items()if c['action']!='create-claim'or k not in {'t.serial_no','t.pno','t.btns'}}
        if any(rows[key][k]!=v for k,v in compare.items()):raise ValueError('薪酬其他人员/金额/银行/税务字段变化')
    if c['action']=='add-person':
        row=rows[c['student_id']]
        if row['t.name']!=c['activity']['person_name']or Decimal(row['t.AMT'].replace(',',''))!=Decimal(c['activity']['amount'])or row['t.premark']!=c['activity']['reason']:raise ValueError('劳务新增人员/金额/事由未持久保存')
        if not person_bank_matches(row,p['default_bank'])or str(row['t.persontype']).strip()!='学生':raise ValueError('劳务银行卡或学生身份不符')
    for k,vv in {'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':c['activity']['period_start'],'dd.ff_enddate':c['activity']['period_end'],'dd.hastax':'T'}.items():
        if r['original'][k].strip()!=vv:raise ValueError('薪酬性质、期间、税前方式不符：'+k)
    allowed={'dd.state_ybx','dd.state','dd.abst','dd.ff_startdate','dd.ff_enddate','dd.hastax','dd.ffcycle','dd.pno','dd.serial_no','dd.resultinfo','dd.reminder'}
    if set(r['original'])!=set(p['original'])or any(r['original'][k]!=vv for k,vv in p['original'].items()if k not in allowed):raise ValueError('薪酬未改基本信息变化')
    return{'schema':SCHEMA,'operation':c['action'],'serial':serial,'people':len(rows),'independent_http_readback':True,'outcome':outcome,'amount':c['activity']['claim_amount'],'source_input':v['input'],'readback':ref(envelope['output'])if envelope.get('output')else None}

def bind_created(paths,receipt,serial):
    from operator_support import write_json,stamp
    pending_path=paths['state'].parent/'pending_write.json';pending=read(pending_path);v=pending.get('verification',{});c=checked(v['input']);envelope=read(receipt)
    if v.get('schema')!=SCHEMA or c['action']!='create-claim'or pending.get('operation')!='remuneration:http-create-claim':raise ValueError('不是原薪酬HTTP建单意图')
    verified=verify_ack(pending,envelope,'saved',allow_create=True)
    if serial!=verified['serial']:raise ValueError('登记单号与原生成及持久回读不符')
    values={k:read(paths[k])for k in ['case','form','invoices','state']}
    if any(value.get('case_id')!=c['case_id']or value.get('serial')not in [None,serial]for value in values.values()):raise ValueError('本地案件绑定身份变化')
    originals={}
    for key in ['case','form']:
        target=Path(pending['output_directory'])/('before-native-binding-'+key+'.json')
        if target.exists():
            if sha(target)!=c[key]['sha256']:raise ValueError('保留原绑定快照，不覆盖')
        else:target.write_bytes(Path(c[key]['path']).read_bytes());target.chmod(0o600)
        originals[key]=ref(target)
    ack=Path(pending['output_directory'])/'operator_readback_ack.json';pending.update(native_remuneration_readback=verified,outcome='saved',bound_serial=serial,readback_receipt=str(Path(receipt).resolve()),status='draft_bound_readback_recorded');write_json(ack,pending)
    for key,value in values.items():
        value['serial']=serial
        if key=='form':value.update(system_mode='remuneration',expected_userid=c['expected_userid'],amount=c['activity']['claim_amount'],expected_invoice_count=0,remuneration={'native_schema':SCHEMA,'document_id':c['activity']['document_id'],'creation_plan':{'plan':v['input']['path'],'sha256':v['input']['sha256'],'originals':originals},'creation_readback':ref(receipt),'native_acknowledgement':ref(ack)})
        if key=='invoices':value.update(expected_count=0,expected_amount='0.00',selected_invoices=[])
        if key=='state':value.update(status='native_remuneration_draft_created_readback',creation_readback=str(receipt),updated_at_utc=stamp())
        write_json(paths[key],value)
    pending_path.unlink();return{'ok':True,'serial':serial,'system_mode':'remuneration','amount':verified['amount'],'system_write':False,'submitted':False}

def verify_bound_case(form):
    data=form['remuneration'];p=data['creation_plan'];raw=Path(p['plan']).read_bytes()
    if sha(p['plan'])!=p['sha256']:raise ValueError('原生薪酬建单输入变化')
    c=json.loads(raw);pending=checked(data['native_acknowledgement']);envelope=checked(data['creation_readback'])
    verified=verify_ack(pending,envelope,'saved',allow_create=True,frozen_sources=p['originals'])
    if c['case_id']!=form['case_id']or c['expected_userid']!=form['expected_userid']or verified['serial']!=form['serial']or verified['amount']!=form['amount']or c['activity']['document_id']!=data['document_id']:raise ValueError('原生薪酬绑定身份、事项或金额变化')
    return verified

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--recover',action='store_true');a=p.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
