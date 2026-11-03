"""有票国内差旅同单补助：固定原生表单检查和来源约束。"""
from __future__ import annotations
import argparse,hashlib,json,os,re,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'tools')not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
from finance_native_create import read,ref,checked,checked_text,frozen,browser
SCHEMA='ustc-native-domestic-allowance-v1'

def validate_plan(plan,cid):
    from validate_config import money
    if plan.get('case_id')!=cid or plan.get('schema')!='ustc-travel-allowance-v1'or not plan.get('authorization')or not 1<=len(plan.get('people',[]))<=20 or not 1<=len(plan.get('entries',[]))<=200:raise ValueError('须本案逐人日期补助来源')
    people={p['id']:p['name']for p in plan['people']};audit=checked(plan['source_audit']);seen=set();total=money('0')
    if len(people)!=len(plan['people']):raise ValueError('补助人员重复')
    for e in plan['entries']:
        datetime.strptime(e['date'],'%Y-%m-%d');key=(e['person_id'],e['date'])
        if key in seen or people.get(e['person_id'])!=e['name']or money(e['amount'])<=0 or not e['departure_city']or not e['arrival_city']or len([x for x in audit['expenses']if x.get('费用编号')==e['source_expense_id']and x.get('源SHA')==e['source_sha256']and x.get('人员')==e['name']])!=1:raise ValueError('补助逐人日期、金额与原始行程未闭合')
        seen.add(key);total+=money(e['amount'])
    if total!=money(plan['amount']):raise ValueError('补助源合计不符')

def command(service,request,folder):
    from finance_http import source
    cid=request['case_id'];p=request['parameters'];_,paths=service.resolve(cid)
    action=p['action'];actions={'domestic-allowance-inspect','domestic-allowance-finalize',*[prefix+'-'+suffix for prefix in ['domestic-travel-add','domestic-allowance-edit']for suffix in ['prepare','save','recover']]}
    if set(p)-{'action','tab_id','start','job_id'}or action not in actions or not re.fullmatch(r'\d+',p.get('tab_id','')):raise ValueError('同单补助仅本案、人员索引、原任务和已核标签')
    if action.endswith('-recover'):
        origin=service.job_folder(cid,p.get('job_id'));prior=read(origin/'request.json');c=read(origin/'domestic-allowance-input.json')
        if request['execute']or prior.get('operation')!='invoice'or prior['parameters'].get('action')!=action.removesuffix('-recover')+'-save'or c['tab_id']!=p['tab_id']or service.result(cid,origin.name)['status']in {'queued','running'}:raise ValueError('只恢复原保存，不重新派发')
        reader=folder/'domestic-allowance-reader.js';reader.write_text(source('travel')+'\n'+(ROOT/'tools/finance_domestic_allowance_api.js').read_text());reader.chmod(0o600)
        frozen(folder/'domestic-allowance-recovery-source.json',{'input':ref(origin/'domestic-allowance-input.json'),'reader_adapter':ref(reader),'write_replayed':False})
        return[service.python,str(ROOT/'tools/finance_domestic_allowance.py'),'--input',str(origin/'domestic-allowance-input.json'),'--output',str(folder/'output.json'),'--recover','--reader-adapter',str(reader)]
    if request['execute']!=action.endswith('-save'):raise ValueError('先prepare，再明确执行保存')
    form=read(paths['form']);manifest=read(paths['invoices']);binding=read(paths['case']).get('material_context',{}).get('travel_allowance_manifest')
    if not binding or form.get('native_bill_type')!='2'or not form.get('serial')or (paths['state'].parent/'pending_write.json').exists():raise ValueError('须已绑定国内差旅及本案补助来源，且无待核写入')
    service.material_path(str(Path(binding['path']).parent));plan=checked(binding)
    validate_plan(plan,cid)
    adapter=folder/'domestic-allowance-adapter.js';adapter.write_text(source('travel')+'\n'+(ROOT/'tools/finance_domestic_allowance_api.js').read_text());adapter.chmod(0o600)
    domains=read(ROOT/'data/finance_business_contracts.json')['domains']
    c={'schema':SCHEMA,'bundle':str(paths['form'].parent/'operator.json'),'case_id':cid,'expected_userid':form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID'),'serial':form['serial'],'expected_amount':form['amount'],'tab_id':p['tab_id'],
       'source_ref':binding,'source_plan':plan,'form':ref(paths['form']),'manifest':ref(paths['invoices']),'expected_guids':sorted(x['guid']for x in manifest['selected_invoices']),
       'travel_contracts':domains['travel'],'used_contracts':domains['used_invoices'],'definition_hashes':read(ROOT/'data/finance_supplement_contract.json')['definitions'],'adapter':ref(adapter),'action':action,'kind':'trip'if action.startswith('domestic-travel-add-')else'edit'}
    if action.endswith('-save'):
        origin=service.job_folder(cid,p.get('job_id'));prior=read(origin/'request.json');before=read(origin/'domestic-allowance-input.json')
        if prior.get('operation')!='invoice'or prior['parameters'].get('action')!=action.removesuffix('-save')+'-prepare'or service.result(cid,origin.name)['status']!='completed'or any(before[k]!=c[k]for k in ['case_id','serial','expected_amount','expected_userid','tab_id','form','manifest','source_ref','expected_guids','kind']):raise ValueError('须同案原准备，当前来源和单据不得漂移')
        c.update({k:before[k]for k in (['person','entries']if c['kind']=='trip'else['row_index'])})
        c.update(prepare_input_ref=ref(origin/'domestic-allowance-input.json'),prepare_ref=ref(origin/'output.json'),execute=True,token=hashlib.sha256((cid+':'+request['request_id']).encode()).hexdigest())
    if action=='domestic-travel-add-prepare':
        index=p.get('start',0)
        if type(index)is not int or not 0<=index<len(plan['people']):raise ValueError('新增差旅人须本案补助人员索引')
        c['person']=plan['people'][index];c['entries']=[e for e in plan['entries']if e['person_id']==c['person']['id']]
    if action=='domestic-allowance-edit-prepare':
        index=p.get('start',0)
        if type(index)is not int or not 0<=index<200:raise ValueError('须原生完整清单中的补助行索引')
        c['row_index']=index
    if action=='domestic-allowance-finalize':
        from validate_config import money
        c['invoice_amount']=format(sum((money(x['amount'])for x in manifest['selected_invoices']),money('0')),'.2f')
        if money(c['invoice_amount'])+money(plan['amount'])!=money(form['amount']):raise ValueError('同单交通和补助尚未与本次总额闭合')
    path=folder/'domestic-allowance-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_domestic_allowance.py'),'--input',str(path),'--output',str(folder/'output.json')]

