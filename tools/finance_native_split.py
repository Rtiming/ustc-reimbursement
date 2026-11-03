"""专项逐票拆分固定HTTP；资格、原件和额度复用correction正本。"""
from __future__ import annotations
import argparse,json,os,re
from datetime import datetime,timezone
from pathlib import Path
from finance_native_create import ref,checked,checked_text,frozen,read,sha
from finance_native_amount import script,browser
ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-invoice-split-v1'

def prepare(c,envelope,rows=False):
    p=envelope.get('result',{})
    if(envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or p.get('ok')is not True or p.get('complete')is not True or p.get('action')!='split-inspect'or p.get('business_transport')!='native_http'or p.get('system_write')is not False or p.get('save_dispatched')is not False or any(p.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or p.get('definition_sha256')!=c['split_contract']['definition_sha256']):raise ValueError('须完整同单拆分HTTP读取')
    if rows and(p.get('rows_complete')is not True or not p.get('rows')):raise ValueError('缺完整持久逐票分配')
    return p

def proof(c):
    p=c.get('initialization')
    if not p:
        if c.get('initialization_verified'):raise ValueError('缺原拆分初始化证明')
        return
    original=checked(p['input']);before=prepare(original,checked(p['prepare']));saved=checked(p['receipt']);b=saved.get('result',{})
    if(original.get('schema')!=SCHEMA or any(original[k]!=c[k]for k in ['case_id','serial','group','plan','expected_userid','tab_id','form'])or saved.get('phase')!='script_completed'or saved.get('tab_id')!=c['tab_id']or b.get('ok')is not True or b.get('initialization_verified')is not True or b.get('action')!='split-apply'or any(b.get(k)!=c[k]for k in ['case_id','serial','expected_userid'])or sha(saved['script'])!=saved.get('source_sha256')or Path(saved['script']).read_text()!=script(original,'reimbursementSplitApply',{'pending_registered':True,'expected_plan':before})):raise ValueError('原拆分保存或刷新未确认，只恢复原marker')

def command(service,request,folder):
    from correction import validate_plan
    from finance_http import source
    p=request['parameters'];bundle,paths=service.resolve(request['case_id']);form=read(paths['form']);plan=p['plan'];validate_plan(plan,form)
    from validate_config import money
    if {f['code']:money(f['amount'])for f in plan['funding']}!={f['code']:money(f['amount'])for f in form['funding']}or plan['policy']['subject']!='30211':raise ValueError('拆分经费须逐项等于本案已确认经费，专项仅往返交通科目')
    for row in plan['people']:
        material_source=service.material_path(row['source_file'])
        if material_source.suffix.lower()not in {'.pdf','.ofd','.png','.jpg','.jpeg'}or not re.fullmatch(r'[A-Fa-f0-9]{32}',row['invoice_guid']):raise ValueError('专项拆分须原始票据文件和完整原生GUID')
    action=p['action'];who=form.get('expected_userid')or os.environ.get('REIMBURSEMENT_EXPECTED_USERID');group=p.get('group')
    if action not in {'inspect','apply','verify','recover'}or request['execute']!=(action=='apply')or group not in plan['expected_groups']or not re.fullmatch(r'[A-Za-z0-9]{2,40}',str(who))or not re.fullmatch(r'\d+',str(p.get('tab_id'))):raise ValueError('拆分须明确同案分组、本人及标签；只有apply执行')
    if form.get('review',{}).get('workflow_started'):raise ValueError('原流程尚未回到草稿，不直接改原单')
    keys=plan.get('native_keys',{}).get(group)
    if not isinstance(keys,list)or len(keys)!=2 or len(set(keys))!=2:raise ValueError('须冻结本组专项和另一经费的两个原生科目/项目/预算键')
    if action=='recover':
        old=service.job_folder(request['case_id'],p.get('job_id'));prior=read(old/'native-split-input.json')
        if any(prior[k]!=v for k,v in {'case_id':request['case_id'],'group':group,'plan':plan,'tab_id':p['tab_id'],'expected_userid':who}.items()):raise ValueError('拆分原任务身份不同')
        return[service.python,str(ROOT/'tools/finance_native_split.py'),'--input',str(old/'native-split-input.json'),'--output',str(folder/'output.json'),'--recover']
    c={'schema':SCHEMA,'case_id':request['case_id'],'serial':form['serial'],'expected_amount':form['amount'],'expected_userid':who,'group':group,'plan':plan,'tab_id':p['tab_id'],'action':action,'execute':request['execute'],'request_id':request['request_id'],'bundle':str(bundle),'form':ref(paths['form']),'initialization_verified':False,
       'amount_contract':read(ROOT/'data/finance_amount_contract.json'),'submission_contract':read(ROOT/'data/finance_submission_contract.json'),'split_contract':read(ROOT/'data/finance_split_contract.json')}
    domains=read(ROOT/'data/finance_business_contracts.json')['domains'];c.update(basic_contracts=domains['basic'],used_contracts=domains['used_invoices'])
    if action=='verify':
        receipt,_,_=service._typed_case_artifact(request['case_id'],p.get('job_id'),p.get('artifact_id'),'correction',['apply'])
        old=service.job_folder(request['case_id'],p['job_id']);c.update(initialization_verified=True,initialization={'input':ref(old/'native-split-input.json'),'prepare':ref(old/'native-split-prepare.json'),'receipt':ref(receipt)});proof(c)
    adapter=folder/'native-split-adapter.js';code=source('split')
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原拆分适配器源码漂移')
    else:adapter.write_text(code,encoding='utf-8',newline='\n');adapter.chmod(0o600)
    c['adapter']=ref(adapter);path=folder/'native-split-input.json';frozen(path,c)
    return[service.python,str(ROOT/'tools/finance_native_split.py'),'--input',str(path),'--output',str(folder/'output.json')]

def run(input_path,output,recover=False):
    from operator_support import bundle
    from correction import validate_plan
    from reimburse import begin_write
    c=read(input_path);form=checked(c['form']);checked_text(c['adapter']);validate_plan(c['plan'],form);proof(c);_,paths=bundle(c['bundle']);output=Path(output)
    if c.get('schema')!=SCHEMA or c['split_contract']!=read(ROOT/'data/finance_split_contract.json'):raise ValueError('拆分固定合同已变')
    if recover:
        from edge_runtime import read_marker,browser_lock
        from finance_backend import assert_original_transport
        from assistant_api import atomic_json
        pending=read(paths['state'].parent/'pending_write.json');v=pending.get('verification',{})
        if pending.get('operation')!='correction:http-apply'or v.get('input')!=ref(input_path):raise ValueError('只恢复原拆分意图')
        before=prepare(c,checked(v['prepare']));old=read(v['save_receipt'])
        if sha(old['script'])!=old.get('source_sha256')or Path(old['script']).read_text()!=script(c,'reimbursementSplitApply',{'pending_registered':True,'expected_plan':before}):raise ValueError('原拆分源码漂移')
        if old.get('phase')=='script_completed':saved=old
        else:
            if old.get('phase')in {'not_dispatched','intent_prepared'}or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(old.get('result_marker',''))):raise ValueError('原拆分未派发或缺marker')
            assert_original_transport(old)
            with browser_lock():body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
            if not isinstance(body,dict):raise ValueError('原拆分marker尚未返回完整结果')
            saved={**old,'phase':'script_completed','result':body,'executed_at_utc':datetime.now(timezone.utc).isoformat(),'original_marker_recovery':{**ref(v['save_receipt']),'script_reexecuted':False}}
        recovered=output.parent/'original-split-result.json';frozen(recovered,saved)
        reader={**c,'initialization_verified':True,'initialization':{'input':ref(input_path),'prepare':v['prepare'],'receipt':ref(recovered)}};proof(reader)
        body=browser(reader,'reimbursementSplitInspect',output);prepare(c,body,True);body['original_save_recovery']=ref(recovered);atomic_json(output,body);return 0
    if c['action']in {'inspect','verify'}:
        prepare(c,browser(c,'reimbursementSplitInspect'if c['action']=='verify'else'reimbursementSplitMetadata',output),c['action']=='verify');return 0
    if c['action']!='apply'or not c['execute']:raise ValueError('拆分仅原apply可写')
    if(paths['state'].parent/'pending_write.json').exists():raise ValueError('原财务写入未核，不重发拆分')
    before=output.parent/'native-split-prepare.json';body=prepare(c,browser(c,'reimbursementSplitMetadata',before));checked(c['form']);validate_plan(c['plan'],form)
    begin_write(paths,form,'correction:http-apply',output.parent,{'schema':SCHEMA,'input':ref(input_path),'prepare':ref(before),'save_receipt':str(output)})
    result=browser(c,'reimbursementSplitApply',output,{'pending_registered':True,'expected_plan':body});return 0 if result.get('result',{}).get('ok')is True else 2

