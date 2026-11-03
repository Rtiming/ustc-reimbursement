#!/usr/bin/env python3
"""只读检索本地表格；返回文件、工作表、单元格和原值。姓名查询仅按标注的相邻学号列关联。"""
import argparse
import csv
import hashlib
import json
import posixpath
import zipfile
from pathlib import Path
import safe_xml as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
SUPPORTED = {'.xlsx', '.xlsm', '.csv', '.tsv'}


def column_number(cell):
    value = 0
    for ch in cell:
        if not ch.isalpha():
            break
        value = value * 26 + ord(ch.upper()) - 64
    return value


def column_name(number):
    value = ''
    while number:
        number, digit = divmod(number - 1, 26)
        value = chr(65 + digit) + value
    return value


def xml_member(z, name):
    if z.getinfo(name).file_size > 64 * 1024 * 1024:
        raise ValueError('表格XML超过64MB，需限定或分拆来源')
    return ET.from_zip(z, name)


def sheets(path):
    if path.suffix.lower() in {'.csv', '.tsv'}:
        with path.open(encoding='utf-8-sig', newline='') as f:
            data = csv.reader(f, delimiter='\t' if path.suffix.lower() == '.tsv' else ',')
            yield path.stem, [{f'{column_name(c)}{r}': {'value': v, 'formula': False} for c, v in enumerate(row, 1)} for r, row in enumerate(data, 1)]
        return
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
            raise ValueError('表格解压体积超过200MB，需限定或分拆来源')
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [''.join(t.text or '' for t in item.findall('.//s:t', NS)) for item in xml_member(z, 'xl/sharedStrings.xml').findall('s:si', NS)]
        relations = {r.get('Id'): r.get('Target') for r in xml_member(z, 'xl/_rels/workbook.xml.rels') if r.get('TargetMode') != 'External'}
        workbook = xml_member(z, 'xl/workbook.xml')
        for sheet in workbook.findall('s:sheets/s:sheet', NS):
            target = relations[sheet.get(REL)]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            root = xml_member(z, member)
            rows = []
            for row in root.findall('s:sheetData/s:row', NS):
                values = {}
                for cell in row.findall('s:c', NS):
                    raw = cell.find('s:v', NS)
                    value = raw.text or '' if raw is not None else ''
                    if cell.get('t') == 's':
                        value = shared[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    values[cell.get('r')] = {'value': value, 'formula': cell.find('s:f', NS) is not None}
                rows.append(values)
            yield sheet.get('name'), rows


def search(paths, query, exact=False, person_lookup=False, limit=30):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('提供非空查询词')
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('limit范围为1至500')
    files = []
    for value in paths:
        p = Path(value).resolve()
        if not p.exists():
            raise ValueError('表格来源不存在：' + str(p))
        if p.is_dir():
            files.extend(sorted(x for x in p.rglob('*') if x.is_file() and x.suffix.lower() in SUPPORTED and not x.name.startswith('~$')))
        elif p.suffix.lower() in SUPPORTED:
            files.append(p)
        else:
            raise ValueError('仅支持xlsx/xlsm/csv/tsv；旧xls需先另存为xlsx')
    files = list(dict.fromkeys(files))
    if len(files) > 2000:
        raise ValueError('来源超过2000个表格，请缩小目录')
    hits = []
    warnings = []
    total = 0
    for file in files:
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        try:
            for sheet_name, rows in sheets(file):
                headers = {}
                for row in rows:
                    for cell, record in row.items():
                        v = record['value'].strip()
                        if v in ('姓名', '学号', '学工号'):
                            headers[column_number(cell)] = v
                    for cell, record in row.items():
                        value = record['value']
                        matched = value.strip() == query.strip() if exact or person_lookup else query.casefold() in value.casefold()
                        if matched:
                            hit = {'path': str(file), 'sha256': sha, 'sheet': sheet_name, 'cell': cell, 'value': value, 'cached_formula_value': record['formula']}
                            if person_lookup:
                                col = column_number(cell)
                                if headers.get(col) != '姓名' or headers.get(col + 1) not in ('学号', '学工号'):
                                    continue
                                id_cell = column_name(col + 1) + ''.join(c for c in cell if c.isdigit())
                                hit.update(student_id=row.get(id_cell, {}).get('value', ''), student_id_cell=id_cell)
                            total += 1
                            if len(hits) < limit:
                                hits.append(hit)
        except (OSError, ValueError, KeyError, IndexError, ET.ParseError, zipfile.BadZipFile) as exc:
            warnings.append({'path': str(file), 'error': str(exc)})
    pairs = sorted({(x['value'].strip(), x['student_id']) for x in hits if x.get('student_id')}) if person_lookup else []
    return {'ok': not warnings, 'query': query, 'files_scanned': len(files), 'total': total, 'returned': len(hits), 'truncated': total > len(hits), 'matches': hits, 'unique_name_student_id_pairs': pairs, 'warnings': warnings, 'read_only': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--query', required=True)
    parser.add_argument('--exact', action='store_true')
    parser.add_argument('--person', action='store_true', help='只查姓名并关联明确标注的相邻学号列；同名多学号须进一步核对')
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(search(args.paths, args.query, args.exact, args.person, args.limit), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
