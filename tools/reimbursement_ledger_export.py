#!/usr/bin/env python3
"""服务器总账导出。XlsxWriter仅负责文件格式；原始证据仍在JSON/SQLite。"""
import html
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def display_text(value):
    value = str(value)
    return value if len(value) <= 32000 else value[:31900]+'\n[Excel展示截短；完整内容请用材料ID或票据ID查询配套JSON/SQLite]'


def validate_saved_cells(path, data, formulas):
    """直接核验已写出单元格，避免预览导入器改写长票号或公式缓存。"""
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    errors = []
    checked = 0
    def column(index):
        value = ''
        while index >= 0:
            value = chr(65 + index % 26) + value
            index = index // 26 - 1
        return value
    with zipfile.ZipFile(path) as z:
        strings = [''.join(t.itertext()) for t in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
        names = [s.attrib['name'] for s in ET.fromstring(z.read('xl/workbook.xml')).findall('m:sheets/m:sheet', ns)]
        if names != [t['name'] for t in data['tables']]: errors.append('sheet_names_or_order')
        for i, table in enumerate(data['tables'], 1):
            root = ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'))
            cells = {c.attrib['r']: c for c in root.findall('.//m:sheetData/m:row/m:c', ns)}
            pane = root.find('m:sheetViews/m:sheetView/m:pane', ns)
            if pane is None or pane.get('ySplit') != '5': errors.append(f'{table["name"]}: frozen_header')
            if table['rows'] and root.find('m:autoFilter', ns) is None: errors.append(f'{table["name"]}: filter_missing')
            expected = {f'{column(c)}5': v for c, v in enumerate(table['headers'])}
            expected.update({f'{column(c)}{r}': v for r, row in enumerate(table['rows'], 6) for c, v in enumerate(row)})
            expected.update({f['cell']: f['value'] for f in formulas if f['sheet'] == table['name']})
            for address, value in expected.items():
                checked += 1
                c = cells.get(address)
                kind = c.get('t') if c is not None else None
                raw = c.findtext('m:v', default='', namespaces=ns) if c is not None else ''
                actual = strings[int(raw)] if kind == 's' and raw else raw
                if value is None or value == '': good = actual == ''
                elif isinstance(value, bool): good = kind == 'b' and actual == str(int(value))
                elif isinstance(value, (int, float)):
                    try: good = kind not in ('s','str','e') and abs(float(actual)-value) < 1e-8
                    except (ValueError, TypeError): good = False
                else: good = kind == 's' and actual == display_text(value)
                if not good: errors.append(f'{table["name"]}!{address}: saved_value_or_type_mismatch')
    return {'ok': not errors, 'cells_checked': checked, 'errors': errors}


def export_amount_chart(summary, path):
    """同一服务器汇总生成静态SVG；未知金额只标待核，不画成零。"""
    people = summary['persons']
    known = [p['expected_amount_fen'] for p in people if p['expected_amount_fen'] is not None]
    maximum = max([1, *known])
    height = 145 + len(people)*43
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="{height}" viewBox="0 0 1080 {height}" role="img">',
             '<title>每人登记应收金额</title><rect width="100%" height="100%" fill="#f6f8fb"/>',
             '<g font-family="Microsoft YaHei,PingFang SC,Noto Sans CJK SC,sans-serif" fill="#25354a">',
             '<text x="32" y="40" font-size="24" font-weight="bold">每人登记应收金额</text>',
             '<text x="32" y="68" font-size="14">单位：元；包含无票费用。收款人及历史到账待核。</text>']
    for i, person in enumerate(people):
        amount = person['expected_amount_fen']
        y = 95 + i*43
        label = '待核' if amount is None else f'{amount/100:,.2f}'
        bar = 0 if amount is None else max(0, amount)*720/maximum
        parts.append(f'<g class="person" data-person-id="{html.escape(person["id"],quote=True)}" data-amount-fen="{"unknown" if amount is None else amount}"><text x="32" y="{y+18}" font-size="16">{html.escape(person["name"])}</text><rect x="160" y="{y}" width="{bar:.2f}" height="27" rx="4" fill="#356a96"/><text x="{175+bar:.2f}" y="{y+19}" font-size="16">{label}</text></g>')
    total = summary['expected_amount_fen']
    parts.append(f'<text x="32" y="{height-22}" font-size="18" font-weight="bold">登记应收合计：{"待核" if total is None else f"{total/100:,.2f}元"}</text></g></svg>')
    path.write_text(''.join(parts), encoding='utf-8')
    root = ET.parse(path).getroot()
    rows = root.findall('.//{http://www.w3.org/2000/svg}g[@class="person"]')
    ok = len(rows) == len(people) and all(r.get('data-person-id') == p['id'] and r.get('data-amount-fen') == ('unknown' if p['expected_amount_fen'] is None else str(p['expected_amount_fen'])) for r,p in zip(rows,people))
    return {'ok':ok,'persons':len(rows),'format':'SVG','source':'same_server_summary','manual_preview_required':False}


