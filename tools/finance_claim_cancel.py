"""本人未提交误拆草稿的固定撤销、原任务恢复和独立票据释放回读。"""
from __future__ import annotations
import argparse,hashlib,json,os,re,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'tools')not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
from finance_native_create import read,ref,checked,checked_text,frozen,browser
SCHEMA='ustc-native-claim-cancel-v1'

def command(service,request,folder):
    from finance_http import source
    cid=request['case_id'];p=request['parameters'];action=p['action'];bundle,paths=service.resolve(cid)
    if set(p)-{'action','tab_id','job_id'}or not re.fullmatch(r'\d+',p.get('tab_id','')):raise ValueError('草稿撤销仅同案、原标签及准备任务')
    if action=='draft-cancel-recover':
        origin=service.job_folder(cid,p['job_id']);prior=read(origin/'request.json');c=read(origin/'claim-cancel-input.json')
        if request['execute']or prior.get('operation')!='invoice'or prior['parameters'].get('action')!='draft-cancel'or c['tab_id']!=p['tab_id']or service.result(cid,p['job_id'])['status']in {'queued','running'}:raise ValueError('只恢复本案原撤销，不重发')
        frozen(folder/'claim-cancel-recovery-source.json',ref(origin/'claim-cancel-input.json'))
        return[service.python,str(ROOT/'tools/finance_claim_cancel.py'),'--input',str(origin/'claim-cancel-input.json'),'--output',str(folder/'output.json'),'--recover']
    form=read(paths['form']);case=read(paths['case']);manifest=read(paths['invoices']);binding=case.get('material_context',{}).get('draft_cancellation_authority')
    if not binding:raise ValueError('须先登记本案误拆草稿的明确纠正安排及原单号')
    authority=checked(binding);service.material_path(str(Path(binding['path']).parent))
    if authority.get('case_id')!=cid or authority.get('serial')!=form.get('serial')or authority.get('amount')!=form.get('amount')or authority.get('cancel_draft')is not True or type(authority.get('reopen_for_rebuild'))is not bool or not authority.get('user_text')or not authority.get('source'):raise ValueError('撤销安排须与本案单号、金额和本次用户来源一致')
    who=form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    if not re.fullmatch(r'\d+',form.get('serial')or'')or not re.fullmatch(r'[A-Za-z0-9]{2,40}',who or'')or form.get('review',{}).get('workflow_started')or (paths['state'].parent/'pending_write.json').exists():raise ValueError('已有流程或待核写入不能撤销重做')
    if action not in {'draft-cancel-prepare','draft-cancel'}or request['execute']!=(action=='draft-cancel'):raise ValueError('撤销先只读prepare，再明确执行')
    adapter=folder/'claim-cancel-adapter.js';adapter.write_text(source('create')+'\n'+(ROOT/'tools/finance_claim_cancel_api.js').read_text());adapter.chmod(0o600)
    domains=read(ROOT/'data/finance_business_contracts.json')['domains'];c={'schema':SCHEMA,'case_id':cid,'bundle':str(bundle),'serial':form['serial'],'expected_amount':form['amount'],'expected_userid':who,'tab_id':p['tab_id'],'action':action,'execute':request['execute'],
      'form':ref(paths['form']),'case':ref(paths['case']),'invoices':ref(paths['invoices']),'authority':binding,'expected_guids':sorted(t['guid']for t in manifest.get('selected_invoices',[])),
      'expected_nonvat_guids':sorted(t['guid']for t in manifest.get('selected_invoices',[])if t.get('source_kind')=='nonvat'and t.get('type')=='MULTITK'),
      'used_contracts':domains['used_invoices'],'pool_contracts':domains['invoices'],'cancel_contract':read(ROOT/'data/finance_cancel_contract.json'),'adapter':ref(adapter),'token':hashlib.sha256((cid+':'+request['request_id']).encode()).hexdigest()}
    if action=='draft-cancel':
        origin=service.job_folder(cid,p.get('job_id'));prior=read(origin/'request.json');before=read(origin/'claim-cancel-input.json')
        if prior.get('operation')!='invoice'or prior['parameters'].get('action')!='draft-cancel-prepare'or service.result(cid,p['job_id'])['status']!='completed'or any(before[k]!=c[k]for k in ['case_id','serial','expected_amount','expected_userid','tab_id','form','case','invoices','authority','expected_guids','expected_nonvat_guids']):raise ValueError('撤销须同案原准备回执，当前字段不得漂移')
        c['prepare_ref']=ref(origin/'output.json')
    path=folder/'claim-cancel-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_claim_cancel.py'),'--input',str(path),'--output',str(folder/'output.json')]

def script(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or{})},ensure_ascii=False)+');})()'

def verify_prepare(c,envelope):
    r=envelope.get('result',{})
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'action':'claim-cancel-prepare','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,'save_dispatched':False,'definition_sha256':c['cancel_contract']['definition_sha256']}.items()):raise ValueError('原撤销准备未完整通过，只保留未提交误单')
    return r

