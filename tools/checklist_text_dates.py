"""保留清单文本日期原文，修正表格SDK导出时的自动日期转换。"""
from __future__ import annotations
import argparse
import html
import json
import os
from pathlib import Path
import posixpath
import re
import tempfile
import zipfile
import safe_xml as ET

DATE_TEXT = re.compile(r'^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$')
SS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def column_name(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def restore_text_dates(workbook, plan):
    if plan.get('schema') != 'ustc-checklist-delivery-v1' or plan.get('system_write') is not False:
        raise ValueError('不是只读清单交付计划')
    workbook = Path(workbook)
    with zipfile.ZipFile(workbook) as source:
        contents = {item.filename: ET.read_zip_member(source, item.filename) for item in source.infolist()}
        entries = source.infolist()
    wb = ET.fromstring(contents['xl/workbook.xml'])
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(contents['xl/_rels/workbook.xml.rels'])}
    sheets = wb.findall(f'{{{SS}}}sheets/{{{SS}}}sheet')
    if [s.attrib['name'] for s in sheets] != [s['name'] for s in plan['sheets']]:
        raise ValueError('工作表与交付计划不一致，停止文本修正')
    changed = 0
    for native, spec in zip(sheets, plan['sheets']):
        target = rels[native.attrib[f'{{{REL}}}id']]
        filename = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        xml = contents[filename].decode('utf-8')
        for row_index, row in enumerate(spec['rows'], 6):
            for col_index, col in enumerate(spec['columns'], 1):
                value = row[col['key']]
                if col['type'] != 'text' or not isinstance(value, str) or not DATE_TEXT.fullmatch(value):
                    continue
                address = column_name(col_index) + str(row_index)
                pattern = re.compile(r'<(?P<tag>(?:[A-Za-z_][\w.-]*:)?c)\b(?P<attrs>[^>]*\br=[\"\']' + address + r'[\"\'][^>]*)>(?P<body>.*?)</(?P=tag)>', re.S)
                match = pattern.search(xml)
                if not match or re.search(r'<(?:[A-Za-z_][\w.-]*:)?f(?:\s|>)', match.group('body')):
                    raise ValueError('文本日期目标缺失或含公式：' + address)
                attributes = re.sub(r'\s+t=[\"\'][^\"\']*[\"\']', '', match.group('attrs'))
                tag = match.group('tag')
                prefix = tag[:-1]
                replacement = '<' + tag + attributes + ' t="inlineStr"><' + prefix + 'is><' + prefix + 't xml:space="preserve">' + html.escape(value, quote=False) + '</' + prefix + 't></' + prefix + 'is></' + tag + '>'
                if replacement != match.group(0):
                    xml = xml[:match.start()] + replacement + xml[match.end():]
                    changed += 1
        contents[filename] = xml.encode('utf-8')
    if changed:
        handle, temporary = tempfile.mkstemp(prefix='.checklist-text-', suffix='.xlsx', dir=workbook.parent)
        os.close(handle)
        try:
            with zipfile.ZipFile(temporary, 'w') as output:
                for entry in entries:
                    output.writestr(entry, contents[entry.filename])
            os.replace(temporary, workbook)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workbook', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'text_date_cells_restored': restore_text_dates(args.workbook, json.loads(args.plan.read_text(encoding='utf-8')))}))
