"""Fixed native receipt and allowance entries, through the ordinary job owner."""
from __future__ import annotations
import argparse,base64,hashlib,json,os,re,subprocess,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'tools')not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
from finance_native_create import read,ref,checked,checked_text,frozen,sha
SCHEMA='ustc-native-nonvat-entry-v1'

def sources(service,cid,index):
    _,paths=service.resolve(cid);case=read(paths['case']);binding=case.get('material_context',{}).get('nonvat_receipts_manifest')
    if not isinstance(binding,dict):raise ValueError('先登记本案已逐页核对的非税/其他凭据清单及SHA')
    manifest_path=Path(binding['path']).resolve();service.material_path(str(manifest_path.parent))
    if manifest_path.suffix!='.json'or not manifest_path.is_file()or manifest_path.stat().st_size>2097152:raise ValueError('凭据清单须材料根内有界JSON')
    m=checked(binding)
    if m.get('schema')!='ustc-nonvat-receipts-v1'or m.get('case_id')!=cid or type(index)is not int or not 0<=index<len(m['entries']):raise ValueError('非税凭据清单或索引不符')
    entry=m['entries'][index]
    if(entry.get('evidence_kind')not in {'other_receipt','fiscal_receipt'}or entry.get('amount_basis_confirmed')is not True
       or not isinstance(entry.get('description'),str)or not entry['description'].strip()or not re.fullmatch(r'\d+\.\d{2}',entry.get('amount',''))or float(entry['amount'])<=0):raise ValueError('只录入已核金额的正式收据，不把申请/应缴回执当实缴凭据')
    files=entry.get('source_files',[])
    if not 1<=len(files)<=10:raise ValueError('每组需1至10份已核原件')
    seen=set()
    for f in files:
        path=service.material_path(f['path'])
        if(path.suffix.lower()not in {'.pdf','.jpg','.jpeg','.png'}or sha(path)!=f['sha256']or path.stat().st_size!=f['size']or not 0<f['size']<=5242880
           or f['sha256']in seen or any(x in f['name']for x in '&|\r\n')or Path(f['name']).suffix.lower()!=path.suffix.lower()):raise ValueError('原件SHA/大小/扩展名或唯一性不符')
        seen.add(f['sha256'])
    return paths,binding,entry

def command(service,request,folder):
    p=request['parameters'];action=p.get('action')
    if action=='allowance-prepare':return allowance_command(service,request,folder)
    if action=='nonvat-select':return selection_command(service,request,folder)
    if action in {'nonvat-save','nonvat-readback','nonvat-recover'}:return entry_command(service,request,folder)
    if action not in {'nonvat-inspect','allowance-inspect'}or set(p)-{'action','tab_id'}or request['execute']:raise ValueError('补充录入检查只接固定类型和已核tab')
    _,paths=service.resolve(request['case_id']);form=read(paths['form']);who=form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',who or '')or not re.fullmatch(r'\d+',p.get('tab_id','')):raise ValueError('须明确本案账号和tab')
    from finance_http import source
    adapter=folder/'supplement-adapter.js';adapter.write_text(source('travel')+'\n'+(ROOT/'tools/finance_supplement_api.js').read_text());adapter.chmod(0o600)
    c={'case_id':request['case_id'],'expected_userid':who,'serial':form.get('serial'),'expected_amount':form['amount'],'kind':action.split('-')[0],
       'tab_id':p['tab_id'],'adapter':ref(adapter),'contracts':read(ROOT/'data/finance_supplement_contract.json'),
       'basic_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['basic']}
    if c['kind']=='allowance':
        binding=read(paths['case']).get('material_context',{}).get('travel_allowance_manifest')
        if binding:
            value=Path(binding['path']).resolve();service.material_path(str(value.parent))
            if value.suffix!='.json'or value.stat().st_size>2097152:raise ValueError('补助清单须有界材料JSON')
            plan=checked(binding)
            if plan.get('case_id')!=request['case_id']or plan.get('schema')!='ustc-travel-allowance-v1'or not 1<=len(plan.get('people',[]))<=20:raise ValueError('须本案已核人员与日期清单')
            if any(not re.fullmatch(r'[A-Za-z0-9]{2,40}',x.get('id',''))or not x.get('name')for x in plan['people']):raise ValueError('补助人员必须有原姓名学号')
            c['allowance_source']=plan;c['allowance_source_ref']=binding
    path=folder/'supplement-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_supplement.py'),'--input',str(path),'--output',str(folder/'output.json')]

