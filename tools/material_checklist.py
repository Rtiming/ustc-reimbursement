#!/usr/bin/env python3
"""依次输出打款人、报销材料、纸质材料清单；只读原件和财务参数。"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import material_coverage
import payment_evidence
from print_packet import digest, load_plan


def money(value):
    return f'{Decimal(value):.2f}'


def md(value):
    return str(value).replace('|', '\\|').replace('\n', '<br>')


def table(columns, rows):
    return ['| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join(['---'] * len(columns)) + ' |',
            *['| ' + ' | '.join(md(v) for v in row) + ' |' for row in rows], '']


def source_link(root, relative, label):
    return f'[{label}](<{root / relative}>)'


def write_csv(path, columns, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)


def payment_rows(plan, form, invoices, wc):
    """每张票只能对应一项支付，同人转卡/公务卡保留两项。"""
    bindings = plan['payment_bindings']
    if sorted(r['payment_index'] for r in bindings) != list(range(len(form['payments']))):
        raise ValueError('打款清单必须绑定全部支付项且仅一次')
    by_index = {r['payment_index']: r['invoice_ids'] for r in bindings}
    assigned = [id_ for r in bindings for id_ in r['invoice_ids']]
    if Counter(assigned) != Counter(invoices.keys()):
        raise ValueError('打款清单必须对应每张已核定发票且仅一次')
    rows = []
    modes = {'bank_transfer': '转卡', 'corporate_card': '公务卡还款'}
    for index, pay in enumerate(form['payments']):
        ids = by_index[index]
        bound = [invoices[id_] for id_ in ids]
        if any(r['payee'] != pay['name'] for r in bound):
            raise ValueError('支付项绑定了其他人的票据')
        if any(r['payment_mode'] != pay['mode'] for r in bound):
            raise ValueError('支付项混用了转卡和公务卡票据')
        total = sum((Decimal(r['amount']) for r in bound), Decimal(0))
        if total != Decimal(pay['amount']):
            raise ValueError('打款金额与对应发票合计不一致：' + pay['name'])
        committee = sum((wc.get(r['sha256'], Decimal(0)) for r in bound), Decimal(0))
        expenses = defaultdict(Decimal)
        for r in bound:
            expenses[r['expense_type']] += Decimal(r['amount'])
        rows.append([index + 1, pay['name'], pay['person_id'], modes[pay['mode']], len(bound),
                     money(total), money(committee), money(total - committee),
                     '；'.join(f'{key}{money(value)}' for key, value in expenses.items()), '、'.join(ids)])
    if sum((Decimal(r[5]) for r in rows), Decimal(0)) != Decimal(plan['amount']):
        raise ValueError('打款总额与本案不一致')
    return rows


def generate(plan_path, output, packet_manifest=None):
    plan, root = load_plan(plan_path)
    coverage = material_coverage.validate(plan, root)
    form = json.loads((root / plan['form_config']).read_text(encoding='utf-8'))
    original_list = json.loads((root / plan['invoice_manifest']).read_text(encoding='utf-8'))
    numbers = {r['sha256']: '、'.join(r.get('invoice_number_candidates', [])) for r in original_list['original_files']}
    correction = json.loads((root / plan['correction_config']).read_text(encoding='utf-8'))
    if correction['case_id'] != plan['case_id'] or str(correction['serial']) != str(plan['serial']) or Decimal(correction['amount']) != Decimal(plan['amount']):
        raise ValueError('拆分参数与清单的案件/单号/总额不一致')
    items = [r for s in plan['sections'] for r in s['items'] if r['kind'] == 'source']
    invoices = {r['id']: r for r in items if r.get('role') == 'invoice'}
    wc = {r['source_sha256']: Decimal(r['wc_amount']) for r in correction['people']}
    if len(wc) != len(correction['people']) or not set(wc).issubset({r['sha256'] for r in invoices.values()}):
        raise ValueError('团委拆分没有对应到本案唯一发票')
    funding = {r['code']: Decimal(r['amount']) for r in form['funding']}
    if funding != {r['code']: Decimal(r['amount']) for r in correction['funding']}:
        raise ValueError('清单拆分与保存的经费参数不一致')
    policy = correction['policy']
    if sum(wc.values()) != funding[policy['project']] or sum(wc.values()) > Decimal(policy['team_cap']):
        raise ValueError('团委拆分合计与经费额/团队限额不一致')
    personal = defaultdict(Decimal)
    invoices_by_sha = {r['sha256']: r for r in invoices.values()}
    for r in correction['people']:
        bound = invoices_by_sha[r['source_sha256']]
        if r['name'] != bound['payee'] or r['source_kind'] != bound.get('expense_kind', bound['expense_type']) or r['source_kind'] not in policy['allowed_kinds']:
            raise ValueError('团委拆分的人员或费用范围与发票不一致')
        personal[r['name']] += Decimal(r['wc_amount'])
    if any(amount > Decimal(policy['person_cap']) for amount in personal.values()):
        raise ValueError('团委个人金额超过本案限额')
    for invoice in invoices.values():
        if not Decimal(0) <= wc.get(invoice['sha256'], Decimal(0)) <= Decimal(invoice['amount']):
            raise ValueError('清单经费拆分超过票据金额')
    payments = payment_rows(plan, form, invoices, wc)

    pages = {}
    packet = None
    if packet_manifest:
        packet = json.loads(packet_manifest.read_text(encoding='utf-8'))
        if packet['plan_sha256'] != digest(plan_path) or packet['case_id'] != plan['case_id'] or str(packet['serial']) != str(plan['serial']):
            raise ValueError('电子包索引不是当前已核对计划')
        if digest(packet['pdf']) != packet['pdf_sha256']:
            raise ValueError('电子包PDF已改变，重新核验')
        if not packet.get('profile') or digest(packet['profile']) != packet['profile_sha256']:
            raise ValueError('电子包打印配置已改变，重新排版后更新清单页码')
        if {r['id'] for r in packet['materials']} != {r['id'] for r in items}:
            raise ValueError('电子包材料与清单不一致')
        if packet['invoice_count'] != len(invoices) or Decimal(packet['invoice_total']) != Decimal(plan['amount']):
            raise ValueError('电子包票数或总额与清单不一致')
        pages = {r['id']: r['output_page'] for r in packet['materials']}
        if any(not isinstance(page, int) or not 1 <= page <= packet['pages'] for page in pages.values()):
            raise ValueError('电子包索引页码超出实际范围')

    paper = plan.get('paper_materials', [])
    if len({r['id'] for r in paper}) != len(paper):
        raise ValueError('纸质清单编号重复')
    supports = defaultdict(list)
    paper_refs = defaultdict(list)
    for r in items:
        if r.get('role') != 'invoice':
            if not r.get('invoice_ids') or any(id_ not in invoices for id_ in r['invoice_ids']):
                raise ValueError('辅助凭证须对应已核定发票：' + r['id'])
            for id_ in r['invoice_ids']:
                supports[id_].append(r['id'])
    for r in paper:
        if r.get('print') is not False or any(id_ not in invoices for id_ in r['invoice_ids']):
            raise ValueError('纸质清单须明确不打印，并绑定本案费用')
        for id_ in r['invoice_ids']:
            paper_refs[id_].append(r['id'])

    report = json.loads((root / plan['coverage_manifest']).read_text(encoding='utf-8'))
    proof_audit = payment_evidence.audit_plan(plan, report)
    proof_by_invoice = {r['invoice_id']: r for r in proof_audit['invoices']} if proof_audit else {}
    originals = {(root / a['source'].split('!/')[0]).resolve() for a in report['assets']}
    targets = [plan['file_prefix'] + '_打款及材料清单.md', '01_打款人清单.csv', '02_报销材料明细.csv',
               '03_纸质材料清单.csv', '04_原始材料覆盖核对.csv', '05_付款凭证逐票核对.csv', '清单核对记录.json', '清单表格交付计划.json', '问题总结.md', '问题总结.html']
    if any((output / name).resolve() in originals for name in targets):
        raise ValueError('生成清单不得覆盖原始材料')
    output.mkdir(parents=True, exist_ok=True)
    lines = [f'# {plan["checklist_title"]}', '', '[先看问题总结与处理动作](问题总结.html) · [文字版](问题总结.md)', '', f'报销单：{plan["serial"]}；总额：{money(plan["amount"])}元。', '',
             '金额为报销申请金额，不代表已付款。纸质材料只列清单，不打印扫描件或留位页。', '',
             *[note + '\n' for note in plan.get('status_notes', [])],
             '## 一、打款人清单', '',
             f'{len(payments)}项支付，{len({r[1] for r in payments})}名收款人；同一人的转卡和公务卡还款分别列示。', '']
    columns = ['序号', '打款人', '学工号', '方式', '票数', '金额/元', '团委/元', '其他经费/元', '费用组成', '对应票据']
    lines += table(columns, payments)
    lines += [f'合计：{money(plan["amount"])}元；' + '；'.join(f'{code}：{money(amount)}元' for code, amount in funding.items()) + '。', '',
              *[note + '\n' for note in plan.get('payment_notes', [])],
              '## 二、报销材料清单及明细', '',
              f'{len(invoices)}张已核定发票合计{money(plan["amount"])}元，{len(items) - len(invoices)}页电子辅助凭证；辅助凭证不重复计入金额。', '']
    if packet:
        layout = '一张发票或一页材料占一页纸；' if packet.get('one_material_per_page') else ''
        ornaments = '无附加标头、页眉、页脚；' if not any(packet.get(key) for key in ['material_labels_enabled', 'headers_enabled', 'footers_enabled']) else ''
        orientations = '纸张横竖按材料自动选择；' if packet.get('auto_orientation') else ''
        lines += [f'电子附件共{packet["pages"]}页，A4单面；{layout}{orientations}{ornaments}下表页码对应最新打印版。纸质材料见第三部分。', '']
    rows = []
    by_payee = defaultdict(list)
    for r in items:
        by_payee[r['payee']].append(r)
    for name in dict.fromkeys(r[1] for r in payments):
        lines += [f'### {name}', '']
        display = []
        for r in by_payee[name]:
            is_invoice = r.get('role') == 'invoice'
            committee = wc.get(r['sha256'], Decimal(0)) if is_invoice else Decimal(0)
            refs = supports[r['id']] + paper_refs[r['id']] if is_invoice else r['invoice_ids']
            link = source_link(root, r['path'], r['label'])
            proof = proof_by_invoice.get(r['id'], {})
            proof_status = payment_evidence.status_label(proof, proof_audit['assembly_mode']) if proof else '—'
            display.append([r['id'], link, money(r['amount']) if is_invoice else '不计金额', '、'.join(refs) or '—', pages.get(r['id'], '未生成'), proof_status])
            rows.append([name, r['id'], '发票' if is_invoice else '电子辅助凭证', r['label'],
                         numbers.get(r['sha256'], '') if is_invoice else '', money(r['amount']) if is_invoice else '',
                         money(committee) if is_invoice else '', money(Decimal(r['amount']) - committee) if is_invoice else '',
                         '、'.join(refs), pages.get(r['id'], ''), str(root / r['path']), r.get('page', 0) + 1, r['sha256'],
                         proof_status, '、'.join(proof.get('proof_ids', [])), proof.get('matched_amount', ''), proof.get('requirement_basis', '')])
        lines += table(['编号', '材料/行程（点击查看原件）', '计入金额/元', '对应辅助/纸质材料或发票', '电子包页码', '支付凭证'], display)
    proof_rows = []
    if proof_audit:
        available = proof_audit['assembly_mode'] == 'attach_available'
        lines += ['### 付款凭证逐票核对', '', proof_audit['policy']['note'], '',
                  ('本次按“只附已有材料”整理：有截图的匹配后放到对应发票旁，没有的跳过，不留空页、不阻止出包。' if available else '本次按“完整材料检查”整理，通常需凭证的费用须覆盖完成。'), '',
                  f'付款凭证实际附入{proof_audit["payment_proof_pages"]}页，完全覆盖{proof_audit["matched_invoice_count"]}张票；' +
                  f'{proof_audit["required_missing_invoice_count"]}张通常需要凭证的票未覆盖，{proof_audit["optional_missing_invoice_count"]}张默认无需凭证的票未提供。要求核对与本次已有材料整理分别记录。', '',
                  '订单、结账单、聊天说明不作为实际付款凭证；已有凭证全部匹配附入，合并付款的凭证只打印一次。', '']
        display = []
        for r in proof_audit['invoices']:
            mids = [m for record in proof_audit['records'] if record['id'] in r['proof_ids'] for m in record['material_ids']]
            position = '、'.join(f'{m}／第{pages[m]}页' if m in pages else m for m in mids) or ('本次不附、不留空页' if available else '无凭证页')
            status = payment_evidence.status_label(r, proof_audit['assembly_mode'])
            proof_rows.append([r['payee'], r['invoice_id'], r['expense'], r['amount'], status,
                               r['matched_amount'], r['missing_amount'], '、'.join(r['proof_ids']), position,
                               r['requirement_basis'], proof_audit['assembly_mode']])
            display.append([r['payee'], r['invoice_id'], r['expense'], status, '、'.join(r['proof_ids']) or '—'])
        if available:
            attached = []
            for record in proof_audit['records']:
                position = '、'.join(f'{m}／第{pages[m]}页' if m in pages else m for m in record['material_ids'])
                attached.append([record['id'], record['payer'], record['paid_amount'], '、'.join(record['invoice_ids']), position])
            lines += table(['付款记录', '实际付款人', '实付/元', '对应票据', '附件位置'], attached) if attached else ['当前所查来源没有实际付款凭证，本次未加入付款截图页。逐票状态与例外见05_付款凭证逐票核对.csv。', '']
        else:
            lines += table(['打款人', '票据', '费用', '付款凭证状态', '对应付款记录'], display)
    lines += [*[note + '\n' for note in plan.get('material_notes', [])],
              '## 三、纸质材料清单', '',
              f'下列{len(paper)}项只登记，不打印纸票扫描件、照片或留位页。扫描件已找到不表示纸质原件已收齐；已确认的原件安排见每项状态。', '']
    paper_rows = []
    for index, r in enumerate(paper, 1):
        bound = [invoices[id_] for id_ in r['invoice_ids']]
        corresponding = '；'.join(f'{item["id"]} {item["label"]}' for item in bound) or '活动公共材料'
        source = '；'.join(f'{a["path"]} 第{a["page"] + 1}页' for a in r['source_assets']) or '未找到本地扫描件／已明确原单归档'
        insert = '、'.join(f'{id_}／电子包第{pages[id_]}页' if id_ in pages else id_ for id_ in r['invoice_ids']) or '适用签章／活动材料放票据前；跨单原件记录单号'
        paper_rows.append([index, r['id'], r['person'], r['label'], corresponding, r['status'], insert, source, '不打印'])
    lines += table(['序号', '编号', '人员/经办人', '纸质材料', '对应报销费用', '原件及版本安排', '整理位置'], [row[:7] for row in paper_rows])
    lines += ['### 待补或待取得', '', *[f'- {value}' for value in plan.get('open_items', [])], '',
              '### 核对范围', '',
              f'逐页核对本案计划明确列出的来源及嵌套压缩包：{coverage["source_occurrences"]}条来源记录（含重复归档），去重后{coverage["unique_assets"]}个材料页/文件，全部已有收录、纸质登记、替代或排除理由。实际来源以04_原始材料覆盖核对.csv为准；此项不代表待补材料已齐备。', '',
              '未列入本案核定票据的其他材料保留归档，不新增报销金额。原件未改动；生成本清单不会写入财务系统。', '']
    main_path = output / (plan['file_prefix'] + '_打款及材料清单.md')
    main_path.write_text('\n'.join(lines), encoding='utf-8')
    write_csv(output / '01_打款人清单.csv', columns, payments)
    write_csv(output / '02_报销材料明细.csv', ['打款人', '编号', '类别', '材料/行程', '发票号码候选', '计入金额/元', '团委/元', '其他经费/元', '对应材料', '电子包页码', '原件路径', '原件页码', '原件SHA256', '付款凭证状态', '付款记录编号', '已匹配付款金额/元', '付款凭证要求'], rows)
    write_csv(output / '05_付款凭证逐票核对.csv', ['打款人', '票据', '费用', '票面金额/元', '付款凭证状态', '已匹配金额/元', '未覆盖金额/元', '付款记录', '凭证整理位置', '付款凭证要求', '整理模式'], proof_rows)
    write_csv(output / '03_纸质材料清单.csv', ['序号', '编号', '人员/经办人', '纸质材料', '对应费用', '原件安排', '整理位置', '扫描件位置', '是否打印'], paper_rows)
    states = {'included':'电子包已收录', 'paper_list':'只列纸质清单', 'represented_by':'已有清晰版本/拆分票据', 'excluded':'排除并归档', 'container':'压缩包已展开'}
    detailed = []
    for asset in report['assets']:
        resolution = report['resolutions'][asset['asset_id']]
        refs = resolution.get('material_ids', []) + resolution.get('paper_ids', [])
        detailed.append([asset['source'], asset.get('page', -1) + 1 or '', asset['asset_id'], states[resolution['state']],
                         '、'.join(refs), '、'.join(str(pages[id_]) for id_ in refs if id_ in pages), resolution.get('reason', '已绑定实际电子材料页')])
    write_csv(output / '04_原始材料覆盖核对.csv', ['来源（压缩包内用!/分隔）', '来源页码', '材料SHA及页标识', '处理结果', '对应清单编号', '电子包页码', '处理理由'], detailed)
    receipt = dict(case_id=plan['case_id'], serial=plan['serial'], plan_sha256=digest(plan_path),
                   checklist=str(main_path.resolve()), payments=len(payments), payees=len(by_payee), invoices=len(invoices),
                   electronic_support_pages=len(items) - len(invoices), paper_materials=len(paper), amount=plan['amount'],
                   coverage=coverage, payment_evidence=proof_audit, open_items=plan.get('open_items', []), pdf_pages=packet['pages'] if packet else None, finance_system_write=False)
    from checklist_delivery import register_delivery, ISSUE_COLUMNS
    issues = [dict(zip(ISSUE_COLUMNS, [f'待办{i}', '待核', '整案', None, item,
              '来源：正式清单待办', '沿原待办核实并记录处理结果。'])) for i, item in enumerate(plan.get('open_items', []), 1)]
    receipt['table_delivery'] = register_delivery(output, 'formal', plan['case_id'], plan['checklist_title'], extra_issues=issues)
    (output / '清单核对记录.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--packet-manifest', type=Path)
    args = parser.parse_args()
    receipt = generate(args.plan.resolve(), args.output_dir.resolve(), args.packet_manifest)
    summary = {k: v for k, v in receipt.items() if k != 'payment_evidence'}
    if receipt['payment_evidence']:
        summary['payment_materials_ready'] = receipt['payment_evidence']['ready_for_payment_material_delivery']
        summary['payment_missing_invoice_count'] = receipt['payment_evidence']['missing_invoice_count']
        summary['payment_assembly_mode'] = receipt['payment_evidence']['assembly_mode']
        summary['payment_assembly_ready'] = receipt['payment_evidence']['ready_for_payment_assembly']
        summary['payment_required_missing_invoice_count'] = receipt['payment_evidence']['required_missing_invoice_count']
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
