"""审核已冻结的远程OCR及逐页目视字段；不识别图片、不信任传入通过标志。"""
from __future__ import annotations
import copy
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

EVIDENCE_KEY = 'reviewed_page_evidence'
MAX_JSON = 16 * 1024 * 1024
FIELDS = ('buyer_title', 'buyer_tax_id', 'issue_date', 'net_amount', 'tax_amount', 'total_amount')


def declaration(value, path_check, sha_check):
    if not isinstance(value, list) or not value or len(value) > 2000:
        raise ValueError('reviewed_page_evidence须为有界逐页证据列表')
    result = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {'page', 'ocr', 'visual'} or type(row['page']) is not int or row['page'] < 1:
            raise ValueError('逐页证据须明确page/ocr/visual；页码从1开始')
        item = {'page': row['page']}
        for kind in ('ocr', 'visual'):
            ref = row[kind]
            keys = {'path', 'sha256'} | ({'pointer'} if kind == 'visual' else set())
            if not isinstance(ref, dict) or set(ref) != keys:
                raise ValueError('证据仅接受明确path/sha256及视觉记录pointer')
            item[kind] = {'path': str(path_check(ref['path'], 'evidence.path')),
                          'sha256': sha_check(ref['sha256'], 'evidence.sha256')}
            if kind == 'visual':
                pointer = ref['pointer']
                if not isinstance(pointer, str) or not pointer.startswith('/') or len(pointer) > 4096:
                    raise ValueError('视觉记录须为明确JSON Pointer')
                item[kind]['pointer'] = pointer
        result.append(item)
    if len({r['page'] for r in result}) != len(result):
        raise ValueError('逐页证据页码重复')
    return result


def _pointer(value, pointer):
    try:
        for token in pointer.split('/')[1:]:
            token = token.replace('~1', '/').replace('~0', '~')
            value = value[int(token)] if isinstance(value, list) else value[token]
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise ValueError('视觉证据pointer未指向实际记录') from error
    return value


