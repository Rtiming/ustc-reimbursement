"""Open one registered native draft, then capture a fresh independent preview.

This fixed browser-local route never saves, audits, acknowledges or searches
outside the currently loaded native drafts list. A dispatched phase is not
replayed, including when its result is unknown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from operator_support import stamp
from validate_config import load_context

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ('open-draft-intent.json', 'open-draft-navigation.json',
             'open-draft-inspect.json', 'open-draft-readback.json',
             'open-draft-readback-2.json', 'open-draft-readback-3.json', 'open-draft-readback-4.json')
HOME_ONCLICK = "if(defaultWin){var defWin=defaultWin.split(':');$.page.openExternal(defWin[0],defWin[1],defWin[2]);}else{window.location.reload();}"
LEGACY_AE_PROOF_ENV = 'REIMBURSEMENT_LEGACY_AE_INVENTORY_PROOF'
# 原生通道自洽隔离：不要求AppleEvent观察，改为"只绑定本次原生标签 + 明确记录AE不可证"。
NATIVE_ONLY_ISOLATION_ENV = 'REIMBURSEMENT_NATIVE_ONLY_ISOLATION'
AE_BACKENDS = (None, 'edge-applescript', 'edge_applescript')


def _legacy_ae_identity(value):
    """Missing old metadata is AE; explicit nested transport must also prove AE."""
    if (value.get('tab_id_namespace', 'edge-applescript') != 'edge-applescript'
            or value.get('backend') not in AE_BACKENDS):
        return False
    if 'browser_transport' in value:
        transport = value['browser_transport']
        if (not isinstance(transport, dict) or transport.get('backend') not in AE_BACKENDS[1:]
                or transport.get('tab_id_namespace') != 'edge-applescript'
                or transport.get('platform', 'darwin') != 'darwin'):
            return False
    return True


def _complete_inventory_ids(value):
    ids = value.get('all_tab_ids')
    if (value.get('tab_inventory_complete') is not True or not isinstance(ids, list) or len(ids) > 10000
            or any(not isinstance(item, str) or not re.fullmatch(r'[0-9]+', item) for item in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('原生全部标签盘点不完整，不能把过滤后的未命中认作闭页')
    return ids


def _inventory_proof(inventory, namespace):
    encoded = json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'tab_id_namespace': namespace, 'collected_at_utc': stamp(),
            'sha256': hashlib.sha256(encoded).hexdigest(), 'sha256_encoding': 'canonical-json-utf8',
            'inventory': json.loads(encoded)}


def is_open_draft(request):
    return (request.get('operation') == 'read_system'
            and request.get('parameters', {}).get('mode') == 'open-draft')


def pending_read_reference(parameters):
    return ({k:parameters.get(k) for k in ['job_id','artifact_id','serial','expected_userid']}
            if parameters.get('job_id') is not None else None)


def validate_context(service, request):
    from assistant_api import APIError
    a = request['parameters']
    base = {'mode', 'serial', 'expected_userid', 'tab_id'}
    if (request.get('execute') is not False or a.get('mode') != 'open-draft'
            or set(a) not in [base, base|{'job_id','artifact_id'}]):
        raise APIError('open_draft_parameters', 'open-draft只接本案单号/账号/标签及成对的正式空预览回执引用；不接受execute或任意恢复开关')
    for key, pattern in [('serial', r'[0-9]+'), ('tab_id', r'[0-9]+'),
                         ('expected_userid', r'[A-Za-z0-9]{2,40}')]:
        if not isinstance(a.get(key), str) or not re.fullmatch(pattern, a[key]):
            raise APIError('open_draft_identity', '打开草稿须有实际ASCII单号、账号和财务tab_id')
    _, paths = service.resolve(request['case_id'])
    form = load_context(paths['form'])
    if form['serial'] != a['serial']:
        raise APIError('open_draft_serial', '参数单号必须严格等于本案form已登记单号')
    if form.get('expected_userid') not in (None, '', a['expected_userid']):
        raise APIError('open_draft_user', '本案已登记账号与本次账号不同')
    return paths, {**form, 'expected_userid': a['expected_userid']}


def closed_department_source(service, case, pending, target_tab_id):
    """Prove the original registered request's tab; never prove it was saved."""
    from assistant_api import APIError, read_json, canonical_request, request_fingerprint
    def reject():
        raise APIError('open_draft_pending', '外案待核来源、原请求或注册回执不完整；保留原pending')
    if (pending.get('case_id') != case or pending.get('operation') != 'submission:department'
            or pending.get('status') != 'await_independent_readback'
            or not isinstance(pending.get('serial'), str) or not re.fullmatch(r'[0-9]+', pending['serial'])):
        reject()
    value = pending.get('verification', {}).get('save_receipt')
    if not isinstance(value, str) or not Path(value).is_absolute(): reject()
    source = Path(value)
    if source.parent.parent != service.jobs/case or source.name != 'output.json': reject()
    folder = service.job_folder(case, source.parent.name)
    status = read_json(folder/'status.json')
    rows = [row for row in status.get('artifacts', []) if row.get('path') == value]
    if (status.get('status') != 'completed' or status.get('exit_code') != 0
            or status.get('recovery_required') or len(rows) != 1): reject()
    artifact = service.artifact(case, folder.name, rows[0].get('artifact_id'))
    if str(artifact) != value: reject()
    request = read_json(folder/'request.json')
    params = request.get('parameters', {})
    if (request.get('case_id') != case or request.get('operation') != 'submission'
            or request.get('execute') is not True or set(params) != {'action', 'plan', 'tab_id'}
            or params.get('action') != 'department' or not isinstance(params.get('plan'), dict)
            or params['plan'].get('serial') != pending['serial']
            or request.get('fingerprint') != request_fingerprint(canonical_request('submission', params, True))): reject()
    original_tab = params.get('tab_id')
    if (not isinstance(original_tab, str) or not re.fullmatch(r'[0-9]+', original_tab)
            or original_tab == target_tab_id): reject()
    if (pending.get('verification', {}).get('submission_plan') != str(folder/'submission_input.json')
            or read_json(folder/'submission_input.json') != params['plan']): reject()
    envelope = read_json(artifact);body = envelope.get('result', {})
    if (envelope.get('tab_id') != original_tab or envelope.get('phase') != 'script_completed'
            or body.get('ok') is not True or body.get('action') != 'save_department_approvers'
            or body.get('serial') != pending['serial'] or body.get('requiresReadback') is not True): reject()
    # This existing isolation contract proves legacy AppleEvent tabs only.
    # An extension tab ID, even with identical digits, is a different namespace.
    if not _legacy_ae_identity(envelope) or not _legacy_ae_identity(request): reject()
    return {'case_id': case, 'serial': pending['serial'], 'job_id': folder.name,
            'artifact_id': rows[0]['artifact_id'], 'artifact_path': value, 'sha256': rows[0]['sha256'],
            'original_tab_id': original_tab, 'target_tab_id': target_tab_id,
            'source_tab_id_namespace': 'edge-applescript',
            'pending_preserved': True, 'system_result_verified': False}


