"""票据号码域：医疗财政票必须同时绑定代码，不能只凭短号码匹配。"""
import re


def source_identity(source, allow_medical=False):
    candidates = source.get('invoice_number_candidates')
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], str):
        raise ValueError('每张原件须有唯一字符串票号')
    number = candidates[0]
    if re.fullmatch(r'[0-9]{20}', number):
        return ('vat', '', number)
    code = source.get('invoice_code')
    if allow_medical and re.fullmatch(r'[0-9]{10}', number) and isinstance(code, str) and re.fullmatch(r'[0-9]{8}', code):
        return ('medical', code, number)
    raise ValueError('须有20位票号；医疗财政票另需8位代码及10位号码')


def source_matches(row, source):
    kind, code, number = source_identity(source, allow_medical=True)
    if kind == 'medical':
        return (row.get('t.tk_type') == 'MEDICAL' and row.get('t.tk_code') == code
                and row.get('t.tk_code_abst') == '医/' + code + '/' + number)
    from finance_invoice_reconcile import invoice_number
    return invoice_number(row)[0] == number
