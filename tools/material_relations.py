"""只读盘点材料的来源、页数和重复关系；票号匹配仅为核对线索，不核定报销额。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import os
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

import pymupdf as fitz
from material_coverage import archive_name, digest_bytes


def rar_members(data):
    """用现有bsdtar只读RAR到字节流；成员名称不参与落盘路径。"""
    binary=shutil.which('bsdtar')
    if not binary:raise ValueError('RAR读取需要已安装的bsdtar；未展开不能记为材料已核完')
    with tempfile.TemporaryDirectory(prefix='reimbursement-rar-') as temp:
        path=Path(temp)/'input.rar';path.write_bytes(data)
        env={**os.environ,'LC_ALL':'en_US.UTF-8'}
        listing=subprocess.run([binary,'-tvf',str(path)],capture_output=True,text=True,encoding='utf-8',env=env,timeout=60)
        if listing.returncode:raise ValueError('RAR目录读取失败；未展开不能跳过')
        names=set();members=[]
        for line in listing.stdout.splitlines():
            values=line.split(maxsplit=8)
            if len(values)!=9 or not values[4].isdigit():raise ValueError('RAR目录格式未支持，不能静默忽略成员')
            mode,size,name=values[0],int(values[4]),values[8]
            if mode.startswith('d'):continue
            if not mode.startswith('-') or any(c in name for c in '\\*?[]\r\n'):
                raise ValueError('RAR含链接或不能精确匹配的成员名，须另行核对')
            if name in names:raise ValueError('RAR含同名成员，须另行区分')
            names.add(name)
            if size>100*1024**2:raise ValueError('RAR单成员超过100MB')
            members.append((name,size))
        if sum(size for _,size in members)>1024**3:raise ValueError('RAR展开总量超过1GB')
        for name,size in members:
            with tempfile.TemporaryFile() as content:
                result=subprocess.run([binary,'-xOf',str(path),'--',name],stdout=content,stderr=subprocess.PIPE,env=env,timeout=60)
                content.seek(0);payload=content.read(100*1024**2+1)
            if result.returncode or len(payload)!=size:raise ValueError('RAR成员读取失败或字节数不一致')
            yield name,payload


def fields(text):
    """保留候选及文本依据，不用模糊OCR票号自动认定同票。"""
    clean = unicodedata.normalize('NFKC', text)
    compact = re.sub(r'\s+', '', clean)
    # 不能删除换行后串接所有数字，否则金额、票号、客票号会拼成虚假的20位号码。
    numbers = sorted(set(re.findall(r'(?<!\d)\d{20}(?!\d)', clean)))
    dates = sorted(set(f'{y}-{int(m):02d}-{int(d):02d}' for y, m, d in
        re.findall(r'(20\d\d)[年/-](\d{1,2})[月/-](\d{1,2})', compact)))
    amount = r'(\d{1,9}(?:\.\d{1,2})?)(?![\d.])'
    yuan = sorted(set(re.findall(r'(?:[¥￥]|CNY)\s*' + amount, clean) +
        re.findall(r'(?<![\d.])' + amount + r'\s*[¥￥]', clean)))
    if '航空运输电子客票' in compact:
        category = '航空电子发票'
    elif '发票' in compact and (numbers or '价税合计' in compact):
        category = '发票'
    elif '行程单' in compact or 'ITINERARY' in clean or 'TRIP TABLE' in clean:
        category = '打车行程单'
    elif '登机凭证' in compact:
        category = '登机凭证'
    elif '行程校验单' in compact:
        category = '航旅校验单'
    elif '说明' in compact:
        category = '说明'
    else:
        category = '待核'
    return dict(invoice_candidates=numbers, date_candidates=dates,
                currency_amount_candidates=yuan, category=category)


def scan(sources, extract_dir=None):
    """递归读取ZIP/RAR，将副本按SHA落位，压缩包成员名不参与磁盘路径。"""
    files, pages = [], []
    total = 0
    extraction = Path(extract_dir).resolve() if extract_dir else None
    if extraction:
        extraction.mkdir(parents=True, exist_ok=True)

    def inspect(data, source, root_source, depth=0):
        nonlocal total
        total += len(data)
        if total > 1024 ** 3 or depth > 5:
            raise ValueError('展开总量超过1GB或嵌套超过5层，须分批核对')
        sha = digest_bytes(data)
        suffix = Path(source).suffix.lower()
        fid = hashlib.sha256(source.encode()).hexdigest()[:16]
        row = dict(occurrence_id=fid, source_chain=source, root_source=root_source,
                   filename=source.split('!/')[-1], sha256=sha, bytes=len(data),
                   kind='archive' if suffix in {'.zip','.rar'} else suffix.lstrip('.'), page_count=0)
        files.append(row)
        if extraction and suffix not in {'.zip','.rar'}:
            destination = extraction / (sha + suffix)
            if destination.exists() and digest_bytes(destination.read_bytes()) != sha:
                raise ValueError('SHA工作副本被改变：' + str(destination))
            if not destination.exists():
                destination.write_bytes(data)
            row['working_copy'] = str(destination)
        if suffix == '.zip':
            names = set()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    name = archive_name(member)
                    if member.is_dir() or '__MACOSX' in name or PurePosixPath(name).name == '.DS_Store':
                        continue
                    if name in names:
                        raise ValueError('同名成员须另行区分：' + source + '!/' + name)
                    names.add(name)
                    if member.file_size > 100 * 1024 ** 2:
                        raise ValueError('单成员超过100MB：' + name)
                    inspect(archive.read(member), source + '!/' + name, root_source, depth + 1)
        elif suffix == '.rar':
            for name,payload in rar_members(data):
                if '__MACOSX' in name or PurePosixPath(name).name=='.DS_Store':continue
                inspect(payload, source+'!/'+name,root_source,depth+1)
        elif suffix == '.pdf':
            with fitz.open(stream=data, filetype='pdf') as document:
                if document.needs_pass or not len(document):
                    raise ValueError('加密或空PDF：' + source)
                row['page_count'] = len(document)
                for index, page in enumerate(document):
                    native_text = page.get_text()
                    pages.append(dict(page_id=f'{fid}:p{index + 1}', occurrence_id=fid,
                        source_chain=source, root_source=root_source, sha256=sha,
                        page=index + 1, native_text=native_text, **fields(native_text)))
        return row

    for source in sources:
        path = Path(source).resolve()
        inspect(path.read_bytes(), str(path), str(path))
    exact = defaultdict(list)
    for row in files:
        if row['kind'] != 'archive':
            exact[row['sha256']].append(row['occurrence_id'])
    same_number = defaultdict(list)
    for page in pages:
        for number in page['invoice_candidates']:
            same_number[number].append(page['page_id'])
    return dict(files=files, pages=pages,
        exact_duplicate_sets=[dict(sha256=k, occurrences=v) for k, v in exact.items() if len(v) > 1],
        same_invoice_candidate_sets=[dict(invoice_number=k, pages=v) for k, v in same_number.items() if len(v) > 1])


def write_csv(path, rows, columns):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (' | '.join(map(str, value)) if isinstance(value, list) else value)
                for key, value in row.items()})


def compare_paper_ocr(pages, ocr_directory):
    """原生电子票号与纸质OCR同号仅作对应证据；未命中仍须原图复核。"""
    paper_pages = []
    index = defaultdict(list)
    for path in sorted(Path(ocr_directory).glob('pdf_*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        row = dict(page=int(record['page_or_order']), ocr_source=str(path.resolve()),
            image_source=record.get('source'), image_sha256=record.get('sha256'),
            text=record['text'], **fields(record['text']))
        paper_pages.append(row)
        for number in row['invoice_candidates']:
            index[number].append(row['page'])
    for page in pages:
        page['paper_same_invoice_pages'] = sorted({p for number in page['invoice_candidates']
            for p in index.get(number, [])})
    electron_index = defaultdict(list)
    for page in pages:
        for number in page['invoice_candidates']:
            electron_index[number].append(page['page_id'])
    for row in paper_pages:
        row['electronic_same_invoice_pages'] = sorted({p for number in row['invoice_candidates']
            for p in electron_index.get(number, [])})
    return paper_pages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', type=Path, nargs='+')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--extract-dir', type=Path)
    parser.add_argument('--paper-ocr', type=Path, help='可选既有pdf_*.json纸质OCR；本工具不执行OCR')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / 'material_inventory.json'
    if output.exists():
        raise ValueError('输出已存在，使用新的工作目录避免覆盖核对记录')
    result = scan(args.sources, args.extract_dir)
    if args.paper_ocr:
        result['paper_pages'] = compare_paper_ocr(result['pages'], args.paper_ocr)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_csv(args.output_dir / 'source_files.csv', result['files'],
        ['occurrence_id', 'root_source', 'source_chain', 'filename', 'sha256', 'bytes', 'kind', 'page_count', 'working_copy'])
    write_csv(args.output_dir / 'source_pages.csv', result['pages'],
        ['page_id', 'source_chain', 'page', 'sha256', 'category', 'invoice_candidates', 'date_candidates',
         'currency_amount_candidates', 'paper_same_invoice_pages', 'native_text'])
    if args.paper_ocr:
        write_csv(args.output_dir / 'paper_same_invoice.csv', result['paper_pages'],
            ['page', 'ocr_source', 'image_source', 'image_sha256', 'category', 'invoice_candidates',
             'electronic_same_invoice_pages', 'text'])
    print(json.dumps(dict(files=len(result['files']), pages=len(result['pages']),
        exact_duplicate_sets=len(result['exact_duplicate_sets']), output=str(output)), ensure_ascii=False))


if __name__ == '__main__':
    main()
