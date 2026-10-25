"""由票面字段报告生成本地官方查验准备页，不向网站上传票据或声称已验真。"""
import argparse
import html
import json
from pathlib import Path
from urllib.parse import urlparse

ISSUE_LABELS = {
    'INVOICE_ID_NEEDS_REVIEW':'票号需对照原件核对',
    'ISSUE_DATE_NEEDS_REVIEW':'日期未完整识别',
    'INVALID_ISSUE_DATE':'日期格式异常',
    'TOTAL_NEEDS_REVIEW':'票面合计未完整识别',
    'DOCUMENT_TYPE_NEEDS_REVIEW':'票据类型需人工核对',
    'BUYER_FIELDS_NEEDS_REVIEW':'购买方抬头或税号需核对',
    'EXPECTED_BUYER_MISMATCH':'与学校主体不同，请按实际经费确认',
    'BUYER_TAX_ID_INVALID':'购买方税号格式或校验位异常',
    'SELLER_TAX_ID_INVALID':'销售方税号格式或校验位异常',
    'FISCAL_QUERY_FIELDS_NEEDS_REVIEW':'财政票据查询字段需补全',
    'NET_AMOUNT_NEEDS_REVIEW':'不含税金额未完整识别',
}


def render(data):
    entries=[]
    for report in data['reports']:
        for page in report['pages']:
            if page.get('kind')=='fee_detail':continue
            query=page.get('verification',{});fields=query.get('fields',{})
            number=fields.get('invoice_number')
            url=query.get('official_url')
            parsed = urlparse(url) if url else None
            if parsed and (parsed.scheme!='https' or parsed.hostname not in ['inv-veri.chinatax.gov.cn','pjcy.mof.gov.cn'] or parsed.username or parsed.password or parsed.port not in [None,443]):
                raise ValueError('字段报告含未允许的查验站点')
            esc=lambda v:html.escape(str(v) if v is not None else '未识别，需核对')
            items=[('票号',number),('开票日期',fields.get('issue_date')),('代码',fields.get('invoice_code'))]
            if page['kind']=='medical_fiscal':items+=[('财政校验码',fields.get('check_code')),('票面合计',fields.get('total_amount'))]
            else:items+=[('不含税金额',fields.get('net_amount')),('价税合计',fields.get('total_amount')),('抬头',page.get('buyer_title')),('税号',page.get('buyer_tax_id'))]
            cells=''.join('<dt>'+esc(k)+'</dt><dd><input readonly value="'+esc(v)+'"></dd>' for k,v in items)
            link='<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">打开官方查验入口</a>' if url else '<b>类型或字段未识别，先人工核对</b>'
            issues='；'.join(ISSUE_LABELS.get(x,'有字段需对照原件复核') for x in page.get('issues',[])) or '已读出票面字段'
            recorded = [row for row in report.get('expected_invoice_rows', []) if row.get('page')==page['page']]
            manual = ''.join('<p>已核对索引登记：票号 '+esc(row['invoice_number'])+'，金额 '+esc(row['amount'])+' 元。此项来自人工登记，自动字段未识别时须对照原件补全查验字段。</p>' for row in recorded) if page['kind']=='unrecognized' else ''
            entries.append('<section><h2>'+esc(report.get('name',''))+' · '+esc(number or '票号待核')+'</h2><p>原文件第'+str(page['page'])+'页；尚未官网查验</p><dl>'+cells+'</dl>'+manual+'<p>'+esc(issues)+'</p>'+link+'</section>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>本批票据查验准备</title><style>body{max-width:980px;margin:32px auto;padding:0 20px;font:16px system-ui;line-height:1.6;color:#223047;background:#f6f8fb}section{background:white;padding:20px;margin:20px 0;border:1px solid #dfe4eb;border-radius:8px}h2{font-size:18px}dl{display:grid;grid-template-columns:120px 1fr;gap:8px}dd{margin:0}input{box-sizing:border-box;width:100%;padding:8px;border:1px solid #cbd3de;font:15px system-ui}a{color:#165ab6}p{color:#566475}</style><h1>本批票据查验准备</h1><p>选择票据，复制字段并打开对应官网，按页面提示完成验证码和查询。此页只整理票面信息；抬头、税号校验通过不代表票据真伪已查。未识别或扫描件须先人工核对，查验回执另外留档。</p>'+''.join(entries)+'</html>'


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    if a.output.exists() or a.output.suffix.lower()!='.html':raise ValueError('输出使用新的html文件，不覆盖已生成的准备页')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(render(json.loads(a.input.read_text())),encoding='utf-8')
    print(json.dumps({'output':str(a.output.resolve()),'official_query_executed':False}))


if __name__=='__main__':main()