def entry_command(service,request,folder):
    p=request['parameters'];action=p['action'];cid=request['case_id']
    if set(p)-{'action','tab_id','start','job_id'}or not re.fullmatch(r'\d+',p.get('tab_id','')):raise ValueError('凭据录入仅本案索引、tab及原任务')
    if action in {'nonvat-readback','nonvat-recover'}:
        if request['execute']:raise ValueError('凭据回读及恢复不重新写入')
        origin=service.job_folder(cid,p.get('job_id'));r=read(origin/'request.json')
        if r.get('operation')!='invoice'or r['parameters'].get('action')!='nonvat-save'or service.result(cid,origin.name)['status']in {'queued','running'}:raise ValueError('只引用本案已停止的原凭据保存任务')
        c=read(origin/'supplement-input.json')
        if c['tab_id']!=p['tab_id']:raise ValueError('原凭据标签不同，不移植待处理状态')
        frozen(folder/'supplement-source.json',ref(origin/'supplement-input.json'))
        return[service.python,str(ROOT/'tools/finance_supplement.py'),'--input',str(origin/'supplement-input.json'),'--output',str(folder/'output.json'),'--readback']
    if not request['execute']:raise ValueError('凭据保存须execute=true；nonvat-inspect先核原生合同')
    paths,binding,entry=sources(service,cid,p.get('start',0));form=read(paths['form'])
    if form.get('serial')or form.get('review',{}).get('workflow_started'):raise ValueError('此入口仅未建单案件，不追加到未知流程')
    who=form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',who or '')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')not in {None,who}:raise ValueError('当前账号与凭据案件不符')
    _,manifest=service.resolve(cid)
    if any(x.get('entry_id')==entry['id']for x in read(paths['invoices']).get('native_nonvat_entries',[])):raise ValueError('本组已登记原生GUID，不重复录入')
    from finance_http import source
    adapter=folder/'supplement-adapter.js';adapter.write_text(source('travel')+'\n'+(ROOT/'tools/finance_supplement_api.js').read_text());adapter.chmod(0o600)
    c={'schema':SCHEMA,'case_id':cid,'bundle':str(paths['form'].parent/'operator.json'),'serial':None,'expected_amount':form['amount'],'expected_userid':who,
       'tab_id':p['tab_id'],'kind':'nonvat','entry':entry,'entry_index':p.get('start',0),'manifest':binding,'case':ref(paths['case']),'form':ref(paths['form']),
       'adapter':ref(adapter),'contracts':read(ROOT/'data/finance_supplement_contract.json'),
       'pool_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['invoices'],'token':hashlib.sha256((cid+':'+request['request_id']).encode()).hexdigest(),
       'execute':True}
    path=folder/'supplement-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_supplement.py'),'--input',str(path),'--output',str(folder/'output.json'),'--save']

def allowance_command(service,request,folder):
    p=request['parameters'];cid=request['case_id']
    if set(p)!={'action','tab_id','job_id'}or request['execute']:raise ValueError('补助标准准备只接受原检查job和原标签，不保存')
    origin=service.job_folder(cid,p['job_id']);prior=read(origin/'request.json');c0=read(origin/'supplement-input.json')
    if(prior.get('operation')!='invoice'or prior['parameters'].get('action')!='allowance-inspect'or c0['tab_id']!=p['tab_id']or
       service.result(cid,p['job_id'])['status']!='completed'):raise ValueError('只使用本案完整补助身份及城市检查')
    from finance_http import source
    adapter=folder/'supplement-adapter.js';adapter.write_text(source('travel')+'\n'+(ROOT/'tools/finance_supplement_api.js').read_text());adapter.chmod(0o600)
    c={**c0,'adapter':ref(adapter),'inspected':ref(origin/'output.json')};checked(c['allowance_source_ref'])
    path=folder/'supplement-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_supplement.py'),'--input',str(path),'--output',str(folder/'output.json'),'--allowance-prepare']

