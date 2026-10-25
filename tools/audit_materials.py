#!/usr/bin/env python3
"""按业务配置核对必要材料，输出缺件和条件；不修改原件、系统或说明。"""
import argparse
import json
from collections import defaultdict
from decimal import Decimal
from payment_evidence import amount, payment_requirement
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READY = {'verified', 'native_verified', 'uploaded_verified', 'physical_ready', 'linked_original'}


def audit(case, rules):
    if rules.get('configuration_status') == 'needs_confirmation':
        raise ValueError('材料规则尚未配置确认；先登记适用规范，不能将空规则当作材料齐全')
    context = case.get('material_context', {})
    profiles = set(context.get('profiles', []))
    if not profiles:
        raise ValueError('先指定material_context.profiles，不能用同一材料要求套所有业务')
    materials = case.get('materials', [])
    checks = []
    for rule in rules['rules']:
        if '*' not in rule['profiles'] and not profiles.intersection(rule['profiles']):
            continue
        conditions = rule.get('when_all', {})
        if any(k in context and context[k] != v for k, v in conditions.items()):
            continue
        unknown = [k for k in conditions if k not in context]
        matches = [m for m in materials if m.get('role') == rule['role']]
        issue = None
        if unknown:
            issue = 'condition_unknown'
        elif not matches:
            issue = 'missing'
        elif any(m.get('status') not in READY for m in matches):
            issue = 'not_verified_or_not_ready'
        elif any(m.get('status') == 'linked_original' and not m.get('original_claim_number') for m in matches):
            issue = 'original_claim_number_missing'
        uncovered = []
        optional_uncovered = []
        expected = []
        if rule.get('coverage') == 'per_invoice':
            scope = case.get('invoices', {}).get('material_ids', [])
            details = case.get('invoices', {}).get('material_details', {})
            expected = [i for i in scope if payment_requirement(details.get(i, {}), profiles)[0]]
            ready = [m for m in matches if m.get('status') in READY
                     and m.get('evidence_type') in {'payment_screenshot', 'bank_receipt'}
                     and m.get('match_status') == 'verified']
            covered = {i for m in ready for i in m.get('invoice_ids', [])}
            uncovered = [i for i in expected if i not in covered]
            amounts = case.get('invoices', {}).get('material_amounts', {})
            allocated = defaultdict(Decimal)
            for m in ready:
                for part in m.get('allocations', []):
                    value = amount(part['amount'])
                    if value <= 0:
                        raise ValueError('付款分摊须为正金额')
                    allocated[part['invoice_id']] += value
            if amounts and set(amounts) == set(scope):
                uncovered = [i for i in expected if allocated[i] < Decimal(amounts[i])]
                optional_uncovered = [i for i in scope if i not in expected and allocated[i] < Decimal(amounts[i])]
            else:
                optional_uncovered = [i for i in scope if i not in expected and i not in covered]
            if scope and not expected:
                issue = None  # 全为明确例外费用时，不因未提供截图而报必需材料缺件。
            if not issue:
                if not scope:
                    issue = 'invoice_scope_unknown'
                elif len(set(scope)) != len(scope) or case.get('invoices', {}).get('count', len(scope)) != len(scope):
                    issue = 'invoice_scope_incomplete'
                elif any(set(m.get('invoice_ids', [])) != {a['invoice_id'] for a in m.get('allocations', [])} for m in ready):
                    issue = 'payment_binding_mismatch'
                elif covered - set(scope):
                    issue = 'payment_binding_outside_case'
                elif set(amounts) != set(scope) and (expected or ready):
                    issue = 'invoice_amount_scope_unknown'
                elif set(allocated) - set(scope):
                    issue = 'payment_binding_outside_case'
                elif any(allocated[i] > Decimal(amounts[i]) for i in scope if i in amounts):
                    issue = 'payment_overallocated'
                elif uncovered:
                    issue = 'payment_coverage_incomplete'
        checks.append({'rule_id': rule['id'], 'role': rule['role'], 'level': rule['level'],
                       'basis': rule['basis'], 'source': rule.get('source'),
                       'issue': issue, 'unknown_conditions': unknown,
                       'uncovered_invoice_ids': uncovered,
                       'optional_uncovered_invoice_ids': optional_uncovered,
                       'payment_required_invoice_count': len(expected) if rule.get('coverage') == 'per_invoice' else None,
                       'observed_statuses': [m.get('status') for m in matches],
                       'instruction': rule['instruction']})
    required = [x for x in checks if x['level'] == 'required' and x['issue']]
    suggested = [x for x in checks if x['level'] == 'suggested' and x['issue']]
    return {'case_id': case['case_id'], 'serial': case.get('serial'), 'profiles': sorted(profiles),
            'ready_for_material_delivery': not required, 'missing_or_pending': required,
            'suggestions': suggested, 'checks': checks, 'system_write': False,
            'note': '材料就绪与财务审批通过分别记录；文件存在不等于内容匹配或已上传。'}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', type=Path, required=True)
    ap.add_argument('--rules', type=Path, default=ROOT/'data/material_rules.json')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    result = audit(json.loads(args.case.read_text()), json.loads(args.rules.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'ready_for_material_delivery': result['ready_for_material_delivery'],
                      'pending_count': len(result['missing_or_pending']), 'output': str(args.output),
                      'system_write': False}, ensure_ascii=False))
