"""Append-only, hash-chained feedback events; all finance state stays read-only."""
import base64
import hashlib
import re
import os
import tempfile
from pathlib import Path
from material_review_model import (ReviewError, atomic_json, canonical, digest, load,
                                   now, read_json, require, asset_path, validate_region)

MAX_ATTACHMENT = 20*1024*1024


def events(workspace):
    out=[];previous=None
    directory=Path(workspace)/'events'
    for path in sorted(directory.glob('*.json')):
        row=read_json(path)
        require(path.name==f"{len(out)+1:08d}.json",'反馈事件序号缺失，拒绝跳过损坏记录')
        payload={k:v for k,v in row.items() if k!='sha256'}
        require(row.get('sha256')==digest(payload),'反馈事件SHA不匹配')
        require(row['previous']==previous,'反馈事件链不连续')
        out.append(row);previous=row['sha256']
    return out


def subject(data, key):
    if key in data['issues']:return data['issues'][key]
    if isinstance(key,str) and key.startswith('item:') and key[5:] in data['items']:
        item=data['items'][key[5:]]
        return {'id':key,'item_id':item['id'],'fingerprint':digest({'batch':data['batch_id'],'item':item['id'],'kind':'item_intake'}),'requirements':[],'intake':True}
    raise ReviewError('问题或事项补件入口不存在')


def state(workspace, data=None):
    if data is None:data,_=load(workspace)
    history=events(workspace)
    result={'revision':len(history),'issues':{},'attachments':{},'history':history}
    for iid in list(data['issues'])+['item:'+i for i in data['items']]:
        result['issues'][iid]={'status':'pending','links':[],'events':[],'stale_events':[]}
    for event in history:
        changes=event.get('changes',[event])
        for change in changes:
            iid=change['issue_id']
            if iid not in result['issues']:continue
            s=result['issues'][iid]
            current=change['issue_fingerprint']==subject(data,iid)['fingerprint']
            s['events'].append({**change,'recorded_at':event['recorded_at'],'actor':event['actor'],
                                'revision':event['revision'],'current_definition':current})
            if not current:s['stale_events'].append(event['revision'])
            if change.get('attachment'):
                asset=change['attachment']
                existing=result['attachments'].get(asset['id']) or data['assets'].get(asset['id'])
                if existing:
                    asset={**existing,'item_ids':sorted(set(existing['item_ids']+asset['item_ids']))}
                result['attachments'][asset['id']]=asset
                s['links'].append({'asset_id':asset['id'],'page':1,'label':'新补件待复核'})
            if change['action']=='link':s['links'].append(change['target'])
            if current:
                if change['action'] in ('reply','attachment','answers_imported'):s['status']='replied'
                if change['action']=='review':s['status']='reviewed'
                if change['action']=='reopen':s['status']='pending'
                if change['action']=='defer':
                    s['status']=change['stage']
                    s['next_action']=change['text']
                elif change['action'] in ('reply','attachment','answers_imported','review','reopen'):
                    s.pop('next_action',None)
    return result


