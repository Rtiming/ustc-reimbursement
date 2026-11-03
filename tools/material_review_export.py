"""Portable, scoped feedback packages; never send messages."""
import html
import io
import json
import zipfile
import hashlib
import os
import tempfile
from pathlib import Path
from material_review_model import asset_path, load, now, require
from material_review_store import state, events


def export_package(workspace, item_ids, issue_ids=None, expected_snapshot=None):
    from material_review_package import select_issues, PUBLIC_PROGRESS, csv_bytes
    data,version=load(workspace); current=state(workspace,data)
    require(expected_snapshot is None or expected_snapshot==(version,current['revision']),
            '筛选后材料或答复已更新，请刷新后重新选择')
    require(item_ids and all(i in data['items'] for i in item_ids),'反馈范围无效')
    selected=set(item_ids);all_assets={**data['assets'],**current['attachments']}
    chosen=select_issues(data,current,selected,issue_ids)
    issues=[i for i in data['issues'].values() if i['id'] in chosen]
    manifest={'schema':'ustc-review-feedback-package-v1','version':version,'event_revision':current['revision'],
              'created_at':now(),'item_ids':item_ids,'issue_ids':[i['id'] for i in issues],'files':[],'omitted':[],
              'messages_sent':False,'scope':'待发送草稿；材料按明确问题位置选取，已复核及内部核实问题不外发'}
    content=['# 材料补充与说明（草稿未发送）','']
    html_parts=['<!doctype html><meta charset="utf-8"><title>材料补充与说明</title><style>body{font:16px/1.7 system-ui;max-width:900px;margin:30px auto;padding:16px}img{max-width:100%}section{border-bottom:1px solid #ddd;padding:20px 0}</style><h1>材料补充与说明</h1><p>草稿未发送；请按问题编号回复。已有材料请指出位置。</p>']
    feedback_rows=[['问题编号','事项编号','社团','活动','当前进度','具体问题','补充说明（请填写）','新增文件名（请填写）']]
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        added={}
        for issue in issues:
            item=data['items'][issue['item_id']]
            status=current['issues'][issue['id']]['status']
            copy=issue.get('feedback',{})
            request=(PUBLIC_PROGRESS[status]+'\n原始缺项：' if status in PUBLIC_PROGRESS else '')+(copy.get('response') or copy.get('request') or issue['request'])
            content.extend([f"## {issue['id']}｜{item['title']}",request,''])
            feedback_rows.append([issue['id'],item['id'],item.get('club'),item['title'],status,request,'',''])
            html_parts.append('<section><h2>'+html.escape(issue['id']+'｜'+item['title'])+'</h2><p>'+html.escape(request)+'</p>')
            targets=issue['targets']+current['issues'][issue['id']]['links']
            for ref in targets:
                a=all_assets[ref['asset_id']]; key=(a['id'],ref['page'])
                if not set(a['item_ids'])<=selected or a['kind']=='正式打印件':
                    manifest['omitted'].append({'asset_id':a['id'],'page':ref['page'],'reason':'共享材料涉及范围外事项或为正式打印件，未自动外发'})
                    continue
                if key not in added:
                    path=asset_path(data,a,workspace)
                    if a['suffix']=='.pdf':
                        import fitz
                        name=f"materials/{a['id'][:16]}-p{ref['page']}.pdf"
                        with fitz.open(path) as source, fitz.open() as single:
                            single.insert_pdf(source,from_page=ref['page']-1,to_page=ref['page']-1)
                            payload=single.tobytes()
                    else:
                        name=f"materials/{a['id'][:16]}{a['suffix']}";payload=path.read_bytes()
                    z.writestr(name,payload);added[key]=name
                    manifest['files'].append({'name':name,'sha256':hashlib.sha256(payload).hexdigest(),
                                              'bytes':len(payload),'original_sha256':a['sha256'],
                                              'page':ref['page'],'label':a['label']})
                name=added[key]
                html_parts.append('<p><a href="'+html.escape(name,quote=True)+'">'+html.escape(ref.get('label') or a['label'])+'（第'+str(ref['page'])+'页）</a></p>')
                if a['suffix'] in {'.png','.jpg','.jpeg','.webp'}:html_parts.append('<img loading="lazy" src="'+name+'" alt="对应材料">')
            html_parts.append('</section>')
        if not issues:
            content.append('此范围当前没有可直接对外发送的问题；请在内部工作台查看待核/暂缓事项。')
            html_parts.append('<p>'+content[-1]+'</p>')
        z.writestr('反馈说明.md','\n'.join(content))
        z.writestr('问题反馈.csv',csv_bytes(feedback_rows))
        z.writestr('index.html',''.join(html_parts))
        z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    require(load(workspace)[1]==version and len(events(workspace))==current['revision'],
            '导出期间记录更新，请重试生成一致快照')
    identity=hashlib.sha256(buf.getvalue()).hexdigest()[:20]
    out=Path(workspace)/'exports'/f'反馈草稿-{identity}.zip';out.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.feedback-',dir=out.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(buf.getvalue());stream.flush();os.fsync(stream.fileno())
        os.replace(temp,out)
    finally:Path(temp).unlink(missing_ok=True)
    return out,manifest
