"""Release only the local staging registry of an independently acknowledged upload."""
import json,re
from pathlib import Path
from finance_native_create import read,ref,sha,checked_text,frozen

ROOT=Path(__file__).resolve().parents[1]

def command(service,request,folder):
    p=request['parameters'];cid=request['case_id']
    if set(p)!={'action','tab_id','job_id'}or p['action']!='upload-release'or request['execute']is not True:raise ValueError('上传暂存释放须原任务、标签及明确执行，不上传或保存')
    origin=service.job_folder(cid,p['job_id']);prior=read(origin/'request.json');c=read(origin/'native-upload-input.json');ack=read(origin/'operator_readback_ack.json')
    _,paths=service.resolve(cid);last=read(paths['state']).get('last_native_upload_batch',{})
    if(prior.get('operation')!='invoice'or prior['parameters'].get('action')!='upload'or c['case_id']!=cid or c['tab_id']!=p['tab_id']or
       ack.get('operation')!='invoice:http-upload'or ack.get('outcome')!='saved'or last.get('token')!=c['token']or last.get('independent_http_readback')is not True or
       last.get('batch')!=c['batch']or ack.get('verification',{}).get('input')!=str(origin/'native-upload-input.json')or
       ack['verification'].get('input_sha256')!=sha(origin/'native-upload-input.json')):raise ValueError('只释放本案同账号、已独立回读完成的原上传暂存')
    adapter=origin/'native-upload-adapter.js'
    if sha(adapter)!=c['adapter_sha256']:raise ValueError('原上传源码变化')
    code=adapter.read_text();config={**c,'action':'finish'}
    script=folder/'upload-local-release.js'
    wrapper='const c='+json.dumps(config,ensure_ascii=False)+';const h=createReimbursementHttp(c.expected_userid,{prefix:"UPLOAD_RELEASE",requests:[],timeout:15000,allowed:[]});h.guard();const current=Object.getOwnPropertyDescriptor(window,"__ustcInvoiceHttpUploadV1")?.value;if(!current)return{ok:true,case_id:c.case_id,action:"upload-release",business_transport:"browser_local",system_write:false,browser_local_released:true,already_released:true};const old=await reimbursementUploadApi(c);return{ok:true,case_id:c.case_id,action:"upload-release",business_transport:"browser_local",system_write:false,browser_local_released:true,original_token:old.token};'
    script.write_text('(async()=>{'+code+';'+wrapper+'})()');script.chmod(0o600)
    frozen(folder/'upload-release-source.json',{'upload_input':ref(origin/'native-upload-input.json'),'ack':ref(origin/'operator_readback_ack.json'),'adapter':ref(adapter),'financial_write':False})
    return[service.python,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(folder/'output.json'),'--tab-id',p['tab_id'],'--operation-kind','browser-local','--quiet']
