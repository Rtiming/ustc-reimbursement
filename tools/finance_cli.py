#!/usr/bin/env python3
"""参数化财务填报入口：生成可移植 MAIN world 脚本，或在已登录 macOS Edge 执行。"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from validate_config import load, load_basic, load_context, load_funding

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('action', choices=['close-payments', 'home-inspect', 'invoice-category', 'invoice-group-inspect', 'invoice-group-open', 'invoice-group-close', 'close-basic', 'close-empty-explanation', 'teacher-lookup', 'inspect-basic', 'recover-main', 'diagnose-preview', 'recover-preview', 'inspect-draft', 'navigate', 'basic', 'expenses', 'transfers', 'save-payments', 'dismiss-payment-remark-error', 'dismiss-payment-validation-error', 'corporate-inspect', 'explanation', 'funding', 'pre-review', 'card-query', 'card-select', 'audit', 'no-contract', 'prepare-review', 'accept-promise', 'department-approvers', 'status', 'wait-section'])
ap.add_argument('--case', type=Path, required=True, help='本案已核定填报配置，不读取默认旧案')
ap.add_argument('--section', choices=['basic', 'funding', 'expenses', 'payments', 'explanation', 'promise', 'department', 'applicant'])
ap.add_argument('--group', help='当前单据预览中明确的发票内容分组，只读打开')
ap.add_argument('--index', type=int, default=0, help='预审或公务卡配置索引，从0开始')
ap.add_argument('--save', action='store_true', help='按原生保存按钮保存；仍需独立回读')
ap.add_argument('--start', type=int, default=0, help='转卡配置起始索引')
ap.add_argument('--limit', type=int, default=5, help='转卡每批1至5人')
ap.add_argument('--output', type=Path, required=True)
ap.add_argument('--emit-script', type=Path, help='仅生成脚本，供其他浏览器/助手执行')
ap.add_argument('--tab-id', help='财务标签ID，多标签时必须明确选择')
ap.add_argument('--expected-userid', help='仅inspect-draft/close-empty-explanation/home-inspect/close-payments：本次已核2–40位字母数字账号，只注入任务上下文')
args = ap.parse_args()
args.output=args.output.resolve()
c = load_context(args.case) if args.action in ['close-payments', 'home-inspect', 'invoice-group-inspect', 'invoice-group-open', 'invoice-group-close', 'close-basic', 'teacher-lookup', 'inspect-basic', 'recover-main', 'diagnose-preview', 'recover-preview', 'inspect-draft', 'navigate', 'status', 'dismiss-payment-remark-error'] else load_basic(args.case) if args.action == 'basic' else load_funding(args.case) if args.action == 'funding' else load(args.case)
runtime_user=os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
if runtime_user is not None:
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',runtime_user) or c.get('expected_userid') not in (None,'',runtime_user):
        ap.error('流水线账号绑定不合法或与本案冲突')
    c={**c,'expected_userid':runtime_user}
if args.expected_userid is not None:
    if args.action not in ['inspect-draft', 'close-empty-explanation', 'home-inspect', 'close-payments'] or not re.fullmatch(r'[A-Za-z0-9]{2,40}', args.expected_userid):
        ap.error('--expected-userid仅供inspect-draft/close-empty-explanation/home-inspect/close-payments，须为2–40位字母数字账号')
    if c.get('expected_userid') not in (None, '', args.expected_userid):
        ap.error('本案已登记账号与本次账号不同')
    c = {**c, 'expected_userid': args.expected_userid}
if args.action in ['home-inspect', 'close-payments'] and args.save:
    ap.error(args.action + '不执行保存，不接受--save')
if args.action in ['home-inspect', 'close-payments'] and not re.fullmatch(r'[A-Za-z0-9]{2,40}', c.get('expected_userid') or ''):
    ap.error(args.action + '须明确本次expected-userid')
if args.action in ['navigate','wait-section'] and not args.section:
    ap.error(args.action + ' 需要 --section')
if args.action == 'navigate' and args.section not in ['basic', 'funding', 'expenses', 'payments', 'explanation']:
    ap.error('navigate 只能进入填写页面')
if args.action == 'wait-section' and args.section not in ['promise', 'department', 'applicant']:
    ap.error('wait-section 只接受 promise、department、applicant')
method = {'close-payments':'closePayments', 'home-inspect':'inspectHome', 'invoice-category':'fillInvoiceCategory', 'invoice-group-inspect':'inspectInvoiceGroup', 'invoice-group-open':'openInvoiceGroup', 'invoice-group-close':'closeInvoiceGroup', 'close-basic':'closeBasic', 'close-empty-explanation':'closeEmptyExplanation', 'teacher-lookup':'lookupTeacher', 'inspect-basic':'inspectBasic', 'recover-main':'recoverMain', 'diagnose-preview':'diagnosePreview', 'recover-preview':'recoverPreview', 'inspect-draft':'inspectDraft', 'basic': 'fillBasic', 'expenses': 'fillExpenses', 'navigate': 'navigate', 'transfers': 'fillTransfers', 'save-payments': 'savePayments', 'dismiss-payment-validation-error':'dismissPaymentValidationError', 'dismiss-payment-remark-error':'dismissPaymentRemarkError', 'corporate-inspect':'inspectCorporates', 'explanation': 'fillExplanation', 'funding':'fillFunding', 'pre-review':'preReview', 'card-query':'cardQuery', 'card-select':'cardSelect', 'audit':'audit', 'no-contract':'noContract', 'prepare-review':'prepareReview', 'accept-promise':'acceptPromise', 'department-approvers':'departmentApprovers', 'status':'status', 'wait-section':'waitSection'}[args.action]
param = json.dumps(args.group,ensure_ascii=False) if args.action=='invoice-group-open' else str(args.index) if args.action in ['invoice-category','pre-review','card-query','card-select'] else json.dumps(args.section) if args.action in ['navigate','wait-section'] else (json.dumps({'start': args.start, 'limit': args.limit}) if args.action == 'transfers' else str(args.save).lower())
source = (ROOT / 'tools/browser_automation.js').read_text()
script = '(async()=>{\n' + source + '\nreturn await ReimbursementAutomation.' + method + '(' + json.dumps(c, ensure_ascii=False) + ',' + param + ');\n})()'
path = (args.emit_script or args.output.with_suffix('.js')).resolve()
path.parent.mkdir(parents=True, exist_ok=True)
if args.emit_script:
    path.write_text(script,encoding='utf-8')
else:
    # 原生回执的source_sha256必须一直对应原脚本；下一案不能覆盖全局cli_*.js。
    with path.open('x',encoding='utf-8')as stream:stream.write(script)
path.chmod(0o600)
if args.emit_script:
    print(json.dumps({'script': str(path.resolve()), 'action': args.action, 'executed': False}, ensure_ascii=False))
else:
    command = [sys.executable, str(ROOT / 'tools/edge_runtime.py'), str(path), '--output', str(args.output), '--quiet']
    if args.action in ['dismiss-payment-validation-error', 'dismiss-payment-remark-error', 'inspect-draft', 'close-empty-explanation', 'close-payments']:
        command += ['--operation-kind', 'browser-local']
    elif args.action in ['corporate-inspect', 'home-inspect']:
        command += ['--operation-kind', 'read']
    if args.tab_id: command += ['--tab-id', args.tab_id]
    result = subprocess.run(command)
    if args.action == 'accept-promise':
        # 即使响应丢失也先阻止重放。是否生成流程须回读，不能靠重试来判断。
        c.setdefault('review', {}).update(workflow_started=True,
            workflow_status='start_requested_readback_required',
            workflow_start_receipt=str(args.output.resolve()))
        args.case.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit(result.returncode)
