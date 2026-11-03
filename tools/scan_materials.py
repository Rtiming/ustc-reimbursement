#!/usr/bin/env python3
"""只读提取材料文字；默认不启动OCR，扫描页交远程识别或显式允许的少量本地OCR。"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path
import safe_xml as ElementTree

VERSION = '0.2.0'
SUPPORTED = {'.pdf','.ofd','.png','.jpg','.jpeg','.tif','.tiff','.webp'}


def run(command, timeout=45):
    # Poppler/Tesseract输出UTF-8，不能跟随Windows当前ANSI代码页解码。
    proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip()[-800:] or f'{command[0]} failed')
    return proc.stdout


def ocr_image(path, language, psm):
    if not shutil.which('tesseract'):
        raise RuntimeError('未安装Tesseract：需要配置本地OCR，或标记模型/人工复核')
    raw = run(['tesseract',str(path),'stdout','-l',language,'--psm',str(psm),'tsv'])
    words=[]
    for row in csv.DictReader(io.StringIO(raw), delimiter='\t'):
        if row.get('level')!='5' or not row.get('text','').strip(): continue
        words.append({key:row[key] for key in ['page_num','block_num','par_num','line_num','left','top','width','height','conf','text']})
    lines={}
    for w in words:
        key=tuple(w[k] for k in ['page_num','block_num','par_num','line_num'])
        lines.setdefault(key,[]).append(w['text'])
    text='\n'.join(' '.join(v) for v in lines.values())
    confidence=[float(w['conf']) for w in words if float(w['conf'])>=0]
    return {'method':'tesseract','text':text,'words':words,
            'mean_confidence':round(sum(confidence)/len(confidence),2) if confidence else None}


def extract_pdf(path, language, psm, allow_local_ocr=False):
    pages=[]
    if shutil.which('pdftotext'):
        text=run(['pdftotext','-enc','UTF-8','-layout',str(path),'-'])
        chunks=text.split('\f')
        if chunks and not chunks[-1].strip():chunks.pop()
    else:
        try:
            from pypdf import PdfReader
            chunks=[p.extract_text() or '' for p in PdfReader(path).pages]
        except ImportError:
            # 分发包已提供PyMuPDF；文字层读取不再隐含依赖系统Poppler或额外pypdf。
            import pymupdf
            with pymupdf.open(path) as document:
                chunks=[page.get_text() for page in document]
    for i,text in enumerate(chunks,1):
        if len(re.sub(r'\s','',text))>=15:
            pages.append({'page':i,'method':'pdf_text','text':text,'mean_confidence':None})
            continue
        if not allow_local_ocr:
            pages.append({'page':i,'method':'unreadable','text':text,'mean_confidence':None,
                          'error':'OCR_REQUIRED: 本机默认只读文字层；请用remote_material_ocr或显式允许少量本地OCR'})
            continue
        try:
            if not shutil.which('pdftoppm'): raise RuntimeError('扫描PDF页渲染需要pdftoppm')
            with tempfile.TemporaryDirectory(prefix='reimbursement-page-') as td:
                stem=Path(td)/'page'
                run(['pdftoppm','-f',str(i),'-l',str(i),'-r','250','-png','-singlefile',str(path),str(stem)])
                data=ocr_image(stem.with_suffix('.png'),language,psm)
                pages.append({'page':i,**data})
        except (RuntimeError,subprocess.TimeoutExpired) as exc:
            pages.append({'page':i,'method':'unreadable','text':text,'error':str(exc),'mean_confidence':None})
    return pages


def extract_ofd(path):
    pages=[]
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if re.search(r'Pages/Page_\d+/Content.xml$',n)]
        names.sort(key=lambda n:int(re.search(r'Page_(\d+)',n).group(1)))
        for i,name in enumerate(names,1):
            info=z.getinfo(name)
            if info.file_size>10*1024*1024:raise RuntimeError('OFD XML异常大，留待复核')
            node=ElementTree.from_zip(z, name)
            text='\n'.join((x.text or '') for x in node.iter() if x.tag.rsplit('}',1)[-1]=='TextCode')
            pages.append({'page':i,'method':'ofd_text' if text.strip() else 'unreadable','text':text,'mean_confidence':None})
    return pages


def candidates(pages, invoice_mode):
    # 原始文字保留在pages中；身份比对把铁路票全角数字归一化为ASCII。
    text=unicodedata.normalize('NFKC','\n'.join(p['text'] for p in pages))
    ids=[]
    # 优先使用明确字段，不能用业务流水号抢占已标注的财政票号。
    all_labeled = list(re.finditer(r'发[ \t]*票[ \t]*(?:号[ \t]*码|号)\s*[:：]?\s*(\d{20}|\d{8})(?!\d)', text))
    referenced=[m for m in all_labeled if re.search(r'(?:原|原始|红冲|作废|对应)[ \t]*$',text[max(0,m.start()-8):m.start()])]
    labeled=[m for m in all_labeled if m not in referenced]
    fiscal = '医疗门诊收费票据' in text
    if fiscal:
        labeled += list(re.finditer(r'(?<!电子)票据号码\s*[:：]?\s*(\d{10})(?!\d)', text))
    matches = labeled or [m for m in re.finditer(r'(?<!\d)\d{20}(?!\d)', text)
                          if not any(r.start()<=m.start()<r.end() for r in referenced)]
    for match in matches:
        value=match.group(1) if labeled else match.group(0)
        if not any(x['value']==value for x in ids):
            ids.append({'value':value,'context':text[max(0,match.start()-40):match.end()+40].strip()})
    amount=[{'value':m.group(1).replace(',',''),'context':text[max(0,m.start()-35):m.end()+20].strip()}
            for m in re.finditer(r'(?:[¥￥]\s*|价税合计[^\n]{0,24}?)(\d[\d,]*\.\d{2})',text)]
    if fiscal:
        amount += [{'value':m.group(1).replace(',',''),'context':text[max(0,m.start()-35):m.end()+20].strip()}
                   for m in re.finditer(r'\(\s*小\s*写\s*\)\s*[¥￥]?\s*(\d[\d,]*\.\d{2})',text)]
    issues=[]
    if not text.strip():issues.append('NO_RECOGNIZED_TEXT')
    if any(p['method']=='unreadable' for p in pages):issues.append('UNREADABLE_PAGE')
    if any(p.get('mean_confidence') is not None and p['mean_confidence']<75 for p in pages):issues.append('LOW_OCR_CONFIDENCE')
    if invoice_mode and len(ids)>1:issues.append('MULTIPLE_INVOICE_NUMBER_CANDIDATES')
    if invoice_mode and not ids:issues.append('INVOICE_ID_NEEDS_REVIEW')
    if invoice_mode and ids and not labeled:issues.append('UNLABELED_INVOICE_ID_NEEDS_REVIEW')
    return {'invoice_number_candidates':ids,'referenced_invoice_number_candidates':list(dict.fromkeys(m.group(1) for m in referenced)),
            'amount_candidates':amount[:20],'issues':issues,
            'one_invoice_per_file':'needs_review' if issues else ('one_candidate' if invoice_mode else 'not_evaluated')}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('paths',nargs='+',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--cache',type=Path,default=Path('generated/ocr_cache'))
    ap.add_argument('--language',default='chi_sim+eng')
    ap.add_argument('--psm',type=int,default=3)
    ap.add_argument('--invoices',action='store_true',help='输入均为发票时启用一票一文件候选检查')
    ap.add_argument('--allow-local-ocr',action='store_true',help='仅明确授权少量本地OCR时启用；大批扫描交remote_material_ocr')
    args=ap.parse_args();args.cache.mkdir(parents=True,exist_ok=True)
    files=[]
    for path in args.paths:
        files.extend(sorted(p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in SUPPORTED) if path.is_dir() else [path])
    files=list(dict.fromkeys(p.resolve() for p in files));records=[];start=time.perf_counter()
    versions={'local_ocr_enabled':args.allow_local_ocr,
              'tesseract':run(['tesseract','--version']).splitlines()[0] if args.allow_local_ocr and shutil.which('tesseract') else None}
    for path in files:
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        key=hashlib.sha256(f'{VERSION}:{digest}:{args.language}:{args.psm}:{versions}'.encode()).hexdigest()
        cache=args.cache/(key+'.json');hit=cache.exists()
        if hit:data=json.loads(cache.read_text())
        else:
            try:
                if path.suffix.lower()=='.pdf':pages=extract_pdf(path,args.language,args.psm,args.allow_local_ocr)
                elif path.suffix.lower()=='.ofd':pages=extract_ofd(path)
                elif path.suffix.lower() in SUPPORTED:
                    if not args.allow_local_ocr: raise RuntimeError('OCR_REQUIRED: 图片须交远程OCR或显式允许少量本地OCR')
                    pages=[{'page':1,**ocr_image(path,args.language,args.psm)}]
                else:raise RuntimeError('文件格式不支持')
                data={'sha256':digest,'pages':pages,'error':None,'versions':versions}
            except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired,zipfile.BadZipFile,ElementTree.ParseError) as exc:
                data={'sha256':digest,'pages':[],'error':str(exc),'versions':versions}
            # 识别失败不缓存为成功，配置恢复后下一次会自动重新尝试。
            if not data['error'] and not any(p['method']=='unreadable' for p in data['pages']):cache.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        record={'path':str(path),'cache_hit':hit,'cache_file':str(cache.resolve()) if cache.exists() else None,**data,
                **candidates(data['pages'],args.invoices)}
        if data['error']:record['issues'].append('EXTRACTION_FAILED')
        records.append(record)
    output={'version':VERSION,'elapsed_seconds':round(time.perf_counter()-start,3),'files':len(records),
            'cache_hits':sum(r['cache_hit'] for r in records),'needs_review':sum(bool(r['issues']) for r in records),'records':records}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k!='records'},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