def qualify_department_current_draft(service, case, pending, target_tab_id, reference):
    """旧准备请求已结束，但原单现为草稿；仅准只读打开，不推断旧结果。"""
    from assistant_api import APIError, read_json
    if pending.get('operation') == 'submission:department':
        closed_department_source(service, case, pending, target_tab_id)
    elif pending.get('operation') == 'submission:next':
        saved_path=Path(pending.get('verification',{}).get('save_receipt',''))
        if saved_path.parent.parent != service.jobs/case or saved_path.name!='output.json':
            raise APIError('prepare_current_draft','旧准备动作须来自同案正式任务')
        saved=read_json(service.artifact(case,saved_path.parent.name,'a0'))
        original=read_json(service.job_folder(case,saved_path.parent.name)/'request.json')
        params=original.get('parameters',{});body=saved.get('result',{})
        if (original.get('operation')!='submission' or original.get('execute') is not True
                or params.get('action')!='next' or params.get('plan',{}).get('serial')!=pending.get('serial')
                or saved.get('phase')!='script_completed' or body.get('ok') is not True
                or body.get('action')!='submission_next' or body.get('serial')!=pending.get('serial')
                or read_json(service.resolve(case)[1]['form']).get('review',{}).get('workflow_started')):
            raise APIError('prepare_current_draft','只读恢复仅用于已结束的准备下一步；承诺、建单和同意的未知结果不能套用')
    else:
        raise APIError('prepare_current_draft','此恢复不适用于其他未知动作')
    path = service.artifact(case, reference['job_id'], reference['artifact_id'])
    request = read_json(service.job_folder(case, reference['job_id'])/'request.json')
    observed = read_json(path); body = observed.get('result', {})
    form = read_json(service.resolve(case)[1]['form'])
    params = request.get('parameters', {})
    if (request.get('case_id') != case or request.get('operation') != 'read_system'
            or params.get('mode', 'readback') != 'readback' or params.get('tab_id') != target_tab_id
            or observed.get('tab_id') != target_tab_id or observed.get('phase') != 'script_completed'
            or body.get('ok') is not True or body.get('projid') != 'WF_YBX'
            or body.get('identity', {}).get('userid') != reference['expected_userid']
            or body.get('serial') not in (None, '') or body.get('visibleDialogs')
            or observed.get('executed_at_utc','') <= pending['started_at_utc']):
        raise APIError('department_current_draft', '须先由当前账号只读查到原单草稿，不凭缓存解除旧任务')
    rows = [r for g in body.get('grids', []) if str(g.get('funcno')) == '101'
            and g.get('visible') and not g.get('loading') for r in g.get('rows', [])
            if r.get('t.serial_no') == form['serial']]
    if (len(rows) != 1 or str(rows[0].get('t.status')) != '-99' or rows[0].get('t.status_name') != '草稿'
            or rows[0].get('t.node_id') or rows[0].get('t.uni_key')
            or Decimal(rows[0]['t.total_amt'].replace(',','')) != Decimal(form['amount'])
            or not str(rows[0].get('t.REQ_INFO','')).startswith(reference['expected_userid']+'-')
            or rows[0].get('t.abst') != form['basic']['fields']['t-abst']):
        raise APIError('department_current_draft', '当前原单身份、金额、摘要或草稿状态不同，保持旧pending')
    pending_path = service.resolve(case)[1]['state'].parent/'pending_write.json'
    return {'classification':'previous_prepare_request_current_draft_readonly', 'case_id':case,
            'serial':form['serial'], 'source_readback':reference,
            'original_pending_path':str(pending_path), 'original_pending_sha256':hashlib.sha256(pending_path.read_bytes()).hexdigest(),
            'original_pending_preserved':True, 'save_replayed':False, 'old_submission_result_inferred':False}


