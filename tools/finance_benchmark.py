"""固定只读业务的实测工具；复用统一任务层，不重放保存、审批或打印动作。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from pathlib import Path
import re
import time
from datetime import datetime, timezone

from assistant_api import AssistantService, atomic_json, read_json
from finance_business_api import READ_DOMAINS
from finance_http import source
from material_intake import reject_links
from registered_inputs import load_definition
from registered_session import RegisteredSession


def statistics(samples, requested, target_seconds):
    successes = [s['elapsed_seconds'] for s in samples if s['ok'] and not s.get('reused')]
    failures = sum(not s['ok'] for s in samples)
    values = sorted(successes)
    p50 = values[math.ceil(len(values) * .5) - 1] if values else None
    p95 = values[math.ceil(len(values) * .95) - 1] if values else None
    eligible = len(values) >= 20 and len(samples) == requested and failures == 0
    return {'requested': requested, 'observed': len(samples), 'successes': len(values), 'failures': failures,
            'reused': sum(bool(s.get('reused')) for s in samples), 'p50_seconds': p50, 'p95_seconds': p95,
            'percentile_method': 'nearest-rank', 'target_seconds': target_seconds,
            'sample_requirement_met': eligible,
            'target_met': eligible and p95 <= target_seconds}


def validate_targets(service, root_case, targets, samples):
    if type(samples) is not int or not 1 <= samples <= 50:
        raise ValueError('samples须为1至50；至少20个成功新请求才评价P95目标')
    if not isinstance(targets, list) or not targets or len(targets) > 12:
        raise ValueError('targets须明确1至12组固定只读工作量')
    cfg, index, auth, _ = load_definition(service, root_case)
    normalized, seen = [], set()
    for target in targets:
        if not isinstance(target, dict) or set(target) - {'case_id', 'action', 'domain'}:
            raise ValueError('基准目标只接受case_id、action和domain')
        action, domain, case = target.get('action'), target.get('domain'), target.get('case_id')
        if not isinstance(case, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', case):
            raise ValueError('基准目标须给已登记case_id')
        if action not in {'bill-read', 'bills-list', 'invoices-list'}:
            raise ValueError('基准只执行完整清单、单据读取及日常票池，不执行写入或打印')
        if (action == 'bill-read' and domain not in READ_DOMAINS) or (action != 'bill-read' and domain is not None):
            raise ValueError('基准读取域不匹配')
        key = (case, action, domain)
        if key in seen:
            raise ValueError('重复基准目标')
        seen.add(key)
        _, paths = service.resolve(case)
        form = read_json(paths['form'])
        if form.get('expected_userid') not in (None, '', cfg['expected_userid']):
            raise ValueError('基准案件账号不同')
        if action == 'bill-read' and not form.get('serial'):
            raise ValueError('单据读取基准须有本案已登记单号')
        normalized.append({**target, 'form_sha256': hashlib.sha256(paths['form'].read_bytes()).hexdigest()})
    return cfg, index, auth, normalized


def run(service, *, root_case, targets, samples=20, output_dir=None, execute=False, progress=None):
    cfg, index, auth, normalized = validate_targets(service, root_case, targets, samples)
    code_sha = hashlib.sha256(source('business').encode()).hexdigest()
    plan = {'schema': 'ustc-native-read-benchmark-v1', 'root_case': root_case, 'samples_per_target': samples,
            'targets': normalized, 'adapter_sha256': code_sha, 'system_write': False,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'environment': {'system': platform.system(), 'release': platform.release(),
                            'machine': platform.machine(), 'python': platform.python_version()},
            'business_transport': 'native_http', 'session_preparation_timed_separately': True}
    if not execute:
        return {'ok': True, 'state': 'preview', 'plan': plan, 'browser_actions_executed': False}
    if not output_dir or not Path(output_dir).is_absolute():
        raise ValueError('实际基准须给新的绝对output-dir')
    output = Path(output_dir); reject_links(output)
    if not any(output.resolve().is_relative_to(root) for root in [service.root / '.local', *service.material_roots]):
        raise ValueError('基准输出须位于工具.local或授权材料根')
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / 'report.json'; atomic_json(output / 'plan.json', plan)
    report = {**plan, 'state': 'running', 'ok': False, 'results': [], 'report_path': str(report_path)}
    atomic_json(report_path, report)
    session = RegisteredSession(service, root_case + '-http-benchmark', cfg, index, auth, progress)
    prepared = time.monotonic()
    try:
        session.ensure()
        report['session_preparation_seconds'] = round(time.monotonic() - prepared, 6)
        run_id = str(time.time_ns()); jobs = set()
        for n, target in enumerate(normalized):
            row = {**target, 'samples': []}; report['results'].append(row)
            consecutive_failures = 0
            for i in range(samples):
                _, paths = service.resolve(target['case_id'])
                if (hashlib.sha256(paths['form'].read_bytes()).hexdigest() != target['form_sha256'] or
                        hashlib.sha256(source('business').encode()).hexdigest() != code_sha):
                    raise ValueError('基准代码或案件输入漂移，保留已有样本')
                params = {'action': target['action'], 'tab_id': session.state['tab_id'], 'expected_userid': cfg['expected_userid']}
                if target.get('domain'): params['domain'] = target['domain']
                sample = {'index': i, 'ok': False}; start = time.monotonic()
                def enqueued(job):
                    sample['job_id'] = job['job_id']
                try:
                    job, body = session.job(target['case_id'], 'business', params, 'http-benchmark',
                        request_id=f'http-benchmark-{run_id}-{n}-{i}', on_enqueued=enqueued)
                    sample['reused'] = job['job_id'] in jobs
                    jobs.add(job['job_id'])
                    sample.update(ok=body.get('ok') is True and body.get('complete') is True,
                                  http_elapsed_ms=body.get('elapsed_ms'), request_count=len(body.get('requests', [])))
                    if target['action'] == 'bills-list': sample['records'] = body.get('total_records')
                except Exception as error:
                    sample.update(error_code=getattr(error, 'code', type(error).__name__))
                sample['elapsed_seconds'] = round(time.monotonic() - start, 6)
                row['samples'].append(sample)
                consecutive_failures = 0 if sample['ok'] else consecutive_failures + 1
                row['statistics'] = statistics(row['samples'], samples, 10 if target['action'] == 'bills-list' else 5)
                if target['action'] == 'bills-list' and any(s.get('records', 0) > 100 for s in row['samples']):
                    row['statistics'].update(target_met=False, workload_outside_target=True)
                atomic_json(report_path, report)
                if progress: progress({'target': n, 'sample': i + 1, 'samples': samples, 'ok': sample['ok'], 'elapsed_seconds': sample['elapsed_seconds']})
                if consecutive_failures >= 3:
                    raise ValueError('连续3次只读失败，停止基准，先定位会话/网络故障')
        report.update(state='completed', ok=all(r['statistics']['failures'] == 0 for r in report['results']),
                      all_targets_met=all(r['statistics']['target_met'] for r in report['results']))
    except Exception as error:
        report.update(state='stopped', error_code=getattr(error, 'code', type(error).__name__), error=str(error))
    atomic_json(report_path, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-id', required=True, help='已登记批次入口，复用本机账号和会话合同')
    p.add_argument('--targets', type=Path, required=True, help='JSON数组：case_id、固定action、可选domain')
    p.add_argument('--samples', type=int, default=20)
    p.add_argument('--output-dir', type=Path)
    p.add_argument('--material-root', action='append')
    p.add_argument('--execute', action='store_true', help='只执行上述固定读取；默认离线计划')
    a = p.parse_args()
    report = run(AssistantService(material_roots=a.material_root), root_case=a.case_id,
                 targets=read_json(a.targets), samples=a.samples, output_dir=a.output_dir, execute=a.execute,
                 progress=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True))
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 2


if __name__ == '__main__': raise SystemExit(main())
