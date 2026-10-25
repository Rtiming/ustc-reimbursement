#!/usr/bin/env python3
"""发票入口和识别保存的参数化封装；不包含删除和审批动作。"""
import argparse
import json
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager, nullcontext
from prepare_native_upload import batch_identity
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def empty_entry_lease(manifest_path, manifest, userid, workspace=ROOT, target_tab_id=None):
    from assistant_api import AssistantService, read_json
    from finance_open_draft import ensure_globally_idle
    service=AssistantService(workspace,[Path(workspace).parent])
    case=manifest['case_id']
    with service.mutex(case),service.mutex(case,browser=True):
        _,paths=service.resolve(case)
        if Path(manifest_path).resolve()!=paths['invoices'] or read_json(paths['invoices'])!=manifest:
            raise ValueError('空入口恢复须本案已登记且未变的manifest')
        form=read_json(paths['form'])
        if form.get('serial') or form.get('expected_userid') not in (None,'',userid):
            raise ValueError('本案已有单号或登记账号不符，不能空入口重载')
        idle=ensure_globally_idle(service,case,None,target_tab_id=target_tab_id)
        yield idle

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('action', choices=['open-pool','diagnose-pool','cancel-leave','recover-empty-entry','open-upload','read','save-upload','create-draft','resume-draft'])
ap.add_argument('--manifest', type=Path, required=True)
ap.add_argument('--form', type=Path, help='resume-draft所需本案表单')
ap.add_argument('--output', type=Path, required=True)
ap.add_argument('--emit-script', type=Path)
ap.add_argument('--tab-id')
ap.add_argument('--expected-userid', help='票池诊断/固定恢复：本次已核2–40位字母数字账号，不修改manifest')
ap.add_argument('--execute', action='store_true', help='仅recover-empty-entry：默认只读预检，显式执行才同URL重载一次')
ap.add_argument('--offset', type=int, default=0, help='save-upload对应原上传批次offset')
ap.add_argument('--limit', type=int, default=10, help='save-upload对应原上传批次limit，最多10')
args = ap.parse_args();m = json.loads(args.manifest.read_text())
if not m.get('case_id'): ap.error('manifest需要case_id')
if args.execute and args.action!='recover-empty-entry':ap.error('--execute仅供recover-empty-entry')
if args.action in ['diagnose-pool','cancel-leave','recover-empty-entry']:
    if not isinstance(args.expected_userid,str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}',args.expected_userid):
        ap.error(args.action+'须显式--expected-userid，本次已核2–40位字母数字账号')
    if not isinstance(args.tab_id,str) or not re.fullmatch(r'[0-9]+',args.tab_id):ap.error(args.action+'须当前财务--tab-id')
    if m.get('expected_userid') not in (None,'',args.expected_userid):ap.error('manifest已登记账号与本次账号不同')
    if args.action in ['diagnose-pool','recover-empty-entry'] and (m.get('serial') or m.get('draft_creation_requested') or m.get('selected_invoices')):ap.error('诊断/恢复仅限无单号、无创建请求、无已选票的本案入口')
elif args.expected_userid is not None:ap.error('--expected-userid当前仅供票池诊断/固定恢复')
if args.action in ['create-draft','resume-draft']:
    native_manifest=m
    if args.action=='resume-draft':
        from finance_draft_readback import prepare_rejected_draft_resume
        if not args.form or args.form.resolve().parent!=args.manifest.resolve().parent:ap.error('接续须为同案表单')
        native_manifest=prepare_rejected_draft_resume(m,json.loads(args.form.read_text()),args.manifest.parent,args.output.parent if not args.emit_script else None)
    elif m.get('serial') or m.get('draft_creation_requested'): ap.error('已有单号或创建请求，须回读而不是重试')
    source = '(' + (ROOT/'tools/create_daily_draft.js').read_text() + ')(' + json.dumps(native_manifest,ensure_ascii=False) + ')'
else:
    name={'open-pool':'open_daily_invoice_pool.js','diagnose-pool':'open_daily_invoice_pool.js','cancel-leave':'open_daily_invoice_pool.js','recover-empty-entry':'open_daily_invoice_pool.js','open-upload':'open_native_invoice_upload.js','read':'read_current_state.js','save-upload':'save_native_uploaded_invoices.js'}[args.action]
    source = (ROOT/'tools/actions'/name).read_text()
if args.action in ['open-pool','diagnose-pool','cancel-leave','recover-empty-entry']:
    source=source.replace('__POOL_CONTEXT__',json.dumps({'mode':args.action,'case_id':m['case_id'],'expected_userid':args.expected_userid,'execute':args.execute},ensure_ascii=False))
if args.action == 'save-upload':
    expected = batch_identity(m, args.offset, args.limit)
    source=source.replace('__EXPECTED_UPLOAD_BATCH__', json.dumps(expected,ensure_ascii=False))
source='(()=>{if(location.origin!=="https://cwzh.ustc.edu.cn"||$.getProjID()!=="WF_YBX")throw Error("财务系统不符");return ('+source+');})()'
path=args.emit_script or ROOT/'private'/('invoice_cli_'+args.action+'_'+uuid.uuid4().hex+'.js');path.parent.mkdir(parents=True,exist_ok=True)
with path.open('x') as script_file: script_file.write(source)
if args.emit_script: print(json.dumps({'script':str(path),'executed':False},ensure_ascii=False))
else:
    command=[sys.executable,str(ROOT/'tools/edge_runtime.py'),str(path),'--output',str(args.output),'--quiet','--operation-kind','browser-local' if args.action in ['cancel-leave','recover-empty-entry'] else 'read' if args.action in ['read','open-pool','open-upload','diagnose-pool'] else 'write']
    if args.tab_id: command += ['--tab-id',args.tab_id]
    with empty_entry_lease(args.manifest,m,args.expected_userid,target_tab_id=args.tab_id) if args.action=='recover-empty-entry' else nullcontext() as idle:
        result=subprocess.run(command)
        if idle and idle.get('isolated_pending') and args.output.is_file():
            from assistant_api import atomic_json
            receipt=json.loads(args.output.read_text())
            receipt['isolated_pending']=idle['isolated_pending']
            atomic_json(args.output,receipt)
    if args.action in ['create-draft','resume-draft']:
        if args.action=='resume-draft':m.setdefault('rejected_creation_history',[]).append(native_manifest['resuming_rejected_creation'])
        m.update(draft_creation_requested=True,creation_receipt=str(args.output.resolve()))
        args.manifest.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
    raise SystemExit(result.returncode)