def ensure_globally_idle(service, own_case, own_job, target_tab_id=None, recovery_source=None):
    """Reuse current operator/pending/job/lease records; old unknown is history."""
    from assistant_api import APIError, read_json
    if target_tab_id is not None and (not isinstance(target_tab_id, str) or not re.fullmatch(r'[0-9]+', target_tab_id)):
        raise APIError('open_draft_identity', '现场隔离须明确当前数字target_tab_id')
    pending_rows = []; own_recovery = None
    for item in service.cases()['cases']:
        case = item['case_id']
        _, paths = service.resolve(case)
        # Existence is authoritative, even if the pending JSON is incomplete.
        if (paths['state'].parent/'pending_write.json').exists():
            if case == own_case and recovery_source is not None and target_tab_id is not None:
                current_pending = read_json(paths['state'].parent/'pending_write.json')
                if current_pending.get('operation') in ('submission:department','submission:next'):
                    own_recovery = qualify_department_current_draft(service,case,current_pending,target_tab_id,recovery_source)
                else:
                    from finance_saved_payment_reopen import qualify
                    own_recovery = qualify(service,case,current_pending,target_tab_id,recovery_source)
            else:
                if target_tab_id is None or case == own_case:
                    raise APIError('open_draft_pending', '有案件原写入尚未独立核对；本案原pending及默认全局护栏不隔离')
                path = paths['state'].parent/'pending_write.json'
                pending_rows.append((case, path, path.read_bytes()))
        if any(case != own_case or job['job_id'] != own_job
               for job in service.active_jobs(case)):
            raise APIError('open_draft_active', '有案件任务仍在排队或执行；先等原任务，不切换草稿')
    if recovery_source is not None and own_recovery is None:
        raise APIError('saved_payment_reopen','原支付已无pending，不使用旧空预览恢复来源')
    locks = service.root/'.local/assistant_locks'
    for path in [locks/'browser.lock', *sorted(locks.glob('case-*.lock'))]:
        if not path.exists():
            continue
        lock = read_json(path)
        if not isinstance(lock, dict) or lock.get('retain_for_recovery') is True:
            raise APIError('open_draft_retained_lease', '当前恢复锁尚未解除；保留原请求和现场，不切换草稿')
    recovery_fields={'same_case_pending_readback':own_recovery} if own_recovery else {}
    if own_recovery and own_recovery.get('classification')=='saved_payment_blank_preview_independent_new_tab_read':
        # 扩展到提交恢复后，保留支付恢复旧消费者的字段合同。
        recovery_fields['same_case_saved_payment_readback']=own_recovery
    if not pending_rows:
        return {'isolated_pending': [], **recovery_fields}
    isolated = [closed_department_source(service, case, json.loads(raw), target_tab_id) for case, path, raw in pending_rows]
    # Isolation is only checked while the formal caller holds the browser lease.
    lock = read_json(locks/'browser.lock')
    allowed_pids = {os.getpid()}
    running = service.jobs/own_case/str(own_job)/'running.json'
    if own_job and running.is_file():
        worker_pid = read_json(running).get('worker_pid')
        if type(worker_pid) is int and worker_pid > 0: allowed_pids.add(worker_pid)
    if lock.get('pid') not in allowed_pids or not lock.get('lock_id'):
        raise APIError('open_draft_isolation_lease', '外案闭页隔离须持有正式全局browser lease')
    from finance_session import native_inventory, native_inventory_apple_event, ENTRY
    inventory = native_inventory()
    namespace = inventory.get('tab_id_namespace', 'edge-applescript')
    native_only = False
    try:
        current_ids = _complete_inventory_ids(inventory)
        target = [tab for tab in inventory.get('tabs', []) if tab.get('tab_id') == target_tab_id and tab.get('path') == ENTRY]
        if target_tab_id not in current_ids or len(target) != 1:
            raise ValueError('当前目标标签不在唯一完整财务盘点中')
        current_proof = _inventory_proof(inventory, namespace)
        if _legacy_ae_identity(inventory):
            if not inventory.get('observed_at_utc'):
                raise ValueError('原AppleEvent盘点缺少观察时间')
            legacy, legacy_proof = inventory, current_proof
        elif os.environ.get(NATIVE_ONLY_ISOLATION_ENV) == '1':
            # 原生通道自洽隔离：AppleEvent标签号与扩展标签号本来就不互认，用AE去证明
            # 扩展下的旧标签是否关闭在语义上不成立；这里改为把安全边界放在"只绑定本次
            # 原生标签"上——业务动作只可能作用于target_tab_id，历史标签一律不触碰，
            # 且明确记录"AE关闭状态不可证"，不把未证当成已证。
            from finance_backend import transport_identity, NM_NAMESPACE
            transport = transport_identity()
            window = transport.get('window_id')
            if (transport.get('platform') != 'darwin' or transport.get('backend') != 'edge_native_messaging'
                    or inventory.get('backend') != 'edge_native_messaging'
                    or not isinstance(namespace, str) or not NM_NAMESPACE.fullmatch(namespace)
                    or transport.get('tab_id_namespace') != namespace
                    or not isinstance(window, str) or not re.fullmatch(r'[0-9]+', window)
                    or target[0].get('window_id') != window
                    or target[0].get('tab_id_namespace', namespace) != namespace
                    or len([w for w in inventory.get('windows', []) if w.get('window_id') == window]) != 1
                    or type(inventory.get('all_tab_count')) is not int or inventory['all_tab_count'] != len(current_ids)
                    or inventory.get('tab_inventory_scope') != 'extension_visible_profile_tabs'
                    or inventory.get('window_inventory_scope') != 'normal'
                    or ('browser_transport' in inventory and inventory['browser_transport'] != transport)):
                raise ValueError('原生通道自洽隔离：本次命名空间、窗口或目标标签未闭合')
            ae_status = 'not_attempted'
            try:
                from finance_session import native_inventory_apple_event
                native_inventory_apple_event()
                ae_status = 'available_but_unused'
            except Exception as error:
                ae_status = 'unavailable:' + type(error).__name__
            legacy = inventory
            legacy_proof = {'tab_id_namespace': namespace, 'native_only_isolation': True,
                            'apple_event_closure': ae_status,
                            'collected_at_utc': stamp(), 'sha256': current_proof['sha256'],
                            'inventory': current_proof['inventory']}
            native_only = True
        else:
            # A second, explicitly enabled attribute observation proves only
            # closed legacy tabs. It never evaluates a page or retries via AE.
            if sys.platform != 'darwin' or os.environ.get(LEGACY_AE_PROOF_ENV) != '1':
                raise ValueError('默认不允许用Native Messaging盘点证明旧AppleEvent标签已关闭')
            from finance_backend import transport_identity, NM_NAMESPACE
            transport = transport_identity()
            window = transport.get('window_id')
            if (transport.get('platform') != 'darwin' or transport.get('backend') != 'edge_native_messaging'
                    or inventory.get('backend') != 'edge_native_messaging'
                    or not isinstance(namespace, str) or not NM_NAMESPACE.fullmatch(namespace)
                    or transport.get('tab_id_namespace') != namespace
                    or not isinstance(window, str) or not re.fullmatch(r'[0-9]+', window)
                    or target[0].get('window_id') != window
                    or target[0].get('tab_id_namespace', namespace) != namespace
                    or len([w for w in inventory.get('windows', []) if w.get('window_id') == window]) != 1
                    or type(inventory.get('all_tab_count')) is not int or inventory['all_tab_count'] != len(current_ids)
                    or inventory.get('tab_inventory_scope') != 'extension_visible_profile_tabs'
                    or inventory.get('window_inventory_scope') != 'normal'
                    or ('browser_transport' in inventory and inventory['browser_transport'] != transport)):
                raise ValueError('当前Mac NM命名空间、窗口或目标标签未与本次显式通道闭合')
            legacy = native_inventory_apple_event()
            if (not _legacy_ae_identity(legacy) or legacy.get('backend') not in AE_BACKENDS[1:]
                    or legacy.get('tab_id_namespace') != 'edge-applescript' or not legacy.get('observed_at_utc')):
                raise ValueError('显式遗留属性观察未返回原AppleEvent命名空间与时间')
            legacy_proof = _inventory_proof(legacy, 'edge-applescript')
        ids = _complete_inventory_ids(legacy)
    except (ValueError, TypeError, AttributeError) as error:
        raise APIError('open_draft_pending', str(error)) from error
    if read_json(locks/'browser.lock') != lock:
        raise APIError('open_draft_isolation_lease', '双盘点期间全局browser lease变化；保留原pending')
    for proof, (_, path, raw) in zip(isolated, pending_rows):
        if path.read_bytes() != raw:
            raise APIError('open_draft_pending', '隔离核对期间原pending变化，停止并保留当前状态')
        if native_only:
            # 标签号跨命名空间不可比：不假装已证明关闭，只记录不可证与限定范围。
            proof.update(closed_tab_confirmed=False, closed_tab_unprovable=True,
                         isolation_basis='native_only_target_tab_binding',
                         apple_event_closure=legacy_proof.get('apple_event_closure'))
        else:
            if proof['original_tab_id'] in ids:
                raise APIError('open_draft_pending', '外案原标签仍存在，保留原现场；非财务URL也不得隔离')
        proof.update(observed_at_utc=legacy.get('observed_at_utc', stamp()), closed_tab_confirmed=proof.get('closed_tab_confirmed', True),
                     all_tab_ids=ids, tab_inventory_complete=True,
                     inventory_tab_id_namespace='edge-applescript',
                     current_inventory_sha256=current_proof['sha256'],
                     legacy_inventory_sha256=legacy_proof['sha256'],
                     pending_sha256=hashlib.sha256(raw).hexdigest())
    return {**recovery_fields, 'isolated_pending': isolated, 'inventory_proofs': {
        'current_target': current_proof, 'legacy_closed_tabs': legacy_proof},
        'native_only_isolation': native_only,
        'legacy_apple_event_attribute_observation': (legacy is not inventory) and not native_only}


