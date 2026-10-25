#!/usr/bin/env python3
"""从已经成功上传的原件回执构建小批识别脚本，不重新上传字节。"""
import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--receipts', type=Path, required=True)
ap.add_argument('--offset', type=int, required=True)
ap.add_argument('--limit', type=int, default=10)
ap.add_argument('--output', type=Path, required=True)
args = ap.parse_args()
d = json.loads(args.receipts.read_text())['result']
t = d['tracking']
assert t['done'] and not t['errors'] and len(t['files']) == len(t['receipts'])
assert 0 < args.limit <= 10 and args.offset >= 0
files = t['files'][args.offset:args.offset + args.limit]
names = {f['name'] for f in files}
receipts = [r for r in t['receipts'] if r['name'] in names]
values = [s for s in d['fieldValue'].split('|') if s.split('&')[1] in names]
assert len(files) == len(receipts) == len(values) > 0
payload = dict(caseId=t['caseId'], files=files, receipts=receipts, errors=[], done=True)
source = """(()=>{
 const t=PAYLOAD,values=VALUES,id='formWF_YBX_84_t-img',u=$('#'+id).data('uploadifive');
 if(!u||u.queue.count||u.uploads.current||$.F.getInputData(id,'uploadfile'))throw new Error('须为新打开的空上传窗口');
 for(const r of t.receipts){if(!/^[a-fA-F0-9]{32}$/.test(r.response)||!values.some(s=>s.startsWith(r.response+'&'+r.name+'&')))throw new Error('原件回执不匹配');}
 $('#'+id).setFileNames(values.join('|'));$.F.getDataToContext('84');window.__reimbursementUploadTracking=t;
 return{ok:true,action:'stage_uploaded_originals_from_verified_receipts',count:t.files.length,utf8Bytes:new TextEncoder().encode(values.join('|')).length,files:t.files};
})()""".replace('PAYLOAD', json.dumps(payload, ensure_ascii=False)).replace('VALUES', json.dumps(values, ensure_ascii=False))
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(source)
print(json.dumps(dict(output=str(args.output), count=len(files)), ensure_ascii=False))
