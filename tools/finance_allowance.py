"""Source-bound counts-mode allowance creation, original-marker recovery, and binding."""
from __future__ import annotations
import argparse,hashlib,json,os,re,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'tools')not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
from finance_native_create import read,ref,checked,checked_text,frozen,browser
SCHEMA='ustc-native-allowance-create-v1'

def validate_standalone_scope(plan):
    # 有票差旅的补助不能因接口暂缺而被改成另一张单；通用做单授权不授予拆单。
    authority=plan.get('separate_claim_authorization')
    if(not isinstance(authority,dict)or authority.get('explicit_separate_claim')is not True
       or not isinstance(authority.get('user_text'),str)or not authority['user_text'].strip()
       or not isinstance(authority.get('source'),str)or not authority['source'].strip()):
        raise ValueError('有票国内差旅及补助须同单；单独补助单须有本案明确安排，不能从通用做单授权推断')

def validate_sources(c):
    from validate_config import money
    plan=checked(c['source']);audit=checked(plan['source_audit']);form=c['form_before']
    if plan.get('schema')!='ustc-travel-allowance-v1'or plan.get('case_id')!=c['case_id']or not plan.get('authorization')or not 1<=len(plan.get('people',[]))<=20 or not 1<=len(plan.get('entries',[]))<=200:raise ValueError('补助须本案已核人员、日期及授权清单')
    people={p['id']:p['name']for p in plan['people']}
    if len(people)!=len(plan['people'])or any(not re.fullmatch(r'[A-Za-z0-9]{2,40}',k)or not v for k,v in people.items()):raise ValueError('补助人员不得重复或无身份')
    totals={k:money('0')for k in people};seen=set()
    for e in plan['entries']:
        day=datetime.strptime(e['date'],'%Y-%m-%d').date();key=(e['person_id'],day)
        if key in seen or people.get(e['person_id'])!=e['name']or money(e['amount'])<=0 or not e.get('departure_city')or not e.get('arrival_city'):raise ValueError('补助人员日期重复或来源不全')
        seen.add(key);totals[e['person_id']]+=money(e['amount'])
        hits=[x for x in audit.get('expenses',[])if x.get('费用编号')==e['source_expense_id']and x.get('源SHA')==e['source_sha256']and x.get('人员')==e['name']]
        if len(hits)!=1:raise ValueError('补助行程须逐人关联已核原始费用及SHA')
    if money(plan['amount'])!=money(c['expected_amount'])or sum(totals.values(),money('0'))!=money(plan['amount'])or form.get('expected_invoice_count')!=0 or form.get('serial'):raise ValueError('补助金额或零发票范围不闭合')
    if len(form['payments'])!=len(people)or any(p.get('mode')!='bank_transfer'or people.get(p.get('person_id'))!=p.get('name')or money(p['amount'])!=totals[p['person_id']]for p in form['payments'])or len({p['person_id']for p in form['payments']})!=len(people):raise ValueError('补助必须逐名本人收款且与日期金额一致')
    if sum((money(x['amount'])for x in form['funding']),money('0'))!=money(plan['amount']):raise ValueError('补助经费未闭合')
    prepared=checked(c['prepared_ref'])
    if prepared.get('phase')!='script_completed'or prepared.get('tab_id')!=c['tab_id']or prepared.get('result')!=c['prepared']or not c['prepared'].get('complete')or c['prepared'].get('system_write')is not False or c['prepared'].get('source_amount')!=plan['amount']:raise ValueError('补助原生标准准备证据不完整')
    grouped=[e for g in c['prepared']['groups']for e in g['entries']]
    if sorted(grouped,key=lambda x:(x['person_id'],x['date']))!=sorted(plan['entries'],key=lambda x:(x['person_id'],x['date'])):raise ValueError('补助原生行程分组与逐人源清单不同')
    for g in c['prepared']['groups']:
        values=g['values'];first=g['entries'][0]
        if any(e['date']!=first['date']or e['departure_city']!=first['departure_city']or e['arrival_city']!=first['arrival_city']for e in g['entries']):raise ValueError('补助同组须真实同日同行程')
        if values['slry']!=str(len(g['entries']))or values['sdate']!=first['date']or values['edate']!=first['date']or values['ylry']!='0'or values['elry']!='0'or values['cllx']!='1'or money(values['feeamount'])!=sum((money(e['amount'])for e in g['entries']),money('0'))or money(values['hsbt'])+money(values['jtbt'])!=money(values['feeamount']):raise ValueError('补助分组人数、日期或原生金额不闭合')
    return plan

