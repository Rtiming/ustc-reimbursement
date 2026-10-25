"""Pure, standard-library adapter for the legacy reimbursement evidence bundle.

No filesystem, network or browser access occurs here. The caller loads named JSON
inputs and runs this adapter on the authorised compute host. Source snapshots are
retained verbatim; normalised values are an index, not replacements for evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from invoice_face_fields import extract_face

REQUIRED_INPUTS = (
    'receivables', 'review', 'workspace', 'field_audit', 'electronic_inventory',
    'tencent_audit', 'medical_audit', 'payee_candidates', 'paper_links', 'case',
)
OPTIONAL_INPUTS = ('finance_audit', 'paper_ocr', 'ocr_results')
TABLES = ('persons', 'expenses', 'invoices', 'invoice_items', 'documents',
          'expense_invoices', 'expense_documents', 'invoice_documents',
          'finance_observations', 'issues', 'source_records')
_SHA = re.compile(r'^[0-9a-fA-F]{64}$')
_STUDENT = re.compile(r'(?<![A-Z0-9])(?:PB|SA|SC|BA)\d{6,12}(?!\d)')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _id(kind, *values):
    return kind + ':' + hashlib.sha256(_json(values).encode('utf-8')).hexdigest()[:24]


def _unique_append(rows, value):
    if value not in rows:
        rows.append(copy.deepcopy(value))


def _money(value):
    """Exact decimal yuan to integer fen; missing is never zero or rounded."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('boolean is not a monetary value')
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid monetary value') from exc
    if not parsed.is_finite() or parsed * 100 != (parsed * 100).to_integral_value():
        raise ValueError('money must be finite and exact to one fen')
    return int(parsed * 100)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text if re.fullmatch(r'\d{6,30}', text) else None