def command(service, request, folder):
    validate_context(service, request)
    a = request['parameters']
    return [service.python, str(ROOT/'tools/finance_open_draft.py'),
            '--workspace', str(service.root), '--case-id', request['case_id'],
            '--serial', a['serial'], '--expected-userid', a['expected_userid'],
            '--tab-id', a['tab_id'], '--output-directory', str(folder)]+(['--recovery-job-id',a['job_id'],'--recovery-artifact-id',a['artifact_id']]if a.get('job_id')is not None else[])


def registered_preview_contexts(service, expected_userid):
    """Bind possible current previews to existing local cases, never infer an owner."""
    from assistant_api import read_json
    contexts, sources = [], {}
    for item in service.cases()['cases']:
        case = item['case_id']
        _, paths = service.resolve(case)
        if (paths['state'].parent/'pending_write.json').exists():
            continue
        form, state = read_json(paths['form']), read_json(paths['state'])
        review = form.get('review', {})
        if (not isinstance(review, dict) or review.get('workflow_started') is not False
                or state.get('submission_completed') is True or state.get('node') not in (None, '')
                or state.get('status') in ['applicant_review_pending', 'submitted_to_approval']):
            continue
        try:
            form = load_context(paths['form'])
            serial = form['serial']
            summary = form.get('basic', {}).get('fields', {}).get('t-abst')
            if (state.get('serial') != serial or form.get('expected_userid') not in (None, '', expected_userid)
                    or not isinstance(summary, str) or not summary.strip() or re.search(r'[\r\n\x00]', summary)):
                continue
            creation = Path(state.get('creation_readback', ''))
            if (not creation.is_absolute() or creation.is_symlink() or not creation.is_file()
                    or not creation.resolve().is_relative_to(paths['state'].parent/'receipts')):
                continue
            receipt = read_json(creation)
            tab = receipt.get('tab_id')
            if (not isinstance(tab, str) or not re.fullmatch(r'[0-9]+', tab)
                    or receipt.get('phase') != 'script_completed' or not receipt.get('executed_at_utc')):
                continue
            body = native_body(receipt, tab)
            if (body.get('projid') != 'WF_YBX' or body.get('serial') != serial
                    or body.get('identity', {}).get('userid') != expected_userid):
                continue
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            continue
        try:
            face_amount = form['amount']
            manifest = read_json(paths['invoices']); selected = manifest.get('selected_invoices', [])
            if selected:
                face_amount = format(sum((Decimal(row['amount']) for row in selected), Decimal(0)), '.2f')
                if Decimal(face_amount) != Decimal(manifest.get('expected_amount', face_amount)):
                    continue
                # 仅离开已绑定的只读预览；原创建回读仍须证明同套发票的原票面合计。
                if Decimal(face_amount) != Decimal(form['amount']):
                    original_text = re.sub(r'\s+', '', '\n'.join(f.get('visibleText','') for f in body.get('frames',[]))).replace(',','')
                    expected = '共选择'+str(len(selected))+'张发票合计'+face_amount+'元'
                    if expected not in original_text or any(row['number'] not in original_text for row in selected):
                        continue
        except (ValueError,OSError,KeyError,TypeError,ArithmeticError):
            continue
        contexts.append({'case_id': case, 'serial': serial, 'amount': form['amount'], 'invoice_face_amount':face_amount, 'summary': summary})
        sources[case] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in [paths['form'], paths['invoices'], paths['state'], creation]}
    return contexts, sources


