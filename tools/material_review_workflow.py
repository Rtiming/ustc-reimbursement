"""Material review to formal workflow: scoped system evidence and local preflight.

This adapter has no financial execution route. It calls only the official CLI's
overview, retaining that owner's parameter/pending checks and existing case IDs.
"""
import json
import re
import subprocess
import tempfile
from collections import Counter
from decimal import Decimal
from datetime import datetime
from pathlib import Path

from material_review_model import atomic_json, digest, load, now, read_json, require, sha
from material_review_store import state

ROOT = Path(__file__).resolve().parents[1]


def binding_file(workspace):
    return ROOT/'.local'/'review-runtime'/(digest(str(Path(workspace).resolve()))[:20]+'.workflow.json')


def import_status(workspace, config_path):
    config = read_json(config_path)
    program_root = Path(config['tool_root']).resolve()
    require((program_root/'tools'/'reimbursement.sh').is_file() and (program_root/'assistant.py').is_file(), '正式工具入口不存在')
    data, version = load(workspace)
    source = config['source']
    require(sha(source['path']) == source['sha256'], '正式回执SHA变化')
    envelope = read_json(source['path'])
    result = envelope.get('result', {})
    require(envelope.get('operation_kind') == 'read' and envelope.get('phase') == 'script_completed', '需要正式只读完成回执')
    require(result.get('ok') is True and result.get('complete') is True and
            result.get('action') == 'bills-list' and result.get('system_write') is False and
            result.get('business_transport') == 'native_http', '需要完整原生清单；失败或部分读取不能覆盖旧状态')
    require(result.get('expected_userid') == config['expected_userid'] and
            result.get('case_id') == config['batch_case_id'], '回执账号或案件不匹配')
    rows = result['rows']
    require(len(rows) == result['total_records'], '系统清单数量不完整')
    by_serial = {r['t.serial_no']: r for r in rows}
    require(len(by_serial) == len(rows), '系统清单单号重复')
    cases_source = config['cases_source']
    require(sha(cases_source['path']) == cases_source['sha256'], '正式案件映射SHA变化')
    cases = read_json(cases_source['path'])
    require(cases.get('ok') is True, '正式案件列表读取失败')
    case_map = {}
    for case in cases['cases']:
        if case.get('serial'):
            require(case['serial'] not in case_map, '一个单号对应多个正式案件，停止自动匹配')
            case_map[case['serial']] = case['case_id']
    applicant_evidence = {}
    for reference in config.get('applicant_receipts', []):
        require(sha(reference['path']) == reference['sha256'], '申请人回读SHA变化')
        proof = read_json(reference['path']); observed = proof.get('result', {})
        require(proof.get('phase') == 'script_completed' and proof.get('operation_kind') == 'read' and
                observed.get('action') == 'applicant-http-inspect' and observed.get('ok') is True and
                observed.get('complete') is True and observed.get('completed') is True and
                observed.get('system_write') is False and observed.get('save_dispatched') is False and
                observed.get('business_transport') == 'native_http', '需要独立完整本人节点完成回读')
        serial = observed['serial']; bill = observed['original']['bill']; row = by_serial.get(serial)
        require(serial not in applicant_evidence and row is not None and case_map.get(serial) == observed['case_id'] and
                observed['expected_userid'] == config['expected_userid'], '申请人回读账号、单号或案件不符')
        require(all(bill.get(k) == row.get(k) for k in ['t.serial_no','t.REQ_INFO','t.total_amt','t.abst','t.uni_key','t.wf_name']), '申请人回读与完整清单身份、金额或实例变化')
        require(datetime.fromisoformat(proof['executed_at_utc'].replace('Z','+00:00')) >= datetime.fromisoformat(result['observed_at'].replace('Z','+00:00')), '须在完整清单之后重新核实流程日志')
        history = observed['original']['history']
        applicant = [r for r in history if r.get('t.node_id') == '508']
        pending = [r for r in history if not r.get('t.operator') and r.get('t.appinfo') not in (None,'','-')]
        require(len(applicant) == 1 and applicant[0].get('t.operator') == config['expected_userid'] and applicant[0].get('t.op_comment') == '同意' and
                len(pending) == 1 and pending[0]['t.node_id'] != '508' and pending[0]['t.node_id'] == observed['original']['workflow_node_id'], '未能证实本人的完成记录及唯一下一节点')
        next_row = pending[0]; name = next_row['t.node_name'].removeprefix('预约流程-')
        applicant_evidence[serial] = {'applicant_completed': True, 'node_id': next_row['t.node_id'],
            'status_name': name+'['+next_row['t.appinfo'].split('-',1)[-1]+']', 'next_approver': next_row['t.appinfo'],
            'workflow_observed_at': proof['executed_at_utc'], 'applicant_receipt': reference,
            'main_list_node_id': row.get('t.node_id'), 'main_list_status_name': row.get('t.status_name')}
    entries = {}
    for iid, item in data['items'].items():
        serial = item.get('bill_number')
        row = by_serial.get(serial)
        if serial:
            require(row is not None, '当前完整清单未找到已登记原单：'+serial)
            require(row.get('t.REQ_INFO', '').split('-', 1)[0] == config['expected_userid'], '原单申请人不符')
            if item.get('claim_total') is not None:
                require(Decimal(str(item['claim_total'])) == Decimal(row['t.total_amt'].replace(',', '')), '原单申请金额变化，请先核实')
        entries[iid] = {'serial': serial or None, 'case_id': case_map.get(serial),
                        'status_code': row.get('t.status') if row else None,
                        'status_name': row.get('t.status_name') if row else '本案未登记单号',
                        'node_id': row.get('t.node_id') if row else None,
                        'workflow_finished': row.get('t.lc_finish') if row else None,
                        'amount': row.get('t.total_amt') if row else item.get('claim_total')}
        entries[iid].update(applicant_evidence.get(serial, {}))
    snapshot = {'schema': 'ustc-material-review-workflow-v1', 'dataset_version': version,
                'observed_at': result['observed_at'], 'imported_at': now(),
                'source_sha256': source['sha256'], 'source_job_id': config['source_job_id'],
                'source_case_id': config['batch_case_id'], 'complete': True,
                'items': entries, 'system_write': False}
    identity = digest(snapshot)
    target = Path(workspace)/'workflow'
    atomic_json(target/'versions'/f'{identity}.json', snapshot)
    atomic_json(target/'current.json', {'version': identity})
    # This machine's program location is deliberately excluded from portable case exports.
    atomic_json(binding_file(workspace), {'tool_root': str(program_root)})
    binding_file(workspace).chmod(0o600)
    return {'ok': True, 'workflow_version': identity, 'counts': dict(Counter(x['status_name'] for x in entries.values())), 'system_write': False}