def original(c,path,recover=False):
    r=read(path);expected=script(c,'reimbursementClaimCancel',{'prepared':checked(c['prepare_ref'])['result'],'pending_registered':True})
    if r.get('tab_id')!=c['tab_id']or ref(r['script'])['sha256']!=r.get('source_sha256')or Path(r['script']).read_text()!=expected:raise ValueError('原撤销源码或标签变化')
    if r.get('phase')=='script_completed':return r['result'],r['executed_at_utc']
    if not recover:raise ValueError('撤销未知，只恢复原marker，不重发')
    from finance_backend import assert_original_transport
    from edge_runtime import browser_lock,read_marker
    assert_original_transport(r);saved=Path(path).with_name('claim-cancel-original-marker.json')
    if saved.exists():
        proof=read(saved)
        if proof['original']!=ref(path)or proof['script_reexecuted']is not False:raise ValueError('原撤销marker证明变化')
    else:
        if r.get('phase')in {'intent_prepared','not_dispatched'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',r.get('result_marker','')):raise ValueError('原撤销未派发或marker缺失')
        with browser_lock():value=read_marker(r['result_marker'],c['tab_id'],timeout=45)
        if not isinstance(value,dict):raise ValueError('原撤销结果未取得')
        proof={'original':ref(path),'script_reexecuted':False,'result':value,'observed_at':datetime.now(timezone.utc).isoformat()};frozen(saved,proof)
    return proof['result'],proof['observed_at']

def run(path,output,recover=False):
    from operator_support import bundle
    from reimburse import begin_write
    from assistant_api import atomic_json
    c=read(path);output=Path(output);_,paths=bundle(c['bundle']);checked(c['authority']);checked(c['form']);checked(c['case']);checked(c['invoices'])
    if c['cancel_contract']!=read(ROOT/'data/finance_cancel_contract.json'):raise ValueError('撤销合同变化')
    if c['action']=='draft-cancel-prepare':verify_prepare(c,browser(c,'reimbursementClaimCancelPrepare',output));return 0
    prepared=verify_prepare(c,checked(c['prepare_ref']));runtime=path.parent/'claim-cancel-save.json';pending_path=paths['state'].parent/'pending_write.json'
    if recover:
        pending=read(pending_path)
        if pending.get('verification',{}).get('input')!=ref(path):raise ValueError('原撤销pending不同')
    else:
        begin_write(paths,read(paths['form']),'invoice:cancel-unsubmitted-draft',path.parent,{'schema':SCHEMA,'input':ref(path),'runtime':str(runtime)})
        browser(c,'reimbursementClaimCancel',runtime,{'prepared':prepared,'pending_registered':True},write=True)
    saved,when=original(c,runtime,recover)
    if saved.get('ok')is not True or saved.get('save_dispatched')is not True or any(saved.get(k)!=c[k]for k in ['case_id','serial','expected_userid','token']):raise ValueError('原撤销尚未确认，保持pending')
    rd=output.parent/'claim-cancel-readback.json';observed=browser(c,'reimbursementClaimCancelReadback',rd,{'prepared':prepared});r=observed.get('result',{})
    if observed.get('phase')!='script_completed'or observed.get('tab_id')!=c['tab_id']or datetime.fromisoformat(observed['dispatch_intent_at_utc'])<=datetime.fromisoformat(when)or any(r.get(k)!=v for k,v in {'ok':True,'complete':True,'claim_absent':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False}.items())or sorted(x['t.tk_guid']for x in r['released'])!=c['expected_guids']:raise ValueError('撤销后须新的完整本人清单及全部原票释放回读')
    proof={'schema':SCHEMA,'input':ref(path),'save':ref(runtime),'readback':ref(rd),'cancelled_serial':c['serial'],'released_guids':c['expected_guids'],'original_files_preserved':True,'workflow_started':False,'cancel_replayed':False};frozen(output,{'ok':True,**proof,'system_write':not recover})
    # 撤销证明只解开本案单号，原文档、票据及旧状态完整归档，不清未知意图。
    checked(c['form']);checked(c['case']);checked(c['invoices']);history=path.parent/'cancelled-case-before-reopen.json';frozen(history,{k:read(paths[k])for k in ['case','form','invoices','state']})
    authority=checked(c['authority'])
    for key in ['case','form','invoices','state']:
        v=read(paths[key]);v['serial']=None
        if key=='invoices':
            for name in ['native_selection','manual_nonvat_selection','native_allowance_source','native_domestic_allowance_source','native_domestic_initial','native_creation_intent','creation_receipt']:v.pop(name,None)
            v.update(selected_invoices=[],selected_count=0,draft_creation_requested=False,selection_requires_review=True)
        if key=='state':v.update(status='cancelled_rebuild_pending'if authority['reopen_for_rebuild']else'cancelled_superseded',last_cancelled_draft=proof,original_documents=ref(history))
        atomic_json(paths[key],v)
    frozen(path.parent/'operator_readback_ack.json',{**read(pending_path),'status':'cancelled_draft_independently_verified','outcome':'saved','proof':ref(output)});pending_path.unlink();return 0

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--recover',action='store_true');a=ap.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
