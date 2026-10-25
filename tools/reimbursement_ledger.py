#!/usr/bin/env python3
"""可重建的报销证据总账。业务构建由远程 worker 调用，本机仅查询已生成快照。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile

from reimbursement_ledger_trace import build_relationship_tables
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
TABLES = ('persons', 'expenses', 'invoices', 'invoice_items', 'documents',
          'expense_invoices', 'expense_documents', 'invoice_documents',
          'finance_observations', 'issues', 'source_records')
REFS = {
    'expenses': {'person_id': 'persons'},
    'invoice_items': {'invoice_id': 'invoices'},
    'expense_invoices': {'expense_id': 'expenses', 'invoice_id': 'invoices'},
    'expense_documents': {'expense_id': 'expenses', 'document_id': 'documents'},
    'invoice_documents': {'invoice_id': 'invoices', 'document_id': 'documents'},
}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_ledger(data, expected_totals=None):
    errors = []
    if data.get('schema_version') != SCHEMA_VERSION:
        errors.append('unsupported schema_version')
    if not isinstance(data.get('case_id'), str) or not data['case_id']:
        errors.append('missing case_id')
    ids = {}
    for table in TABLES:
        rows = data.get(table)
        if not isinstance(rows, list):
            errors.append(f'{table}: expected list')
            rows = []
        ids[table] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                errors.append(f'{table}: missing string id')
                continue
            if row['id'] in ids[table]:
                errors.append(f'{table}: duplicate id {row["id"]}')
            ids[table].add(row['id'])
            for key, value in row.items():
                if key.endswith('_fen') and value is not None and type(value) is not int:
                    errors.append(f'{table}/{row["id"]}/{key}: expected integer fen or null')
    for table, fields in REFS.items():
        for row in data.get(table, []):
            for field, parent in fields.items():
                if row.get(field) not in ids[parent]:
                    errors.append(f'{table}/{row.get("id")}: unknown {field}={row.get(field)}')
    pairs = set()
    for row in data.get('expense_invoices', []):
        pair = (row.get('expense_id'), row.get('invoice_id'))
        if pair in pairs:
            errors.append(f'duplicate expense invoice link: {pair}')
        pairs.add(pair)
    for invoice in data.get('invoices', []):
        face_ids = set()
        for face in invoice.get('face_pages', []):
            fid = face.get('id')
            raw = face.get('raw_text')
            if not isinstance(fid, str) or not fid or fid in face_ids:
                errors.append(f'invoice {invoice.get("id")}: invalid/duplicate face id')
            face_ids.add(fid)
            if face.get('document_id') not in ids['documents']:
                errors.append(f'face {fid}: missing source document')
            if not isinstance(raw, str) or hashlib.sha256(raw.encode('utf-8')).hexdigest() != face.get('text_sha256'):
                errors.append(f'face {fid}: full text SHA mismatch')
                continue
            for field in face.get('fields', []) + face.get('remarks', []):
                start, end = field.get('start'), field.get('end')
                if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(raw) or raw[start:end] != field.get('raw_text'):
                    errors.append(f'face {fid}: evidence span mismatch')
        for remark in invoice.get('remarks', []):
            if remark.get('face_id') is not None and remark['face_id'] not in face_ids:
                errors.append(f'invoice {invoice.get("id")}: orphan remark')
    included = [r for r in data.get('expenses', []) if r.get('accounting_included') is True]
    known = [r for r in included if r.get('amount_fen') is not None]
    summary = {'persons': len(ids['persons']), 'expenses': len(included),
               'amount_fen': sum(r['amount_fen'] for r in known),
               'unknown_amount_expenses': len(included) - len(known),
               'invoice_versions': len(ids['invoices']), 'documents': len(ids['documents'])}
    for key, value in (expected_totals or {}).items():
        if key not in summary or summary[key] != value:
            errors.append(f'independent source control {key}: expected {value}, observed {summary.get(key)}')
    return {'ok': not errors, 'errors': errors, 'counts': {k: len(ids[k]) for k in TABLES},
            'controls': summary, 'face_coverage': {
                'with_text': sum(bool(r.get('face_pages')) for r in data.get('invoices', [])),
                'without_text': sum(not r.get('face_pages') for r in data.get('invoices', [])),
                'text_pages': sum(len(r.get('face_pages', [])) for r in data.get('invoices', [])),
                'remarks_records': sum(len(r.get('remarks', [])) for r in data.get('invoices', [])),
                'field_candidates': sum(len(f.get('fields', [])) for r in data.get('invoices', []) for f in r.get('face_pages', [])),
                'complete_visual_extraction_claimed': False}, 'system_write': False}


def summarize(data):
    linked = {r['expense_id'] for r in data['expense_invoices']}
    categories = sorted({str(r.get('category') or '未分类') for r in data['expenses']})
    result = []
    for person in data['persons']:
        rows = [r for r in data['expenses'] if r['person_id'] == person['id'] and r.get('accounting_included') is True]
        with_invoice = [r for r in rows if r['id'] in linked]
        without_invoice = [r for r in rows if r['id'] not in linked]
        by_category = {}
        for cat in categories:
            amounts = [r.get('amount_fen') for r in rows if (r.get('category') or '未分类') == cat]
            by_category[cat] = sum(amounts) if amounts and all(x is not None for x in amounts) else None
        result.append({**person, 'expense_count': len(rows),
                       'expected_amount_fen': sum(r['amount_fen'] for r in rows) if all(r.get('amount_fen') is not None for r in rows) else None,
                       'known_amount_fen': sum(r['amount_fen'] for r in rows if r.get('amount_fen') is not None),
                       'unknown_amount_expenses': sum(r.get('amount_fen') is None for r in rows),
                       'with_invoice_amount_fen': sum(r['amount_fen'] for r in with_invoice) if all(r.get('amount_fen') is not None for r in with_invoice) else None,
                       'without_invoice_amount_fen': sum(r['amount_fen'] for r in without_invoice) if all(r.get('amount_fen') is not None for r in without_invoice) else None,
                       'category_amounts_fen': by_category,
                       'paid_amount_fen': None, 'remaining_amount_fen': None})
    return {'case_id': data['case_id'], 'persons': result, 'categories': categories,
            'category_unknown_counts': {c: sum(r.get('amount_fen') is None for r in data['expenses'] if r.get('accounting_included') is True and (r.get('category') or '未分类') == c) for c in categories},
            'expected_amount_fen': sum(r['expected_amount_fen'] for r in result) if all(r['expected_amount_fen'] is not None for r in result) else None,
            'known_amount_fen': sum(r['known_amount_fen'] for r in result),
            'unknown_amount_expenses': sum(r['unknown_amount_expenses'] for r in result),
            'scope': '现有登记应收；未按发票有无筛减，未扣除未知历史到账。票面版本不重复计入费用。',
            'finance_system_write': False}


def cell(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return dumps(value)
    return value


def display(value):
    labels={'unknown':'待核','pending':'待核','unconfirmed':'待核','medical':'医疗财政票据','vat':'增值税发票','aviation_vat':'航空运输电子发票','unclassified':'票种待核',
            'invoice_linked':'已关联票据','invoice_missing_or_unlinked':'未取得或未关联正式票据',
            'exact_material_version':'原材料准确对应','exact_tencent_row':'腾讯行准确对应','old_version':'旧版','replacement_candidate':'重开候选',
            'not_audited':'未取得独立审核','buyer_title_and_tax_id_mismatch':'抬头和税号不符','buyer_title_mismatch':'抬头不符',
            'buyer_tax_id_mismatch':'税号不符','buyer_fields_match':'抬头税号匹配','buyer_fields_incomplete':'购方字段不完整',
            'payee_unconfirmed':'收款信息待确认','existing_question':'原有待核事项','invoice_items_partial':'票面项目待核',
            'document_sha_missing':'材料SHA待补','invoice_field_conflict':'票面字段冲突','invoice_money_unparsed':'票面金额待核',
            'invoice_number_candidates':'票号候选待核','ambiguous_invoice_identity':'票据身份待核',
            'attachment_without_exact_expense':'附件未对应本次费用'}
    if isinstance(value,str):return labels.get(value,value)
    if isinstance(value,list):return '；'.join(str(display(x)) for x in value)
    return value


def _remark_display(remark):
    value = remark.get('value', remark.get('candidate_value'))
    if value == '' and str(remark.get('status', '')).startswith('explicit_blank'):
        return '[原文明确空白]'
    return str(value) if value not in (None, '') else '[备注值未确定]'


def _readable_record(row):
    """Project repeated geometry out of table cells; full data stay in JSON/SQLite."""
    result = dict(row)
    for key in ('pages', 'face_pages'):
        if key in result:
            result[key] = [{k: v for k, v in page.items() if k not in
                ('ocr_words', 'ocr_geometry', 'lines', 'fields', 'remarks', 'ignored_labels')}
                for page in result[key]]
    return result


def build_workbook_data(data, summary):
    people = {r['id']: r['name'] for r in data['persons']}
    invoices = {r['id']: r for r in data['invoices']}
    expense_links = {}
    invoice_people = {}
    expense_map = {r['id']: r for r in data['expenses']}
    for link in data['expense_invoices']:
        expense_links.setdefault(link['expense_id'], []).append(link['invoice_id'])
        p = people[expense_map[link['expense_id']]['person_id']]
        invoice_people.setdefault(link['invoice_id'], set()).add(p)
    tables = []

    def add(name, headers, rows, money_cols=(), widths=None, note=''):
        tables.append({'name': name, 'headers': headers, 'rows': [[cell(x) for x in r] for r in rows],
                       'money_columns': list(money_cols), 'widths': widths or [], 'note': note})

    cats = summary['categories']
    person_rows = []
    for p in summary['persons']:
        person_rows.append([p['name'], None if p['expected_amount_fen'] is None else p['expected_amount_fen']/100,
                            *[None if p['category_amounts_fen'][c] is None else p['category_amounts_fen'][c]/100 for c in cats],
                            p['expense_count'], None if p['with_invoice_amount_fen'] is None else p['with_invoice_amount_fen']/100,
                            None if p['without_invoice_amount_fen'] is None else p['without_invoice_amount_fen']/100,
                            p.get('payee_name'), p.get('payee_student_id'), p.get('bank_name'), p.get('bank_account'),
                            display(p.get('confirmation_status')) or '待核', ' / '.join(sorted({str(c.get('student_id')) for c in p.get('identity_candidates',[]) if c.get('student_id')})),
                            None, None, p['id']])
    add('每人应收与收款', ['登记人','应收合计（元）',*[c+'（元）' for c in cats], '费用笔数','关联票据费用（元）','未关联票据费用（元）',
                         '确认收款人','确认学工号','开户行','银行卡号','收款确认状态','身份候选及来源','已到账（元）','尚待支付（元）','人员ID'],
        person_rows, [1,*range(2,2+len(cats)),3+len(cats),4+len(cats),11+len(cats),12+len(cats)],
        note=summary['scope']+' 收款人、账号及历史到账未确认。心理与体检走劳务的办理金额尚待核定。')

    inventory=[]
    for kind in sorted({str(r.get('invoice_type') or 'unclassified') for r in data['invoices']}):
        rows=[r for r in data['invoices'] if (r.get('invoice_type') or 'unclassified')==kind]
        known=[r['amount_fen'] for r in rows if r.get('amount_fen') is not None]
        inventory.append([display(kind),len(rows),sum(known)/100,len(rows)-len(known),
                          sum(r['id'] in invoice_people for r in rows),sum(r['id'] not in invoice_people for r in rows)])
    add('票据库存汇总',['票种','票据版本数','已知票面合计（元）','金额未知张数','关联本次费用的版本数','仅留对照或未关联版本数'],inventory,[2],
        note='库存口径包含新旧票和历史票，票面合计不能作为报销申请额。费用金额只在逐笔费用中计一次。')

    exp_rows=[]
    for e in data['expenses']:
        linked=[invoices[i].get('invoice_number',i) for i in expense_links.get(e['id'],[])]
        exp_rows.append([people[e['person_id']], e['id'], e.get('category'), e.get('description'),
                         None if e.get('amount_fen') is None else e['amount_fen']/100,
                         '纳入' if e.get('accounting_included') else '仅对照', '\n'.join(linked),
                         '已关联票据版本' if linked else '未取得或未关联正式票据',
                         e.get('source_record_ids'), display(e.get('material_status')), e.get('issues'), e['person_id'], e])
    add('逐笔费用', ['登记人','费用ID','类别','费用内容','应收（元）','本次范围','全部关联票号','票据情况','来源记录','材料状态','问题','人员ID','完整记录'],
        exp_rows,[4], note='每行是一笔费用；有新旧票仍只计一次。票据情况不代表可报销、已报销或已支付。')
    add('无票及待补费用', ['登记人','费用ID','类别','费用内容','应收（元）','本次范围','关联票号','票据情况','来源记录','材料状态','问题','人员ID','完整记录'],
        [r for r in exp_rows if not r[6]],[4], note='未关联票据仍保留应收。医疗纸票登记及待补体检材料也在此核对。')

    inv_rows=[]
    for i in data['invoices']:
        inv_rows.append(['、'.join(sorted(invoice_people.get(i['id'],[]))),i.get('invoice_number'),i.get('invoice_code'),
            display(i.get('invoice_type')),i.get('issue_date'),i.get('seller_name'),i.get('seller_tax_id'),i.get('buyer_name'),i.get('buyer_tax_id'),
            *[None if i.get(k) is None else i[k]/100 for k in ('amount_fen','net_fen','tax_fen')],
            display(i.get('content') or i.get('description')),'\n'.join(dict.fromkeys(_remark_display(r) for r in i.get('remarks', []))) or '[未提取到备注值]',display(i.get('header_status')),display(i.get('version_status')),i['id'],i.get('source_record_ids'),_readable_record(i)])
    add('全部票据版本', ['登记人','票据号码','票据代码','票种','开票日期','销售方','销售方税号','购买方','购买方税号','价税合计（元）','未税金额（元）','税额（元）',
        '票面内容','备注','购方审核','版本状态','票据ID','来源记录','记录摘要（全文另表）'],inv_rows,[9,10,11],
        note='包含新旧版本和历史对照票。此表金额不能当作本次应收；不以认证正常代替抬头税号审核。')
    add('票面项目明细', ['票据号码','项目内容','金额（元）','税额（元）','数量','单价（元）','提取方式','原文字依据','项目ID','完整记录'],
        [[invoices[r['invoice_id']].get('invoice_number'),r.get('description'),
          None if r.get('amount_fen') is None else r['amount_fen']/100,None if r.get('tax_fen') is None else r['tax_fen']/100,
          r.get('quantity'),None if r.get('unit_price_fen') is None else r['unit_price_fen']/100,r.get('method'),r.get('raw_text') or r.get('evidence'),r['id'],r]
         for r in data['invoice_items']],[2,3,5], note='保守提取；缺失字段留空，项目金额不可再次计入应收。未结构化内容保留原文字与完整记录。')
    add('费用与票版关系', ['费用ID','票据号码','关系','票据ID','关联ID','完整记录'],
        [[r['expense_id'],invoices[r['invoice_id']].get('invoice_number'),display(r.get('relation')),r['invoice_id'],r['id'],r] for r in data['expense_invoices']],
        note='准确对应关系与候选关系分开；重开关系不证明旧票已经红冲。')
    add('财务系统历史记录', ['票据号码','采集时间','识别状态','认证状态','预约占用','已报销','已支付','观察ID','完整记录'],
        [[r.get('invoice_number') or invoices.get(r.get('invoice_id'),{}).get('invoice_number'),r.get('captured_at') or r.get('capture_at') or r.get('capture_times') or r.get('capture_basis'),
          display(r.get('recognition_status')),display(r.get('authentication_status')),display(r.get('reservation_status')),display(r.get('reimbursement_status')),display(r.get('payment_status')),r['id'],r]
         for r in data['finance_observations']], note='历史快照，不是本轮实时读取；识别、认证、占用、审批、付款分别核对。')
    add('材料与来源', ['材料ID','SHA256','类型','来源及原件位置','页面与文字','材料记录（词框见快照）'],
        [[r['id'],r.get('sha256'),r.get('kind') or r.get('type'),r.get('references') or r.get('sources'),_readable_record(r).get('pages'),_readable_record(r)] for r in data['documents']],
        note='同字节材料合并存储，全部交接来源保留。材料ID可连接费用、票据和具体页。')
    add('材料关联', ['关系类型','费用ID','票据ID','材料ID','关联ID','完整记录'],
        [['费用材料',r.get('expense_id'),None,r['document_id'],r['id'],r] for r in data['expense_documents']]+
        [['票据材料',None,r.get('invoice_id'),r['document_id'],r['id'],r] for r in data['invoice_documents']])
    extraction_codes={'invoice_items_partial','document_sha_missing','invoice_field_conflict','invoice_money_unparsed','invoice_number_candidates','ambiguous_invoice_identity','invoice_face_text_missing'}
    def issue_rows(records):
        return [[display(r.get('type') or r.get('kind') or r.get('code')),people.get(r.get('person_id'),r.get('person')),r.get('expense_id'),r.get('invoice_id'),
                 r.get('message') or r.get('description') or r.get('detail'),display(r.get('status')) or '待核',r['id'],r] for r in records]
    issue_headers=['问题类型','登记人','费用ID','票据ID','问题及处理','状态','问题ID','完整记录']
    add('待确认问题',issue_headers,issue_rows([r for r in data['issues'] if r.get('code') not in extraction_codes]),
        note='已答复“不清楚”的事项保留原状态；不重复要求提供已交材料。字段与来源的技术缺项在下一张表。')
    add('字段与来源待核',issue_headers,issue_rows([r for r in data['issues'] if r.get('code') in extraction_codes]),
        note='由整理工具或原件复核解决，不作为重复向队员询问的清单，也不自动扣减应收。')
    field_rows=[]
    for i in data['invoices']:
        for key,value in _readable_record(i).items():
            field_rows.append([i.get('invoice_number'),i['id'],key,value])
    add('票据全字段索引', ['票据号码','票据ID','字段','值'],field_rows,
        note='字段索引及全文均可查。重复词框、行数组和解析候选不嵌入每个汇总格；完整结构以配套JSON/SQLite为准，候选另见票面分表。')
    remark_rows, supplement_rows, face_rows = [], [], []
    labels = {'multiline_candidate':'多行备注候选', 'label_only_layout_unknown':'有标签，值未确定',
              'explicit_blank_candidate':'明确空白文字，版面待核', 'detached_supplement_candidate':'分离的补充块，备注归属待核',
              'unknown':'未确定', 'value_candidate':'有值候选', 'following_line_candidate':'邻行候选',
              'unassigned_numeric_candidate':'尚未归属具体项目', 'explicit_blank':'原文明确空白'}
    for inv in data['invoices']:
        shared = [inv.get('invoice_number'), inv['id']]
        for r in inv.get('remarks', []):
            remark_rows.append([*shared, r.get('value'), labels.get(r.get('status'), r.get('status')),
                r.get('text_kind'), r.get('document_id'), r.get('page'), r.get('line_start'), r.get('line_end'),
                r.get('raw_text'), r.get('source_ref'), r.get('face_id')])
        if not inv.get('remarks'):
            remark_rows.append([*shared, None, '未提取到备注值；不能据此确认空白', None, None, None, None, None, None, None, None])
        for face in inv.get('face_pages', []):
            face_rows.append([*shared, face['document_id'], face.get('page'), face.get('text_kind'),
                face.get('raw_text'), face.get('unparsed_lines'), face.get('text_sha256'),
                face.get('source_refs'), face.get('parser_version'), face.get('limitations'), face['id']])
            for field in face.get('fields', []):
                supplement_rows.append([*shared, field.get('label'), field.get('key'), field.get('value'),
                    labels.get(field.get('status'), field.get('status')), face['document_id'], face.get('page'),
                    field.get('line_start'), field.get('line_end'), field.get('raw_text'), face['id']])
        if not inv.get('face_pages'):
            face_rows.append([*shared, None, None, '未取得全文', None, None, None, None, None,
                              '已有结构化字段仍保留；未识别部分不能视为空白。', None])
    add('票面备注', ['票据号码','票据ID','备注内容','提取状态','文字来源','材料ID','页码','起始行','结束行','原文依据','来源索引','全文ID'], remark_rows,
        note='多行内容原样保留。文字流候选尚不能证明备注框完整；分离的银行地址块保留其不确定归属。')
    add('票面补充字段', ['票据号码','票据ID','原标签','字段名','候选值','提取状态','材料ID','页码','起始行','结束行','原文依据','全文ID'], supplement_rows,
        note='含票面人员、银行、地址、流水、支付等标签字段；不覆盖独立审核。票面收款人不等于报销款收款人。')
    add('票面全文', ['票据号码','票据ID','材料ID','页码','文字来源','全部原文','未结构化行号','文字SHA256','来源索引','提取器版本','限制与待核','全文ID'], face_rows,
        note='保存全部已有原生文字及OCR文字，未结构化信息也能检索。OCR可能误字漏字；坐标词框另存JSON/SQLite。')
    tables.extend(build_relationship_tables(data))
    return {'schema_version': 1, 'case_id': data['case_id'], 'tables': tables,
            'summary': summary, 'generated_at': data.get('generated_at'),
            'export_policy': '数值为服务器计算快照，编辑Excel不回写证据；新增资料通过固定入口重建新版。'}


def _sqlite(data, path):
    conn=sqlite3.connect(path)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)')
        for key in ('schema_version','case_id','generated_at','provenance'):
            conn.execute('INSERT INTO metadata VALUES (?,?)',(key,dumps(data.get(key))))
        for table in TABLES:
            columns = ['id TEXT PRIMARY KEY', 'person_id TEXT', 'expense_id TEXT', 'invoice_id TEXT',
                       'document_id TEXT', 'name TEXT', 'invoice_number TEXT', 'category TEXT', 'amount_fen INTEGER', 'data_json TEXT NOT NULL']
            columns += [f'FOREIGN KEY ({k}) REFERENCES {parent}(id)' for k,parent in REFS.get(table,{}).items()]
            conn.execute(f'CREATE TABLE {table} ({",".join(columns)})')
        for table in TABLES:
            for row in data[table]:
                values=[row.get(k) for k in ('id','person_id','expense_id','invoice_id','document_id','name','invoice_number','category','amount_fen')]
                conn.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?)',values+[dumps(row)])
            for col in ('person_id','expense_id','invoice_id','document_id','invoice_number'):
                conn.execute(f'CREATE INDEX {table}_{col} ON {table} ({col})')
        conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity_check failed')
    finally:
        conn.close()


def write_ledger(dataset, output_dir, expected_totals=None):
    """只向新快照目录写出；业务执行由受管远程 worker 提供。"""
    validation=validate_ledger(dataset,expected_totals)
    if not validation['ok']:
        raise ValueError(dumps(validation))
    output=Path(output_dir)
    names=('ledger.json','ledger.sqlite','summary.json','workbook_data.json','validation.json')
    output.mkdir(parents=True,exist_ok=True)
    if any((output/n).exists() for n in names):
        raise FileExistsError('snapshot already exists; select a new output directory')
    data={**dataset,'generated_at':datetime.now(timezone.utc).isoformat()}
    summary=summarize(data)
    with tempfile.TemporaryDirectory(prefix='.ledger-',dir=output) as temporary:
        stage=Path(temporary)
        for name,value in [('ledger.json',data),('summary.json',summary),('workbook_data.json',build_workbook_data(data,summary)),('validation.json',validation)]:
            (stage/name).write_text(dumps(value)+'\n',encoding='utf-8')
        _sqlite(data,stage/'ledger.sqlite')
        files=[]
        for name in names:
            p=stage/name
            p.chmod(0o600)
            files.append({'name':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size})
        for name in names:
            os.replace(stage/name,output/name)
    return {'ok':True,'case_id':data['case_id'],'files':files,'validation':validation,'system_write':False}


def query(database, *, case_id=None, table='expenses', person=None, invoice_number=None, record_id=None, limit=200):
    if table not in TABLES or type(limit) is not int or not 1<=limit<=2000:
        raise ValueError('invalid table or limit (1..2000)')
    path=Path(database).resolve(strict=True)
    uri=path.as_uri()+'?mode=ro&immutable=1'
    conn=sqlite3.connect(uri,uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        metadata={k:json.loads(v) for k,v in conn.execute('SELECT key,value_json FROM metadata')}
        if metadata.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('unsupported schema_version')
        if case_id and metadata.get('case_id')!=case_id:
            raise ValueError('case_id mismatch')
        where=[];args=[]
        if record_id is not None:where.append('t.id=?');args.append(record_id)
        if person is not None:
            if table=='persons':where.append('(t.name=? OR t.id=?)');args.extend([person,person])
            elif table=='expenses':where.append('t.person_id IN (SELECT id FROM persons WHERE name=? OR id=?)');args.extend([person,person])
            elif table=='expense_invoices':
                where.append('t.expense_id IN (SELECT e.id FROM expenses e JOIN persons p ON p.id=e.person_id WHERE p.name=? OR p.id=?)');args.extend([person,person])
            elif table in ('invoices','invoice_items'):
                key='t.id' if table=='invoices' else 't.invoice_id'
                where.append(f'{key} IN (SELECT ei.invoice_id FROM expense_invoices ei JOIN expenses e ON e.id=ei.expense_id JOIN persons p ON p.id=e.person_id WHERE p.name=? OR p.id=?)');args.extend([person,person])
            else:raise ValueError('person filter supported for persons, expenses, invoices, invoice_items, expense_invoices')
        if invoice_number is not None:
            if table in ('invoices','finance_observations'):where.append('t.invoice_number=?');args.append(invoice_number)
            elif table in ('invoice_items','expense_invoices','invoice_documents'):
                where.append('t.invoice_id IN (SELECT id FROM invoices WHERE invoice_number=?)');args.append(invoice_number)
            else:raise ValueError('invoice filter unsupported for selected table')
        condition=' WHERE '+' AND '.join(where) if where else ''
        total=conn.execute(f'SELECT COUNT(*) FROM {table} t{condition}',args).fetchone()[0]
        rows=[json.loads(r[0]) for r in conn.execute(f'SELECT t.data_json FROM {table} t{condition} ORDER BY t.id LIMIT ?',args+[limit])]
        return {'ok':True,'case_id':metadata['case_id'],'table':table,'total':total,'returned':len(rows),'truncated':total>len(rows),'records':rows,'system_write':False}
    finally:conn.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    q=sub.add_parser('query');q.add_argument('--database',required=True);q.add_argument('--case-id')
    q.add_argument('--table',choices=TABLES,default='expenses');q.add_argument('--person');q.add_argument('--invoice-number');q.add_argument('--record-id');q.add_argument('--limit',type=int,default=200)
    args=vars(ap.parse_args());args.pop('command')
    print(json.dumps(query(**args),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