def summary(workspace, data=None, current=None):
    if data is None:
        data, version = load(workspace)
    else:
        version = digest(data)
    if current is None:
        current = state(workspace, data)
    pointer = Path(workspace)/'workflow'/'current.json'
    if not pointer.is_file():
        return {'available': False, 'reason': '尚未导入完整系统回执；本页保留历史材料快照'}
    identity = read_json(pointer)['version']
    require(re.fullmatch('[a-f0-9]{64}', identity), '办理状态版本无效')
    snapshot = read_json(pointer.parent/'versions'/f'{identity}.json')
    require(digest(snapshot) == identity, '办理状态快照损坏')
    if snapshot['dataset_version'] != version:
        return {'available': False, 'reason': '材料版本已更新，请重新核对并导入系统状态'}
    result = {'available': True, 'observed_at': snapshot['observed_at'], 'version': identity,
              'items': {}, 'counts': {}, 'system_write': False, 'formal_program_connected': binding_file(workspace).is_file()}
    for iid, item in data['items'].items():
        native = snapshot['items'][iid]
        open_issues = [qid for qid in item['issue_ids'] if current['issues'][qid]['status'] != 'reviewed']
        hold = item.get('queue') == '按用户安排暂缓'
        code = native['status_code']
        if code == '10' and native['workflow_finished'] == 'T' and native['status_name'] == '已预约待投递':
            route, label, action = 'delivery', '已预约，待投递', '沿原单整理投递；不再建单或重复提交。'
        elif native['node_id'] == '508' and code == '-2':
            route, label, action = 'applicant', '原单申请人环节', '此单号的系统回执显示已进入申请人环节；由本人核对原单后确认，不重新做单或重放旧同意请求。'
        elif native.get('applicant_completed'):
            route, label, action = 'approval', '已提交，待'+native['status_name'], '本人已同意，等待下一节点办理；不再做单或重复提交。'
        elif hold:
            route, label, action = 'hold', '按安排暂缓', '保留原安排，待明确恢复本事项后再办理。'
        elif code == '-99':
            route, label = 'resume', '已建草稿，材料待核' if open_issues else '已建草稿，可检查原单'
            if open_issues and all(current['issues'][q]['status']=='before_submit' for q in open_issues):
                label='已建草稿，提交材料待补'
            action = '沿原单核对并补充草稿字段；材料问题保留到对应核验环节，提交条件单独检查。'
        elif native['serial']:
            route, label, action = 'existing', '已有单据，核对当前环节', '沿原单核对；当前状态不放行新建。'
        elif item.get('draft_authority', {}).get('scope') == 'draft_only':
            route, label, action = 'draft_candidate', '已授权仅做草稿', '先由正式程序核定金额、收款及票据并保存草稿；保留材料问题，不据此提交审批。'
        elif open_issues:
            route, label, action = 'blocked', '待核做单条件', item.get('next_action') or '先区分做单必需字段和提交前材料要求。'
        else:
            route, label, action = 'candidate', '可进入做单准备', '核对申请额、收款、经费及原件，生成正式计划；问题清单为空不等于财务提交通过。'
        result['items'][iid] = {**native, 'route': route, 'label': label, 'next_action': action,
                                'open_issues': open_issues, 'can_prepare': route in ('candidate', 'draft_candidate', 'resume'),
                                'submission_ready': False,
                                'can_inspect_formal': bool(native['case_id']) and result['formal_program_connected']}
    result['counts'] = dict(Counter(x['route'] for x in result['items'].values()))
    return result


