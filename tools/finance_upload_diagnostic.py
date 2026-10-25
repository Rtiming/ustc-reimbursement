#!/usr/bin/env python3
"""只读上传控件及事件定义，用于定位派发阻塞；不选文件、不触发上传。"""
import argparse
import subprocess
import sys
from pathlib import Path

SOURCE = r'''(()=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('wrong finance project');
 const id='formWF_YBX_84_t-img',u=$('#'+id).data('uploadifive');
 if(!u)throw Error('upload control not loaded');
 const input=u.currentInput?.get(0),fn=x=>typeof x==='function'?String(x).slice(0,50000):null;
 const events=input&&$._data?$._data(input,'events'):null;
 return {ok:true,system_write:false,selection_write:false,identity:$.UC.bindData('#0-userinfo.userid#'),
  keys:Object.keys(u),settingKeys:Object.keys(u.settings||{}),
  constructors:{File:fn(globalThis.File),DataTransfer:fn(globalThis.DataTransfer),Event:fn(globalThis.Event),from:fn(Uint8Array.from),filesSetter:fn(Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'files')?.set)},
  settings:Object.fromEntries(Object.entries(u.settings||{}).filter(([k,v])=>typeof v==='function'||['auto','multi','fileSizeLimit','queueSizeLimit','uploadLimit','fileType','fileTypeDesc','checkScript','uploadScript'].includes(k)).map(([k,v])=>[k,typeof v==='function'?fn(v):typeof v==='string'?v.split('?')[0]:v])),
  input:input?{id:input.id,type:input.type,multiple:input.multiple,disabled:input.disabled,accept:input.accept}:null,
  changeHandlers:(events?.change||[]).map(e=>({namespace:e.namespace,handler:fn(e.handler)})),
  methods:Object.fromEntries(['addQueueItem','uploadFile','checkExists','createInput','filesToUpload','queueComplete','uploadComplete'].map(k=>[k,fn(u[k])])),
  plugin:fn($.fn.uploadifive),queue:{count:u.queue?.count,queued:u.queue?.queued},uploads:u.uploads,
  tracking:window.__reimbursementUploadTracking?{caseId:window.__reimbursementUploadTracking.caseId,done:window.__reimbursementUploadTracking.done,saveRequested:window.__reimbursementUploadTracking.saveRequested}:null};
})()'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tab-id',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();s=a.output.with_suffix('.js')
    if s.exists() or a.output.exists():p.error('保留原诊断，不覆盖')
    s.parent.mkdir(parents=True,exist_ok=True);s.write_text(SOURCE)
    return subprocess.run([sys.executable,str(Path(__file__).with_name('edge_runtime.py')),str(s),
        '--tab-id',a.tab_id,'--output',str(a.output),'--operation-kind','read','--quiet']).returncode


if __name__=='__main__':raise SystemExit(main())