def _native_item_rows(text):
    """Extract only local item/rate/tax sequences, retaining exact snippets.

    Header order is required for quantity/price; amounts adjacent to a percent
    column are accepted only from a contiguous numeric sequence. No OCR totals
    are distributed among items and high-precision unit prices are not rounded.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.search(r'\*[^*\n]{1,50}\*[^\s*\n]{1,100}', line)
        if not match:
            continue
        following = []
        for candidate in lines[index + 1:index + 13]:
            if '*' in candidate or re.search(r'合\s*计|价税合计', candidate):
                break
            following.append(candidate)
        tokens = (line[match.end():] + ' ' + ' '.join(following)).split()
        rate_index = next((i for i, t in enumerate(tokens) if re.fullmatch(r'\d+(?:\.\d+)?%', t)), None)
        result = {'description': match.group(0), 'amount_fen': None, 'tax_fen': None,
                  'quantity': None, 'unit_price_fen': None, 'raw_text': '\n'.join([line, *following]),
                  'method': 'conservative_native_text_regex', 'tax_rate': None}
        numeric = re.compile(r'-?\d+(?:\.\d+)?')
        if rate_index is not None and rate_index > 0 and rate_index + 1 < len(tokens):
            prior = []
            for token in reversed(tokens[:rate_index]):
                if not numeric.fullmatch(token):
                    break
                prior.insert(0, token)
            if len(prior) in (1, 3) and numeric.fullmatch(tokens[rate_index + 1]):
                try:
                    result['amount_fen'] = _money(prior[-1])
                    result['tax_fen'] = _money(tokens[rate_index + 1])
                except ValueError:
                    result['amount_fen'] = result['tax_fen'] = None
                else:
                    result['tax_rate'] = tokens[rate_index]
                    result['method'] = 'native_item_adjacent_rate_columns'
                    result['raw_text'] = '\n'.join([line, *following])
                    # The *last* header is the closest applicable table header.
                    prefix = re.sub(r'\s+', '', '\n'.join(lines[:index]))
                    header = prefix.rsplit('项目名称', 1)[-1]
                    quantity_pos, price_pos = header.rfind('数量'), header.rfind('单价')
                    if len(prior) == 3 and quantity_pos >= 0 and price_pos >= 0:
                        q, price = (prior[0], prior[1]) if quantity_pos < price_pos else (prior[1], prior[0])
                        result['quantity'] = q
                        try:
                            result['unit_price_fen'] = _money(price)
                        except ValueError:
                            pass
        yield result


def build_legacy_ledger(inputs: dict[str, dict], case_id: str) -> dict:
    """Normalise explicitly supplied legacy inputs without calculating a claim.

    Required keys are REQUIRED_INPUTS; finance_audit is optional. Expenses come
    only from receivables.records. Candidate joins never become invoice links.
    """
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError('case_id must be a nonempty string')
    if not isinstance(inputs, dict):
        raise ValueError('inputs must be a mapping')
    for key in REQUIRED_INPUTS:
        if key not in inputs or not isinstance(inputs[key], dict):
            raise ValueError('missing or invalid input: ' + key)
    for key, value in inputs.items():
        if key not in REQUIRED_INPUTS + OPTIONAL_INPUTS or not isinstance(value, dict):
            raise ValueError('unsupported or invalid input: ' + str(key))
        if value.get('case_id') not in (None, '', case_id):
            raise ValueError('input case_id mismatch: ' + key)
    if not isinstance(inputs['receivables'].get('records'), list):
        raise ValueError('receivables.records must be a list')

    out = {'schema_version': 1, 'case_id': case_id, **{t: [] for t in TABLES}}
    out['provenance'] = {
        'adapter': 'legacy-v1', 'input_keys': sorted(inputs),
        'accounting_scope': 'receivables.records; no filtering by invoice completeness',
        'financial_system_write': False, 'live_finance_refresh': False,
        'source_snapshots_preserved': True,
        'invoice_item_extraction': 'conservative; absent numeric fields remain unknown',
    }
    source_ids = {key: _id('src', case_id, key) for key in inputs}
    for key in sorted(inputs):
        out['source_records'].append({'id': source_ids[key], 'input_key': key,
                                      'raw': copy.deepcopy(inputs[key])})

    def ref(key, pointer):
        return {'source_record_id': source_ids[key], 'input_key': key, 'pointer': pointer}

    def provenance(row, origin):
        _unique_append(row.setdefault('source_refs', []), origin)
        _unique_append(row.setdefault('source_record_ids', []), origin['source_record_id'])

    def issue(code, message, origin=None, **extra):
        row = {'id': _id('issue', code, message, origin, extra), 'code': code,
               'message': message, 'status': 'pending', **copy.deepcopy(extra)}
        if origin:
            provenance(row, origin)
        if row['id'] not in issue_ids:
            issue_ids.add(row['id'])
            out['issues'].append(row)
        return row['id']

    issue_ids = set()
    persons = {}
    expenses = {}
    invoice_map = {}
    number_map = {}
    docs = {}
    asset_map = {}
    path_map = {}
    tencent_files = {}
    links = {t: {} for t in ('expense_invoices', 'expense_documents', 'invoice_documents')}
    original_expense_map = {}
    review_number_map = {}

    def person(name, origin, raw=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('expense/person has no material owner name')
        name = name.strip()
        if name not in persons:
            row = {'id': _id('person', case_id, name), 'name': name,
                   'identity_candidates': [], 'payee_name': None, 'payee_student_id': None,
                   'bank_name': None, 'bank_account': None, 'confirmation_status': 'unconfirmed'}
            persons[name] = row
            out['persons'].append(row)
        row = persons[name]
        provenance(row, origin)
        if raw:
            for field in ('student_ids', 'student_id', 'student_id_from_ledger'):
                value = raw.get(field)
                if value:
                    found = _STUDENT.findall(str(value))
                    for sid in found:
                        _unique_append(row['identity_candidates'], {
                            'student_id': sid, 'status': 'candidate', 'raw': value,
                            'field': field, 'source_ref': origin})
                    if not found:
                        _unique_append(row['identity_candidates'], {
                            'student_id': None, 'status': 'unparsed', 'raw': value,
                            'field': field, 'source_ref': origin})
        return row['id']

    def document(sha, origin, raw=None, path=None, kind=None, page=None, text=None,
                 role=None, filename=None):
        valid_sha = str(sha).lower() if isinstance(sha, str) and _SHA.fullmatch(sha) else None
        # An unhashed occurrence stays a source identity even if a known file
        # has the same path: names/paths do not prove identical bytes.
        did = ('sha256:' + valid_sha) if valid_sha else _id('source-document', origin, path)
        if did not in docs:
            row = {'id': did, 'sha256': valid_sha, 'identity_basis': 'content_sha256' if valid_sha else 'source_reference',
                   'kind': kind, 'references': [], 'pages': [], 'roles': [], 'raw_fields': []}
            docs[did] = row
            out['documents'].append(row)
            if not valid_sha:
                issue('document_sha_missing', '材料缺少内容 SHA；仅保留稳定来源身份，未推定与其他文件相同。',
                      origin, document_id=did)
        row = docs[did]
        provenance(row, origin)
        if path:
            path_map.setdefault(path, set()).add(did)
        _unique_append(row['references'], {'path': path, 'original_filename': filename,
                                          'source_ref': origin})
        if role:
            _unique_append(row['roles'], role)
        if raw is not None:
            _unique_append(row['raw_fields'], {'source_ref': origin, 'fields': raw})
        if page is not None:
            _unique_append(row['pages'], {'page': page, 'native_text': text,
                                         'role': role, 'source_ref': origin})
        return did

    def add_link(table, *, expense_id=None, invoice_id=None, document_id=None,
                 origin, relation='exact', page=None, role=None):
        if expense_id is not None and expense_id not in expenses:
            return
        values = {'expense_id': expense_id, 'invoice_id': invoice_id, 'document_id': document_id}
        values = {k: v for k, v in values.items() if v is not None}
        # Each expense/version pair is unique. Document links retain page/role.
        identity = values if table == 'expense_invoices' else {**values, 'page': page, 'role': role}
        lid = _id(table, identity)
        if lid not in links[table]:
            row = {'id': lid, **values, 'relation': relation}
            if table != 'expense_invoices':
                row.update(page=page, role=role)
            else:
                row['relations'] = []
            links[table][lid] = row
            out[table].append(row)
        row = links[table][lid]
        if table == 'expense_invoices':
            _unique_append(row['relations'], relation)
        provenance(row, origin)

    def known_money(value, origin, field, iid=None):
        try:
            return _money(value)
        except ValueError:
            issue('invoice_money_unparsed', '票面金额字段未能精确转换，原值已保留。', origin,
                  invoice_id=iid, field=field, raw_value=value)
            return None

    def invoice(number, origin, raw, *, code=None, kind=None, values=None, authoritative=False):
        number = _number(number)
        if not number:
            return None
        code = str(code).strip() if code not in (None, '') else None
        existing = number_map.get(number, [])
        # Explicit medical code is part of the identity. An untyped observation
        # can join a number only if there is exactly one already-known identity.
        if kind is None and code is None and len(existing) > 1:
            issue('ambiguous_invoice_identity', '同票号存在多个票种或代码，未选择其中一个。', origin,
                  invoice_number=number, candidate_invoice_ids=existing)
            return None
        if kind is None and code is None and len(existing) == 1:
            iid = existing[0]
        else:
            invoice_kind = kind or 'unclassified'
            iid = 'invoice:' + invoice_kind + ':' + (code or '-') + ':' + number
        if iid not in invoice_map:
            row = {'id': iid, 'invoice_number': number, 'invoice_code': code,
                   'invoice_type': kind or 'unclassified', 'buyer_name': None, 'buyer_tax_id': None,
                   'seller_name': None, 'seller_tax_id': None, 'issue_date': None,
                   'amount_fen': None, 'tax_fen': None, 'net_fen': None, 'currency': 'CNY',
                   'raw_fields': [], 'field_sources': {}, 'version_status': [],
                   'content': [], 'remarks': [], 'header_status': 'not_audited',
                   'header_audits': [], 'item_extraction_status': 'not_structured'}
            invoice_map[iid] = row
            number_map.setdefault(number, []).append(iid)
            out['invoices'].append(row)
        row = invoice_map[iid]
        provenance(row, origin)
        _unique_append(row['raw_fields'], {'source_ref': origin, 'fields': raw})
        for field, value in (values or {}).items():
            if value is None or value == '':
                continue
            if field.endswith('_fen'):
                value = known_money(value, origin, field, iid)
                if value is None:
                    continue
            previous = row.get(field)
            if previous is None:
                row[field] = copy.deepcopy(value)
                row['field_sources'][field] = {'source_ref': origin, 'authoritative': authoritative}
            elif previous != value:
                prior = row['field_sources'].get(field, {})
                issue('invoice_field_conflict', '同一票据字段在来源之间不一致，保留全部原值。', origin,
                      invoice_id=iid, field=field, selected_value=previous, competing_value=value)
                if authoritative and not prior.get('authoritative'):
                    row[field] = copy.deepcopy(value)
                    row['field_sources'][field] = {'source_ref': origin, 'authoritative': True}
        return iid

    def header_audit(iid, raw, origin, fallback=None):
        if not iid:
            return
        validation = raw.get('validation') or {}
        title = validation.get('title_matches_expected')
        tax = validation.get('tax_id_matches_expected')
        if title is False or tax is False:
            status = ('buyer_title_and_tax_id_mismatch' if title is False and tax is False else
                      'buyer_title_mismatch' if title is False else 'buyer_tax_id_mismatch')
            priority = 3
        elif title is True and tax is True:
            status, priority = 'buyer_fields_match', 2
        elif title is not None or tax is not None:
            status, priority = 'buyer_fields_incomplete', 2
        else:
            status = {'购方字段匹配': 'buyer_fields_match', 'reported_fields_match': 'buyer_fields_match',
                      '购方税号不符': 'buyer_tax_id_mismatch', '购方税号不匹配': 'buyer_tax_id_mismatch',
                      'buyer_tax_id_mismatch': 'buyer_tax_id_mismatch'}.get(fallback)
            priority = 1
            if not status and fallback:
                status = 'reported: ' + str(fallback)
        if not status:
            return
        row = invoice_map[iid]
        _unique_append(row['header_audits'], {'status': status, 'validation': validation,
                                            'reported_status': fallback, 'source_ref': origin,
                                            'independent_of_authentication': True})
        if priority > row.get('header_audit_priority', 0):
            row['header_status'], row['header_audit_priority'] = status, priority

    def field_values(row):
        return {'buyer_name': row.get('buyer_title'), 'buyer_tax_id': row.get('buyer_tax_id'),
                'seller_name': row.get('seller_title'), 'seller_tax_id': row.get('seller_tax_id'),
                'issue_date': row.get('issue_date'), 'amount_fen': row.get('total_amount'),
                'net_fen': row.get('net_amount'), 'tax_fen': row.get('tax_amount')}

    def add_fields(row, origin, raw=None):
        numbers = [_number(x) for x in row.get('invoice_number_candidates', [])]
        numbers = list(dict.fromkeys(x for x in numbers if x))
        if len(numbers) != 1:
            if numbers:
                issue('invoice_number_candidates', '票面有多个候选号码，未据此建立确定票据关系。', origin,
                      invoice_numbers=numbers)
            return None
        # Medical invoices are registered first with their audited kind/code.
        kind = row.get('kind')
        if kind not in ('vat', 'aviation_vat'):
            kind = None
        if len(number_map.get(numbers[0], [])) == 1:
            kind = None
        iid = invoice(numbers[0], origin, raw or row, kind=kind,
                      values=field_values(row), authoritative=True)
        header_audit(iid, row, origin)
        return iid

    # The receivable ledger is the sole accounting scope. Missing evidence does
    # not remove an amount; invoice-version totals are never summed here.
    for i, raw in enumerate(inputs['receivables']['records']):
        origin = ref('receivables', f'/records/{i}')
        if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not raw['id']:
            raise ValueError('receivable record must have a string id')
        if raw['id'] in expenses:
            raise ValueError('duplicate receivable id: ' + raw['id'])
        row = {'id': raw['id'], 'person_id': person(raw.get('owner'), origin, raw),
               'category': raw.get('category'), 'description': raw.get('purpose'),
               'amount_fen': _money(raw.get('amount')), 'currency': 'CNY',
               'accounting_included': True, 'raw_fields': copy.deepcopy(raw), 'issues': []}
        provenance(row, origin)
        expenses[row['id']] = row
        out['expenses'].append(row)

    for key, field in (('receivables', 'rows'), ('review', 'persons'), ('review', 'medical_people'),
                       ('review', 'active_ledger'), ('review', 'medical_rows'),
                       ('payee_candidates', 'persons'), ('medical_audit', 'people')):
        for i, raw in enumerate(inputs[key].get(field, [])):
            name = raw.get('name') or raw.get('owner')
            if name in persons:
                person(name, ref(key, f'/{field}/{i}'), raw)
    for name, row in persons.items():
        issue('payee_unconfirmed', '材料登记人及学号候选不等同确认收款人；收款姓名、账号和历史到账仍待确认。',
              person_id=row['id'], person=name)

    # Register medical identities before generic field extraction: a ten-digit
    # receipt number and its eight-digit code must not become a VAT identity.
    medical_invoice_by_row = {}
    for i, raw in enumerate(inputs['medical_audit'].get('rows', [])):
        origin = ref('medical_audit', f'/rows/{i}')
        iid = invoice(raw.get('invoice_number'), origin, raw, code=raw.get('invoice_code'),
                      kind='medical', authoritative=True,
                      values={'buyer_name': raw.get('payer_name_on_receipt'), 'seller_name': raw.get('issuer'),
                              'issue_date': raw.get('issue_date'), 'amount_fen': raw.get('invoice_amount')})
        if iid:
            medical_invoice_by_row[raw.get('row')] = iid
            for j, item in enumerate(raw.get('invoice_items', [])):
                item_ref = ref('medical_audit', f'/rows/{i}/invoice_items/{j}')
                out['invoice_items'].append({
                    'id': _id('item', iid, item_ref), 'invoice_id': iid,
                    'description': item.get('item'), 'amount_fen': known_money(item.get('amount'), item_ref, 'amount_fen', iid),
                    'tax_fen': known_money(item.get('tax'), item_ref, 'tax_fen', iid), 'currency': 'CNY',
                    'quantity': item.get('quantity'), 'unit_price_fen': known_money(item.get('unit_price'), item_ref, 'unit_price_fen', iid),
                    'method': 'existing_medical_audit', 'raw_text': None, 'raw_fields': copy.deepcopy(item),
                    'source_refs': [item_ref], 'source_record_ids': [source_ids['medical_audit']]})
                invoice_map[iid]['item_extraction_status'] = 'existing_structured_audit'
                _unique_append(invoice_map[iid]['content'], item.get('item'))

    for i, raw in enumerate(inputs['field_audit'].get('reports', [])):
        origin = ref('field_audit', f'/reports/{i}')
        iid = add_fields(raw.get('page', {}), origin, raw)
        did = document(raw.get('sha256'), origin, raw, raw.get('path'), 'electronic_invoice',
                       raw.get('page', {}).get('page'), role='invoice')
        if iid:
            add_link('invoice_documents', invoice_id=iid, document_id=did, origin=origin,
                     page=raw.get('page', {}).get('page'), role='invoice')

    # Content-addressed documents keep all occurrences, pages and text variants.
    for i, raw in enumerate(inputs['electronic_inventory'].get('files', [])):
        origin = ref('electronic_inventory', f'/files/{i}')
        document(raw.get('sha256'), origin, raw, raw.get('working_copy'), raw.get('kind'),
                 filename=raw.get('filename'))
    for i, raw in enumerate(inputs['electronic_inventory'].get('pages', [])):
        origin = ref('electronic_inventory', f'/pages/{i}')
        did = document(raw.get('sha256'), origin, raw, kind='electronic', page=raw.get('page'),
                       text=raw.get('native_text'), role=raw.get('category'))
        asset_map['e:' + str(raw.get('page_id'))] = (did, raw.get('page'), raw.get('category'))
    for i, raw in enumerate(inputs['workspace'].get('assets', [])):
        origin = ref('workspace', f'/assets/{i}')
        did = document(raw.get('sha256'), origin, raw, raw.get('source_path'), raw.get('kind'),
                       raw.get('page'), role=raw.get('role'))
        asset_map[raw['id']] = (did, raw.get('page'), raw.get('role'))
    for field in ('files', 'tencent_downloaded_files', 'excluded'):
        for i, raw in enumerate(inputs['review'].get(field, [])):
            origin = ref('review', f'/{field}/{i}')
            did = document(raw.get('sha256'), origin, raw, raw.get('file'), raw.get('kind'),
                           role='excluded' if field == 'excluded' else None)
            if raw.get('source_file_id'):
                tencent_files[raw['source_file_id']] = did
    for i, raw in enumerate(inputs['tencent_audit'].get('files', [])):
        origin = ref('tencent_audit', f'/files/{i}')
        did = document(raw.get('sha256'), origin, raw, raw.get('relative_path'), 'tencent_attachment',
                       filename=raw.get('filename'))
        tencent_files[raw.get('file_id')] = did
        for j, page in enumerate(raw.get('pages', [])):
            page_origin = ref('tencent_audit', f'/files/{i}/pages/{j}')
            is_ocr = any(word in str(page.get('method', '')).lower() for word in ('ocr', 'tesseract'))
            document(raw.get('sha256'), page_origin, page,
                     raw.get('relative_path'), page=page.get('page'), text=None if is_ocr else page.get('text'))
            if is_ocr:
                docs[did]['pages'].append({'page': page.get('page'), 'native_text': None,
                    'ocr_text': page.get('text'), 'source_ref': page_origin,
                    'ocr_method': page.get('method'), 'mean_confidence': page.get('mean_confidence', page.get('confidence')),
                    'ocr_words': copy.deepcopy(page.get('words', page.get('lines'))),
                    'ocr_source': page_origin})
        for j, page in enumerate(raw.get('invoice_fields', [])):
            porigin = ref('tencent_audit', f'/files/{i}/invoice_fields/{j}')
            iid = add_fields(page, porigin)
            if iid:
                add_link('invoice_documents', invoice_id=iid, document_id=did, origin=porigin,
                         page=page.get('page'), role='invoice')
    for i, raw in enumerate(inputs['medical_audit'].get('attachments', [])):
        origin = ref('medical_audit', f'/attachments/{i}')
        did = document(raw.get('sha256'), origin, raw, raw.get('relative_to_case') or raw.get('path'),
                       raw.get('detected_format'), role=raw.get('role'), filename=raw.get('original_filename'))
        if raw.get('path'):
            path_map.setdefault(raw['path'], set()).add(did)
        tencent_files[raw.get('file_id')] = did
        invoice_ids = number_map.get(str(raw.get('invoice_number')), [])
        if len(invoice_ids) == 1:
            add_link('invoice_documents', invoice_id=invoice_ids[0], document_id=did, origin=origin,
                     role=raw.get('role'))

    # Original material groups are identified through explicit review crosswalks.
    for i, raw in enumerate(inputs['review'].get('expenses', [])):
        eid = '材料-' + str(raw.get('number'))
        if eid not in expenses:
            continue
        origin = ref('review', f'/expenses/{i}')
        original_expense_map[raw.get('expense_id')] = eid
        review_number_map[str(raw.get('number'))] = eid
        provenance(expenses[eid], origin)
        expenses[eid]['review_status'] = raw.get('status_code')
        for asset in raw.get('assets', []):
            mapped = asset_map.get(asset.get('id'))
            if mapped:
                did, page, role = mapped
                add_link('expense_documents', expense_id=eid, document_id=did, origin=origin, page=page, role=role)
                if asset.get('path'):
                    path_map.setdefault(asset['path'], set()).add(did)
                    _unique_append(docs[did]['references'], {'path': asset['path'], 'source_ref': origin})
        for j, version in enumerate(raw.get('invoice_versions', [])):
            vorig = ref('review', f'/expenses/{i}/invoice_versions/{j}')
            iid = invoice(version.get('number'), vorig, version,
                          values={'amount_fen': version.get('amount'), 'issue_date': version.get('date')})
            if iid:
                header_audit(iid, {}, vorig, version.get('buyer'))
                _unique_append(invoice_map[iid]['version_status'], version.get('role'))
                add_link('expense_invoices', expense_id=eid, invoice_id=iid, origin=vorig,
                         relation='exact_material_version')
                out['finance_observations'].append({
                    'id': _id('finance', vorig), 'invoice_id': iid, 'invoice_number': version.get('number'),
                    'capture_at': None, 'capture_basis': inputs['review'].get('finance_basis'),
                    'rawstatus': {'system': version.get('system'), 'reservation': version.get('reservation')},
                    'authentication_status': version.get('system'), 'reservation_status': version.get('reservation'),
                    'reimbursement_status': 'unknown', 'payment_status': 'unknown', 'historical': True,
                    'source_refs': [vorig], 'source_record_ids': [source_ids['review']]})
        if raw.get('same_name_amount_candidates'):
            issue('candidate_material_relation', '姓名或金额候选没有成为确定费用关联。', origin, expense_id=eid,
                  candidates=raw['same_name_amount_candidates'])

    for i, raw in enumerate(inputs['workspace'].get('expenses', [])):
        eid = original_expense_map.get(raw.get('id'))
        origin = ref('workspace', f'/expenses/{i}')
        for aid in raw.get('asset_ids', []):
            if eid and aid in asset_map:
                did, page, role = asset_map[aid]
                add_link('expense_documents', expense_id=eid, document_id=did, origin=origin, page=page, role=role)
        for j, version in enumerate(raw.get('versions', [])):
            vorig = ref('workspace', f'/expenses/{i}/versions/{j}')
            iid = invoice(version.get('invoice_number'), vorig, version,
                          values={'amount_fen': version.get('amount_display'), 'issue_date': version.get('issue_date')})
            if not iid:
                continue
            _unique_append(invoice_map[iid]['version_status'], version.get('version_role'))
            header_audit(iid, {}, vorig, version.get('buyer_status'))
            if eid:
                add_link('expense_invoices', expense_id=eid, invoice_id=iid, origin=vorig, relation='exact_material_version')
            for aid in version.get('asset_ids', []):
                if aid in asset_map:
                    did, page, role = asset_map[aid]
                    add_link('invoice_documents', invoice_id=iid, document_id=did, origin=vorig, page=page, role=role)
            evidence = version.get('finance_evidence') or {}
            out['finance_observations'].append({
                'id': _id('finance', vorig), 'invoice_id': iid, 'invoice_number': version.get('invoice_number'),
                'capture_at': evidence.get('observed_at_utc'), 'recorded_at': evidence.get('recorded_at_utc'),
                'rawstatus': version.get('finance_status'), 'historical': True,
                'raw_evidence': copy.deepcopy(evidence), 'reimbursement_status': 'unknown', 'payment_status': 'unknown',
                'source_refs': [vorig], 'source_record_ids': [source_ids['workspace']]})
        if not eid:
            issue('material_group_outside_receivables', '材料组未与应收记录建立显式对应，保留材料但未增加应收。',
                  origin, original_expense_id=raw.get('id'))

    # Table cells identify attachments; only invoice-number/exact-material
    # crosswalks establish invoice relations. Name/amount guesses stay issues.
    traffic_rows = {r.get('row'): (i, r) for i, r in enumerate(inputs['review'].get('active_ledger', []))}
    row_expenses = {}
    for rowno, (i, raw) in traffic_rows.items():
        origin = ref('review', f'/active_ledger/{i}')
        exact = [review_number_map[str(n)] for n in raw.get('exact_material_numbers', []) if str(n) in review_number_map]
        extra = '腾讯交通-' + str(rowno)
        if extra in expenses:
            exact.append(extra)
        row_expenses[('traffic', rowno)] = list(dict.fromkeys(exact))
        for eid in row_expenses[('traffic', rowno)]:
            provenance(expenses[eid], origin)
        if raw.get('same_name_amount_candidates') or raw.get('ambiguity'):
            issue('candidate_material_relation', '腾讯行的候选对应保留待核，不按姓名或金额强行关联。', origin,
                  candidates=raw.get('same_name_amount_candidates'), ambiguity=raw.get('ambiguity'))
        # These numbers are the audited full row evidence, never filename guesses.
        for number in raw.get('invoice_numbers', []):
            iid = invoice(number, origin, {'invoice_number': number, 'row': raw})
            if iid:
                for eid in row_expenses[('traffic', rowno)]:
                    # A multi-expense table row is disambiguated by each material
                    # group's existing exact invoice links, not a cartesian join.
                    existing_pair = _id('expense_invoices', {'expense_id': eid, 'invoice_id': iid}) in links['expense_invoices']
                    if len(row_expenses[('traffic', rowno)]) == 1 or existing_pair:
                        add_link('expense_invoices', expense_id=eid, invoice_id=iid, origin=origin, relation='exact_tencent_row')
                    else:
                        issue('ambiguous_row_invoice_relation', '一行涉及多笔费用，未把该行全部票号交叉关联。',
                              origin, expense_id=eid, invoice_id=iid)

    for i, raw in enumerate(inputs['medical_audit'].get('rows', [])):
        origin = ref('medical_audit', f'/rows/{i}')
        eid = '腾讯医疗-' + str(raw.get('row'))
        if eid not in expenses:
            continue
        row_expenses[('medical', raw.get('row'))] = [eid]
        provenance(expenses[eid], origin)
        iid = medical_invoice_by_row.get(raw.get('row'))
        if iid:
            add_link('expense_invoices', expense_id=eid, invoice_id=iid, origin=origin, relation='exact_medical_audit')
        for fid in raw.get('downloaded_attachment_ids', []):
            did = tencent_files.get(fid)
            if did:
                add_link('expense_documents', expense_id=eid, document_id=did, origin=origin, role='medical_evidence')
        for page in raw.get('invoice_evidence_pages', []):
            for did in path_map.get(page.get('path'), set()):
                add_link('expense_documents', expense_id=eid, document_id=did, origin=origin,
                         page=page.get('page'), role='invoice')
                if iid:
                    add_link('invoice_documents', invoice_id=iid, document_id=did, origin=origin,
                             page=page.get('page'), role='invoice')

    for i, raw in enumerate(inputs['tencent_audit'].get('files', [])):
        did = tencent_files.get(raw.get('file_id'))
        if not did:
            continue
        for j, cell in enumerate(raw.get('references', [])):
            origin = ref('tencent_audit', f'/files/{i}/references/{j}')
            tab = cell.get('tab')
            section = 'medical' if tab == 'gsxxzh' or cell.get('sheet') == '医疗报销' else (
                'traffic' if tab == '000001' or cell.get('sheet') == '仅交通' else None)
            eids = row_expenses.get((section, cell.get('row')), [])
            for eid in eids:
                add_link('expense_documents', expense_id=eid, document_id=did, origin=origin, role='tencent_row_attachment')
            if not eids:
                issue('attachment_without_exact_expense', '附件已归档，但所在行没有本次应收的确定对应。',
                      origin, document_id=did)

    # Paper itinerary bindings include exact invoice numbers (both old/new where
    # applicable). Preserve the OCR/image evidence separately, not as invoices.
    for i, raw in enumerate(inputs['paper_links'].get('bindings', [])):
        origin = ref('paper_links', f'/bindings/{i}')
        mapped = asset_map.get('p:' + str(raw.get('paper_page')).zfill(3))
        if mapped:
            did, page, role = mapped
            for number in raw.get('invoice_numbers', []):
                invs = number_map.get(str(number), [])
                if len(invs) == 1 and raw.get('status') == 'confirmed':
                    add_link('invoice_documents', invoice_id=invs[0], document_id=did, origin=origin,
                             page=page, role=role or 'itinerary')
        for key in ('ocr_source', 'scan_image', 'invoice_ocr_source'):
            evidence = raw.get(key)
            if isinstance(evidence, dict) and evidence.get('path'):
                document(evidence.get('sha256'), ref('paper_links', f'/bindings/{i}/{key}'), evidence,
                         evidence.get('path'), 'derived_paper_evidence', role=key)

    # Supplemental machine OCR has its own report/source SHA and page geometry.
    # Existing audited relationships own the invoice; OCR cannot invent one.
    for index, record in enumerate(inputs.get('ocr_results', {}).get('reports', [])):
        raw = record.get('result')
        if not isinstance(raw, dict) or raw.get('schema') != 'reimbursement.remote-ocr.v1' or raw.get('status') != 'machine_read_only':
            raise ValueError('unsupported supplemental OCR result')
        item = raw.get('item', {})
        sha = item.get('sha256')
        did = 'sha256:' + str(sha)
        if not isinstance(sha, str) or not _SHA.fullmatch(sha) or did not in docs:
            raise ValueError('supplemental OCR source SHA must identify an existing document')
        if not isinstance(record.get('sha256'), str) or not _SHA.fullmatch(record['sha256']):
            raise ValueError('supplemental OCR report SHA is required')
        seen = set()
        for j, page in enumerate(raw.get('pages', [])):
            number = page.get('page')
            angle = page.get('rotation_clockwise')
            if (type(number) is not int or number < 1 or number not in item.get('pages', [])
                    or type(angle) is not int or angle not in (0, 90, 180, 270)
                    or (number, angle) in seen or not isinstance(page.get('text'), str)):
                raise ValueError('supplemental OCR page identity invalid')
            seen.add((number, angle))
            origin = ref('ocr_results', f'/reports/{index}/result/pages/{j}')
            docs[did]['pages'].append({'page': number, 'native_text': None, 'ocr_text': page['text'],
                'source_ref': origin, 'role': 'supplemental_ocr_evidence',
                'ocr_source': {k: record.get(k) for k in ('source_path', 'sha256')},
                'ocr_geometry': {k: copy.deepcopy(page.get(k)) for k in
                    ('rotation_clockwise', 'image', 'image_sha256', 'width', 'height', 'lines')},
                'ocr_method': 'rapidocr_onnxruntime', 'ocr_run_id': raw.get('run_id')})
            provenance(docs[did], origin)

    # Reuse frozen OCR as page evidence, never as a new invoice or an amount.
    if 'paper_ocr' in inputs:
        supplement = inputs['paper_ocr']
        sha = supplement.get('document_sha256')
        did = 'sha256:' + str(sha)
        paper_shas = {a.get('sha256') for a in inputs['workspace'].get('assets', []) if a.get('kind') == 'paper_page'}
        if not isinstance(sha, str) or not _SHA.fullmatch(sha) or did not in docs or sha not in paper_shas:
            raise ValueError('paper_ocr document SHA must identify an existing document')
        registered_pages = {a.get('page') for a in inputs['workspace'].get('assets', []) if a.get('sha256') == sha and a.get('kind') == 'paper_page' and type(a.get('page')) is int}
        seen_pages = set()
        for index, record in enumerate(supplement.get('pages', [])):
            page_no = record.get('page')
            raw = record.get('ocr')
            if type(page_no) is not int or page_no < 1 or page_no not in registered_pages or page_no in seen_pages or not isinstance(raw, dict):
                raise ValueError('paper_ocr pages must be unique positive integers with OCR objects')
            if raw.get('kind') != 'pdf' or raw.get('page_or_order') != page_no:
                raise ValueError('paper_ocr embedded page identity mismatch')
            seen_pages.add(page_no)
            text = raw.get('text')
            if not isinstance(text, str):
                raise ValueError('paper_ocr text must be a string')
            origin = ref('paper_ocr', f'/pages/{index}')
            docs[did]['pages'].append({'page': page_no, 'native_text': None, 'ocr_text': text,
                'source_ref': origin, 'ocr_source': copy.deepcopy(record.get('ocr_source')),
                'ocr_words': copy.deepcopy(raw.get('words', [])), 'ocr_method': raw.get('method'),
                'mean_confidence': raw.get('mean_confidence'), 'role': 'paper_ocr_evidence'})
            provenance(docs[did], origin)

    # Invoice text is not rewritten. Conservative descriptions/remarks provide
    # searchable rows; OCR line numerals do not become guessed prices or taxes.
    seen_items = {}
    face_pages = {}
    # 全部页面证据已追加完毕，同一文档只建一次页索引。页内列表保持原始
    # 来源和文本版本顺序；精确 int 排除 True（等于 1）和字符串页码，避免串页。
    document_pages = {}
    for link in out['invoice_documents']:
        iid = link['invoice_id']
        inv = invoice_map[iid]
        # Linked itinerary/statement text is evidence, not invoice-face text.
        if link.get('role') != 'invoice' or type(link.get('page')) is not int or link['page'] < 1:
            continue
        did = link['document_id']
        if did not in document_pages:
            by_page = {}
            for page in docs[did]['pages']:
                if type(page.get('page')) is int:
                    by_page.setdefault(page['page'], []).append(page)
            document_pages[did] = by_page
        for page in document_pages[did].get(link['page'], ()):
            text = page.get('native_text') or page.get('ocr_text')
            if not isinstance(text, str) or not text.strip():
                continue
            # Preserve repeated identical item occurrences *within a page*;
            # merge only the same occurrence carried by another source copy.
            occurrence = {}
            if inv['invoice_type'] != 'medical' and page.get('native_text'):
                for parsed in _native_item_rows(text):
                    # Layout and extraction method describe evidence, not an
                    # item identity. Unknown fields remain distinct from known
                    # values; occurrence preserves real repeated rows.
                    if parsed['quantity'] is not None:
                        quantity = Decimal(parsed['quantity'])
                        parsed['quantity'] = '0' if quantity == 0 else format(quantity.normalize(), 'f')
                    signature = _json({k: parsed[k] for k in (
                        'description', 'amount_fen', 'tax_fen', 'quantity', 'unit_price_fen')})
                    occurrence[signature] = occurrence.get(signature, 0) + 1
                    ident = (iid, signature, occurrence[signature])
                    evidence = {'document_id': link['document_id'], 'page': page.get('page'),
                                'source_ref': page['source_ref'], 'raw_text': parsed['raw_text'],
                                'method': parsed['method'], 'tax_rate': parsed.get('tax_rate')}
                    if ident not in seen_items:
                        item = {'id': _id('item-text', *ident), 'invoice_id': iid, **parsed,
                                'currency': 'CNY', 'evidence': [], 'source_refs': [], 'source_record_ids': [],
                                'independent_accounting': False,
                                'record_kind': 'partial_item_candidate' if any(parsed[k] is None for k in (
                                    'amount_fen', 'tax_fen', 'quantity', 'unit_price_fen')) else 'parsed_item',
                                'unknown_fields': [k for k in ('amount_fen', 'tax_fen', 'quantity', 'unit_price_fen') if parsed[k] is None]}
                        seen_items[ident] = item
                        out['invoice_items'].append(item)
                    item = seen_items[ident]
                    _unique_append(item['evidence'], evidence)
                    provenance(item, page['source_ref'])
                    _unique_append(inv['content'], parsed['description'])
                    inv['item_extraction_status'] = 'partial_native_text'
            text_sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
            identity = (iid, link['document_id'], page.get('page'), text_sha)
            if identity not in face_pages:
                face = {**extract_face(text, invoice_type=inv['invoice_type']),
                        'id': _id('face', *identity), 'document_id': link['document_id'],
                        'page': page.get('page'), 'text_sha256': text_sha, 'source_refs': [],
                        'text_kind': 'native' if page.get('native_text') else 'ocr',
                        'ocr_source': copy.deepcopy(page.get('ocr_source')),
                        'ocr_words': copy.deepcopy(page.get('ocr_words')),
                        'ocr_geometry': copy.deepcopy(page.get('ocr_geometry')),
                        'ocr_mean_confidence': page.get('mean_confidence')}
                face_pages[identity] = face
                inv.setdefault('face_pages', []).append(face)
                for remark in face['remarks']:
                    inv['remarks'].append({**copy.deepcopy(remark), 'face_id': face['id'],
                        'candidate_value': remark['value'], 'document_id': link['document_id'],
                        'page': page.get('page'), 'text_kind': face['text_kind'],
                        'source_ref': page['source_ref']})
            _unique_append(face_pages[identity]['source_refs'], page['source_ref'])

    if 'finance_audit' in inputs:
        report = inputs['finance_audit']
        scopes = report.get('query_scopes', [])
        for i, raw in enumerate(report.get('invoices', [])):
            origin = ref('finance_audit', f'/invoices/{i}')
            iid = invoice(raw.get('invoice_number'), origin, raw)
            out['finance_observations'].append({
                'id': _id('finance', origin), 'invoice_id': iid, 'invoice_number': raw.get('invoice_number'),
                'capture_at': None, 'capture_scopes': copy.deepcopy(scopes),
                'capture_times': [s.get('observed_at') for s in scopes if s.get('observed_at')],
                'report_generated_at': report.get('generated_at_utc'), 'historical': True,
                'rawstatus': copy.deepcopy(raw), 'recognition_status': raw.get('recognition_statuses'),
                'authentication_status': raw.get('school_statuses'), 'reservation_status': raw.get('reservation_status'),
                'reimbursement_status': raw.get('reimbursement_status'), 'payment_status': raw.get('payment_status'),
                'visibility_status': raw.get('visibility_status'),
                'source_refs': [origin], 'source_record_ids': [source_ids['finance_audit']]})
    else:
        pointer = inputs['case'].get('material_context', {}).get('school_authentication_report')
        if pointer:
            origin = ref('case', '/material_context/school_authentication_report')
            out['finance_observations'].append({'id': _id('finance-reference', origin), 'invoice_id': None,
                                              'capture_at': None, 'historical': True, 'reference_only': True,
                                              'report_reference': copy.deepcopy(pointer), 'source_refs': [origin],
                                              'source_record_ids': [source_ids['case']]})
            issue('finance_source_not_loaded', '案件引用了财务报告，但未提供 finance_audit；未生成该报告的票据状态。', origin)

    for key, field in (('workspace', 'questions'), ('review', 'issues')):
        for i, raw in enumerate(inputs[key].get(field, [])):
            origin = ref(key, f'/{field}/{i}')
            issue('existing_question', raw.get('question') or raw.get('action') or raw.get('known') or raw.get('topic') or '原记录待核问题',
                  origin, raw_fields=raw, person=raw.get('person') or raw.get('who'),
                  status=raw.get('status') or 'pending', ask_user_again=raw.get('ask_user_again'))
    linked_expenses = {r['expense_id'] for r in out['expense_invoices']}
    for eid, row in expenses.items():
        row['material_status'] = 'invoice_linked' if eid in linked_expenses else 'invoice_missing_or_unlinked'
        if eid not in linked_expenses:
            row['issues'].append(issue('invoice_missing_or_unlinked', '应收保留；尚无确定关联的正式票据，不能以附件缺失推定应收为零。',
                                       expense_id=eid, person_id=row['person_id']))
    for iid, row in invoice_map.items():
        row.setdefault('face_pages', [])
        row['face_extraction_status'] = 'text_candidates_available' if row['face_pages'] else 'face_text_missing'
        if not row['face_pages']:
            issue('invoice_face_text_missing', '未取得该票可用的票面全文；不能把备注或其他未提取字段当成空白。', invoice_id=iid)
        if row['item_extraction_status'] in ('not_structured', 'partial_native_text'):
            issue('invoice_items_partial', '票面服务项目或数字列未完整结构化；原文和所有已有字段已保留，需按原件继续核对。',
                  invoice_id=iid, extraction_status=row['item_extraction_status'])
    return out