def prepare_allowance(path,output):
    c=read(path);observed=checked(c['inspected']);checked(c['allowance_source_ref'])
    if observed.get('phase')!='script_completed'or observed.get('result',{}).get('complete')is not True:raise ValueError('原补助检查不完整')
    browser(c,'reimbursementAllowancePrepare',output,{'inspected':observed['result']});return 0

def browser(c,method,path,extra=None):
    from registered_page_owner import RegisteredPageOwner
    path=Path(path);script=path.with_suffix('.js')
    if script.exists():raise ValueError('已有原凭据步骤，只恢复原marker，不重放')
    script.write_text(script_text(c,method,extra));script.chmod(0o600)
    RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(path),'--tab-id',c['tab_id'],'--operation-kind','write'if method=='reimbursementNonvatWrite'else'read','--quiet'],check=False)
    value=read(path)
    if value.get('phase')!='script_completed'or value.get('result',{}).get('ok')is not True:raise ValueError('原凭据步骤未确认：'+str(value.get('error')or value.get('result',{}).get('error')))
    return value

def script_text(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or {})},ensure_ascii=False)+');})()'

def entry_proofs(paths):
    m=read(paths['invoices']);rows=m.get('native_nonvat_entries',[]);proofs=[]
    if not rows or len({r['guid']for r in rows})!=len(rows)or len({r['entry_id']for r in rows})!=len(rows):raise ValueError('非税凭据尚未完整登记或重复')
    for row in rows:
        c=checked(row['input']);ack=Path(row['input']['path']).parent/'operator_readback_ack.json';a=read(ack)
        if(a.get('status')!='operator_readback_recorded'or a.get('outcome')!='saved'or a.get('native_nonvat_readback')!=row or
           c['case_id']!=m['case_id']or c['entry']['id']!=row['entry_id']or c['entry']['source_files']!=row['source_files']):raise ValueError('凭据原保存与独立回读登记不符')
        proofs.append(ref(ack))
    return rows,proofs

def selection_command(service,request,folder):
    p=request['parameters'];cid=request['case_id'];_,paths=service.resolve(cid);f=read(paths['form']);rows,proofs=entry_proofs(paths)
    if set(p)!={'action','tab_id'}or request['execute']is not True or f.get('serial')or (paths['state'].parent/'pending_write.json').exists():raise ValueError('非税凭据冻结选择须未建单、无待核写入及明确执行')
    _,binding,_=sources(service,cid,0);manifest=checked(binding)
    who=f.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',who or '')or any(checked(row['input'])['expected_userid']!=who for row in rows):raise ValueError('凭据冻结选择须同一个明确账号')
    from validate_config import money
    if(len(rows)!=len(manifest['entries'])or {r['entry_id']for r in rows}!={r['id']for r in manifest['entries']}or
       sum((money(r['amount'])for r in rows),money('0'))!=money(f['amount'])):raise ValueError('原收据组数/金额与本次申请范围未闭合')
    from finance_http import source
    adapter=folder/'supplement-adapter.js';adapter.write_text(source('travel')+'\n'+(ROOT/'tools/finance_supplement_api.js').read_text());adapter.chmod(0o600)
    c={'case_id':cid,'expected_userid':who,'expected_amount':f['amount'],'tab_id':p['tab_id'],'adapter':ref(adapter),
       'pool_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['invoices'],'entries':rows,'acknowledgements':proofs,'source_manifest':binding,'bundle':str(paths['form'].parent/'operator.json')}
    path=folder/'supplement-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_supplement.py'),'--input',str(path),'--output',str(folder/'output.json'),'--select']

