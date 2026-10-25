"""只读提取票面字段、校验抬头税号，并准备分平台官方查验参数。"""
import argparse
import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from decimal import Decimal
from urllib.parse import urlparse
from scan_materials import extract_pdf, extract_ofd, ocr_image, candidates

USTC = {'title':'中国科学技术大学','tax_id':'12100000485001086E',
        'source':'https://finance.ustc.edu.cn/2017/1106/c21820a440902/page.htm','checked_on':'2026-10-12'}
TAX_URL = 'https://inv-veri.chinatax.gov.cn/fpcygzfw/'
FISCAL_URL = 'https://pjcy.mof.gov.cn/'
ANHUI_URL = 'http://czpj.ahzwfw.gov.cn:8888/'
ALPHABET = '0123456789ABCDEFGHJKLMNPQRTUWXY'
WEIGHTS = [1,3,9,27,19,26,16,17,20,29,25,13,8,24,10,30,28]
TAX_ID_LABEL = r'(?:统\s*一\s*社\s*会\s*信\s*用\s*代\s*码(?:\s*/\s*纳\s*税\s*人\s*识\s*别\s*号)?|纳\s*税\s*人\s*识\s*别\s*号)'


def normalize(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def check_tax_id(value):
    code = normalize(value).upper()
    if not code: return {'status':'missing','normalized':code}
    if len(code)==15 and code.isdigit(): return {'status':'legacy_format_only','normalized':code,'checksum_verified':False}
    if len(code)!=18 or any(c not in ALPHABET for c in code): return {'status':'invalid_format','normalized':code,'checksum_verified':False}
    expected=ALPHABET[(31-sum(ALPHABET.index(c)*w for c,w in zip(code[:17],WEIGHTS))%31)%31]
    return {'status':'checksum_passed' if code[-1]==expected else 'checksum_failed','normalized':code,
            'expected_check_digit':expected,'checksum_verified':code[-1]==expected,
            'note':'校验位通过只证明编码格式，不证明主体存在、抬头归属或票据真伪。'}


def header_tax_ids(text):
    """版面文字的代码可逐字符空格或在标签下一行；空的购方栏不能借用销方代码。"""
    table = re.search(r'项目\s*名称', text)
    header = text[:table.start()] if table else text
    labels = list(re.finditer(TAX_ID_LABEL+r'\s*[:：]', header))
    if len(labels) != 2:
        return None
    lines=header.splitlines()
    positions=[]
    for label in labels:
        row=header.count('\n',0,label.start())
        col=label.start()-(header.rfind('\n',0,label.start())+1)
        positions.append((row,col))
    # Some PDF generators put a value above its label in each of two columns.
    # Scope the neighboring lines to the original column, never to the other party.
    if positions[0][0]!=positions[1][0] and positions[1][1]-positions[0][1]>=20:
        boundary=positions[1][1]
        values=[]
        for index,(row,col) in enumerate(positions):
            low,high=(0,boundary) if index==0 else (boundary,None)
            value=None
            for neighbor in [row,row-1,row+1]:
                if not 0<=neighbor<len(lines):continue
                matches=[normalize(m.group(1)) for m in re.finditer(r'(?<![0-9A-Z])([0-9A-Z](?:[ \t]*[0-9A-Z]){14,17})(?![ \t]*[0-9A-Z])',lines[neighbor])
                         if (m.start()+m.end())/2>=low and (high is None or (m.start()+m.end())/2<high)]
                matches=[code for code in matches if len(code) in [15,18]]
                if len(matches)==1:value=matches[0];break
            values.append(value)
        return tuple(values)
    values = []
    for index, label in enumerate(labels):
        segment = header[label.end():labels[index + 1].start() if index + 1 < len(labels) else len(header)]
        codes = re.findall(r'(?<![0-9A-Z])([0-9A-Z](?:[ \t]*[0-9A-Z]){14,17})(?![ \t]*[0-9A-Z])', segment)
        normalized = [normalize(code) for code in codes if len(normalize(code)) in [15, 18]]
        values.append(normalized[0] if len(normalized) == 1 else None)
    return tuple(values)


def near_label_value(text, label, pattern=None):
    """只取本行标签后的值或上一行相近列；不跨到其他字段。"""
    lines=text.splitlines()
    for i,line in enumerate(lines):
        match=re.search(re.escape(label)+r'\s*[:：]',line)
        if not match:continue
        parts=[line[match.end():]]
        if i:parts.append(lines[i-1][max(0,match.start()-4):])
        for part in parts:
            if pattern:
                value=re.search(pattern,part)
                if value:return value.group(0)
            else:
                value=re.split(r'\s{2,}',part.strip(),maxsplit=1)[0]
                if value and ':' not in value and '：' not in value and not re.fullmatch(r'[0-9A-Z./-]+|\d{4}年\d{1,2}月\d{1,2}日',value):return value
    return None


def railway_fields(text):
    buyer=near_label_value(text,'购买方名称')
    code=re.search(r'统一社会信用代码(?:/纳税人识别号)?\s*[:：]\s*([0-9A-Z]{15,18})(?![0-9A-Z])',text)
    fare=re.search(r'票价\s*[:：]\s*[¥￥]\s*(\d+(?:\.\d{1,2})?)(?![\d.])',text)
    return dict(buyer_title=buyer,buyer_tax_id=code.group(1) if code else None,
                total_amount=f'{Decimal(fare.group(1)):.2f}' if fare else None)


def vat_aggregate_amounts(text):
    """部分PDF将合计数字排在“合计”标题前一行；只读该邻近行，不借用价税合计。"""
    lines=text.splitlines()
    for index,line in enumerate(lines):
        if not re.match(r'^\s*合\s*计(?:\s|[¥￥]|$)',line):
            continue
        match=re.findall(r'[¥￥]\s*(\d[\d,]*(?:\.\d{1,2})?)(?![\d.])',line)
        if not match and not normalize(line).replace('合计','') and index:
            match=re.findall(r'[¥￥]\s*(\d[\d,]*(?:\.\d{1,2})?)(?![\d.])',lines[index-1])
        if len(match)==2:
            return tuple(f'{Decimal(v.replace(",","")):.2f}' for v in match)
    return None,None


def aviation_fields(text):
    """航空行程单使用购买方、填开日期与CNY合计，不把航班日期当开票日。"""
    buyer=re.search(r'购买方名称\s*[:：]',text)
    buyer_section=text[buyer.start():] if buyer else ''
    code=re.search(r'统一社会信用代码/纳税人识别号\s*[:：]\s*([0-9A-Z](?:[ \t]*[0-9A-Z]){17})(?![ \t]*[0-9A-Z])',buyer_section)
    seller=near_label_value(text,'填开单位')
    issued_value=near_label_value(text,'填开日期',r'\d{4}年\d{1,2}月\d{1,2}日')
    issued=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日',issued_value or '')
    printed_status=re.search(r'开票状态\s*[:：]\s*(\S+)',text)
    components=None
    lines=text.splitlines()
    for i,line in enumerate(lines):
        if all(label in normalize(line) for label in ['票价','燃油附加费','增值税税额','民航发展基金','合计']) and i+1<len(lines):
            values=re.findall(r'CNY\s*(\d+\.\d{2})',lines[i+1])
            if len(values)==6:
                components=dict(zip(['fare','fuel_fee','vat','civil_aviation_fund','other_taxes','total'],values))
                break
            if len(values)==5:
                components=dict(zip(['fare','fuel_fee','vat','civil_aviation_fund','total'],values))
                components['other_taxes']=None
                break
    issued_iso=None
    if issued:
        try:issued_iso=date(*map(int,issued.groups())).isoformat()
        except ValueError:pass
    return dict(buyer_title=near_label_value(text,'购买方名称'),
        buyer_tax_id=normalize(code.group(1)) if code else None,
        seller_title=seller,
        issue_date=issued_iso,
        total_amount=components['total'] if components else None,aviation_components=components,
        printed_invoice_status=printed_status.group(1) if printed_status else None)


def fields_from_page(text, page=1, expected_title=None, expected_tax_id=None):
    from invoice_travel_fields import extract_travel
    text = unicodedata.normalize('NFKC', text)
    tight = normalize(text)
    fiscal = '医疗门诊收费票据' in tight
    aviation = '航空运输电子客票行程单' in tight
    railway = '铁路电子客票' in tight
    if '医疗收费明细' in tight and not fiscal: return {'page':page,'kind':'fee_detail','invoice_counted':False}
    identity_candidates = candidates([{'text':text,'method':'pdf_text','mean_confidence':None}],True)
    ids = identity_candidates['invoice_number_candidates']
    issues=[issue for issue in identity_candidates['issues'] if issue == 'UNLABELED_INVOICE_ID_NEEDS_REVIEW']
    kind='medical_fiscal' if fiscal else 'aviation_vat' if aviation and ids else 'railway_vat' if railway and ids else 'vat' if '发票' in text and ids else 'unrecognized'
    match = re.search(r'开票日期\s*[:：]\s*(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})',text)
    issued=None
    if match:
        try: issued=date(*map(int,match.groups())).isoformat()
        except ValueError:issues.append('INVALID_ISSUE_DATE')
    code_match=re.search(r'(?:票据代码|发票代码)\s*[:：]\s*(\d{8,12})(?!\d)',text)
    verification_match=re.search(r'校验码\s*[:：]\s*([A-Za-z0-9]+)',text)
    payer=re.search(r'交款人\s*[:：]\s*([^\s]+)',text)
    buyer=seller=buyer_id=seller_id=None
    for line in text.splitlines():
        names=re.findall(r'名\s*称\s*[:：]\s*(\S.*?)(?=\s{2,}|$)',line)
        if len(names)>=2:
            buyer,seller=names[:2]
        taxids=re.findall(TAX_ID_LABEL+r'\s*[:：]\s*([0-9A-Z*]+)',line)
        if not fiscal and len(taxids)>=2:buyer_id,seller_id=taxids[:2]
    if not fiscal:
        header_ids = header_tax_ids(text)
        if header_ids is not None:
            buyer_id, seller_id = header_ids
    amounts=candidates([{'text':text,'method':'pdf_text','mean_confidence':None}],False)['amount_candidates']
    total = next((m.group(1).replace(',','') for line in text.splitlines() if '小写' in normalize(line)
                  for m in [re.search(r'(\d[\d,]*(?:\.\d{1,2})?)\s*$',line.strip())] if m),None)
    # Join a decimal split only at the labeled total, never arbitrary digits.
    wrapped=re.search(r'小\s*写\s*[)）][ \t]*[¥￥]?[ \t]*(\d[\d,]*\.)[ \t]*\n[ \t]*(\d{1,2})(?!\d)',text)
    if wrapped:total=wrapped.group(1).replace(',','')+wrapped.group(2)
    if total is not None:
        total = f'{Decimal(total):.2f}'
    net=next((m.group(1).replace(',','') for line in text.splitlines()
              for m in [re.match(r'\s*合\s*计\s*[¥￥]\s*([\d,]+\.\d{2})',line)] if m),None)
    tax_amount=None
    amount_extraction=None
    if kind=='vat':
        aggregate_net,tax_amount=vat_aggregate_amounts(text)
        net=aggregate_net or net
        from invoice_amounts import vat_total
        total,amount_extraction,amount_issues=vat_total(text,net,tax_amount)
        issues.extend(amount_issues)
    air=aviation_fields(text) if aviation else {}
    if aviation:
        buyer,buyer_id,seller,issued,total=air['buyer_title'],air['buyer_tax_id'],air['seller_title'],air['issue_date'],air['total_amount']
    if railway:
        rail=railway_fields(text)
        buyer,buyer_id,total=rail['buyer_title'],rail['buyer_tax_id'],rail['total_amount']
    if not ids or len(ids)!=1:issues.append('INVOICE_ID_NEEDS_REVIEW')
    if not issued:issues.append('ISSUE_DATE_NEEDS_REVIEW')
    if not total:issues.append('TOTAL_NEEDS_REVIEW')
    validation={'profile_source':USTC['source'] if expected_title==USTC['title'] and expected_tax_id==USTC['tax_id'] else None}
    if kind in ['vat','aviation_vat','railway_vat']:
        validation.update(buyer_tax_id=check_tax_id(buyer_id),seller_tax_id=check_tax_id(seller_id),
                          title_matches_expected=normalize(buyer)==normalize(expected_title) if expected_title and buyer else None,
                          tax_id_matches_expected=normalize(buyer_id)==normalize(expected_tax_id) if expected_tax_id and buyer_id else None)
        if not buyer or not buyer_id:issues.append('BUYER_FIELDS_NEEDS_REVIEW')
        if validation['title_matches_expected'] is False or validation['tax_id_matches_expected'] is False:issues.append('EXPECTED_BUYER_MISMATCH')
        if validation['buyer_tax_id']['status'] in ['invalid_format','checksum_failed']:issues.append('BUYER_TAX_ID_INVALID')
        if validation['seller_tax_id']['status'] in ['invalid_format','checksum_failed']:issues.append('SELLER_TAX_ID_INVALID')
    elif kind=='medical_fiscal':
        validation.update(tax_requirement='not_applicable_to_personal_medical_fiscal_ticket',payer_name=payer.group(1) if payer else None)
        if not code_match or not verification_match:issues.append('FISCAL_QUERY_FIELDS_NEEDS_REVIEW')
    else: issues.append('DOCUMENT_TYPE_NEEDS_REVIEW')
    query_fields={'invoice_number':ids[0]['value'] if len(ids)==1 else None,'issue_date':issued,
                  'invoice_code':code_match.group(1) if code_match else None,'total_amount':total,
                  'net_amount':net,'check_code':verification_match.group(1) if verification_match else None}
    if kind in ['vat','aviation_vat','railway_vat']:
        verification={'platform':'national_tax','official_url':TAX_URL,'fields':query_fields,
                      'amount_note':'开具金额(不含税)使用net_amount；不同票种字段以官网当前提示为准，不能把价税合计直接填作不含税金额。'}
        if not net: issues.append('AVIATION_QUERY_AMOUNT_RULE_NEEDS_REVIEW' if aviation else 'RAILWAY_QUERY_AMOUNT_RULE_NEEDS_REVIEW' if railway else 'NET_AMOUNT_NEEDS_REVIEW')
        if aviation:
            verification['amount_note']='航空票含票价、燃油、增值税及民航基金，按官网当前航空票种字段查询；不将合计、基金或各分项擅自当开具金额。'
    elif kind=='medical_fiscal':
        verification={'platform':'national_fiscal','official_url':FISCAL_URL,'alternative_official_url':ANHUI_URL,
                      'fields':query_fields,'amount_note':'医疗票据使用票据代码、号码、校验码等财政查验字段，不送到税务发票平台。'}
    else:verification={'platform':None,'official_url':None,'fields':query_fields}
    verification.update(status='not_queried',captcha_or_owner_interaction_required=True,authenticity_verified=False)
    return {'page':page,'kind':kind,'invoice_number_candidates':[x['value'] for x in ids],
            'referenced_invoice_numbers':identity_candidates['referenced_invoice_number_candidates'],
            'issue_date':issued,'total_amount':total,'net_amount':net,'tax_amount':tax_amount,'buyer_title':buyer,'buyer_tax_id':buyer_id,
            'seller_title':seller,'seller_tax_id':seller_id,'payer_name':payer.group(1) if payer else None,
            'travel_fields':extract_travel(text),
            'validation':validation,'verification':verification,'issues':list(dict.fromkeys(issues)),
            'amount_candidates':amounts,'amount_extraction':amount_extraction,'invoice_counted':kind!='unrecognized',**{k:v for k,v in air.items() if k in ['aviation_components','printed_invoice_status']}}


def read_invoice(path, expected_title=None, expected_tax_id=None):
    path=Path(path).resolve()
    if path.stat().st_size>30*1024*1024:raise ValueError('单份票据超过30MiB，请分来源核对')
    suffix=path.suffix.lower()
    if suffix=='.pdf': pages=extract_pdf(path,'chi_sim+eng',3)
    elif suffix=='.ofd':pages=extract_ofd(path)
    elif suffix in ['.png','.jpg','.jpeg','.tif','.tiff','.webp']:
        pages=[{'page':1,'method':'unreadable','text':'','mean_confidence':None,
                'error':'OCR_REQUIRED: 图片先经远程OCR或人工核对；票面审核不隐式启动本地识别'}]
    else:raise ValueError('票据字段入口支持PDF、OFD及常用图片')
    fields=[]
    for p in pages:
        result=fields_from_page(p['text'],p['page'],expected_title,expected_tax_id)
        result['extraction_method']=p['method']
        if p['method']=='unreadable' or (p.get('mean_confidence') is not None and p['mean_confidence']<75):result['issues'].append('EXTRACTION_NEEDS_REVIEW')
        fields.append(result)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'pages':fields,
            'authenticity_verified':False,'system_write':False,'parser_version':'0.2.1'}


