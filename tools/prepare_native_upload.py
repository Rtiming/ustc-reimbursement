#!/usr/bin/env python3
"""校验原件和批次身份，生成原生上传控件File输入脚本；不自行上传。"""
import argparse
import base64
import hashlib
import json
import mimetypes
import re
from pathlib import Path


INSTALL_TRACKING_JS = " const tracking=window.__reimbursementUploadTracking={...expected,receipts:[],errors:[],done:false,saveRequested:false,dispatchRequested:true,dispatchAt:new Date().toISOString()};\n history[expected.batchSha256]=tracking;\n const handlers=uploader.__reimbursementOriginalHandlers||(uploader.__reimbursementOriginalHandlers={complete:uploader.settings.onUploadComplete,queueComplete:uploader.settings.onQueueComplete,onError:uploader.settings.onError});\n uploader.settings.onUploadComplete=function(file,response){tracking.receipts.push({name:file.name,response:String(response)});return handlers.complete?.call(this,file,response)};\n uploader.settings.onQueueComplete=function(...args){tracking.done=true;return handlers.queueComplete?.apply(this,args)};\n uploader.settings.onError=function(...args){tracking.errors.push(args.map(x=>typeof x==='string'?x:(x?.name||typeof x)));return handlers.onError?.apply(this,args)};\n"

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def batch_identity(manifest, offset=0, limit=10):
    if offset < 0 or not 0 < limit <= 10:
        raise ValueError('已实测识别批次为1至10个文件；请按批次上传、保存并回读')
    case_id = manifest.get('case_id')
    userid = manifest.get('expected_userid')
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError('manifest缺少case_id')
    if not isinstance(userid, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', userid):
        raise ValueError('写入manifest须有来自本案明确身份回执的expected_userid；不猜默认账号')
    rows = manifest['original_files'][offset:offset+limit]
    if not rows:
        raise ValueError('上传范围为空')
    files = []
    for row in rows:
        path = Path(row['path'])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('原件哈希变化：'+str(path))
        if path.suffix.lower() not in ['.pdf', '.ofd', '.png', '.jpg', '.jpeg']:
            raise ValueError('未验收的上传格式')
        if any(x in path.name for x in ['&', '|', '\n', '\r']):
            raise ValueError('文件名含原生文件列表分隔符，请生成干净命名的上传副本')
        files.append({'name': path.name, 'type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                      'sha256': row['sha256'], 'size': len(data)})
    if len({x['name'] for x in files}) != len(files) or len({x['sha256'] for x in files}) != len(files):
        raise ValueError('上传批次含同名或同SHA文件')
    identity = {'schema': 'ustc-native-upload-batch-v2', 'caseId': case_id, 'expectedUserid': userid,
                'manifestSha256': digest(manifest), 'offset': offset, 'count': len(files), 'files': files}
    identity['batchSha256'] = digest(identity)
    return identity


def prepare(manifest, offset, limit):
    identity = batch_identity(manifest, offset, limit)
    payload = [{**item, 'base64': base64.b64encode(Path(row['path']).read_bytes()).decode()}
               for item, row in zip(identity['files'], manifest['original_files'][offset:offset+limit])]
    # 对第二次读取再次验SHA，防止核验后到生成脚本之间原件被替换。
    for item in payload:
        if hashlib.sha256(base64.b64decode(item['base64'])).hexdigest() != item['sha256']:
            raise ValueError('原件在生成期间改变')
    return ('''(()=>{
 const payload=%s,expected=%s;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw new Error('上传域名或系统变化');
 if($.UC.bindData('#0-userinfo.userid#')!==expected.expectedUserid)throw new Error('上传登录身份不同');
 const id='formWF_YBX_84_t-img',control=$('#'+id),uploader=control.data('uploadifive');
 if(!uploader||uploader.queue.count||uploader.uploads.current||$.F.getInputData(id,'uploadfile'))throw new Error('原生上传控件不存在或队列非空');
 if(!uploader.settings.uploadScript.startsWith('fileSystem_saveAction.action?projid=WF_YBX&'))throw new Error('上传接口变化');
 const previous=window.__reimbursementUploadTracking;
 if(previous&&!previous.saveRequested)throw new Error('上批上传尚未保存或结果未知；先核对，不能覆盖tracking');
 const history=window.__reimbursementUploadBatches||(window.__reimbursementUploadBatches={});
 if(history[expected.batchSha256])throw new Error('此批次已有派发记录；只读核对，不重复上传');
 const transfer=new DataTransfer();
 for(const item of payload){const raw=atob(item.base64),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));if(bytes.length!==item.size)throw new Error('文件传输大小不符');transfer.items.add(new File([bytes],item.name,{type:item.type}));}
 __INSTALL_TRACKING__
 const input=uploader.currentInput.get(0);input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
 return{ok:true,action:'native_invoice_files_queued',batch:expected,count:payload.length,names:payload.map(x=>x.name),requiresUploadAndRecognitionReadback:true};
})()''' % (json.dumps(payload, ensure_ascii=False), json.dumps(identity, ensure_ascii=False))).replace('__INSTALL_TRACKING__', INSTALL_TRACKING_JS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, required=True, help='本案明确的原件manifest，不读取默认旧案')
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    source = prepare(json.loads(args.manifest.read_text()), args.offset, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        output.write(source)
    print(json.dumps({'script': str(args.output), 'offset': args.offset, 'limit': args.limit, 'contains_file_bytes': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
