"""付款凭证逐票匹配、覆盖和位置检查；不把订单、聊天或发票当实际付款。"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from assistant_contract import CASE_PROFILES

PAYMENT_TYPES = {'payment_screenshot', 'bank_receipt'}
OTHER_TYPES = {'invoice', 'boarding_pass', 'paper_original', 'order_only', 'chat_only', 'other'}
ASSEMBLY_MODES = {'attach_available', 'require_complete'}
TAXI_KINDS = {'打车', '出租车', '网约车', 'taxi', 'ride_hailing'}


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        raise ValueError('付款金额须为非负两位小数字符串')
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('付款金额不合法') from exc


def policy(profiles, assembly_mode='attach_available'):
    if not profiles or not set(profiles).issubset(CASE_PROFILES):
        raise ValueError('付款规则先明确本案业务profiles')
    if assembly_mode not in ASSEMBLY_MODES:
        raise ValueError('付款整理模式须为attach_available或require_complete')
    return dict(assembly_mode=assembly_mode, require_student_non_taxi='student_activity' in profiles,
                student_taxi_exception=True,
                attach_all_available=True, source='用户2026-10-09办理要求',
                note='学生活动除打车发票外通常需要支付凭证，打车默认不要求；科研通常不要求。已有凭证均匹配附入，具体审核要求可逐票覆盖。')


def payment_requirement(invoice, profiles):
    """按已明确的费用性质判断，不能把所有市内交通/包车都当打车例外。"""
    if 'payment_proof_required' in invoice:
        if not isinstance(invoice['payment_proof_required'], bool) or not invoice.get('payment_requirement_basis', '').strip():
            raise ValueError('逐票付款要求覆盖须为布尔值并附本案依据')
        return invoice['payment_proof_required'], invoice['payment_requirement_basis']
    if 'student_activity' in profiles:
        kind = invoice.get('expense_kind') or invoice.get('expense_type')
        if kind in TAXI_KINDS:
            return False, '学生活动打车发票默认不要求支付凭证'
        return True, '学生活动非打车费用通常需要支付凭证'
    return False, '科研等普通业务默认不要求支付凭证'


def enforce_assembly(report):
    if not report['ready_for_payment_assembly']:
        raise ValueError('完整材料模式尚缺付款凭证；可按用户安排切换attach_available整理已有材料')


def status_label(row, assembly_mode):
    if row['status'] == 'matched':
        return '已匹配并附入'
    if row['status'] == 'partial':
        return '已有凭证已附（金额部分覆盖）'
    if assembly_mode == 'attach_available':
        return '未提供，本次不附' if row['required'] else '默认无需，未提供则不附'
    return '待补' if row['required'] else '默认无需'


def discover(report):
    """列出需人工识别的页；名称/金额/OCR只产生候选，不自动证明已付款。"""
    found = {}
    for asset in report['assets']:
        text = asset.get('text', '').strip()
        name = asset['source']
        hints = []
        if asset['kind'] == 'image' or (asset['kind'] == 'pdf_page' and not text):
            hints.append('image_or_scanned_page')
        if asset['kind'] in {'image', 'pdf_page'} and re.search(r'付款|支付|交易|账单|订单|截图|实付', name + '\n' + text):
            hints.append('payment_or_order_terms')
        if not hints:
            continue
        row = found.setdefault(asset['asset_id'], dict(asset_id=asset['asset_id'], sources=[], hints=hints))
        row['sources'].append(name)
    return list(found.values())


def arrange(plan):
    """已明确绑定的付款页只放一次，跟在最后一张对应发票及其辅助材料之后。"""
    out = copy.deepcopy(plan)
    items = {r['id']: r for s in out['sections'] for r in s['items']}
    order = [r['id'] for s in out['sections'] for r in s['items']]
    records = out.get('payment_evidence', {}).get('records', [])
    proofs = {m for record in records for m in record['material_ids']}
    for section in out['sections']:
        section['items'] = [r for r in section['items'] if r['id'] not in proofs]
    at = defaultdict(list)
    for record in records:
        ids = [a['invoice_id'] for a in record['allocations']]
        if not ids or any(i not in order for i in ids):
            raise ValueError('付款凭证须绑定本案发票后才能排序')
        anchor = max(ids, key=order.index)
        at[anchor].extend(items[i] for i in record['material_ids'])
    for section in out['sections']:
        result, anchor = [], None
        for item in section['items']:
            if item.get('role') == 'invoice':
                if anchor:
                    result.extend(at[anchor])
                anchor = item['id']
            result.append(item)
        if anchor:
            result.extend(at[anchor])
        section['items'] = result
    return out


def audit_plan(plan, coverage=None):
    cfg = plan.get('payment_evidence')
    if any(r.get('role') == 'payment_proof' and r['kind'] != 'source' for s in plan['sections'] for r in s['items']):
        raise ValueError('付款附件只放真实凭证，不生成缺失截图占位页')
    items = [r for s in plan['sections'] for r in s['items'] if r['kind'] == 'source']
    proof_items = {r['id']: r for r in items if r.get('role') == 'payment_proof'}
    if len({(r['sha256'], r.get('page', 0)) for r in proof_items.values()}) != len(proof_items):
        raise ValueError('同一付款截图不能使用副本重复附入')
    if cfg is None:
        raise ValueError('先配置payment_evidence.profiles及逐票付款记录；不能跳过付款材料核对')
    rules = policy(cfg.get('profiles', []), cfg.get('assembly_mode', 'attach_available'))
    invoices = {r['id']: r for r in items if r.get('role') == 'invoice'}
    by_id = {r['id']: r for r in items}
    allocations = defaultdict(Decimal)
    refs = defaultdict(list)
    record_rows, seen_records, seen_materials = [], set(), set()
    known_assets = {a['asset_id'] for a in coverage['assets']} if coverage else None
    reviewed = cfg.get('reviewed_assets', {})
    if coverage:
        for candidate in discover(coverage):
            review = reviewed.get(candidate['asset_id'], {})
            if review.get('type') not in PAYMENT_TYPES | OTHER_TYPES or not review.get('reason', '').strip():
                raise ValueError('付款候选页未逐页识别，不能遗漏截图：' + candidate['sources'][0])
    for record in cfg.get('records', []):
        id_ = record['id']
        if id_ in seen_records:
            raise ValueError('付款记录编号重复')
        seen_records.add(id_)
        mids = record['material_ids']
        if not mids or len(set(mids)) != len(mids) or any(m not in proof_items or m in seen_materials for m in mids):
            raise ValueError('每张付款页须唯一附入，不遗漏、不重复')
        seen_materials.update(mids)
        if record.get('type') not in PAYMENT_TYPES or record.get('match_status') != 'verified' or record.get('payment_status') != 'success':
            raise ValueError('订单/聊天不等于付款成功；付款记录须人工核对匹配')
        if not all(record.get(k, '').strip() for k in ['payer', 'merchant', 'paid_at', 'match_basis']):
            raise ValueError('付款凭证须核对付款人、收款方、时间及匹配依据')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].+)?', record['paid_at']):
            raise ValueError('付款时间须明确日期')
        try:
            datetime.fromisoformat(record['paid_at'])
        except ValueError as exc:
            raise ValueError('付款日期或时间不合法') from exc
        bound = record.get('allocations', [])
        ids = [a['invoice_id'] for a in bound]
        if not ids or len(ids) != len(set(ids)) or any(i not in invoices for i in ids):
            raise ValueError('付款记录只能绑定本案唯一的核定发票')
        for mid in mids:
            item = by_id[mid]
            asset_id = f"{item['sha256']}:p{item.get('page', 0)}"
            if set(item.get('invoice_ids', [])) != set(ids):
                raise ValueError('付款页清单绑定与付款分摊不一致')
            if known_assets is not None and asset_id not in known_assets:
                raise ValueError('付款页不在本案来源范围内')
            if coverage and reviewed.get(asset_id, {}).get('type') != record['type']:
                raise ValueError('付款页识别类型与实际凭证不一致')
            if item.get('payee') not in {invoices[i]['payee'] for i in ids}:
                raise ValueError('付款页清单归属与对应收款人不一致')
        total = Decimal(0)
        for a in bound:
            invoice = invoices[a['invoice_id']]
            share = amount(a['amount'])
            if not 0 < share <= amount(invoice['amount']):
                raise ValueError('付款分摊超过对应发票金额')
            if record['payer'] != invoice['payee'] and not record.get('payer_relationship', '').strip():
                raise ValueError('付款人与报销收款人不同，须有代付/团购对应说明')
            allocations[a['invoice_id']] += share
            refs[a['invoice_id']].append(id_)
            total += share
        paid, unclaimed = amount(record['paid_amount']), amount(record.get('unclaimed_amount', '0.00'))
        if total + unclaimed != paid or (unclaimed and not record.get('difference_reason', '').strip()):
            raise ValueError('付款总额与报销分摊不符；自费、保险、退款等差异须明确核对')
        order = [r['id'] for r in items]
        anchor = max(ids, key=order.index)
        anchor_section = next(s for s in plan['sections'] if any(r['id'] == anchor for r in s['items']))
        section_ids = [r['id'] for r in anchor_section['items']]
        if any(mid not in section_ids for mid in mids) or min(order.index(m) for m in mids) <= order.index(anchor):
            raise ValueError('付款页须跟在最后一张对应发票或费用组后')
        last = max(order.index(m) for m in mids)
        if any(by_id[i].get('role') == 'invoice' for i in order[order.index(anchor) + 1:last + 1]):
            raise ValueError('付款页被放到其他费用之后；先用arrange按对应费用排序')
        record_rows.append(dict(id=id_, payer=record['payer'], merchant=record['merchant'],
                                paid_at=record['paid_at'], paid_amount=f'{paid:.2f}',
                                invoice_ids=ids, material_ids=mids, anchor_invoice_id=anchor))
    if seen_materials != set(proof_items):
        raise ValueError('已有付款截图须全部匹配附入，不允许静默遗漏')
    if coverage:
        for asset_id, review in reviewed.items():
            if review.get('type') in PAYMENT_TYPES and not any(
                    f"{r['sha256']}:p{r.get('page', 0)}" == asset_id for r in proof_items.values()):
                raise ValueError('已找到付款截图但没有实际附件页：' + asset_id)
    rows = []
    for invoice in invoices.values():
        required, basis = payment_requirement(invoice, cfg['profiles'])
        matched = allocations[invoice['id']]
        needed = amount(invoice['amount'])
        if matched > needed:
            raise ValueError('重复付款匹配超过发票金额：' + invoice['id'])
        rows.append(dict(invoice_id=invoice['id'], payee=invoice['payee'], expense=invoice['label'],
                         amount=f'{needed:.2f}', matched_amount=f'{matched:.2f}',
                         missing_amount=f'{needed - matched:.2f}', proof_ids=refs[invoice['id']],
                         status='matched' if matched == needed else 'partial' if matched else 'missing',
                         required=required, requirement_basis=basis))
    missing = [r for r in rows if r['status'] != 'matched']
    required_missing = [r for r in missing if r['required']]
    complete = not required_missing
    return dict(case_id=plan['case_id'], serial=plan['serial'], policy=rules,
                assembly_mode=rules['assembly_mode'],
                ready_for_payment_assembly=rules['assembly_mode'] == 'attach_available' or complete,
                records=record_rows, invoices=rows, payment_proof_pages=len(proof_items),
                matched_invoice_count=len(rows) - len(missing), missing_invoice_count=len(missing),
                required_missing_invoice_count=len(required_missing),
                optional_missing_invoice_count=len(missing) - len(required_missing),
                ready_for_payment_material_delivery=complete,
                missing_or_optional=missing, finance_system_write=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--discover', action='store_true')
    ap.add_argument('--arrange-output', type=Path)
    ap.add_argument('--assembly-mode', choices=sorted(ASSEMBLY_MODES))
    args = ap.parse_args()
    if args.output.exists() or (args.arrange_output and args.arrange_output.exists()):
        raise ValueError('保留原回执和计划，输出须为新文件')
    if args.arrange_output and args.output.resolve() == args.arrange_output.resolve():
        raise ValueError('排序后计划与核验回执须使用不同文件')
    if args.discover and args.arrange_output:
        raise ValueError('先识别并绑定付款候选页，再单独排序')
    plan = json.loads(args.plan.read_text())
    if args.assembly_mode:
        plan.setdefault('payment_evidence', {})['assembly_mode'] = args.assembly_mode
    root = (args.plan.resolve().parent / plan['source_root']).resolve()
    import material_coverage
    # 新材料无法悄悄绕过来源盘点；候选发现也重新读取全部目录和嵌套ZIP。
    if args.discover:
        report = dict(assets=material_coverage.inventory(plan, root))
        result = dict(case_id=plan['case_id'], serial=plan['serial'], candidates=discover(report),
                      review_required=True, finance_system_write=False)
    else:
        if args.arrange_output:
            plan = arrange(plan)
        material_coverage.validate(plan, root)
        report = json.loads((root / plan['coverage_manifest']).read_text())
        result = audit_plan(plan, report)
        if result is None:
            raise ValueError('先在本案计划明确payment_evidence.profiles及记录，不猜业务')
    if args.arrange_output:
        plan['source_root'] = os.path.relpath(root, args.arrange_output.resolve().parent)
        args.arrange_output.parent.mkdir(parents=True, exist_ok=True)
        args.arrange_output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(ok=True, output=str(args.output.resolve()),
                          matched_invoice_count=result.get('matched_invoice_count'),
                          missing_invoice_count=result.get('missing_invoice_count'),
                          payment_materials_ready=result.get('ready_for_payment_material_delivery'),
                          assembly_mode=result.get('assembly_mode'),
                          payment_assembly_ready=result.get('ready_for_payment_assembly'),
                          required_missing_invoice_count=result.get('required_missing_invoice_count'),
                          finance_system_write=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
