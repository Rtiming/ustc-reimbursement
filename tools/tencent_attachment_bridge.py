"""Private fixed Tencent attachment adapter. No CLI or caller-supplied JavaScript."""
import hashlib
import json
import math
import re
import subprocess
import time
import uuid


class TencentError(RuntimeError):
    def __init__(self, code):
        # Callers pass fixed codes only; never exception text, source or signed URLs.
        super().__init__(code)
        self.code = code


def native_source(plan, operation, item):
    """Build only observed SDK operations against a validated allowlist."""
    if operation not in ('setup', 'link', 'close') or item not in plan['files']:
        raise TencentError('invalid_native_operation')
    config = {k: plan[k] for k in ('docId', 'hostId')}
    config.update(fileId=item['file_id'], name=item['filename'], size=item['expected_size'],
                  allowed=[x['file_id'] for x in plan['files']])
    guard = r'''
const c=CONFIG;
if(location.origin!=='https://docs.qq.com'||location.pathname!=='/sheet/'+c.docId)throw Error('wrong_document');
const p=window.SpreadsheetApp?.sheetStatus;
const host=window.basicClientVars?.docInfo?.padInfo?.padId;
if(!p||String(host)!==c.hostId||!c.allowed.includes(c.fileId))throw Error('document_identity_missing');
if(![true,1].includes(p.canCopy)||![true,1].includes(p.canExportDoc))throw Error('document_permission_denied');
const documentEvidence={loadedSheet:true,hostId:c.hostId,canCopy:p.canCopy,canExportDoc:p.canExportDoc,
 readEvidence:'loaded_sheet_and_bound_host',independentCanRead:null};
'''.replace('CONFIG', json.dumps(config, ensure_ascii=False))
    if operation == 'setup':
        body = r'''
const service=window.__ARK_EXTENSION_SERVICE__;
if(!service||typeof service.getExtension!=='function')throw Error('native_sdk_missing');
const ext=await service.getExtension('docs-component-attachment');
if(!ext||typeof ext.showFileCarousel!=='function')throw Error('native_sdk_missing');
const result=await ext.showFileCarousel({files:[{type:'drive-file',fileId:c.fileId,hostId:c.hostId}],currentIndex:0,zIndex:10000});
let f=result?.fileCarouselStore?.currentFileStore;
const deadline=Date.now()+10000;
while(!f&&Date.now()<deadline){await new Promise(resolve=>setTimeout(resolve,Math.min(200,Math.max(0,deadline-Date.now()))));f=result?.fileCarouselStore?.currentFileStore;}
if(!f)throw Error('native_store_timeout');
if(f.type!=='drive'||typeof f.isDriveFileStore!=='function'||!f.isDriveFileStore())throw Error('unsupported_native_store');
window.__reimbursementTencentAttachmentStore={FileStore:f.constructor,hostId:c.hostId,docId:c.docId,preview:result};
return {ready:true,document:documentEvidence,browserLocalPreview:true};
'''
    elif operation == 'link':
        body = r'''
const d=window.__reimbursementTencentAttachmentStore;
if(!d||d.docId!==c.docId||d.hostId!==c.hostId)throw Error('native_setup_missing');
const f=new d.FileStore({fileId:c.fileId,hostId:c.hostId,downloadInfo:{canDownload:false}});
await f.prepareBasicInfo();
const rsp=f.fileInfoRspData;
if(rsp?.ret!==0||rsp?.retcode!==0)throw Error('native_file_info_failed');
const info=rsp.result,flags=info?.privilege?.attribute;
if(flags?.canRead!==true||flags?.canCopy!==true||flags?.canExport!==true)throw Error('attachment_permission_denied');
const meta=info.fileMeta,nativeName=meta?.name,nativeExt=meta?.ext,nativeSize=meta?.size;
if(typeof nativeName!=='string'||typeof nativeExt!=='string'||!/^[A-Za-z0-9]+$/.test(nativeExt))throw Error('attachment_metadata_mismatch');
const ext=nativeExt.toLowerCase(),dot=c.name.lastIndexOf('.'),expectedBase=c.name.slice(0,dot),expectedExt=c.name.slice(dot+1).toLowerCase();
const base=nativeName.toLowerCase().endsWith('.'+ext)?nativeName.slice(0,-ext.length-1):nativeName;
const size=typeof nativeSize==='number'?nativeSize:(typeof nativeSize==='string'&&/^[0-9]+$/.test(nativeSize)?Number(nativeSize):NaN);
if(base!==expectedBase||ext!==expectedExt||!Number.isSafeInteger(size)||size!==c.size)throw Error('attachment_metadata_mismatch');
await f.prepareDownloadInfo();
const url=f.downloadInfo?.url||f.downloadInfo?.downloadUrl;
if(typeof url!=='string'||!url)throw Error('native_download_url_missing');
return {file_id:c.fileId,name:c.name,size,url,
 native_metadata:{name:nativeName,ext:nativeExt,size:nativeSize},
 permission:{canRead:true,canCopy:true,canExport:true},document:documentEvidence};
'''
    else:
        body = r'''
const d=window.__reimbursementTencentAttachmentStore;
if(!d||d.docId!==c.docId||d.hostId!==c.hostId)throw Error('native_setup_missing');
if(d.previewClosed===true)return {closed:true,alreadyClosed:true,document:documentEvidence};
const store=d.preview?.fileCarouselStore;
if(!store||typeof store.onClose!=='function')throw Error('native_close_unavailable');
await store.onClose(true);
d.previewClosed=true;
return {closed:true,document:documentEvidence};
'''
    return '(async()=>{' + guard + body + '})()'


