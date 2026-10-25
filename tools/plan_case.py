#!/usr/bin/env python3
"""校验案件金额和交付材料，生成结构化计划；不会请求或写入财务系统。"""
import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def money(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d < 0 or d.as_tuple().exponent < -2:
            raise ValueError('金额必须为非负、最多两位小数')
        return d
    except InvalidOperation as exc:
        raise ValueError('金额无效') from exc


def build(case):
    errors, questions, warnings = [], list(case.get('questions', [])), []
    total = money(case['amount'])
    payments = sum((money(p['amount']) for p in case['payments']), Decimal('0'))
    funding = sum((money(p['amount']) for p in case['funding']), Decimal('0'))
    if payments != total:
        errors.append({'code': 'PAYMENT_TOTAL_MISMATCH', 'difference': str(total-payments)})
    if funding != total:
        errors.append({'code': 'FUNDING_TOTAL_MISMATCH', 'difference': str(total-funding)})
    for p in case['payments']:
        if not p.get('person_id') or not p.get('name'):
            errors.append({'code': 'UNRESOLVED_PAYEE', 'payment': p.get('name')})
    for a in case.get('attachments', []):
        if a.get('required') and a.get('status') not in ('uploaded', 'not_applicable', 'linked_original'):
            warnings.append({'code': 'ATTACHMENT_NOT_READY', 'role': a['role'], 'status': a.get('status')})
        if a.get('status') == 'linked_original' and not a.get('original_claim_number'):
            errors.append({'code': 'ORIGINAL_CROSS_REFERENCE_MISSING', 'role': a['role']})
    for explanation in case.get('explanations', []):
        if explanation.get('required') and explanation.get('status') not in ('saved', 'uploaded', 'approved'):
            warnings.append({'code': 'EXPLANATION_NOT_READY', 'role': explanation['role'],
                             'status': explanation.get('status')})
    delegated = case.get('delivery', {}).get('mode') == 'finance_print'
    if delegated:
        for a in case.get('attachments', []):
            if a.get('required') and a.get('status') not in ('uploaded', 'not_applicable', 'linked_original'):
                errors.append({'code': 'FINANCE_PRINT_ATTACHMENT_INCOMPLETE', 'role': a['role']})
        if case.get('delivery', {}).get('originals_required') is not False:
            errors.append({'code': 'FINANCE_PRINT_ORIGINALS_UNRESOLVED'})
    return {'case_id': case['case_id'], 'serial': case.get('serial'), 'amount': str(total),
            'payment_total': str(payments), 'funding_total': str(funding),
            'errors': errors, 'warnings': warnings, 'questions': questions,
            'current_status': case.get('system_status'), 'ready_for_human_review': not errors,
            'ready_for_final_confirmation': not (errors or warnings or questions),
            'final_confirmation': 'human_review_then_native_captcha',
            'rebuild_allowed_now': case.get('system_status') == 'draft' and not errors,
            'actions': ['read_current_case', 'check_invoice_identity_and_occupancy',
                        'check_native_payee_binding', 'compare_three_totals',
                        'check_explanations_and_delivery_materials', 'human_review']}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('case', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    result = build(json.loads(args.case.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'actions'}, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
