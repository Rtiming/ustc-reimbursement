"""拒绝退回的本人原单：固定修改入口、独立同号回读和未知意图恢复。"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from finance_native_create import read, ref, checked, checked_text, frozen, browser
SCHEMA='ustc-native-returned-original-v1'
ACTIONS={'returned-prepare','returned-open','returned-confirm','returned-recover'}
CONFIRMATION='judge:如您需要修改，将会使报销单进入草稿状态，需重新提交后进入流程审批'

def explicit_confirmation(c,saved):
    if saved.get('ok')is not False or saved.get('save_dispatched')is not True or saved.get('native_response')!=CONFIRMATION or any(saved.get(k)!=c[k]for k in ['case_id','serial','expected_userid','token']) or len(saved.get('requests',[]))!=1 or saved['requests'][0].get('status')!='received':raise ValueError('须原修改已完成的明确确认响应；未知请求不能重发')
    return {'native_response':CONFIRMATION,'original_token':c['token']}

def command(service,request,folder):
    from finance_http import source
    p=request['parameters'];cid=request['case_id'];action=p.get('action');bundle,paths=service.resolve(cid)
    if action not in ACTIONS or set(p)-{'action','tab_id','job_id'} or not re.fullmatch(r'\d+',p.get('tab_id','')):raise ValueError('退回接续仅原单、当前标签及原准备任务')
    if request['execute']!=(action in {'returned-open','returned-confirm'}):raise ValueError('只有open/confirm派发修改；prepare/recover只读')
    if action=='returned-recover':
        origin=service.job_folder(cid,p['job_id']);prior=read(origin/'request.json');c=read(origin/'returned-input.json')
        if prior['operation']!='correction' or prior['parameters']['action']not in {'returned-open','returned-confirm'} or c['tab_id']!=p['tab_id'] or service.result(cid,p['job_id'])['status']in {'queued','running'}:raise ValueError('只恢复本案已停止的原修改意图')
        frozen(folder/'returned-recovery-source.json',ref(origin/'returned-input.json'))
        return [service.python,str(ROOT/'tools/finance_returned.py'),'--input',str(origin/'returned-input.json'),'--output',str(folder/'output.json'),'--recover']
    f=read(paths['form']);case=read(paths['case']);invoices=read(paths['invoices']);binding=case.get('material_context',{}).get('returned_correction_authority')
    if not binding:raise ValueError('须登记同案退回原单的本次纠正安排')
    from material_intake import reject_links
    authority_path=Path(binding['path']);reject_links(authority_path)
    if not authority_path.is_absolute() or authority_path.suffix.lower()!='.json':raise ValueError('纠正安排须材料根内的绝对JSON路径')
    service.material_path(str(authority_path.parent));a=checked(binding)
    if a.get('case_id')!=cid or a.get('serial')!=f['serial'] or a.get('observed_amount')!=f['amount'] or a.get('edit_original')is not True or not a.get('user_text') or not a.get('source'):raise ValueError('退回纠正安排与本案原号及现额不符')
    pending_path=paths['state'].parent/'pending_write.json'
    if pending_path.exists() and action!='returned-confirm':raise ValueError('原写入未核，不新开修改')
    who=os.environ.get('REIMBURSEMENT_EXPECTED_USERID') or f.get('expected_userid')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',who or '') or f.get('system_mode')!='daily':raise ValueError('本次仅明确本人日常退回单')
    adapter=folder/'returned-adapter.js';frozen_adapter=source('create')+'\n'+(ROOT/'tools/finance_returned_api.js').read_text()
    if adapter.exists():
        if adapter.read_text()!=frozen_adapter:raise ValueError('原退回适配器源码变化')
    else:adapter.write_text(frozen_adapter);adapter.chmod(0o600)
    c={'schema':SCHEMA,'case_id':cid,'serial':f['serial'],'expected_amount':f['amount'],'expected_userid':who,'tab_id':p['tab_id'],
       'action':action,'execute':request['execute'],'bundle':str(bundle),'form':ref(paths['form']),'case':ref(paths['case']),'invoices':ref(paths['invoices']),
       'authority':binding,'expected_guids':sorted(x['guid']for x in invoices['selected_invoices']),
       'used_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['used_invoices'],
       'returned_contract':read(ROOT/'data/finance_returned_contract.json'),'adapter':ref(adapter),
       'token':hashlib.sha256((cid+':'+request['request_id']).encode()).hexdigest()}
    if action=='returned-open':
        origin=service.job_folder(cid,p.get('job_id'));prior=read(origin/'request.json');before=read(origin/'returned-input.json')
        if prior['operation']!='correction' or prior['parameters']['action']!='returned-prepare' or service.result(cid,p['job_id'])['status']!='completed' or any(c[k]!=before[k]for k in ['case_id','serial','expected_amount','expected_userid','tab_id','form','case','invoices','authority','expected_guids','returned_contract']):raise ValueError('须引用同案完整准备；来源及字段不能漂移')
        c['prepare_ref']=ref(origin/'output.json')
    if action=='returned-confirm':
        origin=service.job_folder(cid,p.get('job_id'));prior=read(origin/'request.json');before=read(origin/'returned-input.json')
        if prior['operation']!='correction' or prior['parameters']['action']!='returned-open' or service.result(cid,p['job_id'])['status']in {'queued','running'} or any(c[k]!=before[k]for k in ['case_id','serial','expected_amount','expected_userid','tab_id','form','case','invoices','authority','expected_guids','returned_contract']):raise ValueError('只能确认本案原修改请求')
        if read(pending_path).get('verification',{}).get('input')!=ref(origin/'returned-input.json'):raise ValueError('确认须绑定原pending')
        saved,_=original(before,origin/'returned-save.json',False)
        c.update(confirmation=explicit_confirmation(before,saved),prepare_ref=before['prepare_ref'],confirmation_source=ref(origin/'returned-input.json'),confirmation_response=ref(origin/'returned-save.json'))
    path=folder/'returned-input.json';frozen(path,c)
    return [service.python,str(ROOT/'tools/finance_returned.py'),'--input',str(path),'--output',str(folder/'output.json')]

def script(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or {})},ensure_ascii=False)+');})()'

def verify_prepare(c,envelope):
    r=envelope.get('result',{})
    expected={'ok':True,'complete':True,'action':'returned-prepare','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'system_write':False,'save_dispatched':False,'definition_sha256':c['returned_contract']['definition_sha256']}
    if envelope.get('phase')!='script_completed' or envelope.get('tab_id')!=c['tab_id'] or any(r.get(k)!=v for k,v in expected.items()):raise ValueError('退回原单准备未完整通过')
    return r

def original(c,path,recover):
    r=read(path);expected=script(c,'reimbursementReturnedOpen',{'prepared':checked(c['prepare_ref'])['result'],'pending_registered':True})
    if r.get('tab_id')!=c['tab_id'] or ref(r['script'])['sha256']!=r.get('source_sha256') or Path(r['script']).read_text()!=expected:raise ValueError('原修改源码或标签变化')
    if r.get('phase')=='script_completed':return r['result'],r['executed_at_utc']
    if not recover:raise ValueError('原修改结果未知，只恢复原marker')
    from finance_backend import assert_original_transport
    from edge_runtime import browser_lock,read_marker
    assert_original_transport(r);saved=path.with_name('returned-original-marker.json')
    if saved.exists():
        proof=read(saved)
        if proof['original']!=ref(path) or proof['script_reexecuted']is not False:raise ValueError('原marker证明不同')
    else:
        if r.get('phase')in {'intent_prepared','not_dispatched'} or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',r.get('result_marker','')):raise ValueError('原修改未派发或marker缺失')
        with browser_lock():value=read_marker(r['result_marker'],c['tab_id'],timeout=45)
        if not isinstance(value,dict):raise ValueError('原修改尚无结果')
        proof={'original':ref(path),'script_reexecuted':False,'result':value,'observed_at':datetime.now(timezone.utc).isoformat()};frozen(saved,proof)
    return proof['result'],proof['observed_at']

def verify_readback(c,envelope,when,prepared):
    r=envelope.get('result',{})
    if envelope.get('phase')!='script_completed' or envelope.get('tab_id')!=c['tab_id'] or datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(when):raise ValueError('须原修改结束后的独立回读')
    for k,v in {'ok':True,'complete':True,'action':'returned-readback','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'original_serial_preserved':True,'original_invoice_set_preserved':True,'system_write':False}.items():
        if r.get(k)!=v:raise ValueError('退回原单独立回读不符：'+k)
    stable_rows=lambda rows:sorted(({k:v for k,v in row.items()if k!='t.status_name'}for row in rows),key=lambda x:x['t.tk_guid'])
    if r['bill']['t.status']!='-99' or r['bill']['t.lc_finish']!='F' or r['bill']['t.total_amt']!=prepared['bill']['t.total_amt'] or stable_rows(r['rows'])!=stable_rows(prepared['rows']):raise ValueError('原号、金额、归属或草稿状态未确认')
    return r

def run(path,output,recover=False):
    from operator_support import bundle
    from reimburse import begin_write
    from assistant_api import atomic_json
    c=read(path);output=Path(output);_,paths=bundle(c['bundle'])
    for name in ['authority','form','case','invoices']:checked(c[name])
    if c['returned_contract']!=read(ROOT/'data/finance_returned_contract.json'):raise ValueError('退回修改合同变化')
    if c['action']=='returned-prepare':verify_prepare(c,browser(c,'reimbursementReturnedPrepare',output));return 0
    prepared=verify_prepare(c,checked(c['prepare_ref']));runtime=path.parent/'returned-save.json';lock=paths['state'].parent/'pending_write.json'
    if recover:
        if read(lock).get('verification',{}).get('input')!=ref(path):raise ValueError('原退回修改pending不符')
    else:
        if c['action']=='returned-confirm':
            prior_input=checked(c['confirmation_source']);saved,_=original(prior_input,Path(c['confirmation_response']['path']),False);checked(c['confirmation_response']);explicit_confirmation(prior_input,saved)
            previous=read(lock)
            if previous.get('verification',{}).get('input')!=c['confirmation_source']:raise ValueError('原pending已变，不确认')
            frozen(path.parent/'prior-confirmation-pending.json',previous)
            atomic_json(lock,{**previous,'operation':'correction:returned-confirm','output_directory':str(path.parent),'prior_pending':ref(path.parent/'prior-confirmation-pending.json'),'verification':{'schema':SCHEMA,'input':ref(path),'runtime':str(runtime)}})
        else:begin_write(paths,read(paths['form']),'correction:returned-open',path.parent,{'schema':SCHEMA,'input':ref(path),'runtime':str(runtime)})
        browser(c,'reimbursementReturnedOpen',runtime,{'prepared':prepared,'pending_registered':True},write=True)
    saved,when=original(c,runtime,recover)
    if saved.get('ok')is not True or saved.get('save_dispatched')is not True or any(saved.get(k)!=c[k]for k in ['case_id','serial','expected_userid','token']):raise ValueError('原修改未确认，保留pending')
    rd=output.parent/'returned-readback.json';observed=browser(c,'reimbursementReturnedReadback',rd,{'prepared':prepared});verified=verify_readback(c,observed,when,prepared)
    proof={'schema':SCHEMA,'input':ref(path),'save':ref(runtime),'readback':ref(rd),'serial':c['serial'],'original_serial_preserved':True,'original_invoices_preserved':True,'amount_unchanged':True,'workflow_restarted':False}
    frozen(output,{'ok':True,**proof,'system_write':not recover})
    for name in ['form','case','invoices']:checked(c[name])
    frozen(path.parent/'case-before-returned-edit.json',{k:read(paths[k])for k in ['case','form','invoices','state']})
    form=read(paths['form']);form.setdefault('review',{}).update(workflow_started=False,workflow_status='returned_editing',node=verified['bill'].get('t.node_id',''),returned_edit_proof=ref(output));atomic_json(paths['form'],form)
    state=read(paths['state']);state.update(status='returned_editing',node=verified['bill'].get('t.node_id',''),returned_edit_proof=ref(output),updated_at_utc=datetime.now(timezone.utc).isoformat());atomic_json(paths['state'],state)
    frozen(path.parent/'operator_readback_ack.json',{**read(lock),'status':'returned_original_independently_verified','outcome':'staged','proof':ref(output)});lock.unlink();return 0

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--recover',action='store_true');a=ap.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
