"""Evidence-preserving, text-only invoice face candidates; no IO or accounting.

The caller retains document/page identity and applies this function on a compute
host. Reading order is not a PDF rectangle: candidates never claim complete
remarks or silently replace authoritative audited fields.
"""
from __future__ import annotations

import re

PARSER_VERSION = 'invoice-face-text-v1.1'
_SPACE = r'[ \t\u00a0]*'

# Explicit labels only. Unqualified names/accounts remain unqualified; in
# particular a receipt's 收款人 does not become the reimbursement recipient.
_LABEL_GROUPS = {
    'invoice_number': ('发票号码', '票据号码'),
    'invoice_code': ('发票代码', '票据代码'),
    'issue_date': ('开票日期',),
    'verification_code': ('校验码', '查验码'),
    'issuer_person': ('开票人',),
    'reviewer_person': ('复核人', '复核'),
    'cashier_person': ('收款人', '收款员'),
    'recipient_organization_on_invoice': ('收款单位（章）', '收款单位(章)', '收款单位'),
    'payer_name': ('交款人', '付款人'),
    'payer_identity_code': ('交款人统一社会信用代码',),
    'party_name_unscoped': ('名称',),
    'tax_id_unscoped': ('统一社会信用代码/纳税人识别号', '纳税人识别号', '统一社会信用代码'),
    'buyer_bank_name': ('购方开户银行', '购买方开户银行', '购方开户行'),
    'seller_bank_name': ('销方开户银行', '销售方开户银行', '销方开户行'),
    'bank_name_unscoped': ('开户银行', '开户行'),
    'buyer_bank_account': ('购方银行账号', '购买方银行账号'),
    'seller_bank_account': ('销方银行账号', '销售方银行账号'),
    'bank_account_unscoped': ('银行账号', '银行帐号', '账号', '帐号'),
    'buyer_address': ('购方地址', '购买方地址'),
    'seller_address': ('销方地址', '销售方地址'),
    'address_unscoped': ('地址',),
    'buyer_phone': ('购方电话', '购买方电话'),
    'seller_phone': ('销方电话', '销售方电话'),
    'phone_unscoped': ('电话', '联系电话'),
    'order_number': ('订单号', '订单编号', '订单号码'),
    'contract_number': ('合同编号', '合同号'),
    'business_serial_number': ('业务流水号',),
    'ticket_number': ('电子客票号码', '客票号码', '客票号', '票号'),
    'traveler_name': ('出行人', '行程人'),
    'passenger_name': ('旅客姓名', '乘机人', '旅客'),
    'identity_number': ('有效身份证件号', '身份证件号', '证件号码', '证件号'),
    'travel_date': ('出行日期', '乘车日期'),
    'departure': ('出发地',),
    'destination': ('到达地',),
    'travel_class': ('等级', '舱位'),
    'transport_type': ('交通工具类型',),
    'flight_number': ('航班号', '航班'),
    'carrier': ('承运人', '承运单位'),
    'tax_rate': ('税率/征收率', '税率', '征收率'),
    'specification': ('规格型号',),
    'unit': ('单位',),
    'quantity': ('数量/单位', '数量'),
    'unit_price': ('单价',),
    'tax_amount': ('税额',),
    'total_amount_uppercase': ('价税合计（大写）', '价税合计(大写)', '金额合计（大写）', '金额合计(大写)'),
    'amount_unscoped': ('金额',),
    'clinic_number': ('门诊号', '住院号'),
    'visit_date': ('就诊日期',),
    'visit_information': ('就诊信息',),
    'medical_institution_type': ('医疗机构类型',),
    'insurance_type': ('医保类型',),
    'insurance_number': ('医保编号',),
    'sex_on_receipt': ('性别',),
    'insurance_fund_paid': ('医保统筹基金支付',),
    'other_paid': ('其他支付',),
    'personal_account_paid': ('个人账户支付',),
    'personal_cash_paid': ('个人现金支付',),
    'personal_out_of_pocket': ('个人自付',),
    'personal_self_paid': ('个人自费',),
    'payment_method': ('支付方式', '付款方式'),
}
_LABEL_KEYS = {label: key for key, labels in _LABEL_GROUPS.items() for label in labels}
_LABEL_PATTERN = '|'.join(_SPACE.join(re.escape(c) for c in label)
                          for label in sorted(_LABEL_KEYS, key=len, reverse=True))
_COLON_LABEL = re.compile(r'(?P<label>' + _LABEL_PATTERN + r')' + _SPACE + r'[:：]')
_STANDALONE_LABEL = re.compile(r'^(?P<label>' + _LABEL_PATTERN + r')' + _SPACE + r'[:：]?' + _SPACE + r'$')
_REMARK_LABEL = re.compile(r'备[ \t\r\n\u00a0]*注(?:[ \t\u00a0]*[:：])?')
_SECTION = re.compile(r'开\s*票\s*人\s*[:：]|复\s*核\s*人\s*[:：]|收\s*款\s*人\s*[:：]|'
                      r'收款单位|财政电子票据查验网站|项目名称|价税合计|金额合计|'
                      r'发票号码|票据号码|开票日期|电子发票|购\s*买\s*方|销\s*售\s*方')
