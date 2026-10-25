#!/usr/bin/env python3
"""按参数执行已上传发票后的日常填报；默认只输出执行计划。遇错停在原地，不盲重试。"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from validate_config import load, load_basic, load_funding

ROOT = Path(__file__).resolve().parents[1]
STAGES = ['basic', 'funding', 'categories', 'expenses', 'payments', 'notes', 'pre-review', 'no-contract', 'audit', 'review']


def plan(c, stages):
    actions = []
    for stage in stages:
        if stage in ['basic', 'funding', 'expenses']:
            actions += [('navigate', ['--section', stage]), (stage, ['--save'])]
        elif stage == 'categories':
            actions += [('invoice-category', ['--index', str(i)]) for i in range(len(c.get('expense_categories', [])))]
        elif stage == 'payments':
            actions += [('navigate', ['--section', 'payments'])]
            if c['payments'] and all(x['mode'] == 'corporate_transfer' for x in c['payments']):
                actions += [('transfers', ['--start', '0', '--limit', '1'])]
            else:
                n = len([x for x in c['payments'] if x['mode'] == 'bank_transfer'])
                actions += [('transfers', ['--start', str(i), '--limit', str(min(5, n-i))]) for i in range(0, n, 5)]
            for i, _ in enumerate([x for x in c['payments'] if x['mode'] == 'corporate_card']):
                actions += [('card-query', ['--index', str(i)]), ('card-select', ['--index', str(i)])]
            actions += [('save-payments', [])]
        elif stage == 'notes':
            actions += [('navigate', ['--section', 'explanation']), ('explanation', ['--save'])]
        elif stage == 'pre-review':
            actions += [('pre-review', ['--index', str(i)]) for i in range(len(c.get('pre_review', [])))]
        elif stage == 'no-contract':
            actions += [('no-contract', [])]
        elif stage == 'audit':
            actions += [('audit', [])]
        elif stage == 'review':
            actions += [('prepare-review', []), ('wait-section', ['--section', 'promise']), ('accept-promise', []),
                        ('wait-section', ['--section', 'department']), ('department-approvers', []),
                        ('wait-section', ['--section', 'applicant'])]
    return actions


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', type=Path, required=True)
    ap.add_argument('--steps', default=','.join(STAGES[:-1]), help='逗号分隔；review单独推进到本人审核，不点击同意')
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--execute', action='store_true', help='在已登录macOS Edge实际执行')
    ap.add_argument('--tab-id', help='财务标签ID，多标签时明确选择')
    args = ap.parse_args()
    c = (load_basic if args.steps == 'basic' else load_funding if args.steps == 'funding' else load)(args.case)
    stages = args.steps.split(',')
    if any(x not in STAGES for x in stages): ap.error('未知步骤')
    if 'review' in stages and c.get('review',{}).get('workflow_started'):
        ap.error('本案已有申请人流程，不能再次生成；先按当前状态回读')
    actions = plan(c, stages)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    commands = [[sys.executable, str(ROOT/'tools/finance_cli.py'), action, '--case', str(args.case.resolve()),
                 '--output', str((args.output_dir/f'{i:02}_{action}.json').resolve()), *params]
                for i, (action, params) in enumerate(actions)]
    if args.tab_id:
        commands = [command + ['--tab-id', args.tab_id] for command in commands]
    receipt = {'case_id': c['case_id'], 'serial': c['serial'], 'executed': args.execute, 'commands': commands, 'completed': []}
    output = args.output_dir/'run_journal.json'
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    if not args.execute:
        print(json.dumps({'executed': False, 'steps': stages, 'actions': len(commands), 'plan': str(output)}, ensure_ascii=False))
        return 0
    for i, command in enumerate(commands):
        result = subprocess.run(command)
        if result.returncode:
            receipt['failed_action'] = i
            receipt['status'] = 'stopped_inspect_before_retry'
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
            return result.returncode
        receipt['completed'].append({'index': i, 'action': actions[i][0], 'finished_at_utc': datetime.now(timezone.utc).isoformat()})
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    receipt['status'] = 'actions_completed_independent_readback_required'
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    return 0


if __name__ == '__main__': raise SystemExit(main())