def verification_receipt(invoice_number, receipt_path, official_url, result, note):
    if not isinstance(invoice_number,str) or not re.fullmatch(r'(?:\d{8}|\d{10}|\d{20})',invoice_number):raise ValueError('票号须为8/10/20位文本')
    url=urlparse(official_url)
    if url.hostname not in {'inv-veri.chinatax.gov.cn','pjcy.mof.gov.cn','czpj.ahzwfw.gov.cn'} or url.scheme not in ['http','https'] or url.username or url.password:raise ValueError('回执来源须为已列出的官方查验域名')
    if url.scheme=='http' and url.hostname!='czpj.ahzwfw.gov.cn' or url.port not in [None,443,8888] or url.port==8888 and url.hostname!='czpj.ahzwfw.gov.cn':raise ValueError('官方地址的协议或端口不符')
    if result not in ['matched','mismatch','not_found','voided','red_issued','failed']:raise ValueError('未知查验结果')
    if not isinstance(note,str) or len(note.strip())<8:raise ValueError('说明实际官网查询、状态和核对范围')
    path=Path(receipt_path).resolve()
    if path.stat().st_size>8*1024*1024:raise ValueError('回执超过8MiB')
    if path.suffix.lower()=='.pdf':text='\n'.join(p['text'] for p in extract_pdf(path,'chi_sim+eng',3))
    elif path.suffix.lower() in ['.html','.htm','.txt']:text=path.read_text(encoding='utf-8-sig')
    else:raise ValueError('回执用官网结果PDF、HTML或文本；截图需先经人工/文字提取核对')
    if invoice_number not in normalize(text) or '查验结果' not in normalize(text):raise ValueError('回执须含本票号码和查验结果，原发票不能冒充查询回执')
    if result=='matched' and (any(v in text for v in ['不一致','查无此','作废','已开红票','未查询到']) or not any(v in text for v in ['一致','查验通过'])):raise ValueError('登记一致与回执文字不符，保留实际官网状态')
    return {'invoice_number':invoice_number,'official_url':official_url,'owner_recorded_result':result,'note':note,
            'receipt_path':str(path),'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'evidence_mode':'owner_recorded_official_result','authenticity_automatically_verified':False,'system_write':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('paths',nargs='+',type=Path)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--profile',choices=['ustc','none'],default='none')
    args=ap.parse_args()
    if args.output.exists() or args.output.resolve() in [p.resolve() for p in args.paths]:raise ValueError('输出须为新的JSON，不覆盖原件或已有结果')
    reports=[read_invoice(p,USTC['title'] if args.profile=='ustc' else None,USTC['tax_id'] if args.profile=='ustc' else None) for p in args.paths]
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({'reports':reports,'authenticity_verified':False},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'files':len(reports),'query_status':'not_queried','fields_and_policy_only':True}))


if __name__=='__main__':main()
