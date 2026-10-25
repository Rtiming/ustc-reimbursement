#!/usr/bin/env python3
"""原生上传分阶段工具：文件构造、赋值、一次派发分别留证；无私有HTTP。"""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from prepare_native_upload import batch_identity, INSTALL_TRACKING_JS

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ['stage_files', 'assign_files', 'dispatch', 'status']

COMMON = r'''
 const expected=__EXPECTED__,action=__ACTION__;
 const identity=()=>$.UC.bindData('#0-userinfo.userid#');
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||identity()!==expected.expectedUserid)throw Error('分阶段上传身份或系统不同');
 const id='formWF_YBX_84_t-img',control=$('#'+id),uploader=control.data('uploadifive');
 const input=uploader?.currentInput?.get(0),pageContext=String($.page.currPage);
 const endpointVerified=typeof uploader?.settings?.uploadScript==='string'&&uploader.settings.uploadScript.startsWith('fileSystem_saveAction.action?projid=WF_YBX&');
 if(action!=='status'&&!endpointVerified)throw Error('原生上传接口变化');
 const existing=window.__reimbursementNativeUploadStages;
 const stage=existing?.[expected.batchSha256];
 const sameObjects=s=>!!s&&document.getElementById(id)===s.control&&$('#'+id).data('uploadifive')===s.uploader&&s.uploader.currentInput.get(0)===s.input&&String($.page.currPage)===s.pageContext;
 const sameInput=s=>sameObjects(s)&&s.nativeUploadScript===s.uploader.settings.uploadScript;
 const checkContext=s=>{if(identity()!==expected.expectedUserid||!sameInput(s))throw Error('上传input/uploader/页面或身份已变化');};
 const raw=()=>$.F.getInputData(id,'uploadfile');
 const empty=()=>{if(!uploader||uploader.queue.count||uploader.uploads.current||uploader.uploads.errors||raw())throw Error('原生上传队列或文件列表非空');};
 const metadata=f=>({name:f.name,size:f.size,type:f.type});
 const sameFiles=s=>input?.files?.length===s.filesNative.length&&[...input.files].every((f,i)=>f===s.filesNative[i]&&f.name===expected.files[i].name&&f.size===expected.files[i].size&&f.type===expected.files[i].type);
 const serialTracking=t=>t?{schema:t.schema,caseId:t.caseId,expectedUserid:t.expectedUserid,manifestSha256:t.manifestSha256,batchSha256:t.batchSha256,offset:t.offset,count:t.count,files:t.files,done:t.done,receipts:t.receipts,errors:t.errors,dispatchRequested:t.dispatchRequested,dispatchAt:t.dispatchAt,saveRequested:t.saveRequested,saveRequestedAt:t.saveRequestedAt}:null;
 const observe=s=>({ok:true,action,case_id:expected.caseId,batch:expected,identity:{userid:identity()},stage_found:!!s,endpoint_verified:endpointVerified,endpoint_bound_on_assign:!!s?.endpointBoundOnAssign,phase:s?.phase||'absent',same_input:s?sameInput(s):null,dispatch_requested:s?.dispatchRequested||false,dispatch_marker:s?.dispatchMarker||null,file_hashes_verified:s?.fileHashesVerified||false,staged_files:s?.filesNative?.map(metadata)||[],input_files:input?.files?[...input.files].map(metadata):[],upload_tracking:serialTracking(window.__reimbursementUploadTracking),native_state:uploader?{queueCount:uploader.queue.count,currentUploads:uploader.uploads.current,uploadErrors:uploader.uploads.errors,nativeFileList:raw()}:null,system_write:false,browser_local_changed:false,server_save_performed:false});
'''