def select_entries(path,output):
    from operator_support import bundle
    from assistant_api import atomic_json
    c=read(path);_,paths=bundle(c['bundle']);rows,proofs=entry_proofs(paths)
    if rows!=c['entries']or proofs!=c['acknowledgements']:raise ValueError('原凭据登记在选择期间变化')
    checked(c['source_manifest']);r=browser(c,'reimbursementNonvatSelection',output)['result'];proof=ref(output)
    m=read(paths['invoices']);form=read(paths['form']);originals={}
    for row in rows:
        for f in row['source_files']:originals.setdefault(f['sha256'],{'path':f['path'],'sha256':f['sha256'],'source_kind':'nonvat','invoice_number_candidates':[]})
    selected=[{'guid':row['t.tk_guid'],'number':'','type':'MULTITK','source_kind':'nonvat','receipt_reference':entry['entry_id'],'amount':entry['amount'],'source_sha256':entry['source_files'][0]['sha256'],'native_row':row}for entry,row in zip(rows,r['selected_rows'])]
    m.update(original_files=list(originals.values()),selected_invoices=selected,expected_count=len(selected),expected_amount=c['expected_amount'],expected_userid=c['expected_userid'],
             selection_requires_review=False,manual_nonvat_selection={'schema':'ustc-native-nonvat-selection-v1','readback':proof,'input':ref(path)})
    form['expected_invoice_count']=len(selected);atomic_json(paths['invoices'],m);atomic_json(paths['form'],form)
    frozen(Path(output).parent/'nonvat-selection-local-binding.json',{'ok':True,'case_id':c['case_id'],'count':len(selected),'amount':c['expected_amount'],'readback':proof,'system_write':False});return 0

def creation_selection(paths,case,who):
    from validate_config import money
    m=read(paths['invoices']);form=read(paths['form']);binding=m['manual_nonvat_selection'];c=checked(binding['input']);r=checked(binding['readback'])
    rows,proofs=entry_proofs(paths)
    if(c['case_id']!=case or c['expected_userid']!=who or c['entries']!=rows or c['acknowledgements']!=proofs or
       r.get('phase')!='script_completed'or r['result'].get('ok')is not True or r['result'].get('complete')is not True or
       r['result'].get('action')!='nonvat-selection'or r['result'].get('case_id')!=case or r['result'].get('expected_userid')!=who or
       form['expected_invoice_count']!=len(rows)or money(c['expected_amount'])!=money(form['amount'])):raise ValueError('非税凭据完整选择及原始回执不符')
    expected=[{'guid':row['t.tk_guid'],'number':'','type':'MULTITK','source_kind':'nonvat','receipt_reference':entry['entry_id'],'amount':entry['amount'],'source_sha256':entry['source_files'][0]['sha256'],'native_row':row}for entry,row in zip(rows,r['result']['selected_rows'])]
    if m['selected_invoices']!=expected:raise ValueError('非税凭据选择被修改')
    checked(c['source_manifest'])
    return expected

def save_entry(path,output):
    from operator_support import bundle
    from reimburse import begin_write
    c=read(path);_,paths=bundle(c['bundle']);checked(c['case']);checked(c['form']);checked(c['manifest'])
    prepared=path.parent/'nonvat-prepare.json';v=browser(c,'reimbursementNonvatPrepare',prepared)['result']
    begin_write(paths,read(paths['form']),'invoice:nonvat-entry',path.parent,{'schema':SCHEMA,'input':ref(path),'prepare':ref(prepared),'save_receipt':str(output)})
    browser(c,'reimbursementNonvatWrite',path.parent/'nonvat-start.json',{'step':'start','prepared':v,'pending_registered':True})
    for i,f in enumerate(c['entry']['source_files']):
        raw=Path(f['path']).read_bytes()
        for offset in range(0,len(raw),131072):
            chunk=raw[offset:offset+131072];browser(c,'reimbursementNonvatWrite',path.parent/f'nonvat-append-{i}-{offset}.json',{'step':'append','index':i,'offset':offset,'base64':base64.b64encode(chunk).decode(),'chunk_sha256':hashlib.sha256(chunk).hexdigest(),'pending_registered':True})
        browser(c,'reimbursementNonvatWrite',path.parent/f'nonvat-upload-{i}.json',{'step':'upload','index':i,'pending_registered':True})
    browser(c,'reimbursementNonvatWrite',output,{'step':'save','pending_registered':True});return 0

