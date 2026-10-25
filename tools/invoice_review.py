"""只读串联原包展开、票面字段、按完整票号比对及表格交付；不删票、不核定申请额。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

from material_relations import scan
from scan_materials import extract_pdf, extract_ofd, candidates, SUPPORTED
from invoice_audit import fields_from_page, USTC
from invoice_face_fields import extract_face
from checklist_delivery import SCHEMA, ISSUE_COLUMNS, column_spec, write_issue_summary, validate_manifest

QUERY_NOTES = {'AVIATION_QUERY_AMOUNT_RULE_NEEDS_REVIEW','RAILWAY_QUERY_AMOUNT_RULE_NEEDS_REVIEW'}
LABELS = {
    'OCR_REQUIRED':'无原生文字，待复用识别或原图核读','IDENTIFIED':'完整票号已识别',
    'NOT_CONFIRMED_INVOICE':'未确认为发票，可能为辅助材料','INVOICE_NUMBER_NEEDS_REVIEW':'票号候选待核',
    'INVOICE_CODE_NEEDS_REVIEW':'旧票号对应代码待核','OCR_CONFIDENCE_NEEDS_REVIEW':'OCR置信度不足，待核',
    'UNLABELED_INVOICE_ID_NEEDS_REVIEW':'数字缺明确发票号码标签','INVOICE_ID_NEEDS_REVIEW':'完整票号未确定',
    'ISSUE_DATE_NEEDS_REVIEW':'开票日期未确定','TOTAL_NEEDS_REVIEW':'票面含税合计未确定',
    'BUYER_FIELDS_NEEDS_REVIEW':'购方字段未确定','EXPECTED_BUYER_MISMATCH':'购方名称或税号与本案主体不符',
    'BUYER_TAX_ID_INVALID':'购方税号格式或校验位不符','SELLER_TAX_ID_INVALID':'销方税号格式或校验位不符',
    'DOCUMENT_TYPE_NEEDS_REVIEW':'材料类别未确定','NET_AMOUNT_NEEDS_REVIEW':'不含税金额未确定',
    'AVIATION_QUERY_AMOUNT_RULE_NEEDS_REVIEW':'航空票官方验真的金额参数待核；已读含税合计另列',
    'RAILWAY_QUERY_AMOUNT_RULE_NEEDS_REVIEW':'铁路票官方验真的金额参数待核；已读票价另列',
    'pdf_text':'原生文字','ofd_text':'原生OFD文字','unreadable':'未取得可读文字',
    'manual_transcription':'原图人工核读抄录','remote_ocr':'复用远程OCR','local_ocr':'复用显式本机OCR',
}


def readable(values):
    return '；'.join(LABELS.get(v,v) for v in values)


def identity(audit, text, method):
    """明确票号才参加同票比较；金额、文件SHA与其他20位业务号不构成票据身份。"""
    if method == 'unreadable':
        return None, 'OCR_REQUIRED'
    if audit.get('kind') not in {'vat', 'aviation_vat', 'railway_vat', 'medical_fiscal'}:
        return None, 'NOT_CONFIRMED_INVOICE'
    parsed = candidates([{'text':text, 'method':method}], True)
    if parsed['issues'] or len(parsed['invoice_number_candidates']) != 1:
        return None, 'INVOICE_NUMBER_NEEDS_REVIEW'
    number = parsed['invoice_number_candidates'][0]['value']
    code = audit['verification']['fields']['invoice_code']
    if audit['kind'] == 'medical_fiscal':
        return (f'fiscal:{code}:{number}', 'IDENTIFIED') if code else (None, 'INVOICE_CODE_NEEDS_REVIEW')
    if len(number) == 20:
        return f'tax20:{number}', 'IDENTIFIED'
    return (f'tax8:{code}:{number}', 'IDENTIFIED') if code else (None, 'INVOICE_CODE_NEEDS_REVIEW')


def compare_pages(pages):
    groups = defaultdict(list)
    for page in pages:
        if page['invoice_identity']:
            groups[page['invoice_identity']].append(page)
    result = []
    for key, members in sorted(groups.items()):
        fields = ['total_amount', 'issue_date', 'buyer_tax_id', 'seller_tax_id']
        conflicts = [field for field in fields if len({p['audit'].get(field) for p in members if p['audit'].get(field) is not None}) > 1]
        # 某载体的大小写/税额矛盾会被金额解析器留空，不能因null被过滤就放过矛盾。
        if any('TOTAL_AMOUNT_CONFLICT' in p['audit'].get('issues', []) for p in members) and 'total_amount' not in conflicts:
            conflicts.append('total_amount')
        amount_known = all(p['audit'].get('total_amount') is not None for p in members)
        occurrences = sum(len(p['sources']) for p in members)
        status = '同号字段矛盾，待核' if conflicts else '票面金额未核定，待核' if not amount_known else '同票号多处出现' if occurrences > 1 else '不同票号独立保留'
        result.append(dict(invoice_identity=key, invoice_number=members[0]['audit']['verification']['fields']['invoice_number'],
                           source_occurrences=occurrences, unique_document_pages=len(members),
                           status=status, conflicts=conflicts,
                           page_ids=[p['page_id'] for p in members],
                           sources=[s for p in members for s in p['sources']],
                           amount=members[0]['audit'].get('total_amount') if not conflicts and amount_known else None))
    return result


def analyze(sources, case_id, profile='none', text_evidence=None):
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError('须明确case_id')
    if profile not in {'none','ustc'}:
        raise ValueError('抬头校验须明确none或ustc')
    original = [Path(p).resolve() for p in sources]
    source_evidence = [dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in original]
    recognized = {}
    if text_evidence:
        evidence_path=Path(text_evidence).resolve()
        data=json.loads(evidence_path.read_text(encoding='utf-8'))
        if data.get('schema')!='ustc-invoice-page-text-v1' or data.get('case_id')!=case_id:
            raise ValueError('识别文字须为同案ustc-invoice-page-text-v1')
        source_evidence.append(dict(path=str(evidence_path),sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest()))
        for row in data['records']:
            key=(row['source_sha256'],row['page'])
            evidence=Path(row['evidence_path'])
            if key in recognized or type(row['page']) is not int or row['page']<1 or row['method'] not in {'remote_ocr','local_ocr','manual_transcription'} or not isinstance(row['text'],str) or not row['text'].strip():
                raise ValueError('识别文字的页码、方法、文本或重复来源不合法')
            if not evidence.is_absolute() or not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest()!=row['evidence_sha256']:
                raise ValueError('识别文字证据不存在或SHA变化')
            recognized[key]=row
            source_evidence.append(dict(path=str(evidence),sha256=row['evidence_sha256']))
    used=set()
    with tempfile.TemporaryDirectory(prefix='invoice-review-') as temp:
        inventory = scan(original, temp)
        by_sha = defaultdict(list)
        for f in inventory['files']:
            if f['kind'] != 'archive':
                by_sha[(f['sha256'], Path(f['filename']).suffix.lower())].append(f)
        pages = []
        for (sha, suffix), files in by_sha.items():
            if suffix not in SUPPORTED:
                continue
            path = Path(files[0]['working_copy'])
            try:
                if suffix == '.pdf': extracted = extract_pdf(path, 'chi_sim+eng', 3)
                elif suffix == '.ofd': extracted = extract_ofd(path)
                else: extracted = [dict(page=1,method='unreadable',text='',error='OCR_REQUIRED')]
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                extracted = [dict(page=1,method='unreadable',text='',error=str(error))]
            for page in extracted:
                key=(sha,page['page'])
                imported=recognized.get(key)
                if imported:
                    if page['method']!='unreadable':raise ValueError('复用识别文字不覆盖已有原生文字页')
                    page={**page,'text':imported['text'],'method':imported['method'],'error':None}
                    used.add(key)
                audit = fields_from_page(page['text'], page['page'],
                    USTC['title'] if profile == 'ustc' else None, USTC['tax_id'] if profile == 'ustc' else None)
                key, state = identity(audit, page['text'], page['method'])
                if imported and imported['method'] in {'remote_ocr','local_ocr'} and (imported.get('mean_confidence') is None or imported['mean_confidence']<75):
                    key,state=None,'OCR_CONFIDENCE_NEEDS_REVIEW'
                face = extract_face(page['text'], invoice_type=audit.get('kind'))
                pages.append(dict(page_id=f'{sha}:p{page["page"]}', sha256=sha, page=page['page'],
                    sources=[f['source_chain'] for f in files],
                    source_directories=sorted({PurePosixPath(f['filename']).parent.name for f in files}),
                    method=page['method'], error=page.get('error'),
                    invoice_identity=key, identity_status=state, audit=audit, face=face,
                    text_evidence=imported))
        # Temporary extraction paths are not durable evidence or deliverables.
        for f in inventory['files']: f.pop('working_copy', None)
    if used!=set(recognized):raise ValueError('复用识别文字含未匹配的原件SHA或页码')
    groups = compare_pages(pages)
    return dict(schema='ustc-invoice-review-v1', case_id=case_id, profile=profile,
        sources=source_evidence, inventory=inventory, pages=pages, invoice_groups=groups,
        counts=dict(source_pdf_page_occurrences=len(inventory['pages']), unique_document_pages=len(pages),
                    identified_invoice_numbers=len(groups), same_number_groups=sum(g['source_occurrences']>1 for g in groups),
                    unreadable_pages=sum(p['method']=='unreadable' for p in pages),
                    file_copy_groups=len(inventory['exact_duplicate_sets']),reused_text_pages=len(used)),
        system_write=False, originals_changed=False, invoices_deleted=False,
        authenticity_verified=False, claim_amount=None,
        limitations=['同发票号码比较与同SHA文件副本分别记录；不同票号不按金额、酒店、日期或名称去重。',
                    '扫描页只列待识别，不隐式启动本地OCR；复用远程识别或原图审核后再确认号码。',
                    '不同票号的同费用重开/红冲另核，不直接据票号不同累计申请额。'])


def export_plan(report, report_path, title):
    problems = []
    details = []
    for p in report['pages']:
        a = p['audit']; q = a.get('verification', {}).get('fields', {})
        filename='；'.join(PurePosixPath(s.split('!/')[-1]).name for s in p['sources'])
        directory='；'.join(p['source_directories']).strip('；') or '原包根目录（未按人员分目录）'
        items = '；'.join(line['text'].strip() for line in p['face']['lines'] if line['text'].lstrip().startswith('*'))
        details.append(dict(zip(['文件名','来源目录','发票号码','价税合计/元','开票日期','购方名称','购方税号','销方名称','销方税号','发票代码',
            '不含税金额/元','税额/元','费用项目原文','票面备注候选','提取方式','身份识别状态','字段待核','验真准备提示','材料页','原件来源','原票号码引用'],
            [filename,directory,q.get('invoice_number'),a.get('total_amount'),a.get('issue_date'),a.get('buyer_title'),a.get('buyer_tax_id'),
             a.get('seller_title'),a.get('seller_tax_id'),q.get('invoice_code'),a.get('net_amount'),a.get('tax_amount'),items,
             '；'.join(r['raw_text'] for r in p['face']['remarks']) or None,readable([p['method']]),readable([p['identity_status']]),
             readable([i for i in a.get('issues',[]) if i not in QUERY_NOTES]),readable([i for i in a.get('issues',[]) if i in QUERY_NOTES]),
             p['page_id'],'；'.join(p['sources']),'；'.join(a.get('referenced_invoice_numbers',[]))])))
        travel=a.get('travel_fields',{});segments=travel.get('segments',[])
        details[-1].update({'票面乘机人':'；'.join(dict.fromkeys(s['traveler'] for s in segments if s.get('traveler'))),
            '票面行程':'；'.join(' '.join(str(s.get(k) or '未写明') for k in ['departure_date','departure_city','arrival_city','flight']) for s in segments),
            '票面舱位':'；'.join(s.get('seat_class') or '未写明' for s in segments),
            '行程待核字段':'；'.join(travel.get('missing_fields',[])) if segments else ''})
        issues=[i for i in a.get('issues',[]) if i not in QUERY_NOTES]
        if p['method']=='unreadable' or issues or (a.get('invoice_counted') and not p['invoice_identity']):
            problems.append(dict(zip(ISSUE_COLUMNS,[q.get('invoice_number') or f'材料核对{len(problems)+1}','待识别' if p['method']=='unreadable' else '字段待核',
                directory+'／'+filename,a.get('total_amount'),readable([p['identity_status'],*issues]),
                '原件：'+filename+'；完整来源、SHA与原文见逐页字段。',
                '复用远程OCR或原图确认完整票号后再比对。' if p['method']=='unreadable' else '核对原票对应字段；不按候选号删除或合并。'])))
    comparison = []
    for g in report['invoice_groups']:
        comparison.append(dict(zip(['发票号码','比较状态','票面金额/元','来源次数','不同材料页数','同号字段差异','来源页','原件来源'],
            [g['invoice_number'],g['status'],g['amount'],g['source_occurrences'],g['unique_document_pages'],'；'.join(g['conflicts']),
             '；'.join(g['page_ids']),'；'.join(g['sources'])])))
        if g['source_occurrences']>1:
            problems.append(dict(zip(ISSUE_COLUMNS,[g['invoice_number'],g['status'],'发票来源比对',g['amount'],
                f'同一完整号码在{g["source_occurrences"]}处出现', '完整票号为'+g['invoice_number']+'；字段差异：'+('；'.join(g['conflicts']) or '未发现'),
                '同号字段矛盾须核对原件；其余不同票号各自保留。原包不删，整理版本由经办确认。'])))
    copies = [dict(文件SHA256=g['sha256'],材料出现次数=len(g['occurrences']),判定='完全相同文件的多处来源；不是发票身份判据',来源编号='；'.join(g['occurrences'])) for g in report['inventory']['exact_duplicate_sets']]
    people = [dict(材料来源目录=name or '原包根目录（未按人员分目录）',正式收款人='未核定',正式申请额='未核定') for name in sorted({n for p in report['pages'] for n in p['source_directories']})]
    paper = [dict(纸质定位='本入口只审核给定电子原包，不含纸质实物定位',下一步='沿本案纸质清单核对，不等于缺纸票。')]
    def table(name, rows, keys):
        columns=[column_spec(k) for k in keys]
        for c in columns:
            if c['key'] in {'来源次数','不同材料页数','材料出现次数'}:c['type']='number'
        return dict(name=name,title=name,columns=columns,rows=rows)
    note=(f'按完整票号已识别{len(report["invoice_groups"])}张不同票；同号多处来源{report["counts"]["same_number_groups"]}组。'
          f'未识别扫描页{report["counts"]["unreadable_pages"]}页另列，不能当作不存在发票。'
          '文件副本与发票重复分开；不同票号即使同金额也保留。项目/备注为文字候选，未定位不代表空白。此识别范围不替代已核对清单、申请额或审批。')
    summary = table('问题总结',problems,ISSUE_COLUMNS);summary['note']=note
    sheets=[summary,table('人员核对',people,['材料来源目录','正式收款人','正式申请额']),
            table('票面逐页识别',details,list(details[0]) if details else ['材料页','发票号码']),
            table('纸质材料',paper,['纸质定位','下一步']),
            table('按号比对',comparison,list(comparison[0]) if comparison else ['发票号码','比较状态']),
            table('文件副本',copies,['文件SHA256','材料出现次数','判定','来源编号'])]
    return validate_manifest(dict(schema=SCHEMA,case_id=report['case_id'],title=title,note=note,system_write=False,
        sources=[*report['sources'],dict(path=str(report_path.resolve()),sha256=hashlib.sha256(report_path.read_bytes()).hexdigest())],sheets=sheets))


def generate(sources, output, case_id, profile='none', title='发票字段识别与按号比对', text_evidence=None):
    output = Path(output).resolve()
    if output.exists(): raise ValueError('输出目录已存在，须给新目录')
    report = analyze(sources,case_id,profile,text_evidence)
    output.mkdir(parents=True,exist_ok=False)
    path = output/'发票识别与比对.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    plan = export_plan(report,path,title)
    plan_path = output/'清单表格交付计划.json'
    plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for table in plan['sheets']:
        with (output/(table['name']+'.csv')).open('x',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);keys=[c['key'] for c in table['columns']];writer.writerow(keys)
            for row in table['rows']:
                writer.writerow(["'"+str(row[k]) if row[k] is not None and (str(row[k]).isdigit() and len(str(row[k]))>15 or str(row[k]).lstrip().startswith(('=','+','@'))) else row[k] for k in keys])
    write_issue_summary(plan,output)
    (output/'README.md').write_text('# '+title+'\n\n[问题总结](问题总结.html) · [表格交付计划](清单表格交付计划.json)\n\n'+plan['note']+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--case-id',required=True)
    parser.add_argument('--profile',choices=['none','ustc'],required=True)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--title',default='发票字段识别与按号比对')
    parser.add_argument('--text-evidence',type=Path,help='复用同SHA页的远程OCR或原图人工抄录，保留证据SHA')
    parser.add_argument('--node',help='给出现有Node即可继续导出Excel；不自动安装')
    parser.add_argument('--module-root',type=Path)
    args=parser.parse_args()
    try:
        if args.module_root and not args.node:raise ValueError('module-root须与node一起提供')
        report=generate(args.sources,args.output_dir,args.case_id,args.profile,args.title,args.text_evidence)
        workbook=False
        if args.node:
            output=args.output_dir.resolve()
            command=[sys.executable,str(Path(__file__).with_name('checklist_delivery.py')),'--input',str(output/'清单表格交付计划.json'),
                     '--output',str(output/'发票识别比对.xlsx'),'--node',args.node,'--qa-dir',str(output/'版式核验')]
            if args.module_root:command+=['--module-root',str(args.module_root.resolve())]
            if subprocess.run(command,check=False).returncode:parser.exit(1,'识别报告已保存，Excel未生成；沿原交付计划修复运行时后接续。\n')
            workbook=True
            with (output/'README.md').open('a',encoding='utf-8') as f:f.write('\n[打开发票识别比对Excel](发票识别比对.xlsx)\n')
        print(json.dumps(dict(ok=True,case_id=args.case_id,counts=report['counts'],output_dir=str(args.output_dir.resolve()),
                              workbook_created=workbook,system_write=False),ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as error:parser.exit(1,str(error)+'\n')


if __name__=='__main__': main()
