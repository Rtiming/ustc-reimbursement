"""只在失效会话、全量旧草稿和全量未占用原票共同证明下接续建单。"""
import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from select_case_invoices import select

FIELDS = ['case_id', 'serial', 'expected_count', 'expected_amount', 'original_files', 'selected_invoices', 'creation_receipt']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(manifest, proof, readback=None):
    if manifest.get('serial') or not manifest.get('draft_creation_requested'):
        raise ValueError('仅处理无单号的原创建请求')
    if any(manifest.get(k) != proof['manifest'].get(k) for k in FIELDS):
        raise ValueError('原创建范围已变化，不能复用失效会话证明')
    data = {}
    for kind, ref in proof['sources'].items():
        if digest(ref['path']) != ref['sha256']:
            raise ValueError('恢复证据SHA变化：' + kind)
        data[kind] = json.loads(Path(ref['path']).read_text())
    creation, surface, opening, access, drafts, pool = [data[k] for k in ['creation', 'surface', 'opening', 'access', 'drafts', 'pool']]
    if Path(proof['sources']['creation']['path']).resolve() != Path(manifest['creation_receipt']).resolve():
        raise ValueError('不是最后一次原创建')
    cr = creation.get('result', {})
    if (creation.get('phase') != 'script_completed' or cr.get('ok') is not True
            or cr.get('action') != 'create_daily_draft_requested' or cr.get('case_id') != manifest['case_id']
            or cr.get('count') != manifest['expected_count'] or cr.get('total') != manifest['expected_amount']
            or digest(creation['script']) != creation.get('source_sha256')):
        raise ValueError('原创建请求未完整冻结')
    src = Path(creation['script']).read_text()
    template = (Path(__file__).parent / 'create_daily_draft.js').read_text()
    prefix = '(()=>{if(location.origin!=="https://cwzh.ustc.edu.cn"||$.getProjID()!=="WF_YBX")throw Error("财务系统不符");return ((' + template + ')('
    if not src.startswith(prefix) or not src.endswith('));})()'):
        raise ValueError('创建源码不是固定原生动作')
    frozen = json.loads(src[len(prefix):-7])
    if any(frozen.get(k) != manifest.get(k) for k in FIELDS if k != 'creation_receipt'):
        raise ValueError('冻结创建参数不符')
    userid = proof['expected_userid']
    sr = surface.get('result', {})
    resources = sr.get('previewDiagnostics', {}).get('resources', [])
    if (surface.get('phase') != 'script_completed' or surface.get('tab_id') != creation.get('tab_id')
            or sr.get('ok') is not True or sr.get('serial') or sr.get('identity', {}).get('userid') != userid
            or not resources or resources[-1].get('path') != '/YBX/commonUpdate_responseButtonEvent.action'
            or resources[-1].get('responseStatus') != 404):
        raise ValueError('原标签没有明确会话请求失败证据')
    redirected = opening.get('new_tab_id')
    if (opening.get('case_id') != manifest['case_id'] or opening.get('old_tab_id') != creation['tab_id']
            or opening.get('page_opened') is not True or not redirected
            or not any(t.get('tab_id') == redirected and t.get('path') == 'https://cwzh.ustc.edu.cn/YBX/login.html'
                       for t in opening.get('after', {}).get('tabs', []))):
        raise ValueError('新入口未证实会话失效')
    tab = access.get('finance_tab_id')
    if (access.get('tab_id') != redirected or access.get('result', {}).get('state') != 'ready'
            or access.get('result', {}).get('login_verified') is not True or not tab or tab == creation['tab_id']):
        raise ValueError('未核实独立重新登录的业务标签')
    times = [datetime.fromisoformat(x[k]) for x, k in [(creation, 'executed_at_utc'), (surface, 'executed_at_utc'),
             (opening, 'started_at_utc'), (access, 'observed_at_utc'), (drafts, 'executed_at_utc'), (pool, 'executed_at_utc')]]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('恢复证据时间顺序不符')
    grids = {}
    for kind, envelope, func in [('drafts', drafts, '101'), ('pool', pool, '53')]:
        r = envelope.get('result', {})
        gs = [g for g in r.get('grids', []) if str(g.get('funcno')) == func]
        if (envelope.get('phase') != 'script_completed' or envelope.get('tab_id') != tab
                or r.get('ok') is not True or r.get('projid') != 'WF_YBX' or r.get('serial') or r.get('visibleDialogs')
                or r.get('scopeComplete') is not True or r.get('identity', {}).get('userid') != userid
                or len(gs) != 1 or gs[0].get('records') != len(gs[0].get('rows', [])) or gs[0].get('loading')):
            raise ValueError('新会话须完整、同账号、无编辑状态的草稿与票池回读')
        grids[kind] = gs[0]
    creation_date = times[0].date().isoformat()
    for row in grids['drafts']['rows']:
        date = row.get('t.req_date', '')
        if not date or datetime.strptime(date, '%Y-%m-%d').date().isoformat() >= creation_date:
            raise ValueError('存在本次创建当日或之后草稿，不能证明未建单')
    for original in manifest['original_files']:
        if digest(original['path']) != original['sha256']:
            raise ValueError('原件SHA变化')
    if select(manifest, pool, manifest['expected_amount'])['selected_invoices'] != manifest['selected_invoices']:
        raise ValueError('新票池与原创建逐票身份不一致')
    if readback is not None and readback != pool:
        raise ValueError('须登记冻结的独立完整票池回读')
    return {'schema': 'ustc-draft-readback-v1', 'state': 'expired_session_not_created', 'serial': None,
            'claim_created': False, 'original_creation_preserved': True, 'next': '登记not_applied后用resume-draft接续一次'}


def register(manifest_path, proof_path):
    path = Path(manifest_path); manifest = json.loads(path.read_text()); proof = json.loads(Path(proof_path).read_text())
    result = verify(manifest, proof)
    pending = json.loads((path.parent / 'pending_write.json').read_text())
    if (pending.get('case_id') != manifest['case_id'] or pending.get('operation') != 'invoice:create-draft'
            or Path(pending['output_directory']).resolve() != Path(manifest['creation_receipt']).resolve().parent):
        raise ValueError('原pending与恢复证据不一致')
    if manifest.get('expired_session_creation_recovery'):
        raise ValueError('已有恢复登记，保留原记录')
    manifest['expired_session_creation_recovery'] = {'proof_path': str(Path(proof_path).resolve()), 'proof_sha256': digest(proof_path)}
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return {**result, 'system_write': False, 'pending_preserved': True}


def registered_proof(manifest, readback=None):
    ref = manifest['expired_session_creation_recovery']
    if digest(ref['proof_path']) != ref['proof_sha256']:
        raise ValueError('恢复登记SHA变化')
    proof = json.loads(Path(ref['proof_path']).read_text())
    return proof, verify(manifest, proof, readback)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--proof', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(register(args.manifest, args.proof), ensure_ascii=False))
