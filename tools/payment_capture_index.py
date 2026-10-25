"""离线核对手机付款详情与订单；校验原图摘要，只生成新报告，不写财务或总账。"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from mobile_payment_capture import extract_fields


def apply_allocations(result,plan):
    """完整订单号选择且唯一归属，整张消费发票不能按充值记录任意拆分。"""
    output=copy.deepcopy(result);rows=output['matches'];rules=plan['summary']
    if len({x['key'] for x in rules})!=len(rules):raise ValueError('分组key重复')
    if len({x['label'] for x in rules})!=len(rules):raise ValueError('分组显示名称重复')
    if any('order_refs' in x and len(set(x['order_refs']))!=len(x['order_refs']) for x in rules):raise ValueError('分组内订单号重复')
    selected={}
    for row in rows:
        if row.get('already_reimbursed'):continue
        matched=[r for r in rules if (row['order_ref'] in r['order_refs'] if 'order_refs' in r else row['group'] in r['groups'] and row['order_ref'] not in r.get('exclude_order_refs',[]))]
        if len(matched)!=1:raise ValueError('订单须且只能归入一组：'+row['order_ref'])
        rule=matched[0];selected.setdefault(rule['key'],[]).append(row)
        row.update(allocation_key=rule['key'],allocation_label=rule['label'],fund_id=rule['fund_id'],draft_claim_id=rule.get('draft_claim_id',''),include_in_current=rule.get('include_in_current',True))
    for rule in rules:
        if 'order_refs' in rule and set(rule['order_refs'])!={r['order_ref'] for r in selected.get(rule['key'],[])}:raise ValueError('指定订单未全部找到')
    invoice_owner={}
    for group,basis in plan.get('cost_basis',{}).items():
        if basis.get('kind')=='invoice':
            owners={r['allocation_key'] for r in rows if not r.get('already_reimbursed') and r['group']==group}
            if len(owners)!=1:raise ValueError('同一消费发票不能沿充值行任意拆分')
            invoice_owner[group]=next(iter(owners))
    totals=[]
    for rule in rules:
        part=selected.get(rule['key'],[]);amount=0
        for group in {r['group'] for r in part}:
            basis=plan.get('cost_basis',{}).get(group,{})
            amount+=basis['amount_cents'] if basis.get('kind')=='invoice' else sum(r['amount_cents'] for r in part if r['group']==group)
        if 'expected_amount_cents' in rule and amount!=rule['expected_amount_cents']:raise ValueError('分组金额与已确认金额不一致')
        totals.append(dict(key=rule['key'],label=rule['label'],amount_cents=amount,count=len(part),matched_count=sum(r['status']=='已匹配' for r in part),include_in_current=rule.get('include_in_current',True)))
    output['allocation_totals']=totals
    output['current_amount_cents']=sum(x['amount_cents'] for x in totals if x['include_in_current'])
    output['deferred_amount_cents']=sum(x['amount_cents'] for x in totals if not x['include_in_current'])
    return output


def account_expense_updates(expenses,matches):
    """只更新账号清单名称及本批/下批状态，付款事实与金额不改写。"""
    output=copy.deepcopy(expenses);seen=set()
    for row in matches:
        if row['group']!='闲鱼AI订单' or row.get('already_reimbursed'):continue
        if not row.get('item_name') or not row.get('item_name_basis'):raise ValueError('账号清单名称须有确认或待核依据')
        identity='EXP-XIANYU-'+row['order_ref']
        if identity in seen:raise ValueError('账号订单重复')
        seen.add(identity);choices=[x for x in output if x['expense_id']==identity]
        if len(choices)!=1 or cents(choices[0]['amount'])!=row['amount_cents']:raise ValueError('台账订单或金额不一致')
        expense=choices[0]
        if expense['draft_claim_id']!=row.get('draft_claim_id',''):raise ValueError('已有草案归属不同，不借名称更新改挂草案')
        expense['description']=row['item_name']
        phase='下一批待报，本次不列支' if row.get('include_in_current') is False else '本批待报清单'
        expense['status']=phase+'；'+row['allocation_label']+'；'+row['item_name_basis']+'；未提交财务'
    return output


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cents(value):
    number=Decimal(str(value))
    if not number.is_finite() or number<=0 or number*100!=(number*100).to_integral_value():
        raise ValueError('金额须为正数且不超过两位小数')
    return int(number*100)


def collect(root):
    bills={}
    for step in sorted((root/'steps').iterdir()):
        metadata=step/'capture.json';snapshot=step/'snapshot.json'
        if not step.is_dir() or not metadata.is_file() or not snapshot.is_file():continue
        capture=json.loads(metadata.read_text())
        image=step/'screen.jpg'
        if image.is_symlink() or not image.is_file() or digest(image)!=capture['sha256']:
            raise ValueError('付款截图SHA不符：'+str(step))
        fields=extract_fields(json.loads(snapshot.read_text()))
        bill=fields['bill']
        if not bill or not bill['transaction_ref']:continue
        key=bill['transaction_ref']
        bill={**bill,'amount_cents':cents(bill['amount']),'image':str(image),'sha256':digest(image),
              'snapshot_file':str(snapshot),'capture_file':str(metadata)}
        if key in bills:
            if any(bills[key][field]!=bill[field] for field in ('amount_cents','paid_at','product','merchant_order_ref','status')):
                raise ValueError('同一支付编号的字段冲突：'+key)
            continue
        bills[key]=bill
    return list(bills.values())


def reference_matches(order,bill):
    ref=order['order_ref']
    if order['reference_kind']=='xianyu':return bill['merchant_order_ref']=='T200P'+ref
    if order['reference_kind']=='merchant_order_ref':return bill['merchant_order_ref']==ref
    if order['reference_kind']=='product':return bill['product']==ref
    raise ValueError('未知订单号类型')


def reconcile(orders,bills):
    seen=set();used=set();matches=[]
    for order in orders:
        key=(order['group'],order['order_ref'])
        if key in seen:raise ValueError('输入订单重复')
        seen.add(key)
        result={**order,'amount_cents':cents(order['amount']),'payment':None,'status':'待补充','date_note':''}
        if order.get('already_reimbursed'):
            result['status']='已报销，排除';matches.append(result);continue
        candidates=[b for b in bills if reference_matches(order,b)]
        if not candidates:
            result['date_note']=order.get('pending_note','未取得对应付款详情');matches.append(result);continue
        if len(candidates)!=1:raise ValueError('同一订单存在多笔付款，须独立核对')
        bill=candidates[0];result['payment']=bill
        if bill['transaction_ref'] in used:raise ValueError('同一付款重复对应不同订单')
        used.add(bill['transaction_ref'])
        if bill['amount_cents']!=result['amount_cents'] or bill['status']!='交易成功':
            result['status']='金额或交易状态待核对';matches.append(result);continue
        source=datetime.fromisoformat(order['source_time'])
        paid=datetime.fromisoformat(bill['paid_at'])
        result['observed_delta_seconds']=int((paid-source).total_seconds())
        kind=order['time_kind']
        if kind=='paid_at_exact':
            result['status']='已匹配' if paid==source else '支付日期待核对'
            result['date_note']='支付时间一致' if paid==source else '订单支付时间与支付宝不一致，保留两者'
        elif kind=='vendor_status_at':
            result['status']='已匹配' if paid.date()==source.date() else '支付日期待核对'
            result['date_note']='网站已支付时间与支付宝支付时间分别保留'
        elif kind=='platform_created_at':
            result['status']='已匹配'
            result['date_note']='平台创建时间时区未核；按完整商家订单号匹配，以支付宝支付时间登记'
        else:raise ValueError('未知时间语义')
        matches.append(result)
    return matches


def build(request,root,output):
    for source in request['sources']:
        if digest(Path(source['path']))!=source['sha256']:raise ValueError('订单来源SHA不符')
    bills=collect(root);matches=reconcile(request['orders'],bills)
    totals=[]
    for group in dict.fromkeys(x['group'] for x in matches):
        rows=[x for x in matches if x['group']==group and not x.get('already_reimbursed')]
        totals.append(dict(group=group,count=len(rows),order_amount_cents=sum(x['amount_cents'] for x in rows),
                           matched_count=sum(x['status']=='已匹配' for x in rows),
                           captured_amount_cents=sum(x['amount_cents'] for x in rows if x['status']=='已匹配')))
    result=dict(schema='payment-capture-index.v1',financial_system_write=False,
                sources=request['sources'],totals=totals,matches=matches,
                issues=[x for x in matches if x['status'] not in ('已匹配','已报销，排除')])
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    (output/'匹配结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orders',type=Path,required=True)
    parser.add_argument('--capture-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=build(json.loads(args.orders.read_text()),args.capture_root,args.output)
    print(json.dumps({'totals':result['totals'],'issues':len(result['issues']),'output':str(args.output)},ensure_ascii=False))


if __name__=='__main__':main()
