"""Continue only a saved upload's missing native recognition; never upload/save again."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def source(recovery=False):
    from finance_http import source as compose
    return compose('retry')+'\n'+(ROOT/'tools/finance_upload_recognition_api.js').read_text()

def timeout_proofs(job,files):
    proofs=[]
    for i,f in enumerate(files):
        path=job/f'recognition-{i}.json';script=path.with_suffix('.js');r=read(path)
        if r.get('phase')!='script_completed' or r.get('source_sha256')!=sha(script) or r.get('result',{}).get('ok')is not False or r['result'].get('error')!='UPLOAD_RECOGNITION_REQUEST_UNKNOWN:thirdPartyInterface.action:timeout:http=0':raise ValueError('只接原脚本已结束且明确为客户端识别超时的记录；其他未知先恢复原marker')
        proofs.append({'filecode':f['filecode'],'path':str(path),'sha256':sha(path),'script_sha256':sha(script)})
    return proofs

def original(paths,input_path):
    from finance_native_upload import SCHEMA,upload_sources
    from reimburse import freeze_upload_batch
    p=read(paths['state'].parent/'pending_write.json');v=p.get('verification',{});c=read(input_path)
    if p.get('operation')!='invoice:http-upload' or v.get('schema')!=SCHEMA or v.get('input')!=str(input_path) or v.get('input_sha256')!=sha(input_path):raise ValueError('须保留原HTTP上传pending和源输入')
    if sha(paths['form'])!=v.get('form_sha256') or sha(paths['invoices'])!=c['manifest_sha256'] or sha(v['prepare'])!=v['prepare_sha256'] or sha(input_path.parent/'native-upload-adapter.js')!=c['adapter_sha256']:raise ValueError('原上传证据变化')
    current=freeze_upload_batch(paths,c['case_id'],c['offset'],c['batch']['count'],expected_userid=c['expected_userid'])
    if current['batch']!=c['batch'] or upload_sources(paths,current)!=c['sources']:raise ValueError('原上传来源变化')
    saved=read(v['save_receipt']);b=saved.get('result',{})
    if saved.get('phase')!='script_completed' or b.get('ok')is not True or b.get('phase')!='save_accepted' or b.get('save_dispatched')is not True or b.get('batch')!=c['batch'] or len(b.get('files',[]))!=len(c['sources']):raise ValueError('原保存尚未明确完成，只恢复原marker')
    files=[]
    for f,s in zip(b['files'],c['sources']):
        if f.get('name')!=s['name'] or f.get('sha256')!=s['sha256'] or f.get('phase')!='upload_received' or not re.fullmatch('[a-fA-F0-9]{32}',f.get('filecode','')) or Path(s['name']).suffix.lower()not in {'.pdf','.ofd'}:raise ValueError('只接原保存PDF/OFD的精确filecode和SHA')
        files.append({**s,'filecode':f['filecode']})
    return c,files

def command(service,request,folder):
    from finance_native_upload import frozen
    p=request['parameters'];cid=request['case_id']
    if set(p)!={'action','tab_id','job_id'} or p['action']not in {'upload-recognize','upload-recognize-resume','upload-recover-recognition'} or not re.fullmatch(r'\d+',str(p['tab_id'])):raise ValueError('识别接续只引用原job和当前已核tab')
    job=service.job_folder(cid,p['job_id']);prior=read(job/'request.json');state=service.result(cid,p['job_id'])
    if p['action']=='upload-recognize-resume':
        if prior['operation']!='invoice' or prior['parameters'].get('action')!='upload-recognize' or prior['execute']is not True or state['status']in {'queued','running'}:raise ValueError('只接续已停止的原识别任务')
        path=job/'recognition-input.json';c=read(path)
        if c['case_id']!=cid or c['tab_id']!=p['tab_id'] or not request['execute']:raise ValueError('接续须原case/tab及execute；已准备目标永不重发')
        frozen(folder/'recognition-recovery.json',{'input':str(path),'sha256':sha(path),'no_replay':True})
        return [service.python,str(ROOT/'tools/finance_upload_recognition.py'),'--input',str(path),'--output',str(folder/'output.json'),'--resume']
    recovery=p['action']=='upload-recover-recognition'
    if prior['case_id']!=cid or prior['operation']!='invoice' or prior['parameters'].get('action')!=('upload-recognize'if recovery else'upload') or prior['execute']is not True or state['status']in {'queued','running'}:raise ValueError('须为本案已停止的原上传或原识别任务')
    bundle,paths=service.resolve(cid)
    if recovery:
        prior_input=read(job/'recognition-input.json');upload_job=service.job_folder(cid,prior_input['original_upload_job_id']);origin=upload_job/'native-upload-input.json'
        if prior_input['original_input']!=str(origin) or prior_input['original_input_sha256']!=sha(origin) or sha(job/'recognition-adapter.js')!=prior_input['adapter_sha256']:raise ValueError('原识别输入或冻结源码变化')
    else:origin=job/'native-upload-input.json'
    c,files=original(paths,origin)
    if os.environ.get('REIMBURSEMENT_EXPECTED_USERID')!=c['expected_userid']:raise ValueError('当前明确账号与原上传不同')
    config={'case_id':cid,'bundle':str(bundle),'tab_id':p['tab_id'],'expected_userid':c['expected_userid'],
        'original_input':str(origin),'original_input_sha256':sha(origin),'original_upload_job_id':origin.parent.name,
        'files':files,'execute':request['execute'],'contract':read(ROOT/'data/finance_upload_recognition_contract.json'),
        'recognition_protocol':'native_guarded_ocr_v1','retry_contract':read(ROOT/'data/finance_retry_contract.json'),
        'inventory_contracts':{k:read(ROOT/'data/finance_business_contracts.json')['domains'][k]for k in ['invoices','recognition']}}
    if recovery:config.update(recovery=True,failed_receipts=timeout_proofs(job,files),retry_contract=read(ROOT/'data/finance_retry_contract.json'))
    adapter=folder/'recognition-adapter.js';adapter.write_text(source(recovery));adapter.chmod(0o600);config['adapter_sha256']=sha(adapter)
    path=folder/'recognition-input.json';frozen(path,config)
    return [service.python,str(ROOT/'tools/finance_upload_recognition.py'),'--input',str(path),'--output',str(folder/'output.json')]

def run(path,output,runtime=subprocess.run,*,resume=False):
    from finance_native_upload import frozen
    from operator_support import bundle
    c=read(path);_,paths=bundle(c['bundle']);origin,files=original(paths,Path(c['original_input']))
    code=(path.parent/'recognition-adapter.js').read_text()
    recovery=c.get('recovery')is True
    if sha(c['original_input'])!=c['original_input_sha256'] or sha(path.parent/'recognition-adapter.js')!=c['adapter_sha256'] or code!=source(recovery) or files!=c['files'] or c['contract']!=read(ROOT/'data/finance_upload_recognition_contract.json'):raise ValueError('识别接续证据或实现变化')
    if recovery:
        if c['retry_contract']!=read(ROOT/'data/finance_retry_contract.json') or not c.get('failed_receipts'):raise ValueError('原生重识别合同缺失')
        for proof in c['failed_receipts']:
            if sha(proof['path'])!=proof['sha256'] or sha(Path(proof['path']).with_suffix('.js'))!=proof['script_sha256']:raise ValueError('原超时证据变化')
    if not c['execute']:
        frozen(output,{'ok':True,'state':'recognition_continuation_preview','files':files,'system_write':False});return 0
    # This original-job ledger prevents a different request/page from repeating recognition.
    intent=Path(c['original_input']).parent/('recognition-native-recovery.json'if recovery else'recognition-continuation.json')
    frozen(intent,{'input':str(path),'sha256':sha(path),'original_upload_job_id':c['original_upload_job_id'],'no_replay':True})
    results=[]
    for i,f in enumerate(files):
        script=path.parent/f'recognition-{i}.js';receipt=script.with_suffix('.json')
        if script.exists() or receipt.exists():
            if not resume:raise ValueError('已准备的识别任务不再派发；读取原marker及独立库存')
            if not script.exists():raise ValueError('原识别子脚本缺失')
            if receipt.exists():
                previous=read(receipt)
                if previous.get('source_sha256')!=sha(script):raise ValueError('原识别子脚本SHA变化')
                results.append({'filecode':f['filecode'],'original_receipt':str(receipt),'replayed':False,
                    'state':'completed'if previous.get('phase')=='script_completed'and previous.get('result',{}).get('ok')is True else'unknown_requires_inventory'})
            else:results.append({'filecode':f['filecode'],'state':'prepared_not_replayed','replayed':False})
            continue
        config={**c,'file':f,'pending_registered':True,'original_save_accepted':True}
        method='reimbursementRecoverUploadRecognition'if recovery else'reimbursementUploadRecognition'
        script.write_text('(async()=>{\n'+code+'\nreturn await '+method+'('+json.dumps(config,ensure_ascii=False)+');})()');script.chmod(0o600)
        result=runtime([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(receipt),'--tab-id',c['tab_id'],'--operation-kind','write','--timeout','60','--quiet'])
        if result.returncode:
            if receipt.is_file():
                original_receipt=read(receipt)
                if original_receipt.get('source_sha256')==sha(script) and original_receipt.get('phase')=='await_original_marker':
                    from finance_backend import assert_original_transport
                    from edge_runtime import read_marker
                    assert_original_transport(original_receipt)
                    recovered=read_marker(original_receipt['result_marker'],c['tab_id'],timeout=60)
                    frozen(path.parent/f'recognition-{i}-original-marker.json',{'original_receipt_sha256':sha(receipt),'result':recovered,'script_reexecuted':False})
                    if isinstance(recovered,dict)and recovered.get('ok')is True:
                        results.append(recovered);continue
            results.append({'filecode':f['filecode'],'state':'unknown_requires_inventory','receipt':str(receipt),'replayed':False});continue
        r=read(receipt)
        if r.get('phase')!='script_completed' or r.get('result',{}).get('ok')is not True:raise ValueError('原识别回执未确认')
        results.append(r['result'])
    frozen(output,{'ok':True,'state':'recognition_requests_pending_independent_inventory','case_id':c['case_id'],'action':'upload-recognize','results':results,'system_write':True,'business_transport':'native_http','requires_independent_readback':True,'financial_claim_created':False});return 0

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--resume',action='store_true');a=ap.parse_args()
    raise SystemExit(run(a.input,a.output,resume=a.resume))
