"""Verify saved corporate rows against a complete same-claim native preview."""
import hashlib
import re
from decimal import Decimal

from validate_config import validate_corporate_payments


def verify_corporate_fields(form, frame):
    payments = form.get('payments', [])
    validate_corporate_payments(payments, form['amount'])
    if any(p.get('remark', '') != '' for p in payments):
        raise ValueError('非空对公附言尚无独立预览证据')
    serial = form['serial']
    proof = frame.get('proof_inventory', {})
    if (frame.get('id') != 'iframepage_print'
            or proof.get('schema') != 'ustc-native-proof-inventory-v1'
            or proof.get('serial') != serial or proof.get('preview_serials') != [serial]
            or proof.get('preview_complete') is not True or proof.get('scan_complete') is not True
            or proof.get('truncated') is not False):
        raise ValueError('对公回读须完整同号原生预览')
    tables = frame.get('tables', [])
    headers = [(ti, ri) for ti, table in enumerate(tables) for ri, row in enumerate(table)
               if row == ['汇款', '户名', '账号', '开户行', '金额', '提示']]
    if len(headers) != 1:
        raise ValueError('原生汇款表头不唯一或结构未知')
    ti, hi = headers[0]
    table = tables[ti]
    rows = [(ri, row) for ri, row in enumerate(table) if ri > hi and len(row) == 5]
    if (len(rows) != len(payments) or any(row[-1] != '汇款信息完整' for _, row in rows)
            or any(row and row[-1] == '转卡信息完整' for t in tables for row in t)):
        raise ValueError('对公回读行数、完整性或支付方式不符')
    summaries = [row[0] for t in tables for row in t if len(row) == 1 and row[0].startswith('共填写')]
    expected_summary = f"共填写{len(payments)}条支付信息,小计 {Decimal(form['amount']):,.2f}元"
    if (summaries != [expected_summary] or table[-1] != [expected_summary]
            or len(table[hi + 1:]) != len(rows) + 1):
        raise ValueError('对公原生支付总额或条数不闭合')
    hashes = frame.get('numeric_cell_hashes', {})
    if (hashes.get('schema') != 'ustc-native-numeric-table-cell-hashes-v1'
            or hashes.get('source') != 'captured_same_frame_table_cells'
            or hashes.get('scan_complete') is not True or hashes.get('truncated') is not False):
        raise ValueError('对公账号缺少完整原生单元格SHA证据')
    matched = set()
    for payment in payments:
        digits = payment['bank_account'].replace(' ', '')
        expected_hash = hashlib.sha256(digits.encode()).hexdigest()
        candidates = []
        for ri, row in rows:
            bank = row[2]
            bank_ok = bank == payment['bank_name'] or (
                bank.startswith(payment['bank_name'] + '/')
                and re.fullmatch(r'[0-9]{12}', bank[len(payment['bank_name']) + 1:]))
            raw_account = row[1].replace(' ', '').replace('\u00a0', '')
            cell_hashes = [h for h in hashes.get('candidates', [])
                           if (h.get('table'), h.get('row'), h.get('cell')) == (ti, ri, 1)]
            if (row[0] == payment['name'] and bank_ok and raw_account == digits
                    and row[3] == f"{Decimal(payment['amount']):,.2f}"
                    and len(cell_hashes) == 1 and cell_hashes[0].get('sha256') == expected_hash
                    and cell_hashes[0].get('digits_length') == len(digits)):
                candidates.append(ri)
        if len(candidates) != 1 or candidates[0] in matched:
            raise ValueError('对公户名、完整账号SHA、开户行及金额未唯一核实')
        matched.add(candidates[0])
    return {'verified': True, 'payment_count': len(payments), 'amount': form['amount'],
            'account_hashes_verified': True, 'source': 'complete_saved_native_preview'}


def verify_corporate_readback(form, data):
    users = {p.get('expected_userid') for p in form.get('payments', [])}
    if (len(users) != 1 or data.get('identity', {}).get('userid') not in users
            or data.get('projid') != 'WF_YBX' or data.get('serial') != form['serial']
            or data.get('ok') is not True or data.get('visibleDialogs')):
        raise ValueError('对公独立回读账号、系统或单号不符')
    frames = [f for f in data.get('frames', []) if f.get('id') == 'iframepage_print']
    if len(frames) != 1:
        raise ValueError('对公独立预览不唯一')
    return verify_corporate_fields(form, frames[0])