def navigation_script(expected_userid, registered_previews=None, view='drafts'):
    """Guard unsaved UI before reusing the existing fixed native drafts entry."""
    from finance_invoice_verification import navigation
    if view not in ('drafts', 'in-progress'):raise ValueError('只支持已适配的草稿或审批中列表')
    native = navigation(view, expected_userid)
    return r'''(async()=>{
 const expectedUserid=__USER__,registered=__PREVIEWS__,vis=e=>!!e?.getClientRects().length;
 const identity=()=>{
  if(location.origin!=='https://cwzh.ustc.edu.cn'||location.pathname!=='/YBX/main.jsp'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==expectedUserid)throw Error('草稿导航身份不符');
 };
 const serial=()=>String($.UC.bindData('#61-serialno#')||'').trim(),frames=()=>[...document.querySelectorAll('iframe')].filter(vis);
 const edits=()=>[...document.querySelectorAll('input,select,textarea')].filter(e=>vis(e)&&!e.disabled&&!e.readOnly&&!['hidden','password','button','submit','reset'].includes(e.type));
 const cards=()=>[...document.querySelectorAll('.pageTitle[title="'+__CARD__+'"]')].filter(vis);
 const businessForms=()=>[...document.querySelectorAll('[id^="formWF_YBX_"]')];
 function clean(){
  identity();
  if([...document.querySelectorAll('.ui-dialog')].some(vis))throw Error('仍有原生窗口或提示，保留原现场');
  const node=$.UC.bindData('#61-node_id#'),savedPreviewCache=registered.filter(c=>c.serial===serial()).length===1&&frames().length===1&&frames()[0].id==='iframepage_print'&&!edits().length&&!businessForms().some(vis);
  const knownSerialCache=!serial()||registered.filter(c=>c.serial===serial()).length===1;
  const postApprovalListCache=['508','535'].includes(String(node))&&((knownSerialCache&&(cards().length===1||vis(document.getElementById('gridWF_YBX_101'))||vis(document.getElementById('gridWF_YBX_105'))))||savedPreviewCache);
  if((![undefined,null,''].includes(node)&&!postApprovalListCache)||[...document.querySelectorAll('.foot_winWF_YBX_76 button[btnname="同意"]')].some(vis))throw Error('已有本人审核或流程现场，不切换草稿');
  const track=window.__reimbursementUploadTracking;
  if(track&&(track.dispatchRequested&&!track.done||track.saveRequested&&!track.done))throw Error('原上传请求尚未完成，不切换草稿');
  const upload=$('#formWF_YBX_84_t-img').data('uploadifive');
  if(upload&&(Number(upload.queue?.count)>0||Number(upload.uploads?.current)>0))throw Error('原生上传队列尚未结束');
 }
 function homeReady(waiting=false){
  const entries=cards();if(entries.length>1)throw Error('草稿原生入口不唯一');
  // 返回首页后学校会保留已登记单号及隐藏表单缓存；只以可见业务控件判断未保存现场。
  const current=serial(),knownCache=!current||registered.filter(c=>c.serial===current).length===1;
  const ready=knownCache&&frames().length===0&&!businessForms().some(vis)&&entries.length===1;
  const fields=edits();
  if(fields.length&&!((ready||waiting)&&fields.length===1&&fields[0].tagName==='INPUT'&&fields[0].type==='text'&&
   !fields[0].id&&!fields[0].name&&fields[0].value===''))throw Error('仍有可编辑字段，保留未保存现场');
  return ready&&$.active===0;
 }
 const compact=s=>s.replace(/\s+/g,''),normalize=s=>compact(s).replaceAll(',','').replaceAll('，','');
 const cents=s=>{if(typeof s!=='string'||!/^\d+(?:\.\d{1,2})?$/.test(s))throw Error('已登记预览金额不符');const[a,b='']=s.split('.');return BigInt(a)*100n+BigInt(b.padEnd(2,'0'));};
 function preview(){
  clean();
  if(edits().length||businessForms().some(vis)||$.active!==0)throw Error('预览仍有编辑或原生请求，保留原现场');
  const current=serial(),matches=registered.filter(c=>c.serial===current),visibleFrames=frames();
  if(!current||matches.length!==1||visibleFrames.length!==1||visibleFrames[0].id!=='iframepage_print')throw Error('当前预览不属于唯一已登记安全草稿');
  const c=matches[0],frame=visibleFrames[0],doc=frame.contentDocument;
  if(frame.contentWindow.location.origin!=='https://cwzh.ustc.edu.cn'||frame.contentWindow.location.pathname!=='/YBX/redirectCustomPage.action'||doc?.readyState!=='complete'||typeof doc.body?.innerText!=='string')throw Error('原生预览尚未完整');
  const text=doc.body.innerText;if(text.length>262144)throw Error('预览超出完整核验范围');
  const found=[...text.matchAll(/报销单号[:：]\s*([0-9]+)/g)].map(m=>m[1]);
  const totals=[...normalize(text).matchAll(/共选择[0-9]+张发票[、,，]?合计([0-9]+(?:\.[0-9]{1,2})?)元/g)];
  const claims=[...normalize(text).matchAll(/金额总计[:：]([0-9]+(?:\.[0-9]{1,2})?)/g)];
  const abstracts=text.split(/\r?\n/).filter(line=>/^\s*摘要[\s:：]/.test(line)).map(line=>line.replace(/^\s*摘要[\s:：]+/,''));
  if(!found.length||found.some(s=>s!==current)||totals.length!==1||cents(totals[0][1])!==cents(c.invoice_face_amount||c.amount)||claims.length!==1||cents(claims[0][1])!==cents(c.amount)||
   abstracts.length!==1||compact(abstracts[0])!==compact(c.summary))throw Error('当前预览单号、合计或摘要与本机已登记草稿不同');
  const def=Object.getOwnPropertyDescriptor(window,'defaultWin'),heads=[...document.querySelectorAll('h2')].filter(e=>vis(e)&&e.textContent.trim()==='中国科学技术大学');
  if(!def||!Object.hasOwn(def,'value')||def.value!=='WF_YBX:6:SY-个人首页'||heads.length!==1||heads[0].getAttribute('onclick')!==__HOME_ONCLICK__)throw Error('原生首页入口定义不符，不猜路由');
  return{c,frame,doc,text,head:heads[0]};
 }
 clean();
 const present=document.getElementById('gridWF_YBX_101'),filter=document.getElementById('formWF_YBX_99_t-status');
 const expectedFilter=__CARD__==='草稿'?'BX_YBXCG':'BX_YBXSPZ';
 // 已在目标列表时不绕首页；查询条件不是尚未保存的报销字段。
 if(vis(present)&&present.grid?.hDiv?.loading===false&&vis(filter)&&filter.value===expectedFilter&&$.active===0&&frames().length===0&&!businessForms().some(e=>vis(e)&&!/^formWF_YBX_99(?:_|$)/.test(e.id))){
  return{ok:true,action:__CARD__==='草稿'?'open_drafts_readonly':'open_in_progress_readonly',projid:'WF_YBX',identity:{userid:expectedUserid},draftsReady:true,system_write:false,client_state_changed:false,homeNavigationRequested:false,departed_preview:null,current_list_reused:true};
 }
 let departed=null,homeNavigationRequested=false;const onHome=homeReady();
 if(!onHome&&(serial()||frames().length)){
  const first=preview(),last=preview();
  if(first.c!==last.c||first.frame!==last.frame||first.doc!==last.doc||first.text!==last.text||first.head!==last.head)throw Error('返回首页前预览变化，保留原现场');
  departed={case_id:last.c.case_id,serial:last.c.serial};homeNavigationRequested=true;last.head.click();
  const homeDeadline=Date.now()+8000;
  for(;;){clean();if(serial()&&serial()!==departed.serial)throw Error('首页导航期间单号变化，停止并独立回读');
   if(homeReady(true))break;
   if(Date.now()>=homeDeadline)throw Error('首页尚未明确就绪；仅独立回读，不重复点击');
   await new Promise(r=>setTimeout(r,100));
  }
 }else if(!onHome)throw Error('尚非已核原生首页，保留原现场');
 clean();if(!homeReady())throw Error('草稿导航前首页变化，停止');
 const opened=__NATIVE__;
 const deadline=Date.now()+8000;
 for(;;){
  identity();
  if([...document.querySelectorAll('.ui-dialog')].some(vis))throw Error('草稿导航出现原生提示，停止并独立回读');
  const g=document.getElementById('gridWF_YBX_101');
  if(vis(g)&&g.grid&&g.grid.hDiv.loading===false)return{...opened,identity:{userid:expectedUserid},draftsReady:true,client_state_changed:true,homeNavigationRequested,departed_preview:departed};
  if(Date.now()>=deadline)throw Error('草稿列表尚未就绪；仅独立回读，不重复导航');
  await new Promise(r=>setTimeout(r,100));
 }
})()'''.replace('__USER__', json.dumps(expected_userid)).replace('__CARD__',json.dumps('草稿' if view=='drafts' else '审批中')).replace('__PREVIEWS__', json.dumps(registered_previews or [], ensure_ascii=False)).replace('__HOME_ONCLICK__', json.dumps(HOME_ONCLICK)).replace('__NATIVE__', native)