def command(service,request,folder):
    from finance_http import source
    cid=request['case_id'];p=request['parameters'];action=p['action'];_,paths=service.resolve(cid)
    if set(p)!={'action','tab_id','job_id'}or not re.fullmatch(r'\d+',p['tab_id']):raise ValueError('补助只接受同案原任务和原标签')
    origin=service.job_folder(cid,p['job_id']);prior=read(origin/'request.json')
    if service.result(cid,p['job_id'])['status']in {'queued','running'}:raise ValueError('先等待原补助任务停止')
    if action in {'allowance-readback','allowance-recover'}:
        if request['execute']or prior.get('operation')!='invoice'or prior['parameters'].get('action')!='allowance-create':raise ValueError('补助恢复只读取本案原创建，不再保存')
        c=read(origin/'allowance-input.json')
        if c['tab_id']!=p['tab_id']:raise ValueError('补助原标签不同')
        reader=folder/'allowance-reader-adapter.js';reader.write_text(source('travel')+'\n'+(ROOT/'tools/finance_allowance_api.js').read_text());reader.chmod(0o600)
        frozen(folder/'allowance-recovery-source.json',{'input':ref(origin/'allowance-input.json'),'reader_adapter':ref(reader),'write_replayed':False})
        return[service.python,str(ROOT/'tools/finance_allowance.py'),'--input',str(origin/'allowance-input.json'),'--output',str(folder/'output.json'),'--recover','--reader-adapter',str(reader)]
    if action!='allowance-create'or not request['execute']or prior.get('operation')!='invoice'or prior['parameters'].get('action')!='allowance-prepare':raise ValueError('补助创建须原生准备及明确执行')
    c0=read(origin/'supplement-input.json');form=read(paths['form']);manifest=read(paths['invoices'])
    validate_standalone_scope(checked(c0['allowance_source_ref']))
    if c0['tab_id']!=p['tab_id']or form.get('serial')or manifest.get('selected_invoices')or manifest.get('draft_creation_requested')or (paths['state'].parent/'pending_write.json').exists():raise ValueError('补助须未建单且无待核写入，不混入发票')
    who=c0['expected_userid']
    if os.environ.get('REIMBURSEMENT_EXPECTED_USERID')not in {None,who}:raise ValueError('当前补助账号不同')
    adapter=folder/'allowance-adapter.js';adapter.write_text(source('travel')+'\n'+(ROOT/'tools/finance_allowance_api.js').read_text());adapter.chmod(0o600)
    c={'schema':SCHEMA,'case_id':cid,'tab_id':p['tab_id'],'bundle':str(paths['form'].parent/'operator.json'),'execute':True,'expected_userid':who,'expected_amount':form['amount'],
       'source':c0['allowance_source_ref'],'prepared_ref':ref(origin/'output.json'),'prepared':read(origin/'output.json')['result'],
       'case':ref(paths['case']),'form':ref(paths['form']),'manifest':ref(paths['invoices']),'form_before':form,'manifest_before':manifest,
       'contracts':c0['contracts'],'basic_contracts':c0['basic_contracts'],'adapter':ref(adapter),'token':hashlib.sha256((cid+':'+request['request_id']).encode()).hexdigest()}
    validate_sources(c);path=folder/'allowance-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_allowance.py'),'--input',str(path),'--output',str(folder/'output.json')]

def original(c,runtime,recover=False):
    envelope=read(runtime)
    expected='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementAllowanceCreate('+json.dumps({**c,'creation_prepare':checked(c['creation_prepare'])['result'],'pending_registered':True},ensure_ascii=False)+');})()'
    if envelope.get('tab_id')!=c['tab_id']or ref(envelope['script'])['sha256']!=envelope.get('source_sha256')or Path(envelope['script']).read_text()!=expected:raise ValueError('补助原创建脚本、SHA或标签不同')
    if envelope.get('phase')=='script_completed':return envelope['result'],envelope['executed_at_utc'],ref(runtime)
    if not recover:raise ValueError('补助创建结果未知，保留原marker，不重发')
    from finance_backend import assert_original_transport
    from edge_runtime import browser_lock,read_marker
    assert_original_transport(envelope)
    if envelope.get('phase')in {'not_dispatched','intent_prepared'}:raise ValueError('原补助创建未派发，不重发原意图')
    recovered_path=Path(runtime).with_name('allowance-original-marker-recovered.json')
    if recovered_path.exists():
        proof=read(recovered_path)
        if proof.get('original_runtime')!=ref(runtime)or proof.get('script_reexecuted')is not False or proof.get('schema')!=SCHEMA:raise ValueError('原补助marker恢复证明不同')
        return proof['result'],proof['observed_at_utc'],ref(recovered_path)
    if not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(envelope.get('result_marker',''))):raise ValueError('原补助结果marker不完整')
    with browser_lock():value=read_marker(envelope['result_marker'],c['tab_id'],timeout=45)
    if not isinstance(value,dict):raise ValueError('原补助marker尚未返回，不重建')
    proof={'schema':SCHEMA,'original_runtime':ref(runtime),'script_reexecuted':False,'result':value,'observed_at_utc':datetime.now(timezone.utc).isoformat()};frozen(recovered_path,proof)
    return value,proof['observed_at_utc'],ref(recovered_path)