def script(c,method,extra=None):return'(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or{})},ensure_ascii=False)+');})()'

def method(c,suffix):return('reimbursementDomesticTrip'if c['kind']=='trip'else'reimbursementDomesticAllowanceEdit')+suffix

def original(c,path,recover):
    envelope=read(path)
    if envelope.get('tab_id')!=c['tab_id']or ref(envelope['script'])['sha256']!=envelope.get('source_sha256')or Path(envelope['script']).read_text()!=script(c,method(c,'Save'),{'prepared':checked(c['prepare_ref'])['result'],'pending_registered':True}):raise ValueError('原写入源码和标签不同')
    if envelope.get('phase')=='script_completed':return envelope['result'],envelope['executed_at_utc']
    if not recover:raise ValueError('原行程结果未知，只恢复原marker')
    from finance_backend import assert_original_transport
    from edge_runtime import browser_lock,read_marker
    assert_original_transport(envelope)
    if envelope.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',envelope.get('result_marker','')):raise ValueError('原意图未派发或缺marker，不重发')
    saved=Path(path).with_name('domestic-original-marker.json')
    if saved.exists():
        proof=read(saved)
        if proof.get('original')!=ref(path)or proof.get('script_reexecuted')is not False:raise ValueError('原marker恢复证明变化')
    else:
        with browser_lock():value=read_marker(envelope['result_marker'],c['tab_id'],timeout=45)
        if not isinstance(value,dict):raise ValueError('原marker尚未返回')
        proof={'original':ref(path),'result':value,'observed_at':datetime.now(timezone.utc).isoformat(),'script_reexecuted':False};frozen(saved,proof)
    return proof['result'],proof['observed_at']

def verify_prepare(c,envelope):
    r=envelope.get('result',{})
    before=checked(c['prepare_input_ref'])
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or ref(envelope['script'])['sha256']!=envelope.get('source_sha256')or Path(envelope['script']).read_text()!=script(before,method(c,'Prepare'))or any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'action':'domestic-travel-add-prepare'if c['kind']=='trip'else'domestic-allowance-edit-prepare','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,'save_dispatched':False,'definition_sha256':c['definition_hashes']['353'if c['kind']=='trip'else'128']}.items()):raise ValueError('原生准备不完整')
    return r