def export_workbook(workbook_data_path, output_dir):
    import xlsxwriter
    from xlsxwriter.utility import xl_col_to_name
    data=json.loads(Path(workbook_data_path).read_text(encoding='utf-8'))
    out=Path(output_dir)
    out.mkdir(parents=True,exist_ok=True)
    targets=[out/n for n in ('ledger.xlsx','review.html','workbook_validation.json','amounts.svg')]
    if any(p.exists() for p in targets):
        raise FileExistsError('export already exists')
    wb=xlsxwriter.Workbook(targets[0],{'strings_to_urls':False,'strings_to_formulas':False})
    wb.set_properties({'title':'报销总表','comments':'由服务器从有SHA的证据快照生成；收款确认与历史付款仍待核。'})
    base={'font_name':'Arial','font_size':11,'valign':'vcenter','text_wrap':True}
    body=wb.add_format(base)
    title=wb.add_format({**base,'font_size':16,'bold':True,'font_color':'#17365D','text_wrap':False})
    note=wb.add_format({**base,'font_color':'#596577','text_wrap':True})
    header=wb.add_format({**base,'bold':True,'font_color':'#FFFFFF','bg_color':'#244566','text_wrap':True,'align':'center','bottom':1,'bottom_color':'#FFFFFF'})
    money=wb.add_format({**base,'num_format':'#,##0.00','align':'right'})
    identifier=wb.add_format({**base,'num_format':'@'})
    pending=wb.add_format({**base,'bg_color':'#FFF2CC'})
    total=wb.add_format({**base,'bold':True,'num_format':'#,##0.00','top':1,'top_color':'#8796A8'})
    clipped=[]
    formulas=[]
    tables={t['name']:t for t in data['tables']}
    for idx,t in enumerate(data['tables']):
        ws=wb.add_worksheet(t['name'])
        ws.hide_gridlines(2)
        ws.set_zoom(90)
        ws.set_default_row(56)
        ws.set_row(0,10)
        ws.set_row(1,28)
        ws.set_row(2,46)
        ws.set_row(3,10)
        ws.write_string(1,0,t['name'],title)
        # Separate note text from table headers; keep the note inside the first viewing area.
        ws.merge_range(2,0,2,min(7,len(t['headers'])-1),t.get('note') or data['export_policy'],note)
        ws.freeze_panes(5,2 if len(t['headers'])>10 else 1)
        ws.set_landscape();ws.set_paper(9);ws.fit_to_pages(1,0);ws.repeat_rows(4)
        if idx==0:ws.set_tab_color('#244566')
        for c,h in enumerate(t['headers']):
            width=18
            if any(v in h for v in ('票据号码','关联票号','SHA','票据ID','材料ID','关联ID','人员ID','问题ID','观察ID','税号','学工号','银行卡号')):width=27
            if any(v in h for v in ('内容','说明','问题及','原文','来源','备注','完整','候选','字段','值','页面','采集时间')):width=44
            if h in ('登记人','类别','费用笔数','本次范围'):width=14
            if h in ('完整记录','页面与文字'):width=58
            fmt=money if c in t['money_columns'] else identifier if any(v in h for v in ('号码','代码','ID','税号','学工号','银行卡号')) else body
            ws.set_column(c,c,width,fmt)
            ws.write_string(4,c,h,header)
        ws.set_row(4,48)
        rows=t['rows']
        for r,row in enumerate(rows,5):
            for c,value in enumerate(row):
                fmt=money if c in t['money_columns'] else body
                if value is None:
                    ws.write_blank(r,c,None,pending if idx==0 and c>=len(data['summary']['categories'])+5 else fmt)
                elif isinstance(value,bool):ws.write_boolean(r,c,value,fmt)
                elif isinstance(value,(int,float)):ws.write_number(r,c,value,fmt)
                else:
                    text=str(value)
                    if len(text)>32000:
                        clipped.append({'sheet':t['name'],'row':r+1,'column':c+1,'characters':len(text),'full_value':'ledger.json / ledger.sqlite'})
                        text=display_text(text)
                    if text:
                        ws.write_string(r,c,text,identifier if any(v in t['headers'][c] for v in ('号码','票号','代码','ID','税号','学工号','银行卡号')) else fmt)
                    else:
                        ws.write_blank(r,c,None,fmt)
            if idx==0:ws.set_row(r,28)
        if rows:
            ws.autofilter(4,0,4+len(rows),len(t['headers'])-1)
            ws.conditional_format(5,0,4+len(rows),len(t['headers'])-1,{'type':'text','criteria':'containing','value':'待核','format':pending})
        if idx==0:
            # Stable person IDs, never names, own the amounts after sorting or Excel recalculation.
            count=len(tables['逐笔费用']['rows'])
            end=5+count
            person_col=xl_col_to_name(len(t['headers'])-1)
            for r,row in enumerate(rows,5):
                excel_r=r+1
                if row[1] is not None:
                    formula=f'=IF(SUMPRODUCT((\'逐笔费用\'!$L$6:$L${end}=${person_col}{excel_r})*(\'逐笔费用\'!$F$6:$F${end}="纳入")*ISNUMBER(\'逐笔费用\'!$E$6:$E${end}))<>COUNTIFS(\'逐笔费用\'!$L$6:$L${end},${person_col}{excel_r},\'逐笔费用\'!$F$6:$F${end},"纳入"),"",SUMIFS(\'逐笔费用\'!$E$6:$E${end},\'逐笔费用\'!$L$6:$L${end},${person_col}{excel_r},\'逐笔费用\'!$F$6:$F${end},"纳入"))'
                    ws.write_formula(r,1,formula,money,row[1]);formulas.append({'sheet':t['name'],'cell':f'B{excel_r}','formula':formula,'value':row[1]})
                for c,cat in enumerate(data['summary']['categories'],2):
                    if row[c] is None:continue
                    escaped=cat.replace('"','""')
                    formula=f'=SUMIFS(\'逐笔费用\'!$E$6:$E${end},\'逐笔费用\'!$L$6:$L${end},${person_col}{excel_r},\'逐笔费用\'!$C$6:$C${end},"{escaped}",\'逐笔费用\'!$F$6:$F${end},"纳入")'
                    ws.write_formula(r,c,formula,money,row[c]);formulas.append({'sheet':t['name'],'cell':f'{xl_col_to_name(c)}{excel_r}','formula':formula,'value':row[c]})
            total_r=6+len(rows)
            ws.write_string(total_r,0,'合计',total)
            for c in [1,*range(2,2+len(data['summary']['categories']))]:
                unknown=data['summary'].get('unknown_amount_expenses',0) if c==1 else data['summary'].get('category_unknown_counts',{}).get(data['summary']['categories'][c-2],0)
                if unknown:
                    ws.write_string(total_r,c,'金额待核',pending)
                    continue
                value=sum(row[c] for row in rows if isinstance(row[c],(int,float)))
                formula=f'=SUM({xl_col_to_name(c)}6:{xl_col_to_name(c)}{5+len(rows)})'
                ws.write_formula(total_r,c,formula,total,value)
                formulas.append({'sheet':t['name'],'cell':f'{xl_col_to_name(c)}{total_r+1}','formula':formula,'value':round(value,2)})
    wb.close()
    # Read the actual saved file. Native Excel recalculation is a separate handoff check.
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    errors=[];saved_formulas=0;cached=[]
    with zipfile.ZipFile(targets[0]) as archive:
        for member in archive.namelist():
            if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',member):
                root=ET.fromstring(archive.read(member))
                for c in root.findall('.//m:c',ns):
                    if c.get('t')=='e':errors.append({'sheet':member,'cell':c.get('r'),'error':c.findtext('m:v',namespaces=ns)})
                    if c.find('m:f',ns) is not None:
                        saved_formulas+=1;cached.append(c.findtext('m:v',namespaces=ns))
    saved_cells = validate_saved_cells(targets[0],data,formulas)
    chart = export_amount_chart(data['summary'],targets[3])
    report={'ok':not errors and saved_formulas==len(formulas) and saved_cells['ok'] and chart['ok'],'sheets':[{'name':t['name'],'rows':len(t['rows']),'columns':len(t['headers'])} for t in data['tables']],
            'formula_errors':errors,'saved_formula_count':saved_formulas,'formula_cached_values_present':all(v is not None for v in cached),
            'server_computed_cached_values':True,'native_excel_recalculation_verified':False,
            'long_cells_display_clipped':clipped,'full_values_in':'ledger.json and ledger.sqlite',
            'builder':'XlsxWriter '+xlsxwriter.__version__,'finance_system_write':False,
            'saved_cell_validation':saved_cells,'chart_validation':chart,'template_version':'ledger-table-v1',
            'automatic_quality_gate':True,'manual_preview_required':False,'release_kind':'materials_review_not_payment_authorization'}
    targets[2].write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    esc=lambda x:html.escape('' if x is None else str(x))
    sections=[]
    for idx,t in enumerate(data['tables']):
        head=''.join('<th>'+esc(h)+'</th>' for h in t['headers'])
        body_html=[]
        for row in t['rows']:
            cells=[]
            for c,v in enumerate(row):
                text=f'{v:,.2f}' if c in t['money_columns'] and isinstance(v,(int,float)) else esc(v)
                if len(text)>180:text='<details><summary>'+text[:160]+'…</summary><pre>'+text+'</pre></details>'
                cells.append('<td'+(' class="num"' if c in t['money_columns'] else '')+'>'+text+'</td>')
            body_html.append('<tr>'+''.join(cells)+'</tr>')
        sections.append(f'<section id="s{idx}"><h2>{esc(t["name"])}</h2><p>{esc(t.get("note"))}</p><p>{len(t["rows"])} 条记录</p><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body_html)}</tbody></table></div></section>')
    nav=''.join(f'<a href="#s{i}">{esc(t["name"])}</a>' for i,t in enumerate(data['tables']))
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>报销总表</title><style>
body{font:15px/1.5 Arial,"Microsoft YaHei",sans-serif;margin:26px;color:#25354a;background:#f6f8fb}h1{font-size:26px}h2{font-size:21px}nav{display:flex;gap:15px;flex-wrap:wrap}a{color:#245b94}section{background:white;padding:22px;margin:24px 0;border-radius:8px}p{max-width:1000px;color:#586577}.scroll{overflow:auto;max-height:680px}table{border-collapse:collapse;font-size:14px}th{position:sticky;top:0;background:#244566;color:white;z-index:1;white-space:nowrap}th,td{padding:10px 14px;min-width:110px;text-align:left;border-bottom:1px solid #e3e9ef;vertical-align:top}td{max-width:450px}td.num{text-align:right;white-space:nowrap}tbody tr:nth-child(even){background:#f5f8fc}details{width:360px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 monospace}input{padding:10px;width:400px;border:1px solid #adbcc9;border-radius:4px}summary{cursor:pointer}footer{margin:24px;color:#586577}
</style><h1>报销总表</h1><p>''' +esc(data['summary']['scope'])+'''</p><p>搜索姓名、票号、费用或材料ID，长字段可展开查看。</p><input id="search" placeholder="搜索当前总表"><nav>'''+nav+'</nav>'+''.join(sections)+'''<footer>此页面为服务器生成的证据快照。收款信息、历史到账和财务办理状态仍须分别确认。完整结构化数据见同目录 ledger.json / ledger.sqlite。</footer><script>
const rows=Array.from(document.querySelectorAll('tbody tr'));let searchIndex=null,searchTimer;document.getElementById('search').addEventListener('input',e=>{clearTimeout(searchTimer);const q=e.target.value.trim().toLowerCase();searchTimer=setTimeout(()=>{if(!q){rows.forEach(tr=>tr.hidden=false);return}if(!searchIndex)searchIndex=rows.map(tr=>tr.textContent.toLowerCase());rows.forEach((tr,i)=>tr.hidden=!searchIndex[i].includes(q))},150)});
</script></html>'''
    targets[1].write_text(page,encoding='utf-8')
    if not report['ok']:raise ValueError('saved workbook or chart validation failed')
    return report