NATIVE_ERRORS = ['wrong_document', 'document_identity_missing', 'document_permission_denied',
                 'native_sdk_missing', 'native_store_timeout', 'unsupported_native_store',
                 'native_setup_missing', 'native_file_info_failed', 'attachment_permission_denied',
                 'attachment_metadata_mismatch', 'native_download_url_missing', 'native_close_unavailable']


class TencentBridge:
    def __init__(self, plan, tab_id):
        self.plan = plan
        self.tab_id = tab_id

    def _evaluate(self, source, timeout=8):
        if not 0 < timeout <= 8:
            raise TencentError('invalid_bridge_budget')
        target = 'https://docs.qq.com/sheet/' + self.plan['docId']
        guard = ("(()=>{if(location.origin!=='https://docs.qq.com'||location.pathname!==" +
                 json.dumps('/sheet/' + self.plan['docId']) + ")return 'wrong_document';return (" + source + ');})()')
        apple = '''if application "Microsoft Edge" is not running then error "EdgeNotRunning"
with timeout of %d seconds
tell application "Microsoft Edge"
repeat with w in windows
repeat with t in tabs of w
if (id of t as text) is %s then
set u to URL of t
if not (u is %s or u starts with %s or u starts with %s) then error "Wrong Tencent sheet" number -27002
return execute t javascript %s
end if
end repeat
end repeat
error "Tab missing" number -27001
end tell
end timeout''' % (max(1, math.floor(timeout)), json.dumps(self.tab_id), json.dumps(target),
                 json.dumps(target + '?'), json.dumps(target + '#'), json.dumps(guard, ensure_ascii=False))
        try:
            result = subprocess.run(['/usr/bin/osascript', '-'], input=apple, capture_output=True,
                                    text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TencentError('bridge_timeout') from None
        except OSError:
            raise TencentError('bridge_unavailable') from None
        if result.returncode:
            # Do not expose stderr: AppleScript can echo the source or transient URL.
            raise TencentError('bridge_error')
        if result.stdout.strip() == 'wrong_document':
            raise TencentError('wrong_document')
        return result.stdout.strip()

    def perform(self, operation, item, intent, save, timeout=30):
        source = native_source(self.plan, operation, item)
        digest = hashlib.sha256(source.encode()).hexdigest()
        if intent:
            if intent.get('source_sha256') != digest or intent.get('tab_id') != self.tab_id:
                raise TencentError('original_intent_mismatch')
        else:
            intent.update(marker='reimbursement-tencent-' + uuid.uuid4().hex,
                          source_sha256=digest, tab_id=self.tab_id, phase='dispatch_unknown')
            save()  # Durable intent always precedes the one and only injection.
            marker = json.dumps(intent['marker'])
            main = ("Promise.resolve().then(()=>" + source + ").then(result=>{document.getElementById(" +
                    marker + ").textContent=JSON.stringify({ok:true,result});}).catch(e=>{const code=" +
                    json.dumps(NATIVE_ERRORS) + ".includes(e?.message)?e.message:'native_error';"
                    "document.getElementById(" + marker + ").textContent=JSON.stringify({ok:false,error_code:code});});")
            injection = ("(()=>{if(document.getElementById(" + marker + "))return 'existing';"
                         "const out=document.createElement('script');out.type='application/json';out.id=" + marker +
                         ";out.textContent='pending';document.head.appendChild(out);const s=document.createElement('script');"
                         "const authorized=[...document.scripts].find(x=>x.nonce);if(authorized)s.nonce=authorized.nonce;"
                         "s.textContent=" + json.dumps(main, ensure_ascii=False) +
                         ";document.head.appendChild(s);s.remove();return 'started';})()")
            if self._evaluate(injection) not in ('started', 'existing'):
                raise TencentError('dispatch_unconfirmed')
        deadline = time.monotonic() + timeout
        marker = json.dumps(intent['marker'])
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TencentError('original_marker_pending')
            raw = self._evaluate('document.getElementById(' + marker + ")?.textContent || 'missing'",
                                 timeout=min(8, remaining))
            if time.monotonic() >= deadline:
                raise TencentError('original_marker_pending')
            if raw == 'missing':
                raise TencentError('original_marker_missing')
            if raw not in ('pending', '', 'missing value'):
                try:
                    envelope = json.loads(raw)
                except ValueError:
                    raise TencentError('invalid_native_receipt') from None
                if not isinstance(envelope, dict) or type(envelope.get('ok')) is not bool:
                    raise TencentError('invalid_native_receipt')
                if not envelope['ok']:
                    code = envelope.get('error_code')
                    raise TencentError(code if code in NATIVE_ERRORS else 'native_error')
                if not isinstance(envelope.get('result'), dict):
                    raise TencentError('invalid_native_receipt')
                return envelope['result']  # URL is transient; caller must not serialize this object.
            time.sleep(min(.25, max(0, deadline - time.monotonic())))

    def confirm_known_metadata_failure(self, intent):
        if (not isinstance(intent, dict) or intent.get('tab_id') != self.tab_id
                or intent.get('phase') != 'dispatch_unknown'
                or not re.fullmatch(r'reimbursement-tencent-[0-9a-f]{32}', str(intent.get('marker', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(intent.get('source_sha256', '')))):
            raise TencentError('known_metadata_retry_not_proven')
        raw = self._evaluate('document.getElementById(' + json.dumps(intent['marker']) + ")?.textContent || 'missing'")
        try:
            result = json.loads(raw)
        except ValueError:
            raise TencentError('known_metadata_retry_not_proven') from None
        if (not isinstance(result, dict) or type(result.get('ok')) is not bool
                or result != {'ok': False, 'error_code': 'attachment_metadata_mismatch'}):
            raise TencentError('known_metadata_retry_not_proven')
        return result

    def cleanup(self, intent):
        # The marker can contain a transient signed URL; remove only after local durable success.
        marker = json.dumps(intent['marker'])
        self._evaluate("(()=>{document.getElementById(" + marker + ")?.remove();return 'cleaned'})()")