def run(path,output,recover=False,reader_adapter=None):
    c=read(path);checked(c['source_ref']);checked(c['form']);checked(c['manifest'])
    validate_plan(c['source_plan'],c['case_id'])
    if c['action'].endswith('-save'):return save_trip(path,output,recover,reader_adapter)
    if c['action']=='domestic-allowance-finalize':return finalize(path,output)
    r=browser(c,'reimbursementDomesticAllowanceInspect'if c['action']=='domestic-allowance-inspect'else method(c,'Prepare'),output)
    if r.get('phase')!='script_completed'or r.get('result',{}).get('ok')is not True:raise ValueError('同单补助原生检查未完成')
    return 0

def verify_final(c,envelope):
    from validate_config import money
    r=envelope.get('result',{})
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or Path(envelope['script']).read_text()!=script(c,'reimbursementDomesticAllowanceFinalReadback')or ref(envelope['script'])['sha256']!=envelope.get('source_sha256')or any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,'invoice_amount':c['invoice_amount'],'allowance_amount':c['source_plan']['amount'],'people_count':len(c['source_plan']['people']),'source_person_dates':len(c['source_plan']['entries'])}.items()):raise ValueError('同单最终独立回读来源不符')
    bill=r['bill'];rows=r['allowances'];invoices=r['used_rows'];people={p['id']:p['name']for p in c['source_plan']['people']}
    if bill.get('t.serial_no')!=c['serial']or not bill.get('t.REQ_INFO','').startswith(c['expected_userid']+'-')or bill.get('t.status')!='-99'or bill.get('t.bx_type')!='2'or bill.get('t.lc_finish')!='F'or bill.get('t.wf_name')or bill.get('t.uni_key')or bill.get('t.node_id')or money(bill['t.total_amt'].replace(',',''))!=money(c['expected_amount'])or sorted(x['t.tk_guid']for x in invoices)!=c['expected_guids']:raise ValueError('同单最终单号、本人、票据归属或总额不符')
    from datetime import timedelta
    assigned={i:[]for i in range(len(rows))}
    for e in c['source_plan']['entries']:
        day=datetime.strptime(e['date'],'%Y-%m-%d');hits=[i for i,row in enumerate(rows)if row['t.sno']==e['person_id']and row['t.sname']==e['name']and datetime.strptime(row['t.startdate'][:10],'%Y-%m-%d')<=day<datetime.strptime(row['t.startdate'][:10],'%Y-%m-%d')+timedelta(days=int(row['t.staydays']))]
        if len(hits)!=1:raise ValueError('补助逐人来源日期未唯一归属原生行程')
        assigned[hits[0]].append(e)
    def amt(row,key):return money(row[key].replace(',',''))
    for i,row in enumerate(rows):
        entries=assigned[i]
        if people.get(row['t.sno'])!=row['t.sname']or row['t.serial_no']!=c['serial']or amt(row,'t.hsbt_amt')+amt(row,'t.jtbt_amt')!=sum((money(e['amount'])for e in entries),money('0'))or amt(row,'t.total_amt')!=sum((money(e['amount'])for e in entries),money('0'))or int(row['t.STAYDAYS_HS'])!=len(entries)or int(row['t.STAYDAYS_JT'])!=len(entries)or money(row['t.cg_cq_amt']or'0')!=0 or amt(row,'t.bg_hotel_amt')!=0:raise ValueError('补助原生逐人日期或金额不闭合')
    components={'国内出差伙食补贴':format(sum((amt(row,'t.hsbt_amt')for row in rows),money('0')),'.2f'),'国内出差交通补贴':format(sum((amt(row,'t.jtbt_amt')for row in rows),money('0')),'.2f')}
    if components!=r['components']or sum((amt(row,'t.tk_total_amt')for row in invoices),money('0'))!=money(c['invoice_amount'])or sum(map(money,components.values()),money('0'))!=money(c['source_plan']['amount']):raise ValueError('同单票据与补助分项合计不闭合')
    return{'serial':c['serial'],'amount':c['expected_amount'],'invoice_amount':c['invoice_amount'],'allowance_amount':c['source_plan']['amount'],'components':components,'people_count':len(people),'person_dates':len(c['source_plan']['entries']),'invoice_count':len(invoices)}

def finalize(path,output):
    from operator_support import bundle
    from assistant_api import atomic_json
    c=read(path);_,paths=bundle(c['bundle']);envelope=browser(c,'reimbursementDomesticAllowanceFinalReadback',output);verified=verify_final(c,envelope)
    binding={'schema':SCHEMA,'input':ref(path),'readback':ref(output),'verification':verified}
    manifest=read(paths['invoices']);manifest['native_domestic_allowance_source']=binding;atomic_json(paths['invoices'],manifest)
    state=read(paths['state']);state['domestic_allowance_readback']=binding;atomic_json(paths['state'],state);return 0