def verify_ack(previous,envelope,outcome):
    from correction import verify_group,bindings,validate_plan
    v=previous['verification'];c=checked(v['input']);before=prepare(c,checked(v['prepare']));saved=read(v['save_receipt']);checked_text(c['adapter']);validate_plan(c['plan'],checked(c['form']))
    receipt=envelope.get('original_save_recovery')or ref(v['save_receipt'])
    if envelope.get('original_save_recovery'):
        recovered=checked(receipt);binding=recovered.get('original_marker_recovery',{})
        if saved.get('phase')!='script_completed'and(binding.get('path')!=str(Path(v['save_receipt']).resolve())or binding.get('sha256')!=sha(v['save_receipt'])or binding.get('script_reexecuted')is not False):raise ValueError('拆分恢复未引用原marker')
        if any(recovered.get(k)!=saved.get(k)for k in ['script','source_sha256','tab_id','result_marker']):raise ValueError('拆分恢复身份漂移')
        saved=recovered
    reader={**c,'initialization_verified':True,'initialization':{'input':v['input'],'prepare':v['prepare'],'receipt':receipt}};proof(reader)
    if previous.get('operation')!='correction:http-apply'or previous.get('case_id')!=c['case_id']or outcome!='saved':raise ValueError('拆分须同案完整saved')
    observed=prepare(c,envelope,True)
    if datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(saved['executed_at_utc']):raise ValueError('拆分回读须晚于原保存')
    prefix='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementSplitInspect(';text=Path(envelope['script']).read_text()
    if sha(envelope['script'])!=envelope.get('source_sha256')or not text.startswith(prefix)or not text.endswith(');})()'):raise ValueError('不是原固定拆分HTTP回读')
    actual=json.loads(text[len(prefix):-len(');})()')])
    for k in ['case_id','serial','group','plan','expected_userid','expected_amount','tab_id','form','split_contract','basic_contracts','used_contracts','initialization_verified','initialization']:
        if actual.get(k)!=reader[k]:raise ValueError('拆分独立读取冻结来源变化：'+k)
    for k in ['bill','basic','invoices']:
        if before['original'][k]!=observed['original'][k]:raise ValueError('拆分出现其他单据或原票字段变化：'+k)
    if[r for r in before['original']['categories']if r['group']!=c['group']]!=[r for r in observed['original']['categories']if r['group']!=c['group']]:raise ValueError('拆分改变其他分组')
    old=saved['result']['original_rows'];rows=observed['rows']
    if len(old)!=len(rows)or{r['t.tk_guid']for r in old}!={r['t.tk_guid']for r in rows}:raise ValueError('拆分持久票据集合变化')
    for row in rows:
        prior=next(r for r in old if r['t.tk_guid']==row['t.tk_guid'])
        if{k:v for k,v in prior.items()if k not in {'t.sj_bf','t.sj_amt','t.split_amt','t.select_flag'}}!={k:v for k,v in row.items()if k not in {'t.sj_bf','t.sj_amt','t.split_amt','t.select_flag'}}:raise ValueError('拆分票面/行程/归属字段变化')
        if any('!P!'.join(key)not in c['plan']['native_keys'][c['group']]for key in bindings(row['t.sj_bf'])):raise ValueError('拆分出现未核经费绑定')
    result=verify_group({'ok':True,'serial':c['serial'],'projid':'WF_YBX','grids':[{'id':'gridWF_YBX_1669','records':len(rows),'loading':False,'rows':rows}]},c['plan'],c['group'])
    return{'schema':SCHEMA,'group':c['group'],'independent_http_readback':True,'result':result,'outcome':outcome}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--recover',action='store_true');a=p.parse_args();raise SystemExit(run(a.input,a.output,a.recover))