def append(workspace, request):
    data,version=load(workspace)
    history=events(workspace)
    require(isinstance(request,dict),'请求须为对象')
    rid=request.get('request_id','')
    require(isinstance(rid,str) and re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',rid),'需要稳定request_id')
    request_hash=digest(request)
    for old in history:
        if old['request_id']==rid:
            require(old['request_hash']==request_hash,'同request_id的内容发生变化')
            return {'ok':True,'revision':old['revision'],'replayed':True,'event':old}
    require(request.get('version')==version,'页面版本过期，请刷新后核对')
    require(type(request.get('revision')) is int and request['revision']==len(history),'反馈已更新，请刷新后重试')
    actor=request.get('actor','')
    require(isinstance(actor,str) and len(actor.strip())<=100,'记录人须为不超过100字的文字，可留空')
    actor=actor.strip() or '未署名'
    action=request.get('action')
    require(action in {'note','reply','review','reopen','link','attachment','answers_imported','defer'},'不支持的反馈动作')
    iid=request.get('issue_id')
    issue=subject(data,iid)
    require(not issue.get('intake') or action in {'note','reply','link','attachment'},'事项补件入口只登记材料与说明；请在具体问题下复核')
    text=request.get('text','')
    require(isinstance(text,str) and 0<len(text.strip())<=20000,'请填写说明（不超过20000字）')
    event={'revision':len(history)+1,'previous':history[-1]['sha256'] if history else None,
           'request_id':rid,'request_hash':request_hash,'version':version,'recorded_at':now(),
           'actor':actor,'action':action,'text':text.strip(),'issue_id':iid,'issue_fingerprint':issue['fingerprint']}
    current=state(workspace,data)
    all_assets={**data['assets'],**current['attachments']}
    def valid_target(target):
        require(isinstance(target,dict),'证据位置格式错误')
        a=all_assets.get(target.get('asset_id'))
        require(a is not None and issue['item_id'] in a['item_ids'],'证据不属于当前事项')
        require(type(target.get('page')) is int and 1<=target['page']<=a['pages'],'证据页码越界')
        asset_path(data,a,workspace)
        validate_region(target)
        result = {'asset_id':a['id'],'page':target['page'],'label':str(target.get('label','关联证据'))[:200]}
        if 'box' in target:
            result.update(box=list(target['box']), basis=target['basis'][:2000])
        return result
    if action=='defer':
        require(request.get('stage') in {'waiting_materials','before_submit','hold'},'请选择等待材料、提交前补齐或按安排暂缓')
        event['stage']=request['stage']
        event['review_scope']='答复已处理，缺项仍未关闭；不改变财务状态或授权'
    if action=='review':
        checks=request.get('checks')
        require(isinstance(checks,list) and len(checks)==len(issue['requirements']) and all(c is True for c in checks),'必须逐项核对全部完成条件')
        evidence=request.get('evidence')
        require(isinstance(evidence,list) and evidence,'复核须提供对应证据位置')
        event['evidence']=[valid_target(t) for t in evidence]
        event['checks']=checks
        event['review_scope']='人工登记的材料复核；财务状态未改变，后续业务需正式接口回读'
    if action=='link':event['target']=valid_target(request.get('target'))
    if action=='attachment':
        file=request.get('file',{})
        name=str(file.get('name','')).replace('\\','/').split('/')[-1]
        suffix=Path(name).suffix.lower()
        category=file.get('category','待分类')
        require(category in {'待分类','发票','付款凭证','订单','明白纸','审批表','明细清单','其他'},'材料类别无效')
        require(suffix in {'.pdf','.png','.jpg','.jpeg','.webp','.doc','.docx','.xls','.xlsx','.txt','.zip'},'不支持此补件格式')
        encoded=file.get('base64','')
        require(isinstance(encoded,str) and len(encoded)<=MAX_ATTACHMENT*4//3+8,'补件超过20MB')
        try:raw=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError) as exc:raise ReviewError('补件编码错误') from exc
        require(0<len(raw)<=MAX_ATTACHMENT,'补件为空或过大')
        identity=hashlib.sha256(raw).hexdigest()
        if suffix=='.pdf':
            require(raw.startswith(b'%PDF'),'PDF内容与扩展名不一致')
            import fitz
            try:
                with fitz.open(stream=raw,filetype='pdf') as doc:
                    require(not doc.needs_pass,'加密PDF请先提供可读取的材料副本')
                    pages=len(doc)
            except RuntimeError as exc:raise ReviewError('补件PDF无法读取，请核对文件') from exc
            require(pages>0,'PDF没有页面')
        else:pages=1
        if suffix in {'.png','.jpg','.jpeg','.webp'}:
            from PIL import Image
            import io
            try:
                with Image.open(io.BytesIO(raw)) as im:im.verify()
            except (OSError,ValueError,Image.DecompressionBombError) as exc:raise ReviewError('补件图片无法读取或像素过大') from exc
        if suffix in {'.docx','.xlsx','.zip'}:require(raw.startswith(b'PK'),'压缩文档内容格式错误')
        target=Path(workspace)/'attachments'/f'{identity}{suffix}'
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():require(hashlib.sha256(target.read_bytes()).hexdigest()==identity,'已有补件存储损坏')
        else:
            # Content-addressed immutable bytes. Concurrent identical uploads are harmless.
            fd,temp=tempfile.mkstemp(prefix='.attachment-',dir=target.parent)
            try:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(raw);stream.flush();os.fsync(stream.fileno())
                try:os.link(temp,target)
                except FileExistsError:require(hashlib.sha256(target.read_bytes()).hexdigest()==identity,'补件并发写入冲突')
            finally:Path(temp).unlink(missing_ok=True)
        event['attachment']={'id':identity,'sha256':identity,'attachment':True,'path':target.relative_to(workspace).as_posix(),
            'label':name[:250],'suffix':suffix,'kind':category+'（补件待复核）','declared_category':category,'pages':pages,'item_ids':[issue['item_id']],
            'invoice_numbers':[],'origins':[]}
    if action=='answers_imported':
        # Only the CLI adapter builds this action; HTTP rejects it.
        answer=request.get('answer')
        require(isinstance(answer,dict) and answer.get('question_id')==iid,'Excel答案编号不一致')
        event['answer']=answer
    event['sha256']=digest(event)
    try:atomic_json(Path(workspace)/'events'/f"{event['revision']:08d}.json",event,exclusive=True)
    except FileExistsError as exc:raise ReviewError('反馈版本并发冲突，请刷新；未覆盖他人记录') from exc
    return {'ok':True,'revision':event['revision'],'replayed':False,'event':event}


def import_answers(workspace, workbook, actor):
    from confirmation_workbook import read_answers
    from material_review_model import sha
    data,version=load(workspace)
    source=data['sources'].get('confirmation_manifest')
    require(source and sha(source['path'])==source['sha256'],'冻结Excel映射缺失或已变化')
    result=read_answers(workbook,source['path'])
    # Validate the entire workbook and IDs before adding any feedback.
    docs=[d for d in result['documents'] if any(d['answers'].values())]
    require(all(d['question_id'] in data['issues'] for d in docs),'存在未登记问题，停止导入')
    require(not any(d.get('validation_errors') for d in docs),'Excel答复存在校验错误')
    imported=[]
    for d in docs:
        rid='excel-'+digest({'sha':result['workbook']['sha256'],'question':d['question_id'],'version':version})
        history=events(workspace)
        old=next((e for e in history if e['request_id']==rid),None)
        if old:continue
        request={'request_id':rid,'version':version,'revision':len(history),'actor':actor,
            'action':'answers_imported','issue_id':d['question_id'],'answer':d,
            'text':'Excel答复读回（待复核）：\n'+'\n'.join(f'{k}: {v}' for k,v in d['answers'].items() if v)}
        append(workspace,request);imported.append(d['question_id'])
    return {'ok':True,'imported':imported,'readback_counts':result['counts'],'case_modified':False,'system_write':False}
