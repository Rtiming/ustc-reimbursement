#!/usr/bin/env python3
"""串行调用已验证原生工具上传查验；任何未知结果停在原回执，绝不重发。"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from prepare_native_upload import batch_identity
from invoice_identity import source_matches

ROOT=Path(__file__).resolve().parents[1]
UPLOAD_RAW_BUDGET=480_000  # Base64 plus the native wrapper stays below the 800KB bridge packet cap.


def partition_sources(originals):
    batches=[]; current=[]; size=0
    for source in originals:
        path=Path(source['path']); raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source['sha256']:
            raise ValueError('分批前原件SHA改变')
        if len(raw)>UPLOAD_RAW_BUDGET:
            raise ValueError('单份原件超过后台分批预算；尚未暂存或上传，须接通分块文件构造')
        if current and (len(current)==10 or size+len(raw)>UPLOAD_RAW_BUDGET):
            batches.append(current);current=[];size=0
        current.append(source);size+=len(raw)
    if current:batches.append(current)
    return batches


def verify_budget_rejection(manifest, folder, status_receipt, tab):
    """Only the old local packet-size rejection, never an unknown browser dispatch."""
    from native_upload_staged import build_script
    from windows_edge_bridge import MAX_REQUEST
    from datetime import datetime
    folder=Path(folder); manifest=Path(manifest)
    old=json.loads((folder/'02-stage_files.json').read_text())
    source=(folder/'02-stage_files.js').read_bytes()
    body=old.get('result',{}); observed=json.loads(Path(status_receipt).read_text()); state=observed.get('result',{})
    m=json.loads(manifest.read_text());expected=batch_identity(m,0,10)
    if (source!=build_script('stage_files',m).encode() or len(source)<=MAX_REQUEST
            or old.get('source_sha256')!=hashlib.sha256(source).hexdigest()
            or old.get('source_bytes')!=len(source) or old.get('tab_id')!=tab
            or old.get('operation_kind')!='browser-local'
            or body.get('injection_acknowledged') is not False
            or body.get('error')!='单次浏览器脚本超过Native Messaging预算；未派发，不截断执行'
            or any((folder/name).exists() for name in ['03-assign_files.json','04-dispatch.json','06-save.json'])):
        raise ValueError('原暂存并非可证明的本地预算拒绝；保留原任务，不重传')
    if (observed.get('phase')!='script_completed' or observed.get('tab_id')!=tab
            or observed.get('browser_transport')!=old.get('browser_transport')
            or datetime.fromisoformat(observed['executed_at_utc'])<=datetime.fromisoformat(old['executed_at_utc'])
            or state.get('ok') is not True or state.get('batch')!=expected
            or state.get('stage_found') is not False or state.get('phase')!='absent'
            or state.get('dispatch_requested') is not False or state.get('staged_files')!=[]
            or state.get('input_files')!=[] or state.get('upload_tracking') is not None
            or state.get('native_state')!={'queueCount':0,'currentUploads':0,'uploadErrors':0,'nativeFileList':''}):
        raise ValueError('当前独立回读未证明原批未暂存且上传现场为空')
    return {'classification':'local_packet_budget_rejected_before_dispatch','original_preserved':True,
            'batch_sha256':expected['batchSha256'],'source_sha256':old['source_sha256'],
            'original_receipt_sha256':hashlib.sha256((folder/'02-stage_files.json').read_bytes()).hexdigest(),
            'status_receipt':str(Path(status_receipt).resolve()),
            'status_receipt_sha256':hashlib.sha256(Path(status_receipt).read_bytes()).hexdigest(),
            'no_replay':True,'next':'使用新的按字节分批计划；不执行原超大脚本'}


def recover_budget_rejection(manifest, tab, folder):
    folder=Path(folder);proof=folder/'budget-rejection-recovery.json'
    if proof.exists():
        before=json.loads(proof.read_text())
        if verify_budget_rejection(manifest,folder,before['status_receipt'],tab)!=before:
            raise ValueError('原预算恢复证明已改变')
        return before
    status=folder/('budget-rejection-status-'+str(time.time_ns())+'.json')
    call('native_upload_staged.py',['status','--manifest',manifest,'--tab-id',tab,'--execute'],status)
    evidence=verify_budget_rejection(manifest,folder,status,tab)
    with proof.open('x',encoding='utf-8')as stream:json.dump(evidence,stream,ensure_ascii=False,indent=2)
    return evidence


def call(tool,args,output):
    command=[sys.executable,str(ROOT/'tools'/tool),*map(str,args),'--output',str(output)]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    if not output.exists():raise RuntimeError(f'{tool}未产出回执：{result.stderr[-600:]}')
    data=json.loads(output.read_text()).get('result',{})
    if result.returncode or not data.get('ok'):
        raise RuntimeError(f'{output.name}失败，保留原回执，不重发：{data.get("error",result.stderr[-200:])}')
    return data


def full_grid(tab,grid,userid,out):
    for attempt in range(3):
        target=out if not attempt else out.with_name(out.stem+f'-read-{attempt}'+out.suffix)
        try:
            d=call('finance_invoice_verification.py',['full-grid','--grid',grid,'--tab-id',tab,'--expected-userid',userid],target)
            break
        except RuntimeError:
            evidence=json.loads(target.read_text()) if target.exists() else {}
            observed=evidence.get('result',{})
            scope_changed=(observed.get('error') in {'分页期间记录总数变化','读取期间记录总数变化','恢复后记录总数变化'}
                           and observed.get('pagination',{}).get('viewRestored') is True
                           and observed.get('identity',{}).get('userid')==userid)
            # 仅对已完成且明确尚在加载的只读探针再次读取；未知执行仍停止。
            if (evidence.get('phase')!='script_completed' or
                'unknown' in str(observed.get('execution_status','')) or
                not (scope_changed or observed.get('error') in {
                    '目标表加载中或页面/身份改变',
                    '指定原生表未加载，不能导航或猜测范围'}) or attempt==2):
                raise
            time.sleep(.5)
    if not (d.get('scopeComplete') and d.get('serialization',{}).get('coreCaptureComplete') and d.get('pagination',{}).get('viewRestored')):
        raise RuntimeError('完整分页或原页恢复未验证')
    if d.get('identity',{}).get('userid')!=userid:raise RuntimeError('身份改变')
    g=d['grids'][0]
    if g['records']!=len(g['rows']):raise RuntimeError('分页记录不全')
    return d


def classify(originals,poolrows,recrows,receipts):
    uploaded={}
    for r in receipts:
        if r['name'] in uploaded or not re.fullmatch('[a-fA-F0-9]{32}',r['response']):
            raise RuntimeError('上传filecode或文件名不唯一')
        uploaded[r['name']]=r['response']
    classified=[]
    for row in originals:
        name=Path(row['path']).name
        if name not in uploaded:raise RuntimeError('缺少本批上传文件回执')
        hits=[r for r in poolrows if source_matches(r,row)]
        rh=[r for r in recrows if r.get('t.show_filename')==name and r.get('t.filesequcode')==uploaded[name]]
        if len(hits)+len(rh)!=1:
            raise RuntimeError('发票尚未唯一对应当前池/本批filecode识别明细；仅可继续只读核对')
        status=('authenticated' if hits[0].get('t.auth_rst_desc')=='正常(已认证)' else 'pool_other_status') if hits else ('recognition_error' if rh[0].get('t.show_err_msg','').strip() else 'pending')
        classified.append({'invoice_numbers':row['invoice_number_candidates'],'source_sha256':row['sha256'],
          'filecode':uploaded[name],'status':status,'in_pool':bool(hits),'pool_rows':hits,'recognition_rows':rh})
    return classified


def collect_batch(manifest,tab,folder,tag='readback'):
    m=json.loads(manifest.read_text());batch=batch_identity(m,0,10);userid=batch['expectedUserid']
    save=json.loads((folder/'06-save.json').read_text()).get('result',{})
    if not save.get('ok') or save.get('batch',{}).get('batchSha256')!=batch['batchSha256']:
        raise RuntimeError('无本批原生保存回执，不启动保存后回读')
    for attempt in range(3):
        out=folder/f'{tag}-{attempt+1:02}';out.mkdir(exist_ok=False)
        call('finance_invoice_verification.py',['view','recognition','--tab-id',tab,'--expected-userid',userid],out/'01-open-recognition.json')
        rec=full_grid(tab,'113',userid,out/'02-recognition-full.json')
        call('finance_invoice_verification.py',['view','pool','--tab-id',tab],out/'03-return-pool.json')
        pool=full_grid(tab,'53',userid,out/'04-pool-full.json')
        try:rows=classify(m['original_files'],pool['grids'][0]['rows'],rec['grids'][0]['rows'],save['receipts'])
        except RuntimeError:
            if attempt==2:raise
            time.sleep(1);continue
        pending=sum(r['status']=='pending' for r in rows)
        if pending and attempt<2:
            time.sleep(2);continue
        summary={'ok':not pending,'all_files_accounted':True,'verification_complete':not pending,'pending_count':pending,
          'manifest':str(manifest),'batchSha256':batch['batchSha256'],'count':batch['count'],
          'pool_count':pool['grids'][0]['records'],'recognition_count':rec['grids'][0]['records'],'classified':rows,
          'selection_performed':False,'claim_created':False,'claim_submitted':False}
        (out/'05-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:summary[k] for k in ['ok','count','pool_count','recognition_count','pending_count']},ensure_ascii=False),flush=True)
        if pending:raise RuntimeError('本批仍在识别，保存已完成，仅允许后续只读跟进')
        return summary


def numbers(rows):
    return {n for row in rows for n in re.findall(r'(?<!\d)\d{20}(?!\d)',str(row.get('t.tk_code_abst','')))}


def previous_upload_verified(state,pool,root):
    t=state.get('upload_tracking')
    if not t:return True
    if (t.get('batchSha256')==state.get('batch',{}).get('batchSha256') or t.get('done')is not True
        or t.get('saveRequested')is not True or t.get('errors') or not t.get('files')
        or len(t.get('receipts',[]))!=len(t['files']) or pool.get('identity',{}).get('userid')!=t.get('expectedUserid')):return False
    receipts={r.get('name'):r for r in t['receipts']}
    if len(receipts)!=len(t['files'])or set(receipts)!={f.get('name')for f in t['files']}:return False
    matches=[]
    for p in root.glob('*/**/05-summary.json'):
        d=json.loads(p.read_text())
        if d.get('batchSha256')!=t['batchSha256']or not d.get('verification_complete'):continue
        manifest=Path(d['manifest']);source=json.loads(manifest.read_text())
        if batch_identity(source,0,10)['batchSha256']!=t['batchSha256']or source.get('case_id')!=t.get('caseId'):continue
        classified=d.get('classified',[])
        if len(classified)!=len(t['files'])or any(r.get('status')!='authenticated'for r in classified):continue
        native=pool['grids'][0]['rows'];valid=True
        # 原生并行上传回执按完成时间返回，不能按原文件顺序zip匹配。
        for file in t['files']:
            receipt=receipts[file['name']]
            found=[r for r in classified if r['source_sha256']==file['sha256']and r['filecode']==receipt['response']and receipt['name']==file['name']]
            if len(found)!=1:valid=False;break
            old=found[0]['pool_rows']
            if len(old)!=1 or len([r for r in native if r.get('t.tk_guid')==old[0].get('t.tk_guid')and r.get('t.tk_code_abst')==old[0].get('t.tk_code_abst')and r.get('t.tk_total_amt')==old[0].get('t.tk_total_amt')and r.get('t.auth_rst_desc')=='正常(已认证)'])!=1:valid=False;break
        if valid:matches.append(p)
    return bool(matches)


def wait_upload_ready(manifest,tab,folder,pool=None):
    """Only read while the native upload dialog finishes mounting."""
    for attempt in range(8):
        output=folder/('01-ready-'+str(time.time_ns())+'.json')
        state=call('native_upload_staged.py',['status','--manifest',manifest,'--tab-id',tab,'--execute'],output)
        if state.get('stage_found') or state.get('dispatch_requested') or state.get('input_files') or (state.get('upload_tracking') and not previous_upload_verified(state,pool or {},folder.parent)):
            raise RuntimeError('上传入口已有本批或未知活动，停止等待，不重传')
        if state.get('endpoint_verified') and state.get('native_state')=={'queueCount':0,'currentUploads':0,'uploadErrors':0,'nativeFileList':''}:
            return output
        if attempt<7:time.sleep(.5)
    raise RuntimeError('原生上传入口尚未就绪；未构造或派发文件')


def verify_preflight_rejection(manifest,folder,observed,tab):
    from native_upload_staged import build_script
    from datetime import datetime
    original=folder/'02-stage_files.json';old=json.loads(original.read_text());source=(folder/'02-stage_files.js').read_bytes()
    m=json.loads(manifest.read_text());expected=batch_identity(m,0,10);now=json.loads(observed.read_text());state=now.get('result',{})
    if (source!=build_script('stage_files',m).encode() or old.get('source_sha256')!=hashlib.sha256(source).hexdigest()
        or old.get('source_bytes')!=len(source) or old.get('tab_id')!=tab or old.get('phase')!='script_completed'
        or old.get('operation_kind')!='browser-local' or old.get('result')!={'ok':False,'error':'原生上传接口变化'}
        or any((folder/n).exists()for n in ['03-assign_files.json','04-dispatch.json','06-save.json','02-stage_files-after-ready.json'])):
        raise ValueError('不属于明确的上传入口前置拒绝；保留原任务')
    if (now.get('phase')!='script_completed' or now.get('tab_id')!=tab or now.get('browser_transport')!=old.get('browser_transport')
        or datetime.fromisoformat(now['executed_at_utc'])<=datetime.fromisoformat(old['executed_at_utc'])
        or state.get('ok') is not True or state.get('batch')!=expected or state.get('stage_found') is not False
        or state.get('dispatch_requested') is not False or state.get('phase')!='absent' or not state.get('endpoint_verified')
        or state.get('staged_files')!=[] or state.get('input_files')!=[] or state.get('upload_tracking') is not None
        or state.get('native_state')!={'queueCount':0,'currentUploads':0,'uploadErrors':0,'nativeFileList':''}):
        raise ValueError('独立回读未证明入口就绪及原批从未暂存、赋值或派发')
    return {'classification':'native_upload_dialog_not_ready_before_staging','original_receipt_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
            'status_receipt':str(observed.resolve()),'status_receipt_sha256':hashlib.sha256(observed.read_bytes()).hexdigest(),
            'batch_sha256':expected['batchSha256'],'original_preserved':True,'no_unknown_replay':True}


def resume_preflight(manifest,tab,folder):
    pool=json.loads((folder/'00-before-pool.json').read_text())['result']
    observed=wait_upload_ready(manifest,tab,folder,pool)
    stage_name='02-stage_files-after-ready.json'
    if not(folder/'02-stage_files.json').exists():
        if any((folder/n).exists()for n in ['02-stage_files.js','03-assign_files.json','04-dispatch.json','06-save.json']):raise ValueError('已有暂存或派发意图，不按入口等待失败接续')
        opened=json.loads((folder/'01-open.json').read_text())
        if opened.get('phase')!='script_completed' or opened.get('tab_id')!=tab or opened.get('result',{}).get('ok')is not True:raise ValueError('缺少原入口已打开回执')
        proof={'classification':'readiness_stopped_before_file_staging','original_preserved':True,'status_receipt':str(observed.resolve()),'status_receipt_sha256':hashlib.sha256(observed.read_bytes()).hexdigest()}
        stage_name='02-stage_files.json'
    else:proof=verify_preflight_rejection(manifest,folder,observed,tab)
    with (folder/'preflight-recovery.json').open('x',encoding='utf-8')as f:json.dump(proof,f,ensure_ascii=False,indent=2)
    return finish_upload(manifest,tab,folder,stage_name=stage_name)


def run_batch(manifest,tab,folder):
    m=json.loads(manifest.read_text());batch=batch_identity(m,0,10);userid=batch['expectedUserid'];count=batch['count']
    if count!=len(m['original_files']):raise ValueError('每个manifest必须仅包含当前1至10份原件')
    folder.mkdir(parents=True,exist_ok=False)
    common=['--manifest',manifest,'--tab-id',tab];seq=['--offset','0','--limit',str(count)]
    before=full_grid(tab,'53',userid,folder/'00-before-pool.json')
    if any(source_matches(native,source) for source in m['original_files'] for native in before['grids'][0]['rows']):
        raise RuntimeError('本批存在已入池票号，停止而不重复上传')
    call('invoice_cli.py',['open-upload',*common],folder/'01-open.json')
    wait_upload_ready(manifest,tab,folder,before)
    return finish_upload(manifest,tab,folder)


def finish_upload(manifest,tab,folder,stage_name='02-stage_files.json'):
    m=json.loads(manifest.read_text());batch=batch_identity(m,0,10);userid=batch['expectedUserid'];count=batch['count']
    common=['--manifest',manifest,'--tab-id',tab];seq=['--offset','0','--limit',str(count)]
    for i,action,phase in [(2,'stage_files','files_staged'),(3,'assign_files','files_assigned'),(4,'dispatch','dispatch_event_returned')]:
        d=call('native_upload_staged.py',[action,*common,*seq,'--execute'],folder/(stage_name if i==2 else f'{i:02}-{action}.json'))
        if d.get('phase')!=phase:raise RuntimeError('原生上传阶段未完成')
    ready=None
    for i in range(1,7):
        d=call('invoice_cli.py',['read',*common],folder/f'05-upload-read-{i:02}.json')
        t=d.get('uploadTracking') or {};native=d.get('uploadNativeState') or {}
        if d.get('identity',{}).get('userid')!=userid or t.get('batchSha256')!=batch['batchSha256']:raise RuntimeError('上传回读不是本批/本身份')
        if t.get('errors'):raise RuntimeError('原生上传报告错误，停止本批')
        if (t.get('done') and len(t.get('receipts',[]))==count and
            native.get('queueCount')==0 and native.get('currentUploads')==0 and
            native.get('uploadErrors')==0):
            ready=d;break
        time.sleep(2)
    if ready is None:raise RuntimeError('上传仍未完成，保留本批不重发')
    call('invoice_cli.py',['save-upload',*common,*seq],folder/'06-save.json')
    call('invoice_cli.py',['read',*common],folder/'07-save-read.json')
    # 保存返回后上传窗口/标题可能尚在切换，先回到原生票池并独立读状态。
    call('invoice_cli.py',['open-pool',*common],folder/'08-return-pool.json')
    call('invoice_cli.py',['read',*common],folder/'09-pool-ready.json')
    return collect_batch(manifest,tab,folder)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--tab-id',required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--readback-only',action='store_true')
    p.add_argument('--resume-preflight',action='store_true',help='仅接续有源码与独立空现场证明的入口未就绪拒绝')
    p.add_argument('--readback-tag',default='recovery')
    a=p.parse_args()
    try:
        if a.resume_preflight and a.readback_only:raise ValueError('接续模式只能选择一个')
        if a.readback_only:
            if not re.fullmatch(r'[a-zA-Z0-9_-]+',a.readback_tag):raise ValueError('回读标签无效')
            collect_batch(a.manifest,a.tab_id,a.output_dir,a.readback_tag)
        elif a.resume_preflight:resume_preflight(a.manifest,a.tab_id,a.output_dir)
        else:run_batch(a.manifest,a.tab_id,a.output_dir)
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error),'retry_upload':False},ensure_ascii=False),flush=True);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