def native_body(receipt, tab_id):
    if (not isinstance(receipt, dict) or receipt.get('tab_id') != tab_id
            or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(receipt.get('result_marker', '')))):
        raise ValueError('原生回执缺同标签或正式结果标记')
    body = receipt.get('result')
    if not isinstance(body, dict) or body.get('ok') is not True:
        raise ValueError('原生阶段未明确成功；保留原标记，不重放')
    return body


class PreviewLoading(ValueError):
    """A same-identity complete snapshot whose document is still loading."""


def verify_preview(receipt, serial, expected_userid, tab_id):
    body = native_body(receipt, tab_id)
    if (body.get('projid') != 'WF_YBX' or body.get('serial') != serial
            or body.get('identity', {}).get('userid') != expected_userid
            or body.get('visibleDialogs')):
        raise ValueError('独立回读并非本次同号同账号的无弹框预览')
    frames = [f for f in body.get('frames', []) if f.get('id') == 'iframepage_print']
    if len(frames) != 1:
        raise ValueError('同号原生预览不唯一')
    proof = frames[0].get('proof_inventory', {})
    if (proof.get('schema') == 'ustc-native-proof-inventory-v1' and proof.get('serial') == serial
            and proof.get('preview_serials') == [serial] and proof.get('preview_complete') is False
            and proof.get('document_ready_state') in {'loading', 'interactive'}
            and proof.get('scan_complete') is True and proof.get('truncated') is False):
        raise PreviewLoading('同号预览仍在加载；仅补读，不重复导航')
    if (proof.get('schema') != 'ustc-native-proof-inventory-v1' or proof.get('serial') != serial
            or proof.get('preview_serials') != [serial] or proof.get('preview_complete') is not True
            or proof.get('scan_complete') is not True or proof.get('truncated') is not False):
        raise ValueError('独立预览清点尚未完整；仅继续readback，不重新打开')
    return body


