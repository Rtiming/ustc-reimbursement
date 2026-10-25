#!/usr/bin/env python3
"""Read-only, bounded relationship traces over ledger schema 1.

Paths are deliberately asymmetric: a person/expense never expands to another
expense through a shared invoice or document. Document pages constrain original
document links; a common carrier is not evidence of a common expense or purpose.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sqlite3

SCHEMA = 'ustc-reimbursement-ledger-trace-v1'
TABLES = ('persons', 'expenses', 'invoices', 'invoice_items', 'documents',
          'expense_invoices', 'expense_documents', 'invoice_documents',
          'finance_observations', 'issues', 'source_records')
LINK_TABLES = ('expense_invoices', 'expense_documents', 'invoice_documents')
ENTITY_TABLES = tuple(t for t in TABLES if t not in LINK_TABLES)


def _arguments(case_id, person, expense_id, invoice_number, document_id, page, limit):
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError('case_id must be a nonempty string')
    values = {'person': person, 'expense_id': expense_id,
              'invoice_number': invoice_number, 'document_id': document_id}
    supplied = [(key, value) for key, value in values.items() if value is not None]
    if len(supplied) != 1 or not isinstance(supplied[0][1], str) or not supplied[0][1].strip():
        raise ValueError('exactly one nonempty person/expense_id/invoice_number/document_id is required')
    if type(limit) is not int or not 1 <= limit <= 2000:
        raise ValueError('limit must be an integer in 1..2000')
    if page is not None and (supplied[0][0] != 'document_id' or type(page) is not int or page < 1):
        raise ValueError('page must be a positive integer and is only valid with document_id')
    return supplied[0]


def _plan(selector, page):
    """Fixed SQL only; all user input is carried separately as parameters."""
    anchor_table = {'person': 'persons', 'expense_id': 'expenses',
                    'invoice_number': 'invoices', 'document_id': 'documents'}[selector]
    anchor_where = {'person': '(id=:value OR name=:value)', 'expense_id': 'id=:value',
                    'invoice_number': 'invoice_number=:value', 'document_id': 'id=:value'}[selector]
    parts = [f'anchor AS (SELECT * FROM {anchor_table} WHERE {anchor_where})']
    page_clause = (" AND json_type(data_json,'$.page')='integer' AND json_extract(data_json,'$.page')=:page"
                   if page is not None else '')
    parts.extend([
        f'direct_ed AS (SELECT * FROM expense_documents WHERE document_id IN (SELECT id FROM anchor){page_clause})',
        f'direct_id AS (SELECT * FROM invoice_documents WHERE document_id IN (SELECT id FROM anchor){page_clause})',
    ])
    if selector == 'person':
        expense_where = 'person_id IN (SELECT id FROM anchor)'
    elif selector == 'expense_id':
        expense_where = 'id IN (SELECT id FROM anchor)'
    elif selector == 'invoice_number':
        expense_where = 'id IN (SELECT expense_id FROM expense_invoices WHERE invoice_id IN (SELECT id FROM anchor))'
    else:
        expense_where = ('id IN (SELECT expense_id FROM direct_ed UNION SELECT expense_id FROM expense_invoices '
                         'WHERE invoice_id IN (SELECT invoice_id FROM direct_id))')
    parts.append(f'scope_expenses AS (SELECT * FROM expenses WHERE {expense_where})')
    invoice_where = 'id IN (SELECT invoice_id FROM expense_invoices WHERE expense_id IN (SELECT id FROM scope_expenses))'
    if selector == 'invoice_number':
        invoice_where += ' OR id IN (SELECT id FROM anchor)'
    elif selector == 'document_id':
        invoice_where += ' OR id IN (SELECT invoice_id FROM direct_id)'
    parts.append(f'scope_invoices AS (SELECT * FROM invoices WHERE {invoice_where})')
    parts.append('scope_ei AS (SELECT * FROM expense_invoices WHERE expense_id IN (SELECT id FROM scope_expenses) '
                 'AND invoice_id IN (SELECT id FROM scope_invoices))')
    if selector == 'document_id':
        parts.extend(['scope_ed AS (SELECT * FROM direct_ed)', 'scope_id AS (SELECT * FROM direct_id)',
                      'scope_documents AS (SELECT * FROM anchor)'])
    else:
        parts.extend([
            'scope_ed AS (SELECT * FROM expense_documents WHERE expense_id IN (SELECT id FROM scope_expenses))',
            'scope_id AS (SELECT * FROM invoice_documents WHERE invoice_id IN (SELECT id FROM scope_invoices))',
            'scope_documents AS (SELECT * FROM documents WHERE id IN '
            '(SELECT document_id FROM scope_ed UNION SELECT document_id FROM scope_id))',
        ])
    person_where = 'id IN (SELECT person_id FROM scope_expenses)'
    if selector == 'person':
        person_where += ' OR id IN (SELECT id FROM anchor)'
    parts.append(f'scope_persons AS (SELECT * FROM persons WHERE {person_where})')
    sources = {'persons': 'scope_persons', 'expenses': 'scope_expenses', 'invoices': 'scope_invoices',
               'documents': 'scope_documents', 'expense_invoices': 'scope_ei',
               'expense_documents': 'scope_ed', 'invoice_documents': 'scope_id'}
    sources['invoice_items'] = '(SELECT * FROM invoice_items WHERE invoice_id IN (SELECT id FROM scope_invoices))'
    sources['finance_observations'] = '(SELECT * FROM finance_observations WHERE invoice_id IN (SELECT id FROM scope_invoices))'
    # Every explicit issue endpoint must be in scope. In particular, sharing a
    # file cannot admit an issue explicitly belonging to another person/expense.
    endpoints = {'person_id': 'scope_persons', 'expense_id': 'scope_expenses',
                 'invoice_id': 'scope_invoices', 'document_id': 'scope_documents'}
    issue_terms = [f'({key} IS NULL OR {key} IN (SELECT id FROM {scope}))' for key, scope in endpoints.items()]
    issue_terms.append('(' + ' OR '.join(f'{key} IS NOT NULL' for key in endpoints) + ')')
    if selector == 'document_id' and page is not None:
        issue_terms.append("(document_id IS NULL OR (json_type(data_json,'$.page')='integer' AND json_extract(data_json,'$.page')=:page))")
    elif selector != 'document_id':
        issue_terms.append("(document_id IS NULL OR json_extract(data_json,'$.page') IS NULL OR EXISTS ("
            "SELECT 1 FROM (SELECT document_id,data_json FROM scope_ed UNION ALL "
            "SELECT document_id,data_json FROM scope_id) AS linked "
            "WHERE linked.document_id=issues.document_id AND json_type(linked.data_json,'$.page')='integer' "
            "AND json_type(issues.data_json,'$.page')='integer' "
            "AND json_extract(linked.data_json,'$.page')=json_extract(issues.data_json,'$.page')))" )
    sources['issues'] = '(SELECT * FROM issues WHERE ' + ' AND '.join(issue_terms) + ')'
    return 'WITH ' + ',\n'.join(parts), anchor_table, sources


def _bucket(conn, cte, source, parameters, limit, transform=None):
    total = conn.execute(f'{cte} SELECT COUNT(*) FROM {source}', parameters).fetchone()[0]
    rows = [json.loads(row[0]) for row in conn.execute(
        f'{cte} SELECT data_json FROM {source} ORDER BY id LIMIT :limit', {**parameters, 'limit': limit})]
    if transform:
        rows = [transform(row) for row in rows]
    return {'total': total, 'returned': len(rows), 'truncated': total > len(rows), 'records': rows}


def trace(database, *, case_id, person=None, expense_id=None, invoice_number=None,
          document_id=None, page=None, limit=200):
    """Trace existing evidence links without computing fees or expanding a graph.

    ``limit`` is per entity/link group and per document's page records. Full
    counts describe the fixed scope even if a returned group is truncated.
    The caller registers/verifies the database SHA; this low-level reader checks
    case/schema and opens only an immutable, read-only SQLite snapshot.
    """
    selector, value = _arguments(case_id, person, expense_id, invoice_number, document_id, page, limit)
    path = Path(database).resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        metadata = {key: json.loads(raw) for key, raw in conn.execute('SELECT key,value_json FROM metadata')}
        if metadata.get('schema_version') != 1 or metadata.get('case_id') != case_id:
            raise ValueError('ledger schema_version or case_id mismatch')
        cte, anchor_table, sources = _plan(selector, page)
        parameters = {'value': value, 'page': page}
        scopes = {}
        for source in ('scope_ed', 'scope_id'):
            for did, link_page, page_type in conn.execute(
                    f"{cte} SELECT document_id,json_extract(data_json,'$.page'),json_type(data_json,'$.page') FROM {source}", parameters):
                record = scopes.setdefault(did, {'pages': set(), 'unscoped': 0})
                if page_type == 'integer' and type(link_page) is int and link_page >= 1:
                    record['pages'].add(link_page)
                else:
                    record['unscoped'] += 1

        def project_document(row):
            scoped = scopes.get(row['id'], {'pages': set(), 'unscoped': 0})
            all_pages = row.pop('pages', [])
            row.pop('raw_fields', None)
            if selector == 'document_id' and page is None:
                selected = all_pages
                mode = 'same_source_file_only'
            else:
                wanted = {page} if selector == 'document_id' else scoped['pages']
                selected = [p for p in all_pages if isinstance(p, dict) and type(p.get('page')) is int and p['page'] in wanted]
                mode = 'explicit_link_pages' if wanted else 'file_level_link_only'
            row['pages'] = selected[:limit]
            row['trace_page_scope'] = {'mode': mode, 'linked_pages': sorted(scoped['pages'])[:limit],
                                       'linked_pages_total': len(scoped['pages']),
                                       'linked_pages_truncated': len(scoped['pages']) > limit,
                                       'file_level_links': scoped['unscoped'], 'page_records_total': len(selected),
                                       'page_records_truncated': len(selected) > limit,
                                       'raw_fields_omitted': True}
            return row

        anchor = _bucket(conn, cte, 'anchor', parameters, limit,
                         project_document if selector == 'document_id' else None)
        entities = {}
        links = {}
        for table, source in sources.items():
            group = _bucket(conn, cte, source, parameters, limit, project_document if table == 'documents' else None)
            (links if table in LINK_TABLES else entities)[table] = group

        # Only source metadata is returned; raw source snapshots contain whole
        # input bundles and would leak unrelated expenses into a narrow trace.
        source_selects = [f"SELECT refs.value AS id FROM {source} AS t, "
                          "json_each(t.data_json,'$.source_record_ids') AS refs WHERE refs.type='text'"
                          for source in sources.values()]
        source_cte = cte + ', scoped_source_ids AS (' + ' UNION '.join(source_selects) + ')'

        def project_source(record):
            record.pop('raw', None)
            record['raw_omitted'] = True
            return record

        entities['source_records'] = _bucket(conn, source_cte,
            '(SELECT * FROM source_records WHERE id IN (SELECT id FROM scoped_source_ids))',
            parameters, limit, project_source)
        unscoped = None
        if selector == 'document_id' and page is not None:
            unscoped = {}
            for table in ('expense_documents', 'invoice_documents'):
                unscoped[table] = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE document_id=? "
                    "AND (json_type(data_json,'$.page') IS NULL OR json_type(data_json,'$.page')!='integer' "
                    "OR json_extract(data_json,'$.page')<1)", (value,)).fetchone()[0]
        groups = [anchor, *entities.values(), *links.values()]
        page_truncated = any(row.get('trace_page_scope', {}).get('page_records_truncated') or
                             row.get('trace_page_scope', {}).get('linked_pages_truncated')
                             for row in entities['documents']['records'])
        return {'ok': True, 'schema': SCHEMA, 'case_id': case_id,
                'anchor': {'selector': selector, 'value': value, 'page': page, 'table': anchor_table,
                           'multiple_matches': anchor['total'] > 1, **anchor},
                'entities': entities, 'links': links, 'limit_per_group': limit,
                'truncated': page_truncated or any(group['truncated'] for group in groups),
                'scope': {'strategy': 'fixed_evidence_paths_no_bfs',
                          'expense_expansion': 'person_or_expense_locked' if selector in ('person', 'expense_id') else 'direct_anchor_links_only',
                          'document_context': 'same_source_file_only' if selector == 'document_id' and page is None else 'explicit_page_and_role_links',
                          'unlocated_document_links_not_attributed_to_page': unscoped,
                          'invoice_versions': 'all_versions_of_scoped_expenses_without_reexpanding_expenses',
                          'finance_observations': 'stored_history_only_not_current_verification_or_payment_confirmation',
                          'same_file_does_not_imply_same_expense_or_purpose': True,
                          'source_records_raw_omitted': True},
                'amounts_computed': False, 'system_write': False}
    finally:
        conn.close()


def build_relationship_tables(data):
    """Return two workbook tables: one row per stored link, never a cross join.

    Multiple document links of an invoice stay together in one cell. Document
    rows distinguish direct expense links from invoice links, retaining page,
    role and original evidence. Amounts are deliberately absent.
    """
    if data.get('schema_version') != 1:
        raise ValueError('unsupported schema_version')
    indexed = {}
    for table in ('persons', 'expenses', 'invoices', 'documents'):
        indexed[table] = {}
        for row in data[table]:
            if not isinstance(row.get('id'), str) or not row['id'] or row['id'] in indexed[table]:
                raise ValueError('invalid or duplicate ' + table + ' id')
            indexed[table][row['id']] = row
    people, expenses, invoices, documents = (indexed[t] for t in ('persons', 'expenses', 'invoices', 'documents'))
    for expense in expenses.values():
        if expense.get('person_id') not in people:
            raise ValueError('unknown person in expense')
    for table, fields in (
            ('expense_invoices', {'expense_id': expenses, 'invoice_id': invoices}),
            ('expense_documents', {'expense_id': expenses, 'document_id': documents}),
            ('invoice_documents', {'invoice_id': invoices, 'document_id': documents})):
        seen = set()
        for link in data[table]:
            if not isinstance(link.get('id'), str) or not link['id'] or link['id'] in seen:
                raise ValueError('invalid or duplicate ' + table + ' id')
            seen.add(link['id'])
            if any(link.get(field) not in index for field, index in fields.items()):
                raise ValueError('unknown endpoint in ' + table)
    expense_links = {}
    document_links = {}
    for link in data['expense_invoices']:
        expense_links.setdefault(link['invoice_id'], []).append(link)
    for link in data['invoice_documents']:
        document_links.setdefault(link['invoice_id'], []).append(link)

    def name(eid):
        return people[expenses[eid]['person_id']].get('name')

    def evidence(link):
        return {key: copy.deepcopy(link[key]) for key in ('source_refs', 'source_record_ids', 'basis', 'evidence') if key in link}

    invoice_rows = []
    for iid, invoice in sorted(invoices.items()):
        carriers = [{'document_id': link['document_id'], 'page': link.get('page'), 'role': link.get('role'),
                     'link_id': link['id'], 'source_basis': evidence(link)}
                    for link in sorted(document_links.get(iid, []), key=lambda r: r['id'])]
        for link in sorted(expense_links.get(iid, []), key=lambda r: r['id']) or [None]:
            eid = link['expense_id'] if link else None
            invoice_rows.append([iid, invoice.get('invoice_number'), invoice.get('invoice_type'), eid,
                                 name(eid) if eid else None, link.get('relation') if link else '未关联费用',
                                 carriers, evidence(link) if link else {}, link['id'] if link else None])
    material_rows = []
    linked_documents = set()
    for table in ('expense_documents', 'invoice_documents'):
        for link in sorted(data[table], key=lambda r: r['id']):
            did = link['document_id']
            if did not in documents:
                raise ValueError('unknown document in relation')
            linked_documents.add(did)
            eid, iid = link.get('expense_id'), link.get('invoice_id')
            invoice_expenses = [{'expense_id': row['expense_id'], 'person_id': expenses[row['expense_id']]['person_id'],
                                 'person_name': name(row['expense_id']), 'link_id': row['id']}
                                for row in sorted(expense_links.get(iid, []), key=lambda r: r['id'])] if iid else []
            material_rows.append([did, table, eid, name(eid) if eid else None, iid,
                                  invoices[iid].get('invoice_number') if iid else None, link.get('page'), link.get('role'),
                                  link.get('relation'), invoice_expenses, evidence(link), link['id']])
    for did in sorted(set(documents) - linked_documents):
        material_rows.append([did, '未关联', None, None, None, None, None, None, None, [], {}, None])
    tables = [
        {'name': '票据反向对应', 'headers': ['票据ID', '票据号码', '票种', '费用ID', '登记人', '关系',
                                         '材料及页码角色', '费用关联依据', '费用票据关联ID'],
         'rows': invoice_rows, 'money_columns': [], 'widths': [],
         'note': '每行一条已有费用与票据关系；多份材料保留在同一单元格。新旧票版及共用票据不重复计算费用。'},
        {'name': '材料反向对应', 'headers': ['材料ID', '关联类型', '直接费用ID', '直接登记人', '票据ID', '票据号码',
                                         '页码', '材料角色', '关系', '经票据明确关联的费用', '来源依据', '材料关联ID'],
         'rows': material_rows, 'money_columns': [], 'widths': [],
         'note': '每行一条原材料关系；无页码只证明文件层联系。共享扫描文件不证明同一用途，票据关联费用列表不产生新的费用材料关系。'},
    ]
    for table in tables:
        table['rows'] = [[json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
                          if isinstance(value, (dict, list)) else value for value in row]
                         for row in table['rows']]
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--case-id', required=True)
    selector = parser.add_mutually_exclusive_group(required=True)
    for key in ('person', 'expense-id', 'invoice-number', 'document-id'):
        selector.add_argument('--' + key)
    parser.add_argument('--page', type=int)
    parser.add_argument('--limit', type=int, default=200)
    print(json.dumps(trace(**vars(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