def verify(c,created,readback,created_at,reader_adapter=None):
    from validate_config import money
    serial=created.get('serial');r=readback.get('result',{})
    if not created.get('ok')or created.get('save_dispatched')is not True or created.get('token')!=c['token']or created.get('case_id')!=c['case_id']or created.get('expected_userid')!=c['expected_userid']or not re.fullmatch(r'\d+',serial or''):raise ValueError('原补助创建没有确认新单号；只恢复原任务')
    if readback.get('phase')!='script_completed'or readback.get('tab_id')!=c['tab_id']or datetime.fromisoformat(readback['dispatch_intent_at_utc'])<=datetime.fromisoformat(created_at):raise ValueError('补助须原创建结束后独立回读')
    reader={**c,**({'adapter':reader_adapter}if reader_adapter else{})}
    expected='(async()=>{'+checked_text(reader['adapter'])+';return await reimbursementAllowanceReadback('+json.dumps({**reader,'creation_prepare':checked(c['creation_prepare'])['result'],'serial':serial},ensure_ascii=False)+');})()'
    if ref(readback['script'])['sha256']!=readback.get('source_sha256')or Path(readback['script']).read_text()!=expected:raise ValueError('补助独立回读不是原固定来源脚本')
    if any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'case_id':c['case_id'],'expected_userid':c['expected_userid'],'serial':serial,'expected_amount':c['expected_amount'],'claim_type':'2','member_mode':'counts','system_write':False,'page_context_modified':False,'verified_groups':len(c['prepared']['groups'])}.items()):raise ValueError('补助独立完整行程/金额回读不符')
    b=r['bill']
    if b.get('t.serial_no')!=serial or not b.get('t.REQ_INFO','').startswith(c['expected_userid']+'-')or b.get('t.status')!='-99'or b.get('t.lc_finish')!='F'or money(b['t.total_amt'].replace(',',''))!=money(c['expected_amount']):raise ValueError('补助回读单号/本人/草稿/总额不符')
    aliases={'ylry':'YLRY','elry':'ELRY','slry':'SLRY','cllx':'CLLX','cfxc_s':'CFXC_S','cfxc_e':'CFXC_E','clbz':'CLBZ','sdate':'SDATE','edate':'EDATE','hsbt':'HSBT','jtbt':'JTBT','jtfy':'JTFY','feeamount':'FEEAMOUNT','is_wcsy':'IS_WCSY','jtgj':'JTGJ'}
    remaining=list(r.get('rows',[]))
    if len(remaining)!=len(c['prepared']['groups']):raise ValueError('补助完整回读行数不同')
    for group in c['prepared']['groups']:
        values=group['values']
        def matches(row):return all(money(row[a])==money(values[k])if k in {'hsbt','jtbt','jtfy','feeamount'}else str(row.get(a)or'').strip()==values[k]for k,a in aliases.items())
        hits=[i for i,row in enumerate(remaining)if matches(row)]
        if len(hits)!=1:raise ValueError('补助独立回读人数、城市、日期或逐项金额不符')
        remaining.pop(hits[0])
    return{'serial':serial,'amount':c['expected_amount'],'group_count':len(c['prepared']['groups']),'people_count':len(c['prepared']['people']),'claim_type':'2','independent_http_readback':True}