def require_worker_lease(service, folder):
    """Only the formal worker may dispatch this composed browser-local route."""
    from assistant_api import read_json
    service.case_id(folder.parent.name)
    if (folder.parent != service.jobs/folder.parent.name
            or not re.fullmatch(r'j_[a-f0-9]{24}', folder.name)):
        raise ValueError('打开草稿须位于正式本案任务目录')
    running = read_json(folder/'running.json')
    for name in ['browser.lock', 'case-'+folder.parent.name+'.lock']:
        lock = read_json(service.root/'.local/assistant_locks'/name)
        if (not isinstance(running.get('worker_pid'), int) or running['worker_pid'] <= 0
                or lock.get('pid') != running['worker_pid'] or lock.get('retain_for_recovery')):
            raise ValueError('打开草稿须由持有本案及全局browser lease的正式worker执行')


def run(service, request, folder, runner=subprocess.run):
    from assistant_api import atomic_json, read_json
    folder = Path(folder).resolve()
    paths, form = validate_context(service, request)
    if folder.parent.name != request['case_id']:
        raise ValueError('任务目录并非本案')
    require_worker_lease(service, folder)
    a=request['parameters'];ref=pending_read_reference(a)
    idle = ensure_globally_idle(service, request['case_id'], folder.name, target_tab_id=a['tab_id'],**({'recovery_source':ref}if ref else{}))
    a = request['parameters']
    previews, preview_sources = registered_preview_contexts(service, a['expected_userid'])
    frozen = {key: hashlib.sha256(paths[key].read_bytes()).hexdigest() for key in ['form', 'invoices']}
    intent = {'schema': 'ustc-open-draft-v1', 'case_id': request['case_id'],
              'serial': a['serial'], 'expected_userid': a['expected_userid'], 'tab_id': a['tab_id'],
              'amount': form['amount'], 'form_sha256': frozen['form'], 'manifest_sha256': frozen['invoices'],
              'registered_preview_sources': preview_sources,
              'created_at_utc': stamp(), 'system_write': False, 'no_replay': True, 'phase': 'prepared', **idle}
    # Exclusive intent prevents repeating any navigation after interruption.
    with (folder/'open-draft-intent.json').open('x', encoding='utf-8') as stream:
        json.dump(intent, stream, ensure_ascii=False, indent=2)
    def phase(name, argv, output):
        recovery=idle.get('same_case_pending_readback')
        if recovery and hashlib.sha256(Path(recovery['original_pending_path']).read_bytes()).hexdigest()!=recovery['original_pending_sha256']:
            raise ValueError('原支付pending在独立回读期间改变，保留原请求')
        for key, digest in frozen.items():
            if hashlib.sha256(paths[key].read_bytes()).hexdigest() != digest:
                raise ValueError('排队案件来源已变化，停止后续阶段')
        current_sources = preview_sources.values() if name == 'navigation' else [
            preview_sources[intent['departed_preview']['case_id']]] if intent.get('departed_preview') else []
        for sources in current_sources:
            for source, digest in sources.items():
                if hashlib.sha256(Path(source).read_bytes()).hexdigest() != digest:
                    raise ValueError('当前预览的本机登记来源已变化，停止后续阶段')
        intent.update(phase=name+'_requested', updated_at_utc=stamp())
        atomic_json(folder/'open-draft-intent.json', intent)
        completed = runner(argv, cwd=ROOT, check=False)
        if completed.returncode:
            raise ValueError('原生'+name+'未明确完成；保留原任务回执，仅独立回读')
        value = read_json(output)
        native_body(value, a['tab_id'])
        intent.update(phase=name+'_observed', updated_at_utc=stamp())
        atomic_json(folder/'open-draft-intent.json', intent)
        return value
    try:
        script = folder/'open-draft-navigation.js'
        with script.open('x', encoding='utf-8') as stream:
            stream.write(navigation_script(a['expected_userid'], previews))
        navigated = phase('navigation', [service.python, str(ROOT/'tools/edge_runtime.py'), str(script),
            '--tab-id', a['tab_id'], '--output', str(folder/'open-draft-navigation.json'),
            '--quiet', '--operation-kind', 'browser-local'], folder/'open-draft-navigation.json')
        observed = native_body(navigated, a['tab_id'])
        if (observed.get('action') != 'open_drafts_readonly' or observed.get('draftsReady') is not True
                or observed.get('projid') != 'WF_YBX' or observed.get('system_write') is not False
                or observed.get('identity', {}).get('userid') != a['expected_userid']):
            raise ValueError('草稿列表未取得本次同账号的明确就绪回执')
        departed = observed.get('departed_preview')
        if observed.get('homeNavigationRequested') is True:
            if (not isinstance(departed, dict) or len([c for c in previews if
                    c['case_id'] == departed.get('case_id') and c['serial'] == departed.get('serial')]) != 1):
                raise ValueError('返回首页回执未绑定唯一已登记原预览')
            intent['departed_preview'] = departed
            atomic_json(folder/'open-draft-intent.json', intent)
        elif observed.get('homeNavigationRequested') is not False or departed is not None:
            raise ValueError('缺少明确的首页导航状态，不重复导航')
        inspected = phase('inspect', [service.python, str(ROOT/'tools/finance_cli.py'), 'inspect-draft',
            '--case', str(paths['form']), '--expected-userid', a['expected_userid'],
            '--tab-id', a['tab_id'], '--output', str(folder/'open-draft-inspect.json')], folder/'open-draft-inspect.json')
        observed = native_body(inspected, a['tab_id'])
        if observed.get('action') != 'inspect_existing_draft' or observed.get('serial') != a['serial'] or observed.get('system_write') is not False:
            raise ValueError('打开草稿回执身份或动作不符')
        for attempt in range(4):
            name = 'open-draft-readback' + (('-' + str(attempt + 1)) if attempt else '') + '.json'
            readback = phase('readback', [service.python, str(ROOT/'tools/invoice_cli.py'), 'read',
                '--manifest', str(paths['invoices']), '--tab-id', a['tab_id'],
                '--output', str(folder/name)], folder/name)
            try:
                verify_preview(readback, a['serial'], a['expected_userid'], a['tab_id'])
                break
            except PreviewLoading:
                if attempt == 3:
                    raise
                time.sleep(.5)  # New read only; never repeat inspect/navigation or an unknown dispatch.
        # Preserve the native envelope, marker and source SHA as the public readback.
        atomic_json(folder/'output.json', readback)
        intent.update(phase='independent_preview_verified', updated_at_utc=stamp())
        atomic_json(folder/'open-draft-intent.json', intent)
        return {'ok': True, 'case_id': request['case_id'], 'serial': a['serial'],
                'system_write': False, 'client_state_changed': True, 'saved_result_recorded': False,
                'output': str(folder/'output.json'), **idle}
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result = {'ok': False, 'error': str(error), 'serial': a['serial'], 'system_write': False,
                  'phase': intent['phase'], 'no_replay': True, 'requires_independent_readback': True,
                  'next': '读取本任务原生回执或read_system readback；不重开、不保存、不解除原pending'}
        atomic_json(folder/'output.json', result)
        return result


def main():
    from assistant_api import AssistantService
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['workspace', 'case-id', 'serial', 'expected-userid', 'tab-id', 'output-directory']:
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--recovery-job-id');parser.add_argument('--recovery-artifact-id')
    args = parser.parse_args()
    service = AssistantService(args.workspace, [args.workspace])
    request = {'case_id': args.case_id, 'operation': 'read_system', 'execute': False,
               'parameters': {'mode': 'open-draft', 'serial': args.serial,
                              'expected_userid': args.expected_userid, 'tab_id': args.tab_id}}
    if args.recovery_job_id is not None or args.recovery_artifact_id is not None:
        if not args.recovery_job_id or not args.recovery_artifact_id:parser.error('恢复来源须同时给原任务及附件')
        request['parameters'].update(job_id=args.recovery_job_id,artifact_id=args.recovery_artifact_id)
    result = run(service, request, Path(args.output_directory))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
