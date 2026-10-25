#!/usr/bin/env python3
"""大模型操作入口。默认本地检查/计划；浏览器写入需显式--execute。"""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'tools'))
from operator_support import DEFAULT, bundle, check, child_env, doctor, init_case, stamp, write_json
from run_case import STAGES
from assistant_contract import CASE_PROFILES


def emit(result):
    print(json.dumps(result, ensure_ascii=False, indent=2))


def overview(path):
    b, paths = bundle(path)
    report = check(path)
    c = json.loads(paths['form'].read_text(encoding='utf-8'))
    state = json.loads(paths['state'].read_text(encoding='utf-8'))
    pending = report.get('material_audit') or {}
    started = bool(c.get('review', {}).get('workflow_started'))
    correcting = state.get('status') in ['correction_in_progress', 'correction_saved_await_abstract', 'correction_saved_await_resubmission']
    pending_file = paths['state'].parent/'pending_write.json'
    pending_write = json.loads(pending_file.read_text(encoding='utf-8')) if pending_file.exists() else None
    completed_stages = state.get('operator_verified_stages', [])
    next_stage = next((x for x in STAGES if x not in completed_stages), None) if c.get('serial') and not started else None
    missing = []
    for key, caption in [('amount', '核定报销总额'), ('funding', '经费卡、分担金额和院级审批人'), ('expense_allocations', '系统预算叶节点及分摊金额'), ('payments', '收款人、工号、金额及支付方式')]:
        if key == 'expense_allocations' and c.get('budget_allocation_mode') == 'system_default_single_funding' and len(c.get('funding', [])) == 1 and not c.get('funding_policy', {}).get('invoice_level_required'):
            continue
        if not c.get(key): missing.append({'field': key, 'information': caption})
    for key, caption in [('teacher_name', '教师证明人姓名'), ('teacher_id', '教师证明人工号')]:
        if not c.get('basic', {}).get(key): missing.append({'field': 'basic.'+key, 'information': caption})
    fields = c.get('basic', {}).get('fields', {})
    if not fields.get('t-abst'): missing.append({'field': 'basic.fields.t-abst', 'information': '活动/采购用途摘要'})
    if not (fields.get('t-req_mobile') or fields.get('t-req_tele')): missing.append({'field': 'basic.fields', 'information': '联系人电话'})
    command = ['python3', 'reimburse.py', '--bundle', str(Path(path).resolve())]
    return {'case_id': b['case_id'], 'serial': c.get('serial'), 'amount': c.get('amount'),
            'known_state': state.get('status'), 'known_node': state.get('node'),
            'state_observed_at_utc': state.get('updated_at_utc'), 'state_is_cached_not_live': True,
            'counts': {'invoices': report['original_count'], 'payments': len(c.get('payments', [])), 'funding': len(c.get('funding', []))},
            'local_parameter_errors': report['errors'],
            'input_overview_missing': missing,
            'required_material_gaps': [{k: x[k] for k in ['role', 'issue', 'instruction']} for x in pending.get('missing_or_pending', [])],
            'canonical_explanation': {'record_only': c.get('explanations', {}).get('record_only'), 'source_file': c.get('explanations', {}).get('source_file'),
                                      'system_verified': state.get('canonical_explanation', {}).get('system_verified', False)},
            'workflow_started': started, 'correction_in_progress': correcting, 'delivery': c.get('delivery', {}),
            'unverified_write': pending_write,
            'recommended_stage': next_stage, 'operator_verified_stages': completed_stages,
            'do_next': '上次写入尚未核对：先只读回查，不重试或开始下一写入' if pending_write else '退回修改已保存核验，尚未重新提交；先读同单当前状态，按实际页面续办，不重复拆分经费或建单' if state.get('status') == 'correction_saved_await_resubmission' else '退回单正在修改：用correction逐票核对，经费和摘要全部通过后再进入审批' if correcting else '已有流程：先读当前单据及审批日志，退回单走correction，其余按实际环节续办' if started else '先补输入与材料，再按操作规程处理发票、创建草稿和分阶段填写',
            'commands': {'check': command+['check', '--hashes'], 'tabs': command+['tabs'], 'status': command+['status'], 'plan': command+['plan']},
            'runbook': str(ROOT/'docs/助手操作规程.md'), 'case_handoff': str(ROOT/'HANDOFF.md') if Path(path).resolve() == DEFAULT.resolve() else str(paths['state']),
            'supported_modes': ['daily/bank_transfer', 'daily/one_corporate_card'],
            'unsupported_live_modes': ['remittance', 'quality_deposit', 'multiple_corporate_cards', 'final_agreement', 'captcha'],
            'supplementary_attachment_upload': 'field_identified_full_adapter_not_live_verified',
            'system_write': False}


def output_dir(paths):
    return paths['state'].parent/'receipts'/('run_'+stamp().replace(':', '').replace('+', '_')+'_'+uuid.uuid4().hex[:6])


def run_tool(name, params, **kwargs):
    return subprocess.run([sys.executable, str(ROOT/'tools'/name), *map(str, params)], cwd=ROOT, env=child_env(), **kwargs)


