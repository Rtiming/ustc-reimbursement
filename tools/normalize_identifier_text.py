"""为表格导出的长编号补上显式文本运行；只修格式，不改变编号、金额或日期。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile
import safe_xml as E

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
T=lambda name:'{'+NS+'}'+name
HEADERS={'完整订单号','支付宝交易号'}


def normalize(source,output):
    source=Path(source);output=Path(output)
    if source.resolve()==output.resolve() or output.exists():raise ValueError('只写新的输出文件，不覆盖原件')
    changes=[];payload=[]
    with ZipFile(source) as archive:
        styles=E.from_zip(archive, 'xl/styles.xml')
        fonts=styles.find(T('fonts'));xfs=styles.find(T('cellXfs'))
        for info in archive.infolist():
            data=E.read_zip_member(archive, info.filename)
            if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',info.filename):
                document=E.fromstring(data);columns={}
                for c in document.iter(T('c')):
                    v=c.find(T('v'))
                    if v is not None and v.text in HEADERS:
                        columns[re.sub(r'\d+$','',c.get('r'))]=int(re.search(r'\d+$',c.get('r')).group())
                for c in document.iter(T('c')):
                    address=c.get('r','');match=re.fullmatch(r'([A-Z]+)(\d+)',address)
                    if not match or match[1] not in columns or int(match[2])<=columns[match[1]]:continue
                    v=c.find(T('v'))
                    if v is None or not re.fullmatch(r'\d{16,100}',v.text or ''):continue
                    if c.get('t')!='str':raise ValueError('长编号已是数值型，须回到原始编号重建，不能格式化补救')
                    text=v.text;c.remove(v);c.set('t','inlineStr');rich=E.SubElement(c,T('is'))
                    font=fonts[int(xfs[int(c.get('s','0'))].get('fontId','0'))]
                    for part in [text[:len(text)//2],text[len(text)//2:]]:
                        run=E.SubElement(rich,T('r'));properties=E.SubElement(run,T('rPr'))
                        for element in font:
                            tag=element.tag.removeprefix('{'+NS+'}')
                            if tag=='name':E.SubElement(properties,T('rFont'),dict(element.attrib))
                            elif tag in {'sz','color','b','i','u','strike','vertAlign','family','charset','scheme'}:properties.append(copy.deepcopy(element))
                        E.SubElement(run,T('t')).text=part
                    assert ''.join(x.text or '' for x in rich.iter(T('t')))==text
                    changes.append({'sheet':info.filename,'cell':address,'text':text})
                if columns:
                    E.register_namespace('',NS)
                    data=E.tostring(document,encoding='utf-8',xml_declaration=True)
            payload.append((info,data))
    with output.open('xb') as stream:
        with ZipFile(stream,'w') as archive:
            for info,data in payload:archive.writestr(info,data)
    return dict(status='identifier_text_preserved',cells=len(changes),changes=changes,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args();result=normalize(a.input,a.output)
    with a.receipt.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':result['status'],'cells':result['cells']},ensure_ascii=False))


if __name__=='__main__':main()