def build_script(action, manifest, offset=0, limit=10):
    if action not in ACTIONS:
        raise ValueError('未知分阶段上传动作')
    expected = batch_identity(manifest, offset, limit)
    common = COMMON.replace('__EXPECTED__', json.dumps(expected, ensure_ascii=False)).replace('__ACTION__', json.dumps(action))
    if action == 'status':
        return '(async()=>{'+common+'return observe(stage);})()'
    if action == 'stage_files':
        payload = []
        for row in manifest['original_files'][offset:offset+limit]:
            data = Path(row['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('原件在生成期间改变')
            payload.append(base64.b64encode(data).decode())
        body = r'''
 empty();
 if(!input||input.type!=='file'||!input.isConnected||input.files.length)throw Error('原生file input未就绪或非空');
 if(stage)throw Error('此批已有staging意图；只读status，不重复构造');
 const old=window.__reimbursementUploadTracking;
 if(old&&!old.saveRequested)throw Error('上批上传未保存或未知，不能构造新批');
 const stages=window.__reimbursementNativeUploadStages||(window.__reimbursementNativeUploadStages={});
 const s=stages[expected.batchSha256]={batch:expected,phase:'constructing',input,uploader,control:document.getElementById(id),pageContext,nativeUploadScript:uploader.settings.uploadScript,filesNative:[],transfer:null,fileHashesVerified:false,dispatchRequested:false};
 const encoded=__PAYLOAD__,transfer=new DataTransfer();
 for(let i=0;i<encoded.length;i++){
   s.phase='construct_file_'+i;
   const bytes=Uint8Array.from(atob(encoded[i]),c=>c.charCodeAt(0)),f=expected.files[i];
   if(bytes.length!==f.size)throw Error('构造字节长度不同');
   const nativeFile=new File([bytes],f.name,{type:f.type});
   s.phase='hash_file_'+i;
   const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await nativeFile.arrayBuffer())),x=>x.toString(16).padStart(2,'0')).join('');
   if(hash!==f.sha256)throw Error('浏览器File SHA与原件不符');
   checkContext(s);transfer.items.add(nativeFile);s.filesNative.push(nativeFile);
 }
 s.transfer=transfer;s.fileHashesVerified=true;s.phase='files_staged';
 return{...observe(s),browser_local_changed:true};
'''.replace('__PAYLOAD__', json.dumps(payload))
    elif action == 'assign_files':
        body = r'''
 if(!stage||stage.phase!=='files_staged'||!stage.fileHashesVerified||stage.dispatchRequested)throw Error('必须先成功stage_files，不能重复赋值');
 if(stage.nativeUploadScript===undefined){
   if(!sameObjects(stage)||!endpointVerified||stage.batch?.batchSha256!==expected.batchSha256||stage.batch?.expectedUserid!==expected.expectedUserid||stage.batch?.caseId!==expected.caseId)throw Error('旧staging不能安全绑定原生接口');
   stage.nativeUploadScript=uploader.settings.uploadScript;stage.endpointBoundOnAssign=true;
 }
 checkContext(stage);empty();
 if(input.files.length)throw Error('赋值前原生input非空');
 stage.phase='assigning_files';
 input.files=stage.transfer.files;
 if(!sameFiles(stage))throw Error('赋值后FileList不是已验SHA的本批对象');
 stage.phase='files_assigned';
 return{...observe(stage),browser_local_changed:true};
'''
    else:
        body = r'''
 if(!stage||stage.phase!=='files_assigned'||!stage.fileHashesVerified||stage.dispatchRequested)throw Error('必须先成功assign_files；派发未知或已发出不能重发');
 checkContext(stage);empty();
 if(!sameFiles(stage))throw Error('派发前FileList不是本批对象');
 const previous=window.__reimbursementUploadTracking;
 if(previous&&!previous.saveRequested)throw Error('上批派发尚未核对');
 const history=window.__reimbursementUploadBatches||(window.__reimbursementUploadBatches={});
 if(history[expected.batchSha256])throw Error('此批已有派发记录，禁止重发');
 // 先建立不可重发意图；宏任务回调异常或AppleEvent未知均保留。
 const channel=new MessageChannel();
 stage.dispatchRequested=true;stage.dispatchMarker=__DISPATCH_MARKER__;stage.phase='dispatch_requested';
 __INSTALL_TRACKING__
 return await new Promise((resolve,reject)=>{
   channel.port1.onmessage=()=>{
     channel.port1.onmessage=null;channel.port1.close();channel.port2.close();
     try{
       checkContext(stage);empty();if(!sameFiles(stage))throw Error('宏任务派发前FileList已变化');
       stage.phase='dispatch_event_entered';
       input.dispatchEvent(new Event('change',{bubbles:true}));
       stage.phase='dispatch_event_returned';
       resolve({...observe(stage),system_write:true,browser_local_changed:true,server_save_performed:false,requiresUploadAndRecognitionReadback:true});
     }catch(error){stage.phase='dispatch_error_unknown';stage.error=String(error.message);reject(error);}
   };
   try{channel.port2.postMessage(null);}catch(error){stage.phase='dispatch_post_error_unknown';channel.port1.close();channel.port2.close();reject(error);}
 });
'''.replace('__INSTALL_TRACKING__', INSTALL_TRACKING_JS)
        # batch派发标记稳定于同一manifest/offset；页面拒绝第二次派发。
        body = body.replace('__DISPATCH_MARKER__', json.dumps('native-dispatch-'+expected['batchSha256']))
    return '(async()=>{'+common+body+'})()'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=ACTIONS)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--tab-id', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    if args.output.exists():
        raise ValueError('本阶段已有回执，禁止覆盖重放；用新status回读原阶段')
    source = build_script(args.action, json.loads(args.manifest.read_text()), args.offset, args.limit)
    script = args.output.with_suffix('.js')
    script.parent.mkdir(parents=True, exist_ok=True)
    with script.open('x') as handle:
        handle.write(source)
    script.chmod(0o600)
    if not args.execute:
        print(json.dumps({'script':str(script),'executed':False,'action':args.action,'system_write':False},ensure_ascii=False))
        return 0
    kind = 'write' if args.action == 'dispatch' else 'read' if args.action == 'status' else 'browser-local'
    command = [sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(args.output),'--tab-id',args.tab_id,'--operation-kind',kind,'--quiet']
    return subprocess.run(command).returncode


if __name__ == '__main__':
    raise SystemExit(main())