def _money(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        raise ValueError('目视金额须为非负、最多两位小数的明确文本')
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError('目视金额无效') from error


def _page_count(raw, suffix):
    if suffix == '.pdf':
        import pymupdf
        with pymupdf.open(stream=raw, filetype='pdf') as document:
            if document.is_repaired or document.needs_pass or not 1 <= len(document) <= 2000:
                raise ValueError('原PDF损坏、加密或页数超限')
            if any(len(re.sub(r'\s', '', page.get_text())) >= 15 for page in document):
                raise ValueError('已有原生文字页不能用远程OCR证据覆盖；沿原生审核入口')
            return len(document)
    if suffix in {'.jpg', '.jpeg', '.png', '.webp'}:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            if getattr(image, 'n_frames', 1) != 1 or image.width * image.height > 100_000_000:
                raise ValueError('图片页数或像素超限')
            image.verify()
        return 1
    raise ValueError('已有OCR+目视审核仅支持PDF及单帧常用图片')


def audit_existing_pages(source, read_verified):
    """证据只供字段，票号/抬头/金额及其一致性由当前代码重算。"""
    from pathlib import Path
    from invoice_audit import USTC, fields_from_page, normalize, check_tax_id
    raw = read_verified(source['path'], source['sha256'], 30 * 1024 * 1024)
    count = _page_count(raw, Path(source['path']).suffix.lower())
    declarations = source[EVIDENCE_KEY]
    if sorted(r['page'] for r in declarations) != list(range(1, count + 1)):
        raise ValueError('已有OCR+目视证据必须覆盖原件全部页且页码一致')
    expected = {(r['number'], r['page']) for r in source['invoice_records']}
    pages, receipts = [], []
    for row in sorted(declarations, key=lambda r: r['page']):
        page = row['page']
        ocr = json.loads(read_verified(row['ocr']['path'], row['ocr']['sha256'], MAX_JSON))
        if (ocr.get('schema') != 'reimbursement.remote-ocr.v1'
                or ocr.get('status') != 'machine_read_only'
                or (ocr.get('item') or {}).get('sha256') != source['sha256']
                or page not in (ocr.get('item') or {}).get('pages', [])):
            raise ValueError('远程OCR schema、原件SHA或页码绑定不符')
        variants = [p for p in ocr.get('pages', []) if p.get('page') == page]
        if len(variants) != 1:
            raise ValueError('远程OCR页缺失或存在多方向歧义，须先核定唯一页')
        text = variants[0].get('text')
        if not isinstance(text, str) or len(text.strip()) < 15 or len(text) > 1_000_000:
            raise ValueError('已有OCR文字不足或超限')
        visual = _pointer(json.loads(read_verified(row['visual']['path'], row['visual']['sha256'], MAX_JSON)), row['visual']['pointer'])
        if (not isinstance(visual, dict) or visual.get('sha256') != source['sha256']
                or type(visual.get('page')) is not int or visual['page'] != page
                or visual.get('authenticity_verified') is not False):
            raise ValueError('目视记录缺少原件SHA/页码或真实性边界')
        number = visual.get('invoice_number')
        fields = visual.get('audit_fields')
        if (not isinstance(fields, dict) or not isinstance(fields.get('method'), str)
                or not ('目视' in fields['method'] or fields['method'] == 'visual_review')):
            raise ValueError('缺少逐页目视字段核对方法')
        if not isinstance(number, str) or not re.fullmatch(r'[0-9]{20}', number) or (number, page) not in expected:
            raise ValueError('目视票号/页码与本案登记不符')
        if any(not isinstance(fields.get(k), str) or not fields[k].strip() for k in FIELDS):
            raise ValueError('目视购方、税号、日期及净税总额字段不完整')
        parsed = fields_from_page(text, page, USTC['title'], USTC['tax_id'])
        if parsed.get('invoice_number_candidates') != [number] or parsed.get('kind') != 'vat':
            raise ValueError('既有OCR完整票号与目视/登记不一致或非已适配普通发票')
        # 不用视觉数据盖掉另一笔明确的OCR字段；布局丢失的空字段才允许补齐。
        tight = normalize(text)
        for k in ('buyer_title', 'buyer_tax_id'):
            if normalize(fields[k]) not in tight or (parsed.get(k) and normalize(parsed[k]) != normalize(fields[k])):
                raise ValueError('目视购方字段缺少OCR支持或与其冲突：' + k)
        if normalize(fields['buyer_title']) != normalize(USTC['title']) or normalize(fields['buyer_tax_id']) != USTC['tax_id']:
            raise ValueError('目视购方名称/税号不符合本次固定USTC配置')
        try:
            issued = date.fromisoformat(fields['issue_date']).isoformat()
        except ValueError as error:
            raise ValueError('目视开票日期无效') from error
        if parsed.get('issue_date') != issued:
            raise ValueError('既有OCR与目视开票日期不一致')
        net, tax, total = (_money(fields[k]) for k in ('net_amount', 'tax_amount', 'total_amount'))
        if net + tax != total or parsed.get('total_amount') is None or _money(parsed['total_amount']) != total:
            raise ValueError('OCR小写/大写合计与目视净税总额不闭合')
        for k in ('net_amount', 'tax_amount', 'total_amount'):
            value = _money(fields[k])
            present = any(Decimal(x.replace(',', '')) == value for x in re.findall(r'(?<![\d.])\d[\d,]*\.\d{1,2}(?!\d)', text))
            if not present or (parsed.get(k) is not None and _money(parsed[k]) != value):
                raise ValueError('目视金额缺少OCR数值支持或与已解析字段冲突：' + k)
        parsed.update({k: fields[k] for k in FIELDS})
        parsed['validation'].update(buyer_tax_id=check_tax_id(fields['buyer_tax_id']),
            title_matches_expected=True, tax_id_matches_expected=True)
        removable = {'BUYER_FIELDS_NEEDS_REVIEW', 'NET_AMOUNT_NEEDS_REVIEW'}
        if any(x not in removable for x in parsed['issues']):
            raise ValueError('已有证据仍存在未解决票面问题：' + ','.join(parsed['issues']))
        parsed['issues'] = []
        parsed['extraction_method'] = 'existing_remote_ocr_and_visual'
        parsed['verification']['fields'].update(invoice_number=number, issue_date=issued,
            total_amount=fields['total_amount'], net_amount=fields['net_amount'])
        parsed['field_evidence'] = copy.deepcopy(row)
        pages.append(parsed); receipts.append(copy.deepcopy(row))
    if {(n, p['page']) for p in pages for n in p['invoice_number_candidates']} != expected:
        raise ValueError('已有证据的票号/页码未与本案登记闭合')
    return {'path': source['path'], 'sha256': source['sha256'], 'pages': pages,
            'authenticity_verified': False, 'system_write': False,
            'parser_version': 'existing-page-evidence-v1', 'evidence': receipts}