def inspect_formal(workspace, request):
    """Call exactly one official local overview; never dispatch save/submit or a shell string."""
    require(set(request) == {'item_id', 'version', 'revision'}, '仅接受事项、页面版本和记录版本；不接受执行或任意命令')
    data, version = load(workspace)
    current = state(workspace, data)
    require(request['version'] == version and request['revision'] == current['revision'], '页面已变化，请刷新后核对')
    info = summary(workspace, data, current)
    require(info['available'] and request['item_id'] in info['items'], '事项办理状态未登记')
    item = info['items'][request['item_id']]
    require(item['can_inspect_formal'], '本事项尚未登记正式案件；请先补齐材料并生成正式计划')
    binding = read_json(binding_file(workspace))
    tool_root = Path(binding['tool_root']).resolve()
    case_id = item['case_id']
    require(re.fullmatch('[a-zA-Z0-9_-]+', case_id), '正式案件编号无效')
    with tempfile.TemporaryDirectory(prefix='review-preflight-', dir=binding_file(workspace).parent) as tmp:
        input_path = Path(tmp)/'request.json'
        atomic_json(input_path, {'method': 'overview', 'params': {'case_id': case_id}})
        try:
            result = subprocess.run([__import__('sys').executable, str(tool_root/'assistant.py'), 'call', '--input', str(input_path)],
                                    cwd=tool_root, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('正式程序本地预检超时；未请求任何财务写入，可重新检查') from exc
    require(result.returncode == 0, '正式做单程序预检失败：'+result.stderr[-1000:])
    overview = json.loads(result.stdout)
    require(overview.get('ok') is True and overview.get('case_id') == case_id, '正式程序未返回对应案件')
    require(overview.get('serial') == item['serial'], '正式原单编号变化，请重新核对状态')
    require(load(workspace)[1] == version and state(workspace)['revision'] == current['revision'], '预检期间材料记录变化，请刷新')
    receipt = {'schema': 'ustc-material-review-preflight-v1', 'created_at': now(), 'item_id': request['item_id'],
               'dataset_version': version, 'event_revision': current['revision'], 'workflow_version': info['version'],
               'case_id': case_id, 'serial': item['serial'], 'route': item['route'],
               'material_issues': item['open_issues'], 'candidate_for_preparation': item['can_prepare'],
               'parameter_errors': overview.get('local_parameter_errors', []),
               'input_validation': overview.get('input_validation'), 'pending_write': overview.get('unverified_write'),
               'active_jobs': overview.get('active_jobs', []), 'formal_next_action': overview.get('next_action'),
               'formal_overview_sha256': digest(overview), 'system_write': False,
               'note': '正式程序本地预检已读取；尚未新建、保存、提交或执行本人同意。业务状态以带时间的系统回执为准。'}
    require(receipt['system_write'] is False, '预检不得执行财务写入')
    folder = Path(workspace)/'workflow'/'preflights'/digest(receipt)
    atomic_json(folder/'overview.json', overview)
    atomic_json(folder/'receipt.json', receipt)
    return {'ok': True, 'receipt': receipt}
