"""正式打印文件的交付层：逐单保留，按需原页合并，不重排财务内容。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import uuid

from assistant_api import APIError, read_json
from finance_pdf_export import _no_existing
from material_intake import reject_links


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def publish(path, raw):
    """同目录排他发布；中断不覆盖已有文件。"""
    path = Path(path)
    reject_links(path)
    _no_existing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / ('.' + uuid.uuid4().hex + '.part')
    try:
        with temp.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def deliver(rows, output_root, *, merge_pdf, finance_only):
    """仅消费已核验正式导出的路径/SHA；缺项保留在清单，不伪记整批齐全。"""
    if type(merge_pdf) is not bool or type(finance_only) is not bool:
        raise APIError('print_options', '两个打印开关必须分别使用布尔值')
    entries = [{k: row.get(k) for k in ('case_id', 'serial', 'summary', 'status',
               'pdf', 'pdf_sha256', 'issue')} for row in rows]
    successful = [r for r in entries if r['pdf']]
    if len({r['serial'] for r in entries}) != len(entries):
        raise APIError('print_duplicate_serial', '交付清单单号重复')
    for row in successful:
        path = Path(row['pdf'])
        reject_links(path)
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['pdf_sha256']:
            raise APIError('print_source_changed', '原打印文件缺失或字节改变：' + row['serial'])
    binding = {'schema': 'ustc-print-delivery-v1', 'merge_pdf': merge_pdf,
               'finance_only': finance_only, 'documents': entries}
    directory = Path(output_root) / '交付' / fingerprint(binding)[:20]
    receipt = directory / 'delivery.json'
    if receipt.exists():
        old = read_json(receipt)
        if old['binding'] != binding:
            raise APIError('print_delivery_binding', '已有交付绑定不同')
        for item in old['artifacts']:
            p = directory / item['name']
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != item['sha256']:
                raise APIError('print_delivery_changed', '已有交付文件缺失或变化，不覆盖')
        return {**old, 'reused': True}
    # 有文件而无完成回执时保留现场，不能覆盖中断产物。
    if directory.exists() and any(directory.iterdir()):
        raise APIError('print_delivery_incomplete', '交付目录已有未完成文件，须核对原目录：' + str(directory))
    artifacts, merged, toc = [], None, []
    if merge_pdf and successful:
        import pymupdf as fitz
        document = fitz.open()
        try:
            for row in successful:
                raw = Path(row['pdf']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != row['pdf_sha256']:
                    raise APIError('print_source_changed', '合并时原文件发生变化')
                with fitz.open(stream=raw, filetype='pdf') as source:
                    if source.needs_pass or source.page_count < 1:
                        raise APIError('print_merge_unreadable', 'PDF不能合并：' + row['serial'])
                    toc.append([1, row['serial'] + ' ' + (row['summary'] or ''), document.page_count + 1])
                    document.insert_pdf(source)
            document.set_toc(toc)
            raw = document.tobytes()
        finally:
            document.close()
        name = ('正式报销单_仅财务信息' if finance_only else '正式报销单_含附件')
        if len(successful) != len(entries):
            name += '_部分完成'
        name += '.pdf'
        merged = str(directory / name)
        publish(merged, raw)
        artifacts.append({'name': name, 'sha256': hashlib.sha256(raw).hexdigest()})
    table = io.StringIO(newline='')
    writer = csv.writer(table)
    writer.writerow(['单号', '摘要', '结果', '文件', '问题'])
    for row in entries:
        # 防止摘要被表格程序当成公式，单号保持文本。
        writer.writerow([("'" + str(row[k])) if str(row.get(k) or '').startswith(('=', '+', '-', '@'))
                         else row.get(k) or '' for k in ('serial', 'summary', 'status', 'pdf', 'issue')])
    raw = table.getvalue().encode('utf-8-sig')
    publish(directory / '打印清单.csv', raw)
    artifacts.append({'name': '打印清单.csv', 'sha256': hashlib.sha256(raw).hexdigest()})
    result = {'binding': binding, 'artifacts': artifacts, 'merged_pdf': merged,
              'individual_pdfs': [r['pdf'] for r in successful], 'bookmarks': toc,
              'exported_count': len(successful), 'held_count': len(entries) - len(successful),
              'complete': len(successful) == len(entries), 'receipt': str(receipt)}
    publish(receipt, json.dumps(result, ensure_ascii=False, indent=2).encode())
    return result