def run(path,output,recover=False,reader_adapter=None):
    from operator_support import bundle
    from reimburse import begin_write
    c=read(path);validate_sources(c);_,paths=bundle(c['bundle']);checked(c['case']);checked(c['form']);checked(c['manifest']);output=Path(output)
    if recover:
        pending=read(paths['state'].parent/'pending_write.json');execution=path.parent/'allowance-execution.json'
        if pending.get('operation')!='invoice:allowance-create'or pending.get('verification',{}).get('input')!=ref(execution):raise ValueError('补助恢复须原创建pending')
        c=read(execution);created,created_at,creation=original(c,path.parent/'allowance-create.json',True)
    else:
        prepare=output.parent/'allowance-create-prepare.json';p=browser(c,'reimbursementAllowanceCreatePrepare',prepare)
        if p.get('phase')!='script_completed'or not p.get('result',{}).get('ok'):raise ValueError('补助原生建单校验未通过：'+str(p.get('result',{}).get('guard_response')or p.get('error')))
        c={**c,'creation_prepare':ref(prepare)};execution=output.parent/'allowance-execution.json';frozen(execution,c)
        begin_write(paths,read(paths['form']),'invoice:allowance-create',output.parent,{'schema':SCHEMA,'input':ref(execution),'runtime':str(output.parent/'allowance-create.json')})
        browser(c,'reimbursementAllowanceCreate',output.parent/'allowance-create.json',{'creation_prepare':p['result'],'pending_registered':True},write=True)
        created,created_at,creation=original(c,output.parent/'allowance-create.json')
    if not created.get('ok'):raise ValueError('原补助建单响应未确认，保留pending：'+str(created.get('error')))
    reader_ref=ref(reader_adapter)if reader_adapter else None
    if reader_ref:
        recovery=read(output.parent/'allowance-recovery-source.json')
        if recovery!={'input':ref(path),'reader_adapter':reader_ref,'write_replayed':False}:raise ValueError('补助恢复读取适配器必须属于本次正式恢复任务')
    reader={**c,**({'adapter':reader_ref}if reader_ref else{})}
    observed_path=output.parent/'allowance-readback.json';observed=browser(reader,'reimbursementAllowanceReadback',observed_path,{'creation_prepare':checked(c['creation_prepare'])['result'],'serial':created['serial']})
    verified=verify(c,created,observed,created_at,reader_ref)
    frozen(output,{'ok':True,'schema':SCHEMA,'state':'created_pending_local_binding','input':ref(execution),'creation':creation,'created_at':created_at,'created_result':created,'readback':ref(observed_path),'reader_adapter':reader_ref,'verification':verified,'draft_created':True,'system_write':not recover,'submitted':False,'finalApproval':False});return 0

def bind_created(paths,receipt,serial):
    from assistant_api import atomic_json
    value=read(receipt);c=checked(value['input']);validate_sources(c);pending_path=paths['state'].parent/'pending_write.json';pending=read(pending_path)
    if value.get('schema')!=SCHEMA or pending.get('operation')!='invoice:allowance-create'or pending.get('verification',{}).get('input')!=value['input']:raise ValueError('须原补助创建pending与独立回读证明')
    checked(c['case']);checked(c['form']);checked(c['manifest'])
    created,created_at,creation=original(c,pending['verification']['runtime'],True)
    if created!=value['created_result']or created_at!=value['created_at']or creation!=value['creation']:raise ValueError('原补助创建回执不同')
    verified=verify(c,created,checked(value['readback']),created_at,value.get('reader_adapter'))
    if verified!=value['verification']or serial!=verified['serial']:raise ValueError('补助绑定单号不同')
    for key in ['case','form','invoices','state']:
        record=read(paths[key])
        if record.get('case_id')!=c['case_id']or record.get('serial')not in {None,serial}:raise ValueError('本地补助已有其他单号')
    for key in ['case','form','invoices','state']:
        record=read(paths[key]);record['serial']=serial
        if key=='invoices':record['native_allowance_source']={'schema':SCHEMA,'input':value['input'],'receipt':ref(receipt),'verification':verified}
        if key=='state':record.update(status='draft_created_readback',creation_readback=ref(receipt),last_native_creation=verified)
        atomic_json(paths[key],record)
    frozen(Path(pending['output_directory'])/'operator_readback_ack.json',{**pending,'status':'draft_bound_readback_recorded','outcome':'saved','bound_serial':serial,'readback':ref(receipt),'verification_result':verified});pending_path.unlink()
    return{'ok':True,'serial':serial,'allowance_amount':verified['amount'],'system_write':False,'submitted':False,'finalApproval':False}

def valid_binding(manifest,form):
    binding=manifest['native_allowance_source'];receipt=checked(binding['receipt']);c=checked(binding['input']);validate_sources(c)
    if receipt.get('input')!=binding['input']or receipt.get('verification')!=binding['verification']or binding['verification']['serial']!=form.get('serial')or form.get('amount')!=c['expected_amount']or form.get('expected_invoice_count')!=0 or form.get('payments')!=c['form_before']['payments']or form.get('funding')!=c['form_before']['funding']:raise ValueError('补助零发票登记必须有原创建及完整回读，人员收款和经费须与来源一致')
    verify(c,receipt['created_result'],checked(receipt['readback']),receipt['created_at'],receipt.get('reader_adapter'));return True

def funding_context(manifest,form):
    valid_binding(manifest,form);binding=manifest['native_allowance_source'];c=checked(binding['input'])
    return{'binding':binding,'prepared':c['prepared'],'creation_prepare':checked(c['creation_prepare'])['result'],'contracts':c['contracts'],'basic_contracts':c['basic_contracts']}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--recover',action='store_true');ap.add_argument('--reader-adapter',type=Path);a=ap.parse_args();raise SystemExit(run(a.input,a.output,a.recover,a.reader_adapter))