def freeze_upload_batch(paths, case_id, offset, count, stage='upload', uploaded_evidence=None):
    """冻结实际源文件/票号及明确账号；与原生上传工具使用同一批次摘要。"""
    from prepare_native_upload import batch_identity
    manifest_path = paths['invoices']
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    batch = batch_identity(manifest, offset, count)
    if batch['caseId'] != case_id or stage not in ['upload', 'save-upload']:
        raise ValueError('上传批次案件或阶段不符')
    sources = []
    for row, item in zip(manifest['original_files'][offset:offset+count], batch['files']):
        numbers = row.get('invoice_number_candidates', [])
        if not isinstance(numbers, list): raise ValueError('原票号候选须为数组')
        numbers = [x.get('value') if isinstance(x, dict) else x for x in numbers]
        if any(not isinstance(x, str) or not re.fullmatch(r'[0-9]{8}|[0-9]{10}|[0-9]{20}', x) for x in numbers):
            raise ValueError('上传票号须为已确认8/10/20位ASCII票据号码；未适配号码或流水号先核对')
        sources.append({'name': item['name'], 'sha256': item['sha256'], 'path': str(Path(row['path']).resolve()),
                        'invoice_numbers': list(dict.fromkeys(numbers))})
    known_numbers = [n for source in sources for n in source['invoice_numbers']]
    if len(set(known_numbers)) != len(known_numbers):
        raise ValueError('本批同票号对应多个源文件；先核对版本，不能重复入池计作多票')
    result = {'schema': 'ustc-pending-native-upload-v1', 'stage': stage, 'batch': batch,
              'manifest_path': str(manifest_path.resolve()),
              'source_file_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(), 'source_invoices': sources}
    if uploaded_evidence is not None: result['uploaded_evidence'] = uploaded_evidence
    return result


def upload_receipts(expected, receipts):
    if not isinstance(receipts, list) or len(receipts) != expected['count']:
        raise ValueError('本批原生上传回执缺项')
    names = {f['name'] for f in expected['files']}; pairs = {}
    for row in receipts:
        name, code = row.get('name'), row.get('response')
        if name not in names or name in pairs or not isinstance(code, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', code):
            raise ValueError('本批上传文件名/filecode重复或不符')
        pairs[name] = code.lower()
    if set(pairs) != names or len(set(pairs.values())) != len(pairs):
        raise ValueError('本批上传filecode集合不唯一')
    return pairs


def verified_upload_tracking(r, expected, require_unsaved=False):
    t = r.get('uploadTracking')
    if not isinstance(t, dict) or any(t.get(k) != expected[k] for k in expected):
        raise ValueError('回读上传追踪与冻结案件/账号/manifest/文件SHA批次不符')
    if t.get('done') is not True or t.get('dispatchRequested') is not True or t.get('errors') != []:
        raise ValueError('本批临时上传尚未完整成功；unknown/队列未完成不能清锁')
    pairs = upload_receipts(expected, t.get('receipts'))
    if require_unsaved:
        if t.get('saveRequested') is not False: raise ValueError('此批已请求保存，不能登记为未保存暂存')
        native = r.get('uploadNativeState')
        if not isinstance(native, dict) or any(type(native.get(k)) is not int or native[k] != 0 for k in ['queueCount', 'currentUploads', 'uploadErrors']):
            raise ValueError('原生上传队列/错误未完整回读为零')
        raw = native.get('nativeFileList')
        if not isinstance(raw, str): raise ValueError('缺少原生上传文件列表')
        parts = raw.split('|'); actual = {}
        for item in parts:
            fields = item.split('&')
            if len(fields) < 3 or fields[1] in actual or not re.fullmatch(r'[a-fA-F0-9]{32}', fields[0]):
                raise ValueError('原生上传文件列表格式或唯一性不符')
            actual[fields[1]] = fields[0].lower()
        if actual != pairs: raise ValueError('原生文件列表与本批上传回执不一致')
    return t, pairs


def full_upload_grid(r, funcno):
    matches = [g for g in r.get('grids', []) if str(g.get('funcno')) == funcno or g.get('id') == 'gridWF_YBX_'+funcno]
    if len(matches) != 1: raise ValueError('须唯一完整回读原生表'+funcno)
    g = matches[0]; rows = g.get('rows'); records = g.get('records')
    if not isinstance(rows, list) or type(records) is not int or records < 0 or len(rows) != records or g.get('loading') is not False or g.get('allRecordsVisible') is False or r.get('scopeComplete') is False:
        raise ValueError('原生表'+funcno+'分页/加载未完整，不能据首屏确认本批')
    return rows


def verify_upload_ack(previous, read, paths, outcome):
    v = previous.get('verification', {})
    if v.get('schema') != 'ustc-pending-native-upload-v1':
        raise ValueError('旧上传pending缺冻结来源；保留并核对，普通空池不能解锁')
    expected = v.get('batch', {}); r = read['result']
    current = freeze_upload_batch(paths, previous['case_id'], expected.get('offset'), expected.get('count'), v.get('stage'))
    if any(current[k] != v[k] for k in ['batch', 'manifest_path', 'source_file_sha256', 'source_invoices']):
        raise ValueError('冻结后manifest、源SHA或票号变化；不能据变更输入清锁')
    if r.get('identity', {}).get('userid') != expected['expectedUserid']:
        raise ValueError('上传回读登录身份与冻结批次不符')
    if previous['operation'].startswith('upload:'):
        if outcome != 'staged': raise ValueError('upload只可staged确认临时文件；不能把临时字节登记saved或无证据未执行')
        t, _ = verified_upload_tracking(r, expected, require_unsaved=True)
        return {'phase': 'uploaded_uncommitted', 'verification': v,
                'uploaded_evidence': {'batch': expected, 'receipts': t['receipts']}, 'saved': False}
    if outcome not in ['saved', 'needs_correction']:
        raise ValueError('保存阶段须完整入池/识别证据；unknown不能清锁')
    staged = v.get('uploaded_evidence', {})
    if staged.get('batch') != expected: raise ValueError('缺少同一冻结批次的已暂存证据')
    pairs = upload_receipts(expected, staged.get('receipts'))
    save_confirmed = False
    if r.get('uploadTracking') is not None:
        t, live_pairs = verified_upload_tracking(r, expected)
        if live_pairs != pairs: raise ValueError('保存后本批filecode改变')
        save_confirmed = t.get('saveRequested') is True and t.get('saveRequestBatchSha256') == expected['batchSha256']
    native_path = Path(previous['output_directory'])/'invoice.json'
    if not save_confirmed and native_path.is_file():
        native = json.loads(native_path.read_text()); data = native.get('result', {})
        when = native.get('executed_at_utc')
        if when and datetime.fromisoformat(when) > datetime.fromisoformat(previous['started_at_utc']) and datetime.fromisoformat(when) < datetime.fromisoformat(read['executed_at_utc']) and data.get('ok') is True and data.get('action') == 'save_uploaded_invoices_native' and data.get('saveRequested') is True and data.get('batch') == expected:
            save_confirmed = upload_receipts(expected, data.get('receipts')) == pairs
    if not save_confirmed: raise ValueError('缺少本批原生保存意图证据；保存unknown不凭普通池清锁')
    pool = None; recognition = None; accounted = []
    for source in v['source_invoices']:
        name = source['name']; code = pairs[name]
        # 保存失败/重复占用的文件必须用113原生filecode+文件名精确绑定，不靠粗状态。
        if recognition is None:
            present = any(str(g.get('funcno')) == '113' or g.get('id') == 'gridWF_YBX_113' for g in r.get('grids', []))
            recognition = full_upload_grid(r, '113') if present else []
        errors = [row for row in recognition if row.get('t.show_filename') == name and
                  (str(row.get('t.filesequcode', '')).lower() == code or str(row.get('t.tk_filecode', '')).lower().startswith(code+'&'))]
        if len(errors) > 1: raise ValueError('本批识别filecode歧义')
        if errors:
            message = errors[0].get('t.show_err_msg')
            if not isinstance(message, str) or not message.strip(): raise ValueError('本批识别仍处理中，未有明确错误结论')
            accounted.append({'name': name, 'sha256': source['sha256'], 'filecode': code, 'state': 'recognition_error', 'error': message})
            continue
        if pool is None: pool = full_upload_grid(r, '53')
        numbers = source['invoice_numbers']
        if len(numbers) != 1: raise ValueError('入池文件须唯一原票号，不猜候选或只凭金额确认')
        number = numbers[0]
        matches = [row for row in pool if re.search(r'(?<![0-9])'+re.escape(number)+r'(?![0-9])', str(row.get('t.tk_code_abst', ''))) or row.get('t.tk_no') == number]
        if len(matches) != 1: raise ValueError('本批票号未完整入池或存在歧义：'+number)
        row = matches[0]
        if row.get('t.tk_mod_flag') != 'NORMAL' or '已认证' not in str(row.get('t.auth_rst_desc', '')) or not re.fullmatch(r'[a-fA-F0-9]{32}', row.get('t.tk_guid', '')):
            raise ValueError('本批认证仍未确认，不能把队列或保存请求当完成')
        accounted.append({'name': name, 'sha256': source['sha256'], 'filecode': code, 'invoice_number': number,
                          'native_guid': row['t.tk_guid'], 'state': 'pool_authenticated', 'buyer_fields_verified': False})
    has_errors = any(x['state'] == 'recognition_error' for x in accounted)
    if (outcome == 'needs_correction') != has_errors:
        raise ValueError('本批含识别错误须needs_correction；正常批次用saved，不混淆认证与错误')
    return {'phase': 'save_reconciled_with_errors' if has_errors else 'save_reconciled', 'verification': v,
            'uploaded_evidence': staged, 'accounted_files': accounted, 'saved': not has_errors}


def begin_write(paths, c, operation, out, verification=None):
    lock = paths['state'].parent/'pending_write.json'
    record = {'case_id': c['case_id'], 'serial': c.get('serial'), 'operation': operation,
              'started_at_utc': stamp(), 'output_directory': str(out), 'status': 'await_independent_readback'}
    if verification: record['verification'] = verification
    if operation.startswith('upload:') or operation == 'invoice:save-upload':
        if not verification or verification.get('schema') != 'ustc-pending-native-upload-v1' or verification.get('batch', {}).get('caseId') != c['case_id']:
            raise ValueError('上传/保存pending须冻结本批源SHA、票号、明确账号及manifest摘要')
    try:
        with lock.open('x', encoding='utf-8') as f: json.dump(record, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise ValueError('上一写入尚未独立回读，禁止继续写或重试；运行readback/recover核对，再ack-write登记依据')
    return lock


def acknowledge(paths, receipt, note, outcome):
    lock = paths['state'].parent/'pending_write.json'
    previous = json.loads(lock.read_text(encoding='utf-8'))
    if outcome not in ['saved', 'staged', 'not_applied', 'needs_correction']:
        raise ValueError('未知回读结果')
    is_upload = previous['operation'].startswith('upload:') or previous['operation'] == 'invoice:save-upload'
    if outcome == 'staged' and previous['operation'] not in ['submission:options', 'submission:next', 'expense_amount:open', 'expense_amount:return'] and not previous['operation'].startswith('upload:'):
        raise ValueError('staged只用于提交前合同、打印选项和原生下一步，不能冒充最终提交')
    read = json.loads(Path(receipt).read_text(encoding='utf-8'))
    r = read.get('result', {})
    read_time = read.get('executed_at_utc')
    if not read_time or datetime.fromisoformat(read_time) <= datetime.fromisoformat(previous['started_at_utc']):
        raise ValueError('回读须晚于这次写入，不能用历史回执解锁')
    is_submission = previous.get('operation', '').startswith('submission:')
    expense_result = None
    if previous.get('operation') in ['expense_amount:open', 'expense_amount:return','expense_amount:save']:
        from finance_expense_amount import verify_ack
        if previous['operation']=='expense_amount:save':
            from finance_expense_amount import verify_saved_group
            expense_result=verify_saved_group(previous,read,outcome)
        else:expense_result = verify_ack(previous, read, outcome)
    has_upload_tracking = is_upload and isinstance(r.get('uploadTracking'), dict)
    unsaved_payment_result = None
    if (previous.get('operation') == 'step:payments' and outcome in ['needs_correction', 'not_applied']
            and not r.get('grids') and not r.get('frames') and r.get('forms')):
        from payment_recovery import verify_unsaved_payments
        unsaved_payment_result = verify_unsaved_payments(paths, previous, read, outcome)
    if r.get('ok') is not True or (not r.get('grids') and not r.get('frames') and not (is_submission and r.get('forms')) and not has_upload_tracking and expense_result is None and unsaved_payment_result is None) or r.get('projid') != 'WF_YBX':
        raise ValueError('须用readback的独立财务字段/表格回执，不接受保存请求或status摘要')
    if previous.get('serial') and r.get('serial') != previous['serial']:
        if not ((previous.get('operation') == 'submission:department' and not r.get('serial') and '待审批' in r.get('visibleText', ''))
                or (previous.get('operation') == 'submission:applicant-agree' and not r.get('serial') and outcome == 'saved')):
            raise ValueError('回读单号与此次写入不一致')
    upload_result = verify_upload_ack(previous, read, paths, outcome) if is_upload else None
    draft_result = None
    if previous['operation'] == 'invoice:create-draft':
        from finance_draft_readback import classify_draft_readback
        draft_result=classify_draft_readback(r,json.loads(paths['invoices'].read_text(encoding='utf-8')))
        if outcome in ['needs_correction','not_applied'] and draft_result['state']!='blocked_by_invoice_metadata':
            raise ValueError('建单未取得明确拒绝，不能解除未知创建意图')
        if outcome=='saved':raise ValueError('建单按bind-serial核对；不能用ack-write的saved替代真实单号绑定')
    if previous['operation'] == 'submission:options' and outcome in ['staged', 'saved']:
        from submission import verify_options
        verify_options(r, previous['serial'])
    if previous['operation'] == 'step:payments' and outcome == 'saved':
        payment_form = json.loads(paths['form'].read_text(encoding='utf-8'))
        if any(p.get('mode') == 'corporate_transfer' for p in payment_form.get('payments', [])):
            from finance_corporate_verify import verify_corporate_readback
            verify_corporate_readback(payment_form, r)
    if previous['operation'] == 'submission:next' and outcome in ['staged', 'saved']:
        from submission import verify_next
        verify_next(r, previous['serial'])
    if previous['operation'] == 'submission:promise' and outcome == 'saved':
        from submission import verify_promise
        verify_promise(r, previous['serial'])
    if previous['operation'] == 'submission:department' and outcome == 'saved':
        from submission import verify_department
        save_receipt = json.loads(Path(previous['verification']['save_receipt']).read_text())
        submission_plan = json.loads(Path(previous['verification']['submission_plan']).read_text())
        verify_department(r, previous['serial'], save_receipt.get('result', {}).get('results', []), submission_plan['amount'], submission_plan.get('expected_userid'))
    if previous['operation'] == 'submission:applicant-agree' and outcome == 'saved':
        from submission import verify_applicant_agreement
        submission_plan = json.loads(Path(previous['verification']['submission_plan']).read_text())
        form = json.loads(paths['form'].read_text(encoding='utf-8'))
        verify_applicant_agreement(r, previous['serial'], submission_plan['expected_userid'], submission_plan['amount'], form.get('basic', {}).get('teacher_name'))
    if previous['operation'] == 'submission:applicant-agree' and outcome == 'not_applied':
        from submission import verify_sms_gate
        submission_plan=json.loads(Path(previous['verification']['submission_plan']).read_text())
        verify_sms_gate(r,previous['serial'],submission_plan['expected_userid'])
    expected_remark = previous.get('verification', {}).get('expected_remark')
    if outcome=='saved' and expected_remark:
        from correction import remark_matches
        if not remark_matches(r, previous['serial'], expected_remark):raise ValueError('经费备注未在保存后的单据预览出现，不能登记saved')
    expected_basic = previous.get('verification', {}).get('expected_basic')
    saved_form = None
    if outcome == 'saved' and expected_basic:
        from correction import basic_matches
        persisted = previous['verification'].get('persisted_readback')
        if not persisted:
            raise ValueError('摘要和电话须保存后重开字段独立核对')
        reopened = json.loads(Path(persisted).read_text())
        if datetime.fromisoformat(reopened.get('executed_at_utc', '1970-01-01T00:00:00+00:00')) <= datetime.fromisoformat(previous['started_at_utc']) or not basic_matches(reopened.get('result', {}), previous['serial'], expected_basic):
            raise ValueError('保存后的摘要或电话字段不符，不能登记saved')
        saved_form = json.loads(paths['form'].read_text(encoding='utf-8'))
        if saved_form.get('serial') != previous['serial'] or saved_form.get('case_id') != previous['case_id']:
            raise ValueError('本地基本信息案件或单号已改变，不能同步保存结果')
        saved_form.setdefault('basic', {}).setdefault('fields', {}).update(expected_basic)
    if not note.strip(): raise ValueError('写明检查了哪些字段和结果，不用空白说明解锁')
    previous.update(readback_receipt=str(Path(receipt).resolve()), operator_checked_note=note, outcome=outcome, status='operator_readback_recorded')
    if expense_result is not None: previous['expense_amount_window_readback'] = expense_result
    if draft_result is not None:previous['draft_readback']=draft_result
    output = Path(previous['output_directory'])/'operator_readback_ack.json'
    write_json(output, previous)
    state = json.loads(paths['state'].read_text(encoding='utf-8'))
    previous_status = state.get('status')
    if previous['operation'].startswith('correction:'):
        state.update(status='correction_in_progress', correction_last_operation=previous['operation'],
                     correction_last_readback=str(Path(receipt).resolve()))
    if previous['operation'].startswith('submission:'):
        state.update(status='submission_in_progress', submission_last_operation=previous['operation'],
                     submission_last_readback=str(Path(receipt).resolve()), submission_completed=False)
    if previous['operation'] == 'submission:promise' and outcome == 'saved':
        form = json.loads(paths['form'].read_text(encoding='utf-8'))
        form.setdefault('review', {}).update(workflow_started=True, workflow_status='department_selection',
            workflow_start_receipt=str(Path(receipt).resolve()))
        write_json(paths['form'], form)
    if previous['operation'] == 'submission:department' and outcome == 'saved' and str(r.get('node')) != '508':
        state.update(status='submitted_to_approval', submission_completed=True,
                     submission_status_note='已进入待审批列表；领导审批、验证码/本人后续确认仍未完成')
        form = json.loads(paths['form'].read_text(encoding='utf-8'))
        form.setdefault('contract', {}).update(exists=False, source='本案提交前核对：合同选择否；系统回读见submission options')
        form.setdefault('delivery', {}).update(mode='self_print', delegate_finance_print=False,
                                               system_print_flag_observed='否',
                                               system_print_flag_receipt=str(Path(receipt).resolve()),
                                               originals_required=True)
        form.setdefault('review', {}).update(workflow_started=True, workflow_status='submitted_to_approval')
        write_json(paths['form'], form)
    if previous['operation'] == 'submission:applicant-agree' and outcome == 'saved':
        state.update(status='submitted_to_approval', submission_completed=True,
                     submission_status_note='申请人同意已独立回读；后续领导审批及付款未完成', node='')
        form = json.loads(paths['form'].read_text(encoding='utf-8'))
        form.setdefault('review', {}).update(workflow_started=True, workflow_status='submitted_to_approval', node='')
        write_json(paths['form'], form)
    if saved_form is not None:
        state['basic_correction'] = {'fields': expected_basic, 'system_saved_verified': True,
                                    'persisted_readback': persisted, 'preview_readback': str(Path(receipt).resolve())}
        if 't-abst' in expected_basic and (state.get('abstract_format_pending') or previous_status == 'correction_saved_await_resubmission'):
            state.update(abstract_format_pending=False, status='correction_saved_await_resubmission')
            saved_form.setdefault('review', {}).update(workflow_status='returned_correction_saved_await_resubmission')
        write_json(paths['form'], saved_form)
    if outcome == 'saved' and previous['operation'].startswith('step:'):
        stage = previous['operation'].split(':', 1)[1]
        state['operator_verified_stages'] = list(dict.fromkeys(state.get('operator_verified_stages', []) + [stage]))
    if r.get('node') == '508' and r.get('serial') and not previous['operation'].startswith('correction:'):
        c = json.loads(paths['form'].read_text(encoding='utf-8'))
        c.setdefault('review', {}).update(workflow_started=True, workflow_status='applicant_review_pending', node='508')
        write_json(paths['form'], c)
        state.update(status='applicant_review_pending', node='508')
    state['operator_last_readback'] = str(Path(receipt).resolve())
    if upload_result:
        state['last_native_upload_batch'] = upload_result
        previous['upload_reconciliation'] = upload_result
        write_json(output, previous)
    state['updated_at_utc'] = stamp()
    write_json(paths['state'], state)
    lock.unlink()
    return {'ok': True, 'system_write': False, 'output': str(output), 'note': '这是操作助手按核对清单记录的回读，审批通过仍须实际流程证据'}


def render_document(source, destination):
    import shutil
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists(): raise ValueError('渲染目录已存在；指定新的目录保留每次检查')
    env = child_env()
    office, raster = shutil.which('soffice', path=env['PATH']), shutil.which('pdftoppm', path=env['PATH'])
    if not office or not raster: raise ValueError('缺少LibreOffice或pdftoppm，先运行doctor，按换设备说明配置')
    destination.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='reimbursement-render-', dir=ROOT/'.local') as td:
        result = subprocess.run([office, '-env:UserInstallation='+Path(td).as_uri(), '--headless', '--convert-to', 'pdf', '--outdir', str(destination), str(source)], env=env, capture_output=True, text=True, timeout=60)
        pdf = destination/(source.stem+'.pdf')
        if result.returncode or not pdf.exists(): raise ValueError('PDF转换失败：'+(result.stderr or result.stdout)[-1000:])
        result = subprocess.run([raster, '-r', '140', '-png', str(pdf), str(destination/'page')], env=env, capture_output=True, text=True, timeout=60)
        if result.returncode: raise ValueError('页面渲染失败：'+result.stderr[-1000:])
    pages = sorted(str(x) for x in destination.glob('page-*.png'))
    if not pages: raise ValueError('没有生成页面图片，不能宣布可打印')
    receipt = {'ok': True, 'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'pdf': str(pdf),
               'page_count': len(pages), 'pages': pages, 'visual_review_completed': False, 'next': '助手查看每张PNG，检查中文、裁切、跨页和审批栏留白', 'system_write': False}
    write_json(destination/'render_receipt.json', receipt)
    return receipt


def guard_step(c, stage):
    if c.get('review', {}).get('workflow_started'):
        raise ValueError('已有流程；当前入口禁止重新按草稿填写。按总览处理材料和本人审核；明确变更任务另按实际环节续办')
    if stage == 'notes' and c.get('explanations', {}).get('record_only'):
        raise ValueError('本案说明只留档；没有说明同步授权，不能自动改record_only')
    if stage == 'no-contract' and c.get('contract', {}).get('exists') is not False:
        raise ValueError('没有确认无合同；有合同按真实采购业务关联合同与附件')


def guard_invoice_entry(form, manifest, command, action):
    # 已请求建单后允许查票；resume-draft须另验本案最后一次明确拒绝，不清除标记。
    if form.get('serial') or manifest.get('serial') or manifest.get('draft_creation_requested'):
        allowed=['read','open-pool']+(['resume-draft'] if not form.get('serial') and not manifest.get('serial') else [])
        if command!='invoice' or action not in allowed:
            raise ValueError('已有单号或未回查的创建请求，禁止从零上传/建单；明确新案使用init')


def recover(receipt, tab_id):
    from edge_runtime import read_marker, browser_lock
    from finance_backend import assert_original_transport
    previous = json.loads(Path(receipt).read_text(encoding='utf-8'))
    transport = assert_original_transport(previous)
    marker = previous.get('result', {}).get('result_marker', '')
    if not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', marker):
        raise ValueError('此回执没有可恢复的结果标记；应回读系统状态，不重执行脚本')
    if previous.get('tab_id') and previous['tab_id'] != tab_id:
        raise ValueError('恢复须使用原操作的财务标签ID')
    with browser_lock():
        recovered = read_marker(marker, tab_id, timeout=45)
    raw = json.dumps(recovered, ensure_ascii=False)
    result = {'recovered_at_utc': stamp(), 'previous_receipt': str(Path(receipt).resolve()), 'browser_transport': transport, 'script_reexecuted': False,
              'marker_state': raw if raw in ('pending', 'missing', '') else 'result_found'}
    if result['marker_state'] == 'result_found':
        result['result'] = json.loads(raw)
        result['next'] = '这是原脚本回执；仍按保存/上传/流程的独立回读确认业务结果'
    else:
        result['next'] = '结果仍不确定；回读对应业务列表/单据，不盲重试写入'
    target = Path(receipt).with_name(Path(receipt).stem+'_recovered_'+uuid.uuid4().hex[:6]+'.json')
    write_json(target, result)
    result['output'] = str(target)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle', type=Path, help='显式指定operator.json案件入口；不默认选择任何历史案件')
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('overview', help='一次输出案件总览、缺口、能力和下一步')
    d = sub.add_parser('doctor', help='检查Python/OCR/渲染/桥接；不触碰浏览器')
    d.add_argument('--configure', action='store_true', help='生成本机环境和字体配置，可重复运行')
    n = sub.add_parser('init', help='从空参数建立新案，不复制本案事实')
    n.add_argument('--id', required=True); n.add_argument('--directory', type=Path, required=True)
    n.add_argument('--profile', choices=CASE_PROFILES, default='student_activity')
    q = sub.add_parser('check', help='金额、单号、材料规则和可选原件SHA')
    q.add_argument('--hashes', action='store_true')
    q.add_argument('--output', type=Path)
    q = sub.add_parser('register-originals', help='从发票OCR结果登记原件；逐文件SHA重验，异常集中反馈')
    q.add_argument('--scan-result', type=Path, required=True)
    q = sub.add_parser('select-invoices', help='将当前发票池回执与原件匹配，金额/占用/正常认证均通过才能建单')
    q.add_argument('--pool-receipt', type=Path, required=True)
    q = sub.add_parser('bind-serial', help='建单后依据独立回执登记真实单号；不会再发建单请求')
    q.add_argument('--serial', required=True); q.add_argument('--receipt', type=Path, required=True)
    q = sub.add_parser('ack-write', help='按规程检查独立回读后登记依据；解除待回读写锁')
    q.add_argument('--readback', type=Path, required=True); q.add_argument('--note', required=True)
    q.add_argument('--outcome', choices=['saved', 'staged', 'not_applied', 'needs_correction'], required=True)
    q = sub.add_parser('expense-amount', help='原生金额窗口检查及有完整逐票依据的单次减报保存')
    q.add_argument('action', choices=['open', 'inspect', 'return', 'recover','save'])
    q.add_argument('--input', type=Path, required=True); q.add_argument('--request-id', required=True)
    q.add_argument('--output', type=Path, required=True); q.add_argument('--readback', type=Path)
    q.add_argument('--original-marker'); q.add_argument('--execute', action='store_true')
    q.add_argument('--preparation',type=Path)
    sub.add_parser('tabs', help='列出财务标签；ID每次从当前电脑获取')
    for name in ['status', 'readback']:
        q = sub.add_parser(name, help='status读身份/状态；readback保留可见字段/票据，用于独立核对')
        q.add_argument('--tab-id'); q.add_argument('--output', type=Path)
    p = sub.add_parser('plan', help='本地生成填报计划，绝不执行')
    p.add_argument('--steps', default=','.join(STAGES[:-1]))
    s = sub.add_parser('step', help='单阶段计划；加execute才在macOS Edge执行')
    s.add_argument('stage', choices=STAGES); s.add_argument('--execute', action='store_true'); s.add_argument('--tab-id')
    s = sub.add_parser('invoice', help='发票目录/上传窗口/小批认证保存/读池/建草稿，默认仅生成脚本')
    s.add_argument('action', choices=['open-pool', 'open-upload', 'read', 'save-upload', 'create-draft','resume-draft'])
    s.add_argument('--execute', action='store_true'); s.add_argument('--tab-id')
    s = sub.add_parser('upload', help='按原件SHA生成1至10票原生上传脚本；默认不执行')
    s.add_argument('--start', type=int, required=True); s.add_argument('--count', type=int, default=10)
    s.add_argument('--execute', action='store_true'); s.add_argument('--tab-id')
    s = sub.add_parser('scan', help='文字层/OCR缓存，参数仍为材料路径')
    s.add_argument('paths', nargs='+', type=Path); s.add_argument('--invoices', action='store_true')
    s.add_argument('--output', type=Path, required=True)
    r = sub.add_parser('recover', help='只读超时结果标记，绝不重新执行原脚本')
    r.add_argument('--receipt', type=Path, required=True); r.add_argument('--tab-id')
    s = sub.add_parser('correction', help='退回单逐票拆分；本地校验或原生操作，不重新建单/生成审批流程')
    s.add_argument('action', choices=['check', 'verify', 'apply', 'begin', 'select', 'update', 'save', 'return', 'open', 'remark', 'basic', 'basic-read', 'basic-close', 'drafts', 'resume'])
    s.add_argument('--input', type=Path, required=True, help='本案拆分方案JSON')
    s.add_argument('--readback', type=Path); s.add_argument('--group', default='')
    s.add_argument('--key', default=''); s.add_argument('--kind', default='')
    s.add_argument('--execute', action='store_true'); s.add_argument('--tab-id'); s.add_argument('--output', type=Path)
    s = sub.add_parser('submit', help='已授权的同号单据逐步提交；选项及审批结果独立回读')
    from submission import ACTIONS
    s.add_argument('action', choices=ACTIONS)
    s.add_argument('--input', type=Path, required=True)
    s.add_argument('--execute', action='store_true'); s.add_argument('--tab-id'); s.add_argument('--output', type=Path)
    a = sub.add_parser('activity', help='活动审批表字段检查或原模板填表')
    a.add_argument('--input', type=Path, required=True); a.add_argument('--output', type=Path)
    a.add_argument('--check-only', action='store_true')
    a.add_argument('--allow-incomplete', action='store_true', help='仅本地草稿允许未知字段留白，缺项仍列回执')
    a = sub.add_parser('render', help='本机字体配置下生成PDF和所有页面PNG供检查')
    a.add_argument('--input', type=Path, required=True); a.add_argument('--directory', type=Path, required=True)
    args = ap.parse_args()
    if args.command not in {'doctor', 'init', 'tabs', 'recover', 'render', 'activity', 'scan'} and args.bundle is None:
        ap.error('本操作必须明确 --bundle；可先用assistant.py的cases/overview选择案件')
    # 支持从任意工作目录调用绝对路径入口；进入子进程前把参数路径固定。
    for key in ['bundle', 'directory', 'input', 'output', 'scan_result', 'pool_receipt', 'receipt', 'readback']:
        value = getattr(args, key, None)
        if isinstance(value, Path): setattr(args, key, value.resolve())
    if args.command == 'scan': args.paths = [p.resolve() for p in args.paths]
    if args.command == 'doctor': emit(doctor(args.configure)); return 0
    if args.command == 'init': emit(init_case(args.id, args.directory, args.profile)); return 0
    if args.command == 'overview': emit(overview(args.bundle)); return 0
    if args.command == 'tabs':
        if platform.system() != 'Darwin': raise ValueError('此命令仅支持macOS原生桥；Windows仅支持离线开发，静默后端尚未验收')
        from edge_read_probe import list_tabs
        emit({'finance_tab_ids': list_tabs(), 'system_write': False}); return 0
    if args.command == 'recover': emit(recover(args.receipt, args.tab_id)); return 0
    if args.command == 'render':
        (ROOT/'.local').mkdir(exist_ok=True)
        emit(render_document(args.input, args.directory)); return 0
    if args.command == 'activity':
        from activity_form import inspect_input, inspect_draft, build
        values = json.loads(args.input.read_text(encoding='utf-8'))
        result = inspect_draft(values) if args.allow_incomplete else inspect_input(values)
        if not args.check_only and result['ok']:
            if not args.output: raise ValueError('生成表格需要--output，必须不同于原模板')
            result = build(values, args.output, args.allow_incomplete)
        emit(result); return 0 if result['ok'] else 2
    if args.command == 'scan':
        return run_tool('scan_materials.py', [*args.paths, '--output', args.output, '--cache', ROOT/'generated/ocr_cache', *(['--invoices'] if args.invoices else [])]).returncode
    report = check(args.bundle, getattr(args, 'hashes', False))
    if args.command == 'check':
        if args.output: write_json(args.output, report)
        emit(report); return 0 if report['ok'] else 2
    b, paths = bundle(args.bundle)
    c = json.loads(paths['form'].read_text(encoding='utf-8')); m = json.loads(paths['invoices'].read_text(encoding='utf-8'))
    if args.command == 'expense-amount':
        from finance_expense_amount import validate, validate_source, open_script, return_script, inspect_script, digest, PENDING_SCHEMA, SCHEMA,save_script,validate_reduction_plan
        from finance_invoice_retry import run_browser
        request = validate(json.loads(args.input.read_text(encoding='utf-8')))
        if request['case_id'] != c['case_id'] or request['serial'] != c.get('serial'):
            raise ValueError('窗口请求须绑定本案已登记真实单号')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', args.request_id):
            raise ValueError('窗口操作须稳定请求号')
        if args.output.exists() or args.output.with_suffix('.js').exists():
            raise ValueError('保留原窗口意图和回执，不重放')
        action = args.action
        preparation=None
        if action!='save' and args.preparation:raise ValueError('准备文件仅用于减报保存')
        if action in ['open', 'return','save']:
            validate_source(request)
            if args.original_marker: raise ValueError('写动作不能传恢复marker')
            if action == 'open':
                if args.readback: raise ValueError('打开只使用已冻结来源，不接受其他回读')
                source = open_script(request, args.request_id)
            elif action=='return':
                if not args.readback: raise ValueError('返回须有独立282窗口回读')
                source = return_script(request, json.loads(args.readback.read_text(encoding='utf-8')), args.request_id)
            else:
                if not args.preparation or args.readback:raise ValueError('减报保存仅接受已冻结准备文件')
                preparation=validate_reduction_plan(request,json.loads(args.preparation.read_text()))
                source=save_script(request,preparation,args.request_id)
            if not args.execute:
                result = {'ok': True, 'schema': SCHEMA+'-plan', 'action': action, 'request_digest': digest(request), 'target': request,
                          'execute': False, 'system_write': False, 'amount_save_implemented': True, 'requires_independent_readback': True}
                write_json(args.output, result); emit(result); return 0
            args.output.parent.mkdir(parents=True, exist_ok=True)
            begin_write(paths, c, 'expense_amount:'+action, args.output.parent,
                        {'schema': PENDING_SCHEMA, 'request': request, 'request_id': args.request_id, 'action_output': str(args.output),**({'preparation':preparation}if preparation else{})})
        else:
            if args.execute or args.readback: raise ValueError('检查/恢复不接受execute或另一个窗口回读')
            if action == 'recover':
                validate_source(request)
                if not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', args.original_marker or ''):
                    raise ValueError('恢复须有本案原native marker，不重放')
            elif args.original_marker: raise ValueError('只有recover读取原native marker')
            source = inspect_script(request, args.original_marker)
        result = run_browser(source, request, args.output, 'write' if action in ['open', 'return','save'] else 'read')
        emit(result)
        return 0 if result.get('phase') == 'script_completed' and result.get('result', {}).get('ok') is True else 2
    if args.command == 'submit':
        from submission import validate_plan, script, WRITE_ACTIONS
        proposal = json.loads(args.input.read_text())
        validation = validate_plan(proposal, c)
        out = output_dir(paths); out.mkdir(parents=True)
        source = out/'submission.js'; source.write_text(script(c, proposal, args.action, m))
        if not args.execute:
            emit({'ok': True, 'system_write': False, 'script': str(source), 'validation': validation}); return 0
        if platform.system() != 'Darwin' or not args.tab_id: raise ValueError('执行须为macOS Edge并明确当前tab-id')
        pending = paths['state'].parent/'pending_write.json'
        if pending.exists() and args.action not in ['inspect', 'status', 'status-readback', 'print-inspect', 'template-inspect', 'verification-cancel', 'applicant-return', 'dismiss-validation']: raise ValueError('上一写入未独立核对，先readback/ack-write')
        if args.action in WRITE_ACTIONS: begin_write(paths, c, 'submission:'+args.action, out, {'submission_plan': str(args.input), 'save_receipt': str((args.output or out/'submission.json').resolve())})
        return run_tool('edge_runtime.py', [source, '--output', args.output or out/'submission.json', '--quiet', '--tab-id', args.tab_id]).returncode
    if args.command == 'correction':
        from correction import validate_plan, verify_group, script
        proposal = json.loads(args.input.read_text())
        result = validate_plan(proposal, c)
        if args.action == 'check': emit(result); return 0
        if args.action == 'verify':
            if not args.readback: raise ValueError('verify须提供独立逐票回读')
            result = verify_group(json.loads(args.readback.read_text()), proposal, args.group)
            if args.output: write_json(args.output, result)
            emit(result); return 0
        if args.action == 'apply':
            if args.group not in proposal['expected_groups']: raise ValueError('apply须明确发票分组')
            if not args.execute: emit({'ok': True, 'system_write': False, 'validation': result, 'group': args.group,
                                       'steps': ['open', 'clear_old_funding', 'remainder', 'travel_funding', 'verify', 'save', 'reopen_verify']}); return 0
            if platform.system() != 'Darwin' or not args.tab_id: raise ValueError('执行须为macOS Edge并明确当前tab-id')
            if (paths['state'].parent/'pending_write.json').exists(): raise ValueError('上一写入尚未独立回读，先recover/readback')
            from correction_batch import apply_group
            out = output_dir(paths)
            result = apply_group(paths, c, proposal, args.group, args.tab_id, out)
            if args.output: write_json(args.output, result)
            emit(result); return 0
        out = output_dir(paths); out.mkdir(parents=True)
        source = out/'correction.js'; source.write_text(script(c, proposal, args.action, args.key, args.group, args.kind))
        if not args.execute: emit({'ok': True, 'system_write': False, 'script': str(source), 'validation': result}); return 0
        if platform.system() != 'Darwin' or not args.tab_id: raise ValueError('执行须为macOS Edge并明确当前tab-id')
        pending = paths['state'].parent/'pending_write.json'
        if pending.exists():
            previous = json.loads(pending.read_text())
            if args.action not in ['basic-read', 'basic-close'] or previous.get('serial') != c['serial'] or previous.get('operation') != 'correction:basic:':
                raise ValueError('上一写入未独立回读，先recover/readback，不开始新动作')
        if args.action in ['begin', 'update', 'save', 'remark', 'basic']:
            from correction import funding_remark, basic_fields
            begin_write(paths, c, 'correction:'+args.action+':'+args.group, out,
                        {'expected_basic':basic_fields(proposal)} if args.action=='basic' else {'expected_remark':funding_remark(proposal)} if args.action=='remark' else None)
        receipt = args.output or out/'correction.json'
        code = run_tool('edge_runtime.py', [source, '--output', receipt, '--quiet', '--tab-id', args.tab_id]).returncode
        if code == 0 and args.action == 'basic-read' and pending.exists():
            from correction import basic_matches
            read = json.loads(Path(receipt).read_text())
            previous = json.loads(pending.read_text())
            if not basic_matches(read.get('result', {}), c['serial'], previous['verification']['expected_basic']):
                raise ValueError('保存后重开摘要/电话与目标不一致，保留待回读状态')
            previous['verification']['persisted_readback'] = str(Path(receipt).resolve())
            write_json(pending, previous)
        return code
    if args.command == 'ack-write': emit(acknowledge(paths, args.readback, args.note, args.outcome)); return 0
    if args.command in ['register-originals', 'select-invoices', 'bind-serial']:
        if c.get('serial') or m.get('serial'):
            same_binding = args.command == 'bind-serial' and all(x.get('serial') in [None,args.serial] for x in [c,m])
            if not same_binding: raise ValueError('已有单号，不能覆盖原件/选择关系/单号；按原案继续')
        if args.command == 'register-originals':
            if m.get('original_files') or m.get('draft_creation_requested'): raise ValueError('已登记原件或已请求建单；先核对，不能覆盖')
            scan = json.loads(args.scan_result.read_text(encoding='utf-8'))
            rows, problems = [], []
            seen = set()
            for i, row in enumerate(scan.get('records', [])):
                p = Path(row['path']).resolve(); digest = hashlib.sha256(p.read_bytes()).hexdigest()
                if digest != row['sha256'] or digest in seen: raise ValueError('扫描后原件变化或字节重复：'+str(p))
                if p.suffix.lower() not in ['.pdf', '.ofd', '.png', '.jpg', '.jpeg']: raise ValueError('此格式不能自动上传：'+str(p))
                seen.add(digest)
                candidates = [x['value'] if isinstance(x, dict) else x for x in row['invoice_number_candidates']]
                metadata = {k: row[k] for k in ['invoice_code', 'material_owner', 'document_family'] if k in row}
                if 'invoice_code' in metadata and (not isinstance(metadata['invoice_code'], str) or not re.fullmatch(r'[0-9]{8}', metadata['invoice_code'])):
                    raise ValueError('医疗票据代码须保留票面8位文本')
                if any(not isinstance(v, str) or not v for v in metadata.values()):
                    raise ValueError('票据来源元数据须为非空文本')
                rows.append({'path': str(p), 'sha256': digest, 'invoice_number_candidates': candidates, 'ocr_issues': row['issues'], 'visual_check': 'not_reviewed', **metadata})
                if row['issues']: problems.append({'index': i, 'path': str(p), 'issues': row['issues']})
            if not rows: raise ValueError('扫描结果没有原件')
            m.update(original_files=rows, expected_count=len(rows)); write_json(paths['invoices'], m)
            c['expected_invoice_count'] = len(rows); write_json(paths['form'], c)
            emit({'ok': True, 'count': len(rows), 'needs_review': problems, 'system_write': False, 'next': '一票一文件异常先核对；正常上传后用select-invoices核对当前发票池'}); return 0
        if args.command == 'select-invoices':
            if m.get('draft_creation_requested'):
                from finance_draft_readback import verify_unconfigured_creation
                pending=paths['state'].parent/'pending_write.json';previous=json.loads(pending.read_text())
                original_path=Path(m.get('creation_receipt','')).resolve();root=paths['state'].parent.resolve()
                if (previous.get('case_id')!=c['case_id']or previous.get('serial')or previous.get('operation')!='invoice:create-draft'
                    or not original_path.is_relative_to(root/'receipts')or original_path.parent!=Path(previous['output_directory']).resolve()):
                    raise ValueError('原创建pending与本案回执不一致，不更改选择登记')
                original=json.loads(original_path.read_text());read=json.loads(args.pool_receipt.read_text())
                configured=json.loads((ROOT/'data/finance_access.json').read_text()).get('username')
                selected=verify_unconfigured_creation(m,{**c,'expected_userid':configured},original,read)
                saved_read=original_path.parent/'selection-recovery-readback.json'
                if saved_read.exists():raise ValueError('已有配置拒绝恢复记录，保留原记录')
                saved_read.write_bytes(args.pool_receipt.read_bytes())
                recovery={'original_receipt':str(original_path),'original_receipt_sha256':hashlib.sha256(original_path.read_bytes()).hexdigest(),
                          'readback_receipt':str(saved_read),'readback_sha256':hashlib.sha256(saved_read.read_bytes()).hexdigest(),'expected_userid':configured,
                          'reason':'固定原脚本在任何原生选票/建单动作前因未登记选择参数拒绝；新独立票池核对全部原件/票号/金额/认证及未占用后补登记。'}
                selected.update(selection_receipt=str(saved_read),unconfigured_creation_recovery=recovery)
                previous.update(readback_receipt=str(saved_read),outcome='not_applied',status='operator_readback_recorded',operator_checked_note=recovery['reason'])
                write_json(original_path.parent/'operator_readback_ack.json',previous);write_json(paths['invoices'],selected);pending.unlink()
                emit({'ok':True,'count':len(selected['selected_invoices']),'expected_amount':selected['expected_amount'],'original_creation_preserved':True,'resume_action':'resume-draft','system_write':False});return 0
            from select_case_invoices import select
            selected = select(m, json.loads(args.pool_receipt.read_text(encoding='utf-8')), c['amount'])
            selected['selection_receipt'] = str(args.pool_receipt.resolve())
            write_json(paths['invoices'], selected)
            emit({'ok': True, 'count': len(selected['selected_invoices']), 'expected_amount': selected['expected_amount'], 'system_write': False}); return 0
        if not re.fullmatch(r'\d+', args.serial): raise ValueError('单号须为数字')
        read = json.loads(args.receipt.read_text(encoding='utf-8'))
        r = read.get('result', read)
        if r.get('serial') != args.serial or r.get('projid') != 'WF_YBX' or r.get('ok') is not True or not m.get('draft_creation_requested'):
            raise ValueError('需要原建单请求及当前财务单据独立回读；列表中其他单号或创建请求不作依据')
        selected = m.get('selected_invoices', [])
        rows = [row for grid in r.get('grids', []) for row in grid.get('rows', [])]
        bound = bool(selected) and all(any(row.get('t.tk_guid') == ticket['guid'] and row.get('t.serial_no') == args.serial for row in rows) for ticket in selected)
        from validate_config import money
        preview = False
        for frame in r.get('frames', []):
            text = re.sub(r'\s+', '', frame.get('visibleText', '')).replace(',', '').replace('，', '')
            match = re.search(r'共选择(\d+)张发票[、,，]?合计(\d+(?:\.\d{1,2})?)元', text)
            if ('报销单号：'+args.serial) in text and match and int(match[1]) == m.get('expected_count') and money(match[2]) == money(m.get('expected_amount', '')):
                preview = True
        if not (bound or preview): raise ValueError('单号回读还需所选GUID占用证明或同单号预览的票数/合计一致，避免绑定其他单')
        pending = paths['state'].parent/'pending_write.json'
        creation = json.loads(pending.read_text(encoding='utf-8')) if pending.exists() else None
        if creation is not None:
            if (creation.get('operation') != 'invoice:create-draft' or creation.get('case_id') != c['case_id']
                    or creation.get('serial') not in [None, args.serial]):
                raise ValueError('待回读意图不是本案原建单，不能通过绑定单号解除')
            if (not read.get('executed_at_utc') or
                    datetime.fromisoformat(read['executed_at_utc']) <= datetime.fromisoformat(creation['started_at_utc'])):
                raise ValueError('单号绑定回读须晚于原建单意图')
        for key in ['case', 'form', 'invoices', 'state']:
            current = json.loads(paths[key].read_text(encoding='utf-8'))
            if current.get('case_id') != c['case_id'] or current.get('serial') not in [None, args.serial]:
                raise ValueError('本地案件或已有单号与绑定结果不符')
        for key in ['case', 'form', 'invoices', 'state']:
            value = json.loads(paths[key].read_text(encoding='utf-8')); value['serial'] = args.serial
            if key == 'state': value.update(status='draft_created_readback', updated_at_utc=stamp(), creation_readback=str(args.receipt.resolve()))
            write_json(paths[key], value)
        if creation is not None:
            creation.update(status='draft_bound_readback_recorded', outcome='saved', bound_serial=args.serial,
                readback_receipt=str(args.receipt.resolve()),
                operator_checked_note='原建单后的独立回读：单号、项目及所选GUID或票数金额一致；完成本地绑定，未重发建单。')
            write_json(Path(creation['output_directory'])/'operator_readback_ack.json', creation)
            pending.unlink()
        emit({'ok': True, 'serial': args.serial, 'system_write': False, 'next': 'check --hashes后按步骤填写'}); return 0
    out = output_dir(paths); out.mkdir(parents=True)
    if args.command in ['status', 'readback']:
        if not c.get('serial'): raise ValueError('尚无单号，先读取发票池/建单列表')
        output = args.output or out/'readback.json'
        tool = 'finance_cli.py' if args.command == 'status' else 'invoice_cli.py'
        params = ['status', '--case', paths['form']] if tool == 'finance_cli.py' else ['read', '--manifest', paths['invoices']]
        return run_tool(tool, [*params, '--output', output, *(['--tab-id', args.tab_id] if args.tab_id else [])]).returncode
    if args.command in ['plan', 'step']:
        if args.command == 'step' and args.stage in ['basic', 'funding']:
            report = check(args.bundle, validation_stage=args.stage)
        if not report['ok']: emit(report); return 2
        stage = getattr(args, 'stage', None)
        if stage: guard_step(c, stage)
        steps = stage or args.steps
        execute = getattr(args, 'execute', False)
        if execute:
            if platform.system() != 'Darwin': raise ValueError('实际执行目前仅macOS Edge；Windows静默后端尚未验收，不自动转前台')
            status_path = out/'before_status.json'
            result = run_tool('finance_cli.py', ['status', '--case', paths['form'], '--output', status_path, *(['--tab-id', args.tab_id] if args.tab_id else [])])
            if result.returncode: return result.returncode
            status = json.loads(status_path.read_text(encoding='utf-8'))['result']
            if status.get('applicantAgreementVisible') or status.get('node') == '508': raise ValueError('现场已到本人审核，禁止重走填写/普通承诺')
            begin_write(paths, c, 'step:'+stage, out)
        return run_tool('run_case.py', ['--case', paths['form'], '--steps', steps, '--output-dir', out, *(['--execute'] if execute else []), *(['--tab-id', args.tab_id] if getattr(args, 'tab_id', None) else [])]).returncode
    if args.command in ['invoice', 'upload']:
        execute = args.execute
        if execute and platform.system() != 'Darwin': raise ValueError('此原生发票执行桥目前仅macOS Edge；Windows仅支持离线开发，静默后端尚未验收')
        if execute:guard_invoice_entry(c,m,args.command,getattr(args,'action',None))
        if args.command == 'invoice':
            save_options = []
            if execute and args.action == 'save-upload':
                state = json.loads(paths['state'].read_text(encoding='utf-8'))
                staged = state.get('last_native_upload_batch', {})
                if staged.get('phase') != 'uploaded_uncommitted':
                    raise ValueError('先用本批独立上传追踪staged核对；不直接保存未知或其他批次')
                expected = staged.get('verification', {}).get('batch', {})
                verification = freeze_upload_batch(paths, c['case_id'], expected.get('offset'), expected.get('count'),
                                                   'save-upload', staged.get('uploaded_evidence'))
                if verification['batch'] != expected: raise ValueError('暂存后本批manifest/SHA发生变化')
                begin_write(paths, c, 'invoice:save-upload', out, verification)
                save_options = ['--offset', expected['offset'], '--limit', expected['count']]
            elif execute and args.action in ['create-draft','resume-draft']:
                if args.action=='resume-draft':
                    from finance_draft_readback import prepare_rejected_draft_resume
                    prepare_rejected_draft_resume(m,c,paths['state'].parent)
                else:
                    from finance_draft_readback import validate_creation_selection
                    validate_creation_selection(m)
                begin_write(paths, c, 'invoice:create-draft', out)
            if args.action=='resume-draft':save_options+=['--form',paths['form']]
            return run_tool('invoice_cli.py', [args.action, '--manifest', paths['invoices'], '--output', out/'invoice.json',
                            *save_options, *([] if execute else ['--emit-script', out/'invoice.js']), *(['--tab-id', args.tab_id] if args.tab_id else [])]).returncode
        script = out/'upload_private.js'
        if execute:
            for row in m.get('original_files', [])[args.start:args.start+args.count]:
                status = row.get('visual_check')
                verified = status in ['verified', 'user_confirmed'] if isinstance(status, str) else isinstance(status, dict) and status.get('status') == 'verified'
                if row.get('ocr_issues') and not verified: raise ValueError('本批原件OCR异常未复核：'+row['path'])
        prepared = run_tool('prepare_native_upload.py', ['--manifest', paths['invoices'], '--offset', args.start, '--limit', args.count, '--output', script])
        if prepared.returncode or not execute: return prepared.returncode
        verification = freeze_upload_batch(paths, c['case_id'], args.start, args.count)
        begin_write(paths, c, 'upload:'+str(args.start), out, verification)
        return run_tool('edge_runtime.py', [script, '--output', out/'upload.json', '--quiet', '--operation-kind', 'write', *(['--tab-id', args.tab_id] if args.tab_id else [])]).returncode
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        emit({'ok': False, 'error': str(exc), 'next': '按docs/助手操作规程.md对应情形处理；超时先回读，不能自动重试写入'})
        raise SystemExit(2)
