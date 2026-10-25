#!/usr/bin/env python3
"""将案件一票一文件清单匹配到已认证发票池；只生成选择参数，不创建单据。"""
import argparse
import json
import re
from decimal import Decimal
from pathlib import Path


def select(manifest, receipt, amount):
    result = receipt.get('result', receipt)
    grids = [x for x in result.get('grids', []) if str(x.get('funcno')) == '53']
    if len(grids) != 1:
        raise ValueError('须提供完整的发票池53独立回读')
    records = grids[0]['rows']
    selected = []
    for f in manifest['original_files']:
        candidates = set(f.get('invoice_number_candidates', []))
        matches = []
        for r in records:
            nums = set(re.findall(r'(?<!\d)(?:\d{20}|\d{8})(?!\d)', r.get('t.tk_code_abst', '')))
            medical = re.fullmatch(r'医/([0-9]{8})/([0-9]{10})', r.get('t.tk_code_abst', ''))
            if medical and r.get('t.tk_type') == 'MEDICAL' and medical[1] == r.get('t.tk_code') == f.get('invoice_code'):
                nums = {medical[2]}
            if candidates.intersection(nums):
                matches.append((r, nums.intersection(candidates)))
        exposed = True
        if len(matches) != 1 or len(matches[0][1]) != 1:
            # 航空行程单列表仅显示航班号，不能把航班号或NaN当发票号。
            # 只接受带原件SHA和回读依据、已明确核验的原生绑定，不按文件名猜。
            b = f.get('verified_native_binding', {})
            if b.get('source_sha256') != f['sha256'] or b.get('confirmed') is not True or not b.get('receipt'):
                raise ValueError('票号无法唯一匹配，须补已核验的原生绑定：' + f['path'])
            found = [r for r in records if r.get('t.tk_guid') == b.get('guid')
                     and r.get('t.tk_type') == b.get('type') and r.get('t.tk_display_abst') == b.get('display')
                     and r.get('t.tk_total_amt', '').replace(',', '') == b.get('amount')
                     and r.get('t.tk_buysno') == b.get('person_id')]
            if len(found) != 1 or b.get('number') not in candidates:
                raise ValueError('已核验原生绑定与回读不符：' + f['path'])
            row, nums, exposed = found[0], {b['number']}, False
        else:
            row, nums = matches[0]
        if row.get('t.serial_no') or row.get('t.tk_mod_flag') != 'NORMAL':
            raise ValueError('票据占用或未正常认证：' + f['path'])
        selected.append({'guid': row['t.tk_guid'], 'number': next(iter(nums)),
                         'type': row['t.tk_type'], 'amount': row['t.tk_total_amt'].replace(',', ''),
                         'source_sha256': f['sha256'], 'number_exposed': exposed,
                         'native_code_abst': row['t.tk_code_abst'], 'native_display_abst': row['t.tk_display_abst'] if not exposed else None,
                         'native_person_id': row.get('t.tk_buysno') if not exposed else None,
                         'binding_verified': not exposed,
                         **({'invoice_code': f['invoice_code']} if row.get('t.tk_type') == 'MEDICAL' else {})})
    if len({x['guid'] for x in selected}) != len(selected):
        raise ValueError('多个原件指向同一票，须去重')
    if len(selected) != manifest['expected_count']:
        raise ValueError('选择票数与预期不符')
    if sum((Decimal(x['amount']) for x in selected), Decimal(0)) != Decimal(amount):
        raise ValueError('票据合计与核定总额不符')
    return {**manifest, 'expected_amount': amount, 'selected_invoices': selected,
            'selection_requires_review': True}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--pool-receipt', type=Path, required=True)
    ap.add_argument('--amount', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    data = select(json.loads(args.manifest.read_text()), json.loads(args.pool_receipt.read_text()), args.amount)
    data['selection_receipt'] = str(args.pool_receipt.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'selected': len(data['selected_invoices']), 'total': data['expected_amount'],
                      'output': str(args.output), 'executed': False}, ensure_ascii=False))