def funding_context(manifest,form):
    from validate_config import money
    binding=manifest['native_domestic_allowance_source'];c=checked(binding['input']);validate_plan(c['source_plan'],form['case_id']);checked(c['source_ref'])
    verified=verify_final(c,checked(binding['readback']))
    if binding['verification']!=verified or form.get('serial')!=verified['serial']or money(form['amount'])!=money(verified['amount'])or sorted(x['guid']for x in manifest['selected_invoices'])!=c['expected_guids']or len(manifest['selected_invoices'])!=form['expected_invoice_count']or sum((money(x['amount'])for x in manifest['selected_invoices']),money('0'))!=money(verified['invoice_amount']):raise ValueError('同单补助登记与当前票据、单号或金额不同')
    return{'binding':binding,'source_plan':c['source_plan'],'invoice_amount':c['invoice_amount'],'expected_guids':c['expected_guids'],'travel_contracts':c['travel_contracts'],'used_contracts':c['used_contracts'],'components':verified['components']}

def save_trip(path,output,recover=False,reader_adapter=None):
    from operator_support import bundle
    from reimburse import begin_write
    from assistant_api import atomic_json
    from validate_config import money
    c=read(path);output=Path(output);_,paths=bundle(c['bundle']);prepared=verify_prepare(c,checked(c['prepare_ref']));runtime=path.parent/'domestic-travel-save.json';pending_path=paths['state'].parent/'pending_write.json'
    if recover:
        if read(pending_path).get('verification',{}).get('input')!=ref(path):raise ValueError('原行程pending不同')
    else:
        begin_write(paths,read(paths['form']),'invoice:domestic-'+c['kind'],path.parent,{'schema':SCHEMA,'input':ref(path),'runtime':str(runtime)})
        browser(c,method(c,'Save'),runtime,{'prepared':prepared,'pending_registered':True},write=True)
    saved,when=original(c,runtime,recover)
    if saved.get('ok')is not True or saved.get('save_dispatched')is not True or any(saved.get(k)!=c[k]for k in ['case_id','serial','expected_userid','token']):raise ValueError('原行程保存未确认，保留pending')
    reader=c
    if reader_adapter:
        proof=read(output.parent/'domestic-allowance-recovery-source.json')
        if proof!={'input':ref(path),'reader_adapter':ref(reader_adapter),'write_replayed':False}:raise ValueError('恢复读取器须本次正式恢复任务固定源码')
        reader={**c,'adapter':ref(reader_adapter)}
    rd=output.parent/'domestic-travel-readback.json';observed=browser(reader,method(c,'Readback'),rd,{'prepared':prepared});r=observed.get('result',{})
    identity={'person_id':c['person']['id']}if c['kind']=='trip'else{'row_index':c['row_index']}
    if observed.get('phase')!='script_completed'or observed.get('tab_id')!=c['tab_id']or datetime.fromisoformat(observed['dispatch_intent_at_utc'])<=datetime.fromisoformat(when)or any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,**identity}.items())or Path(observed['script']).read_text()!=script(reader,method(c,'Readback'),{'prepared':prepared})or ref(observed['script'])['sha256']!=observed.get('source_sha256'):raise ValueError('原保存后独立完整回读不符')
    amount=format(money(r['bill']['t.total_amt'].replace(',','')),'.2f');proof={'schema':SCHEMA,'input':ref(path),'save':ref(runtime),'readback':ref(rd),'reader_adapter':ref(reader_adapter)if reader_adapter else None,'serial':c['serial'],**identity,'amount_after':amount,'allowance_correction_required':True,'write_replayed':False};frozen(output,{'ok':True,**proof,'system_write':not recover})
    # 仅绑定系统当前金额；经费和逐人付款须待最终补助核对后另行更新。
    for key in ['form','invoices','state']:
        v=read(paths[key]);v.setdefault('native_domestic_adjustments',[]).append(proof)
        if key=='form':v['amount']=amount
        if key=='invoices':v['expected_amount']=amount
        atomic_json(paths[key],v)
    frozen(path.parent/'operator_readback_ack.json',{**read(pending_path),'status':'domestic_trip_add_independently_verified','outcome':'saved','proof':ref(output)});pending_path.unlink();return 0

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--recover',action='store_true');ap.add_argument('--reader-adapter',type=Path);a=ap.parse_args();raise SystemExit(run(a.input,a.output,a.recover,a.reader_adapter))
