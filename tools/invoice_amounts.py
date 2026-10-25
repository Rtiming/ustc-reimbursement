"""价税合计的保守文字解析：完整小写、中文大写与净额加税额分别核对。"""
from decimal import Decimal
import re
import unicodedata

DIGITS = dict(zip('零壹贰叁肆伍陆柒捌玖', range(10)))
UNITS = {'拾': 10, '佰': 100, '仟': 1000}
LARGE = {'万': 10000, '亿': 100000000}


def uppercase_amount(value):
    value = re.sub(r'\s+', '', value).replace('人民币', '').replace('圆', '元')
    sign = -1 if value.startswith('负') else 1
    value = value.removeprefix('负')
    match = re.fullmatch(r'([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+)元([零壹贰叁肆伍陆柒捌玖角分整正]*)', value)
    if not match:
        return None
    whole, fraction = match.groups()
    total = section = number = 0
    previous_digit = False
    last_unit, last_large = 10000, 10**12
    for char in whole:
        if char in DIGITS:
            if previous_digit and number != 0:
                return None
            number = DIGITS[char]
            previous_digit = True
        elif char in UNITS:
            unit = UNITS[char]
            if unit >= last_unit or number == 0:
                return None
            section += number*unit
            number, previous_digit, last_unit = 0, False, unit
        else:
            unit = LARGE[char]
            if unit >= last_large or section + number == 0:
                return None
            total += (section+number)*unit
            section, number, previous_digit, last_unit, last_large = 0, 0, False, 10000, unit
    total += section + number
    fraction = fraction.removesuffix('整').removesuffix('正')
    if not fraction:
        cents = 0
    else:
        matched = re.fullmatch(r'(?:([零壹贰叁肆伍陆柒捌玖])角)?(?:零)?(?:([零壹贰叁肆伍陆柒捌玖])分)?', fraction)
        if not matched or not any(matched.groups()):
            return None
        jiao, fen = matched.groups()
        cents = DIGITS.get(jiao, 0)*10 + DIGITS.get(fen, 0)
    return f'{Decimal(sign*(total*100+cents))/100:.2f}'


def vat_total(text, net=None, tax=None):
    text = unicodedata.normalize('NFKC', text)
    upper_values = []
    for match in re.finditer(r'大\s*写\s*[):：]?\s*([人民币负零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆角分整正\s]+)', text):
        value = uppercase_amount(match.group(1))
        if value is not None:
            upper_values.append(value)
    upper = upper_values[0] if len(set(upper_values)) == 1 else None
    labels = list(re.finditer(r'小\s*写\s*[):：]?([^\n]*)', text))
    lower_values, lower_rows = [], []
    for label in labels:
        raw = label.group(1).strip()
        lower = None
        number = r'(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)'
        matched = re.fullmatch(r'[¥￥]?\s*'+number, raw)
        rest = text[label.end():].splitlines()
        second = rest[1].strip() if len(rest) > 1 else ''
        continuation = bool(re.fullmatch(r'[\d.,]+', second))
        if matched:
            digits = matched.group(1)
            # 整数/一位小数后还有纯数字行，可能是错位续行，不取前半段作小额。
            if not continuation or re.search(r'\.\d{2}$', digits):
                lower = f'{Decimal(digits.replace(",", "")):.2f}'
        else:
            # 只支持明确在小数点断行；不猜测散落数字的重新排列。
            first = re.fullmatch(r'[¥￥]?\s*(-?\d[\d,]*\.)', raw)
            if first and re.fullmatch(r'\d{1,2}', second):
                lower = f'{Decimal(first.group(1).replace(",", "")+second):.2f}'
        lower_rows.append({'raw':raw,'following_numeric_line':second if continuation else None,'amount':lower})
        if lower is not None:
            lower_values.append(lower)
    lower = lower_values[0] if len(set(lower_values)) == 1 and len(lower_values) == len(labels) else None
    raw = lower_rows[0]['raw'] if len(lower_rows) == 1 else None
    aggregate = f'{Decimal(net)+Decimal(tax):.2f}' if net is not None and tax is not None else None
    evidence = {'lowercase_raw': raw, 'lowercase_amount': lower, 'uppercase_amount': upper,
                'lowercase_candidates':lower_rows, 'net_plus_tax': aggregate, 'status': 'needs_review', 'basis': None}
    if len(set(upper_values)) > 1 or len(set(lower_values)) > 1:
        return None, evidence, ['TOTAL_AMOUNT_CONFLICT']
    if len(labels) > 1 and len(lower_values) != len(labels):
        return None, evidence, ['MULTIPLE_TOTAL_LABELS_NEEDS_REVIEW']
    known = [x for x in [lower, upper, aggregate] if x is not None]
    if lower is not None:
        if any(Decimal(x) != Decimal(lower) for x in known):
            return None, evidence, ['TOTAL_AMOUNT_CONFLICT']
        evidence.update(status='cross_checked' if len(known) > 1 else 'explicit_lowercase', basis='labeled_lowercase')
        return lower, evidence, []
    if upper is not None and aggregate is not None and Decimal(upper) == Decimal(aggregate):
        evidence.update(status='cross_checked', basis='uppercase_and_net_tax')
        return upper, evidence, []
    return None, evidence, ['TOTAL_AMOUNT_CONFLICT' if upper is not None and aggregate is not None else 'TOTAL_NEEDS_REVIEW']
