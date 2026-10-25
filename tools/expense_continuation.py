"""从冻结材料推导预付账户及订单续报边界；不修改案件和财务状态。"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path


def cents(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f'{name}: expected non-negative integer cents, got {value!r}')
    return value


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be text')
    result = datetime.fromisoformat(value)
    if result.tzinfo is not None or len(value) < 19:
        raise ValueError('timestamp must preserve a full naive display time')
    return result


def deduplicate(transactions):
    seen = {}
    fields = ('timestamp', 'amount_cents', 'status', 'channel')
    for original in transactions:
        row = copy.deepcopy(original)
        identity = row.get('id')
        if not isinstance(identity, str) or not identity:
            raise ValueError('transaction id is required')
        stamp(row['timestamp'])
        cents(row['amount_cents'], identity)
        if row['status'] not in ('success', 'cancelled'):
            raise ValueError(f'{identity}: unknown status')
        if not isinstance(row['channel'], str) or not row['channel']:
            raise ValueError(f'{identity}: channel is required')
        row['source_refs'] = list(row.get('source_refs', []))
        if identity in seen:
            if any(row[k] != seen[identity][k] for k in fields):
                raise ValueError(f'{identity}: conflicting observations')
            seen[identity]['source_refs'] = sorted(set(seen[identity]['source_refs'] + row['source_refs']))
        else:
            seen[identity] = row
    return sorted(seen.values(), key=lambda r: (stamp(r['timestamp']), r['id']))


def fifo_inference(history, current, opening, closing):
    """假定FIFO且无其他余额变动；历史数据不足的余额保留为未知。"""
    remaining = opening
    carry = []
    for row in reversed(history):
        if not remaining:
            break
        value = min(remaining, row['amount_cents'])
        carry.append(dict(row, opening_available_cents=value, period='opening_carry'))
        remaining -= value
    carry.reverse()
    if remaining:
        carry.insert(0, dict(id='UNKNOWN_OPENING', timestamp='', channel='unknown',
                            amount_cents=None, opening_available_cents=remaining,
                            period='opening_carry', source_refs=[]))
    supply = carry + [dict(r, opening_available_cents=r['amount_cents'], period='new_recharge') for r in current]
    to_consume = opening + sum(r['amount_cents'] for r in current) - closing
    if to_consume < 0:
        return {'available': False, 'reason': 'closing balance exceeds known funds', 'rows': []}
    for row in supply:
        used = min(to_consume, row['opening_available_cents'])
        row['inferred_consumed_cents'] = used
        row['inferred_remaining_cents'] = row['opening_available_cents'] - used
        row['basis'] = 'FIFO assumption; not an invoice allocation'
        to_consume -= used
    return {'available': True, 'assumption_only': True, 'unattributed_opening_cents': remaining, 'rows': supply}


def reconcile_prepaid(data):
    start, end = date.fromisoformat(data['period_start']), date.fromisoformat(data['period_end'])
    if start > end:
        raise ValueError('period_start is after period_end')
    opening = cents(data['opening_balance_cents'], 'opening_balance_cents')
    closing = cents(data['closing_balance_cents'], 'closing_balance_cents')
    invoice = cents(data['invoice_cents'], 'invoice_cents')
    rows = deduplicate(data['transactions'])
    current = [r for r in rows if start <= stamp(r['timestamp']).date() <= end]
    success = [r for r in current if r['status'] == 'success']
    shift = data.get('display_to_payment_shift_minutes')
    if shift is not None and (type(shift) is not int or abs(shift) > 1440):
        raise ValueError('invalid estimated time shift')
    for row in success:
        row['estimated_payment_time'] = ((stamp(row['timestamp']) + timedelta(minutes=shift)).isoformat(' ')
                                          if shift is not None else None)
    channels = {}
    for row in success:
        channels[row['channel']] = channels.get(row['channel'], 0) + row['amount_cents']
    recharge = sum(r['amount_cents'] for r in success)
    burn = opening + recharge - closing
    result = dict(period_start=str(start), period_end=str(end), input_observations=len(data['transactions']),
                  unique_transactions=len(rows), duplicate_observations=len(data['transactions'])-len(rows),
                  opening_balance_cents=opening, closing_balance_cents=closing, invoice_cents=invoice,
                  new_recharge_count=len(success), new_recharge_cents=recharge, channels=channels,
                  inferred_paid_wallet_consumption_cents=burn, invoice_minus_inferred_cents=invoice-burn,
                  cancelled_period_count=sum(r['status']=='cancelled' for r in current),
                  new_recharges=success, assumption='same paid wallet; no other net balance movements',
                  allocation_confirmed=False, warnings=[])
    if burn < 0:
        result['warnings'].append('closing balance exceeds known funds; balance bridge not valid')
    for key in ('opening_lifetime_spend_cents', 'closing_lifetime_spend_cents'):
        if key in data:
            cents(data[key], key)
    if 'confirmed_non_paid_consumption_cents' in data and not all(
            k in data for k in ('opening_lifetime_spend_cents', 'closing_lifetime_spend_cents')):
        raise ValueError('confirmed non-paid consumption requires both cumulative snapshots')
    if all(k in data for k in ('opening_lifetime_spend_cents', 'closing_lifetime_spend_cents')):
        delta = data['closing_lifetime_spend_cents'] - data['opening_lifetime_spend_cents']
        result.update(lifetime_consumption_delta_cents=delta, lifetime_minus_paid_wallet_cents=delta-burn)
        if 'confirmed_non_paid_consumption_cents' in data:
            bonus = cents(data['confirmed_non_paid_consumption_cents'], 'confirmed_non_paid_consumption_cents')
            basis = data.get('non_paid_consumption_basis')
            if not isinstance(basis, str) or not basis.strip():
                raise ValueError('non_paid_consumption_basis is required')
            result.update(confirmed_non_paid_consumption_cents=bonus,
                          non_paid_consumption_basis=basis,
                          unexplained_after_non_paid_cents=delta-burn-bonus)
            if delta-burn-bonus:
                result['warnings'].append('residual remains after confirmed non-paid consumption; snapshots not fully aligned')
        elif delta != burn:
            result['warnings'].append('lifetime consumption and paid-wallet bridge differ; cause unresolved')
    if data.get('fifo_inference'):
        history = [r for r in rows if r['status']=='success' and stamp(r['timestamp']).date() < start]
        result['fifo'] = fifo_inference(history, success, opening, closing)
    return result


def order_checkpoint(records, candidates=()):
    seen_payments, seen_orders = set(), set()
    confirmed = []
    for row in records:
        identity = row.get('payment_id')
        if not identity or identity in seen_payments:
            raise ValueError('missing or duplicate payment_id')
        seen_payments.add(identity)
        if type(row.get('reimbursed')) is not bool:
            raise ValueError('reimbursed must be explicit boolean')
        stamp(row['paid_at']); cents(row['amount_cents'], identity)
        if row.get('order_id'):
            if row['order_id'] in seen_orders:
                raise ValueError('duplicate order_id')
            seen_orders.add(row['order_id'])
        if row['reimbursed']:
            confirmed.append(copy.deepcopy(row))
    confirmed.sort(key=lambda r: (stamp(r['paid_at']), r['payment_id']))
    last = confirmed[-1] if confirmed else None
    known = {r['order_id']: r for r in confirmed if r.get('order_id')}
    classified = []
    for row in candidates:
        when = stamp(row['paid_at'])
        if row.get('order_id') in known:
            previous = known[row['order_id']]
            conflict = when != stamp(previous['paid_at'])
            if 'amount_cents' in row:
                conflict |= cents(row['amount_cents'], row['order_id']) != previous['amount_cents']
            verdict = 'known_order_fields_changed_review' if conflict else 'already_reimbursed_order'
        elif not row.get('order_id'):
            verdict = 'needs_order_id'
        elif last and when.date() <= stamp(last['paid_at']).date():
            verdict = 'boundary_or_older_unmatched_review'
        else:
            verdict = 'new_candidate_not_approved'
        classified.append(dict(row, classification=verdict))
    return dict(confirmed=confirmed, count=len(confirmed), amount_cents=sum(r['amount_cents'] for r in confirmed),
                latest=last, recommended_export_start=last['paid_at'][:10] if last else None,
                next_full_day=str(stamp(last['paid_at']).date()+timedelta(days=1)) if last else None,
                date_is_filter_not_proof=True, candidates=classified)


def verify_sources(entries, base):
    if not entries:
        raise ValueError('sources are required')
    checked=[]
    for entry in entries:
        source = Path(entry['path'])
        if not source.is_absolute():
            source = base/source
        actual=hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != entry['sha256']:
            raise ValueError(f'source SHA mismatch: {source}')
        checked.append(dict(path=str(source.resolve()), sha256=actual))
    return checked


def write_csv(path, rows, fields):
    with path.open('x', encoding='utf-8-sig', newline='') as handle:
        writer=csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore');writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})


def run(input_path, output_dir):
    input_bytes=input_path.read_bytes()
    data=json.loads(input_bytes.decode('utf-8'))
    if data.get('schema') != 'expense-continuation-v1':
        raise ValueError('unsupported schema')
    checked=verify_sources(data['sources'],input_path.parent)
    result=dict(schema='expense-continuation-result-v1', financial_system_write=False, verified_sources=checked,
                input_sha256=hashlib.sha256(input_bytes).hexdigest())
    if 'prepaid' in data:
        result['prepaid']=reconcile_prepaid(data['prepaid'])
    if 'orders' in data:
        result['orders']=order_checkpoint(data['orders'],data.get('candidate_orders',[]))
    output_dir.mkdir(parents=True,exist_ok=False)
    (output_dir/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    prepaid=result.get('prepaid',{})
    write_csv(output_dir/'new_recharges.csv',prepaid.get('new_recharges',[]),
              ['id','timestamp','estimated_payment_time','amount_cents','channel','status','source_refs'])
    write_csv(output_dir/'fifo_inference.csv',prepaid.get('fifo',{}).get('rows',[]),
              ['id','timestamp','channel','period','amount_cents','opening_available_cents','inferred_consumed_cents','inferred_remaining_cents','basis','source_refs'])
    write_csv(output_dir/'order_checkpoint.csv',result.get('orders',{}).get('confirmed',[]),
              ['payment_id','order_id','paid_at','amount_cents','reimbursed','source_refs'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    try:
        result=run(args.input,args.output_dir)
    except (ValueError,KeyError,OSError) as exc:
        parser.exit(2,f'{type(exc).__name__}: {exc}\n')
    print(json.dumps({'output':str(args.output_dir),'prepaid_orders':result.get('prepaid',{}).get('new_recharge_count'),
                      'order_checkpoint':result.get('orders',{}).get('recommended_export_start')},ensure_ascii=False))


if __name__=='__main__':
    main()
