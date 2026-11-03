"""医疗/单段行程HTTP字段保存；原件、一次意图和独立回读分别核对。"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
from finance_native_create import sha,ref,checked,checked_text,frozen,read
ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-medical-edit-v1'
TRAVEL_SCHEMA='ustc-native-travel-edit-v1'

def kind(c):
    return 'travel'if c.get('schema')==TRAVEL_SCHEMA else'medical'

def method(c,suffix):
    return 'reimbursement'+kind(c).capitalize()+suffix



def script(c,method,extra=None):
    return '(async()=>{'+checked_text(c['adapter'])+';return await '+method+'('+json.dumps({**c,**(extra or{})},ensure_ascii=False)+');})()'


def command(service,case_id,edit,action,execute,request_id,folder):
    from finance_http import source
    bundle,paths=service.resolve(case_id)
    manifest=read(paths['case']).get('material_context',{}).get('invoice_edit_manifest')
    if not isinstance(manifest,dict):raise ValueError('须正式登记本案医疗修改清单')
    checked(manifest)
    domain='medical'if edit['schema']=='ustc-invoice-medical-edit-v1'else'travel'
    adapter=folder/('native-'+domain+'-adapter.js');code=source(domain)
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原票据修改适配器源码漂移，不覆盖')
    else:
        with adapter.open('x',encoding='utf-8',newline='\n')as stream:stream.write(code)
        adapter.chmod(0o600)
    c={'schema':SCHEMA if domain=='medical'else TRAVEL_SCHEMA,'case_id':case_id,'edit':edit,'expected_userid':edit['expected_userid'],'tab_id':edit['tab_id'],
       'action':action,'execute':execute,'request_id':request_id,'bundle':str(bundle),'adapter':ref(adapter),
       'case':ref(paths['case']),'form':ref(paths['form']),'edit_manifest':manifest,
       domain+'_contract':read(ROOT/('data/finance_'+domain+'_contract.json')),
       'pool_contracts':read(ROOT/'data/finance_business_contracts.json')['domains']['invoices']}
    path=folder/('native-'+domain+'-input.json');frozen(path,c)
    return [service.python,str(ROOT/'tools/finance_native_invoice_edit.py'),'--input',str(path),'--output',str(folder/'output.json')]


def browser(c,method,output,extra=None):
    from registered_page_owner import RegisteredPageOwner
    RegisteredPageOwner.before_child_dispatch(c['tab_id'])
    path=Path(output).with_suffix('.js');frozen_script=script(c,method,extra)
    if path.exists():raise ValueError('已有原医疗marker，只恢复原任务')
    path.write_text(frozen_script,encoding='utf-8',newline='\n');path.chmod(0o600)
    subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(path),'--output',str(output),'--tab-id',c['tab_id'],
                    '--operation-kind','write'if method.endswith('Save')else'read','--quiet'],check=False)
    if not Path(output).exists():raise ValueError('医疗原任务未留下回执，保留原pending')
    return read(output)


def validate_prepare(c,envelope):
    body=envelope.get('result',{})
    if(envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or body.get('ok')is not True or
       body.get('complete')is not True or body.get('business_transport')!='native_http'or body.get('action')!=kind(c)+'-prepare'or
       body.get('system_write')is not False or body.get('save_dispatched')is not False or
       any(body.get(k)!=c[k]for k in ['case_id','expected_userid'])or body.get('native_guid')!=c['edit']['native_guid']or
       body.get('definition_sha256')!=c[kind(c)+'_contract']['definition_sha256']):raise ValueError('不是完整同票医疗HTTP预检')
    return body


def run(input_path,output,*,recover=False):
    from finance_invoice_edit import validate
    from operator_support import bundle
    from reimburse import begin_write
    c=read(input_path)
    if c.get('schema')not in {SCHEMA,TRAVEL_SCHEMA}:raise ValueError('未知票据修改合同')
    checked(c['case']);checked(c['form']);checked(c['edit_manifest']);checked_text(c['adapter']);validate(c['edit'])
    if c[kind(c)+'_contract']!=read(ROOT/('data/finance_'+kind(c)+'_contract.json')):raise ValueError('医疗固定合同已变')
    _,paths=bundle(c['bundle']);output=Path(output)
    if recover:
        from assistant_api import atomic_json
        from finance_backend import assert_original_transport
        from edge_runtime import browser_lock,read_marker
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if pending.get('operation')!='invoice:'+kind(c)+'-http'or v.get('input')!=ref(input_path):raise ValueError('只能恢复原医疗pending')
        old=read(v['save_receipt']);prepared=validate_prepare(c,checked(v['prepare']))
        if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=script(c,method(c,'Save'),{'pending_registered':True,'expected_plan':prepared}):raise ValueError('原医疗保存源码变化')
        if old.get('phase')=='script_completed':saved=old
        else:
            import re
            if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原医疗动作未派发或缺marker')
            assert_original_transport(old)
            with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
            if not isinstance(body,dict):raise ValueError('原医疗marker未返回结构化结果')
            saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now(timezone.utc).isoformat(),
                   'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
        recovered=output.parent/'original-medical-result.json';frozen(recovered,saved)
        value=browser({**c,'action':'inspect','execute':False},method(c,'Prepare'),output)
        validate_prepare(c,value);value['original_save_recovery']=ref(recovered);atomic_json(output,value);return 0
    if c['action']=='inspect':return 0 if validate_prepare(c,browser(c,method(c,'Prepare'),output))else 2
    if c['action']!=('medical-run'if kind(c)=='medical'else'run')or c['execute']is not True:raise ValueError('医疗HTTP仅inspect及medical-run')
    if (paths['state'].parent/'pending_write.json').exists():raise ValueError('原财务写入未核，不能重新保存')
    prepared_path=output.parent/'native-medical-prepare.json';body=validate_prepare(c,browser(c,method(c,'Prepare'),prepared_path))
    checked(c['case']);checked(c['form']);checked(c['edit_manifest'])
    begin_write(paths,read(paths['form']),'invoice:'+kind(c)+'-http',output.parent,
                {'schema':c['schema'],'input':ref(input_path),'prepare':ref(prepared_path),'save_receipt':str(output)})
    saved=browser(c,method(c,'Save'),output,{'pending_registered':True,'expected_plan':body})
    return 0 if saved.get('phase')=='script_completed'and saved.get('result',{}).get('ok')is True else 2


def verify_ack(previous,envelope,outcome):
    v=previous['verification'];c=checked(v['input']);p=validate_prepare(c,checked(v['prepare']));saved=read(v['save_receipt']);s=saved.get('result',{})
    if envelope.get('original_save_recovery'):
        recovered=checked(envelope['original_save_recovery']);binding=recovered.get('original_marker_recovery',{})
        if saved.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('医疗恢复未绑定原marker')
        if any(recovered.get(k)!=saved.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('医疗恢复身份漂移')
        saved=recovered;s=saved.get('result',{})
    checked(c['case']);checked(c['form']);checked(c['edit_manifest'])
    if(previous.get('case_id')!=c['case_id']or previous.get('operation')!='invoice:'+kind(c)+'-http'or saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or
       s.get('action')!=kind(c)+'-save'or any(s.get(k)!=c[k]for k in ['case_id','expected_userid'])or
       s.get('native_guid')!=c['edit']['native_guid']or sha(saved['script'])!=saved.get('source_sha256')or
       Path(saved['script']).read_text()!=script(c,method(c,'Save'),{'pending_registered':True,'expected_plan':p})):
        raise ValueError('原医疗保存尚未取得同任务完整停止回执')
    body=validate_prepare(c,envelope)
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('医疗独立回读须晚于保存结束')
    text=Path(envelope['script']).read_text();prefix='(async()=>{'+checked_text(c['adapter'])+';return await '+method(c,'Prepare')+'('
    if sha(envelope['script'])!=envelope.get('source_sha256')or not text.startswith(prefix)or not text.endswith(');})()'):raise ValueError('不是固定医疗读取源码')
    reader=json.loads(text[len(prefix):-len(');})()')])
    for key in ['case_id','edit','expected_userid','tab_id',kind(c)+'_contract','pool_contracts','case','form','edit_manifest']:
        if reader.get(key)!=c[key]:raise ValueError('医疗回读来源与原意图不符')
    target=p['target']if outcome=='saved'else p['original']if outcome=='not_applied'and s.get('save_dispatched')is False else None
    if target is None or body.get('original')!=target:raise ValueError('医保类型或受保护的票面/支付字段不符')
    return {'schema':c['schema'],'native_guid':c['edit']['native_guid'],'independent_http_readback':True,'outcome':outcome,'protected_fields_verified':True}


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--recover',action='store_true');a=ap.parse_args();raise SystemExit(run(a.input,a.output,recover=a.recover))