def readback_entry(path,output):
    c=read(path);checked(c['case']);checked(c['form']);checked(c['manifest'])
    saved=path.parent/'output.json';envelope=read(saved)
    if envelope.get('phase')!='script_completed':
        from edge_runtime import browser_lock,read_marker
        from finance_backend import assert_original_transport
        assert_original_transport(envelope)
        with browser_lock():value=read_marker(envelope['result_marker'],c['tab_id'],timeout=45)
        envelope={**envelope,'result':value,'phase':'script_completed'}
        frozen(Path(output).parent/'nonvat-original-marker.json',envelope)
    if not isinstance(envelope.get('result'),dict):raise ValueError('原凭据marker尚未完成')
    browser(c,'reimbursementNonvatReadback',output,{'original_save':envelope['result']});return 0

def verify_ack(previous,envelope,outcome):
    v=previous['verification'];c=checked(v['input']);prepared=checked(v['prepare'])['result'];saved=read(v['save_receipt']);s=saved.get('result',{});r=envelope.get('result',{})
    checked(c['case']);checked(c['form']);checked(c['manifest']);checked_text(c['adapter'])
    if(previous.get('operation')!='invoice:nonvat-entry'or previous.get('case_id')!=c['case_id']or c.get('schema')!=SCHEMA
       or Path(v['save_receipt']).resolve()!=(Path(previous['output_directory'])/'output.json').resolve()
       or saved.get('tab_id')!=c['tab_id']or envelope.get('tab_id')!=c['tab_id']
       or sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script_text(c,'reimbursementNonvatWrite',{'step':'save','pending_registered':True})
       or sha(envelope['script'])!=envelope.get('source_sha256')or Path(envelope['script']).read_text()!=script_text(c,'reimbursementNonvatReadback',{'original_save':s})
       or any(sha(f['path'])!=f['sha256']for f in c['entry']['source_files'])):raise ValueError('凭据读写源码、案件或原件已变化')
    if(outcome!='saved'or saved.get('phase')!='script_completed'or s.get('ok')is not True or s.get('save_dispatched')is not True
       or s.get('guid')!=prepared['guid']or envelope.get('phase')!='script_completed'or r.get('ok')is not True or r.get('complete')is not True
       or r.get('action')!='nonvat-readback'or r.get('system_write')is not False or any(r.get(k)!=c[k]for k in ['case_id','expected_userid'])
       or r.get('guid')!=prepared['guid']or r.get('entry')!=c['entry']or len(r.get('source_readbacks',[]))!=len(c['entry']['source_files'])
       or any(z.get('verified')is not True for z in r['source_readbacks'])):raise ValueError('凭据原保存及完整独立源字节/GUID回读未闭合')
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('凭据独立回读须晚于保存完成')
    return {'schema':SCHEMA,'entry_id':c['entry']['id'],'guid':r['guid'],'amount':c['entry']['amount'],'source_files':c['entry']['source_files'],
            'native_row':r['native_row'],'input':v['input'],'independent_http_readback':True}

def run(path,output):
    from registered_page_owner import RegisteredPageOwner
    c=read(path);code='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementSupplementInspect('+json.dumps(c,ensure_ascii=False)+');})()'
    script=Path(output).with_suffix('.js');script.write_text(code);script.chmod(0o600);RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    r=subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(output),'--tab-id',c['tab_id'],'--operation-kind','read','--quiet'],check=False)
    return r.returncode

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--save',action='store_true');p.add_argument('--readback',action='store_true');p.add_argument('--select',action='store_true');p.add_argument('--allowance-prepare',action='store_true');a=p.parse_args();raise SystemExit(save_entry(a.input,a.output)if a.save else readback_entry(a.input,a.output)if a.readback else select_entries(a.input,a.output)if a.select else prepare_allowance(a.input,a.output)if a.allowance_prepare else run(a.input,a.output))