_SUPPLEMENT = re.compile(r'购方开户银行|购买方开户银行|销方开户银行|销售方开户银行|'
                         r'购方地址|购买方地址|销方地址|销售方地址|订单号|订单编号|合同编号')
_EXPLICIT_BLANK = frozenset(('空白', '(空白)', '（空白）', '[空白]', '未填写'))


def _label_key(label):
    return _LABEL_KEYS[re.sub(r'\s+', '', label)]


def _table_header(line):
    compact = re.sub(r'\s+', '', line)
    return '备注' in compact and '项目名称' in compact and any(
        word in compact for word in ('金额', '数量', '单价', '规格型号'))


def extract_face(text, *, invoice_type=None):
    """Return original text, source spans and conservative face-field candidates.

    Offsets are Python Unicode codepoint indices, end-exclusive. Line numbers are
    one-based. Empty labels mean unknown; only explicit blank markers mean blank.
    No amounts are converted, identities matched, or accounting links created.
    """
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    if invoice_type is not None and not isinstance(invoice_type, str):
        raise TypeError('invoice_type must be a string or None')
    lines = []
    offset = 0
    for number, original in enumerate(text.splitlines(keepends=True), 1):
        content = original.rstrip('\r\n')
        lines.append({'number': number, 'text': content, 'start': offset, 'end': offset + len(content)})
        offset += len(original)
    result = {
        'parser_version': PARSER_VERSION, 'invoice_type': invoice_type,
        'raw_text': text, 'lines': lines, 'remarks': [], 'fields': [],
        'unparsed_lines': [], 'ignored_labels': [],
        'coordinate_system': 'unicode_codepoint_offsets_end_exclusive; 1-based lines',
        'limitations': [
            '只有文字流，无 PDF 坐标或备注框边界；不能证明完整备注或版面归属。',
            '候选字段不覆盖已审核字段；未限定的名称、银行账号及电话不推定所属购销方。',
            '竖排标签、交错双栏及移位值保留候选或未知，不凭姓名、金额自动建立关系。',
            '票面收款人、交款人和银行信息不等同本次报销的确认收款人。',
        ],
    }
    used_lines = set()

    def span(start, end):
        touched = [r['number'] for r in lines if r['start'] < end and r['end'] >= start]
        if not touched:
            touched = [r['number'] for r in lines if r['start'] <= start <= r['end']]
        return {'start': start, 'end': end, 'line_start': min(touched) if touched else None,
                'line_end': max(touched) if touched else None, 'raw_text': text[start:end]}

    def mark(record):
        if record['line_start'] is not None:
            used_lines.update(range(record['line_start'], record['line_end'] + 1))

    def value_state(value):
        value = value.strip(' \t\r\n\u00a0;；')
        if not value:
            return None, 'unknown'
        if value in _EXPLICIT_BLANK:
            return '', 'explicit_blank'
        return value, 'value_candidate'

    # Same-line explicit labelled values. Multiple column labels are segmented
    # without carrying a value across an intervening label.
    for index, line in enumerate(lines):
        matches = list(_COLON_LABEL.finditer(line['text']))
        if not matches:
            match = _STANDALONE_LABEL.fullmatch(line['text'].strip())
            if match:
                left = len(line['text']) - len(line['text'].lstrip())
                rec = {**span(line['start'] + left, line['end']), 'key': _label_key(match['label']),
                       'label': match['label'], 'value': None, 'status': 'unknown',
                       'method': 'standalone_label_no_value', 'complete': False}
                result['fields'].append(rec)
                mark(rec)
            continue
        for pos, match in enumerate(matches):
            end = matches[pos + 1].start() if pos + 1 < len(matches) else len(line['text'])
            value, status = value_state(line['text'][match.end():end])
            rec = {**span(line['start'] + match.start(), line['start'] + end),
                   'key': _label_key(match['label']), 'label': match['label'], 'value': value,
                   'status': status, 'method': 'same_line_explicit_label', 'complete': False}
            result['fields'].append(rec)
            mark(rec)
            # A single label followed by one isolated value has a useful but
            # explicitly uncertain continuation. Never assign trailing values
            # from a multi-label row (e.g. 复核人 / 收款人) to either person.
            if value is None and len(matches) == 1 and index + 1 < len(lines):
                following = lines[index + 1]
                candidate = following['text'].strip()
                if candidate and len(candidate) <= 100 and not re.search(r'[:：*]', candidate) and not _STANDALONE_LABEL.fullmatch(candidate) and not _SECTION.search(candidate):
                    val, state = value_state(candidate)
                    continuation = {**span(line['start'] + match.start(), following['end']),
                                    'key': rec['key'], 'label': rec['label'], 'value': val,
                                    'status': 'following_line_candidate' if state != 'explicit_blank' else 'explicit_blank_candidate',
                                    'method': 'adjacent_text_not_coordinate_verified', 'complete': False}
                    result['fields'].append(continuation)
                    mark(continuation)
        # Tax-rate occurrences are searchable, but stay unassigned to a row.
    for line in lines:
        for match in re.finditer(r'(?<![\d.])\d+(?:\.\d+)?%', line['text']):
            rec = {**span(line['start'] + match.start(), line['start'] + match.end()),
                   'key': 'tax_rate', 'label': None, 'value': match.group(0),
                   'status': 'unassigned_numeric_candidate', 'method': 'literal_percent', 'complete': False}
            result['fields'].append(rec)
            mark(rec)

    # Label-anchored remarks may span lines. Section boundaries only constrain
    # candidates; they do not prove the actual PDF rectangle is complete.
    remark_matches = []
    for candidate in _REMARK_LABEL.finditer(text):
        candidate_span = span(candidate.start(), candidate.end())
        line_index = candidate_span['line_start'] - 1
        line = lines[line_index]
        prefix = text[line['start']:candidate.start()]
        header_context = '\n'.join(r['text'] for r in lines[max(0, line_index - 3):line_index + 1])
        explicit_colon = candidate.group(0).rstrip().endswith((':', '：'))
        after = text[candidate.end():candidate.end() + 1]
        label_boundary = explicit_colon or not after or after.isspace()
        # Ordinary mentions inside a sentence or inside the remark value are
        # not new labels, even when that word is followed by a colon. Retain
        # actual item-table headings here so their exclusion is auditable.
        if (_table_header(line['text']) or _table_header(header_context)
                or (not prefix.strip() and label_boundary)
                or (explicit_colon and re.search(r'[|;；]\s*$', prefix))):
            remark_matches.append(candidate)
    valid_labels = []
    for match in remark_matches:
        label_span = span(match.start(), match.end())
        line = lines[label_span['line_start'] - 1] if label_span['line_start'] else None
        line_index = label_span['line_start'] - 1 if label_span['line_start'] else 0
        header_context = '\n'.join(r['text'] for r in lines[max(0, line_index - 3):line_index + 1])
        if line and (_table_header(line['text']) or _table_header(header_context)):
            result['ignored_labels'].append({**label_span, 'reason': 'item_table_column_heading'})
            continue
        valid_labels.append(label_span)
        section = _SECTION.search(text, match.end())
        following_label = next((m for m in remark_matches if m.start() >= match.end()), None)
        end = min([len(text)] + ([section.start()] if section else []) +
                  ([following_label.start()] if following_label else []))
        value, state = value_state(text[match.end():end])
        # Single-character 购/销 prefixes of vertical headings are not remarks.
        if value and re.match(r'^[购销]\s*\n', value):
            end, value, state = match.end(), None, 'unknown'
        rec = {**span(match.start(), end), 'value': value,
               'status': 'multiline_candidate' if state == 'value_candidate' else
                         'explicit_blank_candidate' if state == 'explicit_blank' else 'label_only_layout_unknown',
               'complete': False, 'method': 'label_to_next_known_section',
               'label_span': label_span, 'value_start': match.end(), 'value_end': end}
        result['remarks'].append(rec)
        mark(rec)

    # Some native PDFs place 备/注 near the beginning of their text stream while
    # the actual supplemental bank/address block is emitted after the total.
    # Preserve that block as a separate *possible* remark with its own evidence.
    if valid_labels:
        index = 0
        while index < len(lines):
            line = lines[index]
            if not _SUPPLEMENT.search(line['text']):
                index += 1
                continue
            last = index
            while last + 1 < len(lines):
                next_text = lines[last + 1]['text']
                if _SUPPLEMENT.search(next_text) or re.match(r'^\s*(?:银行账号|银行帐号|电话|开户银行)\s*[:：]', next_text):
                    last += 1
                else:
                    break
            start, end = line['start'], lines[last]['end']
            contains_remark_label = any(start <= label['start'] < end for label in valid_labels)
            if not contains_remark_label and not any(r['start'] <= start and r['end'] >= end and r['value'] is not None for r in result['remarks']):
                rec = {**span(start, end), 'value': text[start:end].strip(),
                       'status': 'detached_supplement_candidate', 'complete': False,
                       'method': 'supplement_labels_outside_remark_reading_order',
                       'possible_label_spans': valid_labels.copy(), 'association_confirmed': False}
                result['remarks'].append(rec)
                mark(rec)
            index = last + 1
    result['unparsed_lines'] = [r['number'] for r in lines if r['text'].strip() and r['number'] not in used_lines]
    return result
