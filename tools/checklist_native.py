"""无专有创作运行时的清单XLSX导出；消费原清单合同，不重新计算业务金额。"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid


def require(condition, message):
    if not condition:raise ValueError(message)


def cell_value(value, kind):
    if value is None or value=='':return None
    require(isinstance(value,(str,int,float,bool)),'单元格须为标量值；复杂关系先由清单生成器展开')
    if isinstance(value,float):require(math.isfinite(value),'单元格不接受NaN或无穷值')
    if kind=='text':
        text=str(value)
        require(len(text)<=32767,'单元格文字超过Excel上限；请先分段，不能截断')
        return text
    require(kind in {'money','number'},'未知清单列类型')
    raw=str(value)
    if re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',raw) is None:
        require(len(raw)<=32767,'单元格文字超过Excel上限')
        return raw  # 兼容既有计划中的“未核定”等明确标记，不伪造为0。
    if kind=='money':require(re.fullmatch(r'-?[0-9]+(?:\.[0-9]{1,2})?',raw)is not None,'金额超过两位小数')
    number=Decimal(raw)
    require(number.is_finite() and abs(number)<=10**12,'数字超出清单支持范围')
    return float(number)


def validate_plan(plan):
    require(isinstance(plan,dict)and plan.get('schema')=='ustc-checklist-delivery-v1'and plan.get('case_id')and plan.get('system_write')is False,'清单交付合同不符')
    sheets=plan.get('sheets');require(isinstance(sheets,list)and bool(sheets),'清单至少需要一个工作表')
    names=set();cells=0
    for sheet in sheets:
        name=sheet.get('name');require(isinstance(name,str)and 0<len(name)<=31 and not re.search(r'[\\/:*?\[\]]',name)and name.lower()not in names,'工作表名称无效或重复');names.add(name.lower())
        columns=sheet.get('columns');rows=sheet.get('rows');require(isinstance(columns,list)and columns and isinstance(rows,list),'清单表头或数据无效')
        keys=[column.get('key')for column in columns];require(all(isinstance(k,str)and k for k in keys)and len(keys)==len(set(keys)),'表头字段无效或重复')
        require(all(column.get('type')in {'text','number','money'}for column in columns),'列类型无效')
        require(all(isinstance(row,dict)and set(row)==set(keys)for row in rows),'数据字段与表头不一致')
        require(len(rows)<=100000 and len(columns)<=100,'清单超过支持范围，请分批导出')
        cells+=len(rows)*len(columns);require(cells<=1000000,'清单超过100万单元格预算')
        for row in rows:
            for column in columns:cell_value(row[column['key']],column['type'])
    require(isinstance(plan.get('sources'),list)and plan['sources'],'缺少清单来源')


def verify_sources(plan):
    from material_intake import reject_links
    for source in plan['sources']:
        path=Path(source['path']);require(path.is_absolute(),'来源使用绝对路径');reject_links(path)
        require(hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256'],'清单来源已改变，须重新核对当前版本')


def render(plan_path, output):
    from openpyxl import Workbook,load_workbook
    from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
    from openpyxl.utils import get_column_letter
    from material_intake import reject_links
    plan_path=Path(plan_path).resolve();output=Path(output).absolute();reject_links(plan_path);reject_links(output)
    require(output.suffix.lower()=='.xlsx'and not output.exists(),'输出须为全新的.xlsx文件')
    plan_raw=plan_path.read_bytes();plan=json.loads(plan_raw.decode('utf-8-sig'));validate_plan(plan);verify_sources(plan)
    output.parent.mkdir(parents=True,exist_ok=True)
    book=Workbook();book.remove(book.active);book.properties.creator='USTC Reimbursement';book.properties.lastModifiedBy='USTC Reimbursement'
    normal=Font(name='Microsoft YaHei',size=10,color='172435');header=Font(name='Microsoft YaHei',size=10,bold=True,color='172435')
    for spec in plan['sheets']:
        sheet=book.create_sheet(spec['name']);sheet.sheet_view.showGridLines=False
        sheet['A2']=spec.get('title')or spec['name'];sheet['A2'].data_type='s';sheet['A2'].font=Font(name='Microsoft YaHei',size=14,bold=True)
        sheet['A3']=spec.get('note')or plan.get('note')or'';sheet['A3'].data_type='s';sheet['A3'].font=normal
        columns=spec['columns'];end=get_column_letter(len(columns))
        for c,column in enumerate(columns,1):
            cell=sheet.cell(5,c,column['key']);cell.data_type='s';cell.font=header;cell.fill=PatternFill('solid',fgColor='E8EDF3');cell.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
            sheet.column_dimensions[get_column_letter(c)].width=min(65,max(14,column.get('width',26)))
        sheet.row_dimensions[5].height=28
        for r,row in enumerate(spec['rows'],6):
            sheet.row_dimensions[r].height=min(409,max(24,spec.get('rowHeight',56)))
            for c,column in enumerate(columns,1):
                value=cell_value(row[column['key']],column['type']);cell=sheet.cell(r,c,value);cell.font=normal
                cell.alignment=Alignment(vertical='top',wrap_text=True,horizontal='left'if column['type']=='text'else'right')
                if column['type']=='text' or isinstance(value,str):cell.data_type='s';cell.number_format='@'
                else:cell.number_format='#,##0.00'if column['type']=='money'else'#,##0'
                cell.border=Border(bottom=Side(style='hair',color='DEE4EB'))
        sheet.freeze_panes='B6';sheet.auto_filter.ref=f'A5:{end}{max(5,5+len(spec["rows"]))}'
        sheet.print_title_rows='1:5';sheet.sheet_properties.pageSetUpPr.fitToPage=True
        sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A4;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    temporary=output.with_name('.xlsx-'+uuid.uuid4().hex+'.tmp.xlsx')
    try:
        book.save(temporary)
        checked=load_workbook(temporary,data_only=False,read_only=True)
        require(checked.sheetnames==[s['name']for s in plan['sheets']],'保存后工作表不一致')
        verified_cells=0
        for spec in plan['sheets']:
            sheet=checked[spec['name']]
            for r,row in enumerate(spec['rows'],6):
                for c,column in enumerate(spec['columns'],1):
                    expected=cell_value(row[column['key']],column['type']);cell=sheet.cell(r,c)
                    require(cell.value==expected and cell.data_type!='f','保存后单元格内容或类型不一致')
                    if column['type']=='text'and expected is not None:require(cell.data_type=='s','文字标识被转换为其他类型')
                    verified_cells+=1
        checked.close();verify_sources(plan)
        require(plan_path.read_bytes()==plan_raw,'导出期间计划发生改变')
        os.link(temporary,output)
    finally:
        book.close();temporary.unlink(missing_ok=True)
    result={'ok':True,'case_id':plan['case_id'],'output':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'renderer':'openpyxl','source_plan_sha256':hashlib.sha256(plan_raw).hexdigest(),'verified_cells':verified_cells,
        'sheets':[{'name':s['name'],'rows':len(s['rows']),'columns':len(s['columns'])}for s in plan['sheets']],
        'system_write':False,'source_manifest':str(plan_path)}
    with output.with_suffix('.xlsx.receipt.json').open('x',encoding='utf-8')as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(render(args.input,args.output),ensure_ascii=False))
