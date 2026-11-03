"""Reconcile one stopped cross-category amount save without replaying it."""
from pathlib import Path
from datetime import datetime
import argparse,json
from finance_native_create import read,ref,checked,checked_text,frozen,sha
import finance_native_amount as amount

SCHEMA='ustc-native-amount-persistence-recovery-v1'
# Frozen pre-fix adapter: this error follows an accepted no-allocation save.
LEGACY_ADAPTER_SHA256='846c733f0d4ca489aeeaad85266bedb6a9ac9b18edb82f9f12e97f13d5dd33c1'

def original(proof):
    c=checked(proof['input']);saved=checked(proof['receipt']);before=amount.preparation(c,checked(proof['prepare']),rows=True)
    checked(c['form']);checked_text(c['adapter']);amount.initialization(c)
    b=saved.get('result',{})
    if(c.get('adapter',{}).get('sha256')!=LEGACY_ADAPTER_SHA256 or c.get('action')!='save'or c.get('budget_allocations')or before.get('allocation_tree')or before.get('editor_scope')!='whole_bill'or
       saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or b.get('ok')is not False or
       b.get('error')!='AMOUNT_API_PERSISTED_GROUP_TOTAL'or b.get('action')!='amount-save'or b.get('save_dispatched')is not True or
       b.get('financial_amount_save_requested')is not True or
       {k:v for k,v in b.get('prepared',{}).items()if k!='requests'}!={k:v for k,v in before.items()if k!='requests'} or
       any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or
       sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=amount.script(c,'reimbursementAmountSave',{'pending_registered':True,'expected_plan':before})):
        raise ValueError('仅恢复已停止、完整原回执明确分类小计校验失败的整单减报；不重发未知保存')
    return c,saved,before

def command(service,request,folder,input_path,saved_path):
    from finance_http import source
    p=request['parameters'];proof={'input':ref(input_path),'prepare':ref(input_path.parent/'native-amount-prepare.json'),'receipt':ref(saved_path)}
    c,_,before=original(proof)
    if request['execute']or request['case_id']!=c['case_id']or any(p.get(k)!=c[k]for k in ['group','subject','expected_userid']):raise ValueError('金额恢复须同案同分组本人，仅核对原意图')
    if not str(p.get('tab_id','')).isdigit():raise ValueError('恢复须当前已登记财务标签')
    adapter=folder/'native-amount-recovery-adapter.js';adapter.write_text(source('amount'),encoding='utf-8',newline='\n');adapter.chmod(0o600)
    reader={**c,'action':'recover','execute':False,'tab_id':p['tab_id'],'adapter':ref(adapter),'persistence_recovery':proof,'expected_plan':before}
    value={'schema':SCHEMA,'reader':reader,'original':proof};path=folder/'native-amount-recovery-input.json';frozen(path,value)
    return [service.python,str(amount.ROOT/'tools/finance_amount_recovery.py'),'--input',str(path),'--output',str(folder/'output.json')]

def run(path,output):
    from operator_support import bundle
    from assistant_api import atomic_json
    v=read(path);c,saved,before=original(v['original']);reader=v['reader'];checked_text(reader['adapter'])
    _,paths=bundle(c['bundle']);pending=read(paths['state'].parent/'pending_write.json')
    if pending.get('operation')!='expense_amount:http-save'or pending.get('verification',{}).get('input')!=v['original']['input']:raise ValueError('不是当前原金额pending')
    result=amount.browser(reader,'reimbursementAmountRecoverPersisted',output)
    result['amount_persistence_recovery']=ref(path);atomic_json(output,result)
    verify_ack(pending,result,'saved');return 0

def verify_ack(pending,envelope,outcome):
    v=checked(envelope['amount_persistence_recovery']);c,saved,before=original(v['original']);reader=v['reader']
    proof=v['original'];pv=pending.get('verification',{})
    if(v.get('schema')!=SCHEMA or outcome!='saved'or pending.get('case_id')!=c['case_id']or pending.get('operation')!='expense_amount:http-save'or
       pv.get('input')!=proof['input']or pv.get('prepare')!=proof['prepare']or ref(pv['save_receipt'])!=proof['receipt']):raise ValueError('恢复必须绑定当前原写入及完整冻结证据')
    for key,value in c.items():
        if key not in {'action','execute','tab_id','adapter'}and reader.get(key)!=value:raise ValueError('恢复改变原保存意图：'+key)
    if reader.get('persistence_recovery')!=proof or reader.get('expected_plan')!=before or reader.get('action')!='recover'or reader.get('execute')is not False:raise ValueError('恢复不是固定无重放读取')
    if(sha(envelope['script'])!=envelope.get('source_sha256')or Path(envelope['script']).read_text()!=amount.script(reader,'reimbursementAmountRecoverPersisted')or
       datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc'])):raise ValueError('恢复读取源码、时间不符')
    after=amount.preparation(reader,envelope,rows=True)
    if after.get('allocation_tree')!=before.get('allocation_tree'):raise ValueError('恢复时逐票分配树变化')
    p=after.get('persistence_recovery',{});refresh=p.get('refresh',{})
    if(p.get('original')!=proof or p.get('monetary_save_replayed')is not False or p.get('temporary_editor_refreshed')is not True or
       refresh.get('ok')is not True or refresh.get('initialized')is not True or refresh.get('financial_amount_save_requested')is not False or refresh.get('action')!='amount-initialize'):
        raise ValueError('恢复须原生刷新暂存行后独立读取，禁止重放金额保存')
    for key in ['case_id','serial','expected_userid']:
        if refresh.get(key)!=c[key]:raise ValueError('恢复暂存刷新归属变化')
    # The preserved original error is only reachable after the old adapter
    # accepted the save and resolved count==0 (no allocation plan was supplied).
    result=amount.verify_saved(c,before,{'financial_amount_save_requested':True,'branch':{'count':0}},after)
    return {**result,'original_validation_error':saved['result']['error'],'original_write_replayed':False,'reader_tab_id':reader['tab_id']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();raise SystemExit(run(a.input,a.output))
