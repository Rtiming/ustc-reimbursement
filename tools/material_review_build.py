"""Adapt existing batch input + group mapping + latest issue snapshot without OCR."""
from pathlib import Path, PureWindowsPath
import re
from material_review_model import (SCHEMA, ReviewError, atomic_json, canonical, digest,
                                   in_roots, read_json, refs, require, sha, validate)

IMAGE = {'.jpg','.jpeg','.png','.webp','.tif','.tiff','.bmp'}
SUPPORTED = IMAGE | {'.pdf','.doc','.docx','.xls','.xlsx','.txt','.zip'}


def build(config_path, workspace):
    config_path, workspace = Path(config_path).resolve(), Path(workspace).resolve()
    config = read_json(config_path)
    roots = [str(Path(p).resolve()) for p in config['roots']]
    batch = Path(config['batch_root']).resolve()
    in_roots(batch, roots)
    sources = {}
    def source(key):
        desc = config['sources'][key]
        path = in_roots(desc['path'], roots)
        require(sha(path) == desc['sha256'], f'输入来源变化: {key}；请明确更新配置，不覆盖旧快照')
        sources[key] = {'path':str(path), 'sha256':desc['sha256']}
        return read_json(path)
    snapshot, normalized, mapping = source('issues'), source('normalized'), source('mapping')
    annotations = source('annotations') if 'annotations' in config['sources'] else {}
    if 'confirmation_manifest' in config['sources']:
        source('confirmation_manifest')
    data = {'schema':SCHEMA,'title':config['title'],'batch_id':config['batch_id'],
            'roots':roots,'sources':sources,'system_snapshot_at':snapshot.get('system_snapshot_at'),
            'items':{},'assets':{},'expenses':{},'issues':{},'warnings':[],
            'resolved_prior':snapshot.get('resolved_prior',[]),'system_write':False}
    documents = {str(seq):doc for doc in normalized['documents'] for seq in doc.get('export_sequences',[])}
    groups = {g['group_id']:g for g in mapping['groups']}
    photos = {p['id']:p for p in mapping['photos']}
    # Explicit legacy locations, not a filesystem search or basename-only match.
    def resolve(path, expected=None):
        p = Path(path)
        if not p.is_file() and '\\' in path:
            parts = PureWindowsPath(path).parts
            if 'SHA工作副本' in parts:
                p = batch/'02_解析'/'SHA工作副本'/parts[-1]
            elif 'MI照片' in parts:
                p = batch/'01_原件'/'MI照片'/parts[-1]
        p = in_roots(p, roots)
        require(p.is_file(), f'材料未找到: {p}')
        if expected:
            require(sha(p) == expected, f'材料SHA不匹配: {p.name}')
        return p
    def add(path, item_id, label=None, expected=None, kind='辅助材料', pages=None, origin=None):
        p = resolve(path, expected)
        if p.suffix.lower() not in SUPPORTED:
            return None
        identity = expected or sha(p)
        if identity not in data['assets']:
            if p.suffix.lower() == '.pdf':
                import fitz
                with fitz.open(p) as doc:
                    count = len(doc)
            else:
                count = 1
            data['assets'][identity] = {'id':identity,'sha256':identity,'path':str(p),
                'label':label or p.name,'suffix':p.suffix.lower(),'kind':kind,'pages':count,
                'item_ids':[],'origins':[],'invoice_numbers':[]}
        a = data['assets'][identity]
        if kind in ('明白纸','发票','正式打印件'):
            a['kind'] = kind
        if item_id not in a['item_ids']:
            a['item_ids'].append(item_id)
        if identity not in data['items'][item_id]['asset_ids']:
            data['items'][item_id]['asset_ids'].append(identity)
        if origin and origin not in a['origins']:
            a['origins'].append(origin)
        return identity
    def kind_for(name):
        return next((kind for word,kind in [('支付','付款凭证'),('付款','付款凭证'),('订单','订单'),('购物','订单'),('审批','审批表'),('申请表','申请表'),('清单','明细清单'),('收据','收据')] if word in name),'辅助材料')
    def visual_refs(value, item_id):
        out = []
        for r in refs(value):
            p = Path(r['path'])
            if p.suffix.lower() not in IMAGE | {'.pdf'}:
                continue
            aid = add(r['path'],item_id,expected=r['sha256'])
            pair = {'asset_id':aid,'page':r['page']}
            if pair not in out:
                out.append(pair)
        return out
    for material in snapshot['materials']:
        ident = str(material['序号'])
        doc = documents.get(ident,{})
        group = groups.get(doc.get('document_id'),{})
        item = {'id':ident,'source_id':doc.get('document_id'),'title':material['事项'],
                'club':'','material_type':material['材料类别'],'face_total':material['票面合计'],
                'paper_total':material['纸面登记额'],'claim_total':material['申请或已建单额'],
                'bill_number':material['单号'],'finance_status':material['本次系统状态'],
                'finance_observed_at':material.get('系统采集时间',snapshot.get('system_snapshot_at')),
                'conclusion':material['材料结论'],'next_action':material['问题与下一步'],
                'queue':material.get('处理队列','待投递核对'),'asset_ids':[],'paper_ids':[],
                'expense_ids':[],'issue_ids':[],'history':[],'source':material['来源'],
                'scope_basis':doc.get('material_review',{}).get('scope_review'),
                'prior_reply':doc.get('user_feedback_review')}
        review = doc.get('material_review', {})
        if review.get('draft_authorized') is True:
            authority = review.get('draft_authority', {})
            authority_path = in_roots(authority.get('path', ''), roots)
            require(authority.get('sha256') and sha(authority_path) == authority['sha256'], '仅做草稿授权来源缺失或变化')
            item['draft_authority'] = {'path': str(authority_path), 'sha256': authority['sha256'], 'scope': 'draft_only'}
        data['items'][ident] = item
        for f in group.get('files',[]):
            label = str(f['filename']).replace('\\','/').split('/')[-1]
            add(f['working_copy'],ident,label,f['sha256'],kind_for(label),f.get('page_count'),f.get('source_chain'))
        pids = group.get('paper_photos',[])[:]
        if doc.get('document_id','').startswith('paper-'):
            pids.append(doc['document_id'][6:])
        for pid in dict.fromkeys(pids):
            photo = photos[pid]
            aid = add(photo['path'],ident,'明白纸｜'+photo['filename'],photo['sha256'],'明白纸')
            item['paper_ids'].append(aid)
        # Only explicit leaf source references, never infer invoice ownership by equal amount.
        visual_refs(doc.get('review_candidates',{}), ident)
        by_invoice = {}
        for invoice in doc.get('invoices',[]):
            invoice_refs = visual_refs(invoice,ident)
            by_invoice[invoice['invoice_id']] = (invoice,invoice_refs)
            for r in invoice_refs:
                asset = data['assets'][r['asset_id']]
                asset['kind']='发票'
                if invoice['invoice_number'] not in asset['invoice_numbers']:
                    asset['invoice_numbers'].append(invoice['invoice_number'])
        for expense in doc.get('expenses',[]):
            erefs=[]; numbers=[]; amounts=[]
            for inv_id in expense.get('invoice_ids',[]):
                if inv_id in by_invoice:
                    inv, pairs = by_invoice[inv_id]
                    erefs.extend(p for p in pairs if p not in erefs)
                    numbers.append(inv['invoice_number']); amounts.append(inv.get('face_amount'))
            eid=expense['expense_id']
            data['expenses'][eid]={'id':eid,'item_id':ident,'label':expense.get('description') or '费用范围待核',
                'invoice_numbers':numbers,'face_amount':amounts[0] if len(amounts)==1 else None,
                'refs':erefs,'status':expense.get('status'),'payments':expense.get('payments',[]),
                'source_refs':expense.get('source_refs',[]),'association':'原归一化输入的费用/发票关联'}
            item['expense_ids'].append(eid)
    # Explicit, source-bound corrections are separate from historical normalized input.
    for extra in annotations.get('expenses',[]):
        ident=extra['item_id']; item=data['items'][ident]
        row=dict(extra); row.setdefault('payments',[]);row.setdefault('status','待核申请范围')
        for r in row['refs']:
            require(r['asset_id'] in item['asset_ids'],'补充费用未对应本事项原件')
            a=data['assets'][r['asset_id']];a['kind']=row.get('material_kind','发票')
            a['invoice_numbers']=sorted(set(a['invoice_numbers']+row.get('invoice_numbers',[])))
        data['expenses'][row['id']]=row
        if row['id'] not in item['expense_ids']:item['expense_ids'].append(row['id'])
    # 呈现关系与费用归属分别保存，不把相关纸并入本项费用或改写问题指纹。
    for ident, fields in annotations.get('items', {}).items():
        require(ident in data['items'] and set(fields) == {'paper_relations'}, '事项关系标注无效')
        require(isinstance(fields['paper_relations'],list), '明白纸关系须为列表')
        data['items'][ident]['paper_relations'] = fields['paper_relations']
    for issue in snapshot['issues']:
        ident=str(issue['事项序号']); iid=issue['问题编号']; item=data['items'][ident]
        a=annotations.get('issues',{}).get(iid,{})
        row={'id':iid,'item_id':ident,'title':issue['问题'],'state':issue['状态'],
             'club':issue['社团'],'queue':issue['办理队列'],'owner':issue['处理责任'],
             'known':issue['已确认依据'],'request':issue['具体所需答复'],'requirements':a.get('requirements',[issue['关闭条件']]),
             'targets':a.get('targets',[]),'refs':visual_refs(issue,ident),'amount':issue['涉及金额'],
             'amount_scope':issue['金额含义'],'status':'pending','sendable':issue['办理队列'] not in ['按用户安排暂缓','非阻断信息核对','待本人确认','经办先核实'],
             'tone':a.get('tone','amber'),'location_note':'已定位到具体材料' if a.get('targets') else '当前定位到事项或原纸；未将全部发票判为异常'}
        row['fingerprint']=digest(row)
        # 对外短文只改变呈现；绑定业务指纹，保留既有答复及复核关联。
        if 'feedback' in a: row['feedback']=a['feedback']
        data['issues'][iid]=row;item['issue_ids'].append(iid);item['club']=issue['社团']
    for item in data['items'].values():
        item['club']=item['club'] or '无待反馈问题'
        for old in data['resolved_prior']:
            if re.search(r'(?<!\d)'+re.escape(item['id'])+r'(?!\d)',old['事项']):
                item['history'].append(old)
    for report in snapshot.get('formal_pdf_mapping',[]):
        ident=str(report['事项序号'])
        if ident in data['items']:
            add(report['正式信息面'],ident,Path(report['正式信息面']).name,kind='正式打印件')
    for aid, fields in annotations.get('assets',{}).items():
        require(aid in data['assets'],'材料标注对象不存在')
        require(set(fields) <= {'kind','rotation'},'不能通过标注修改原件身份')
        data['assets'][aid].update(fields)
    data['counts']={key:len(data[key]) for key in ('items','issues','assets','expenses')}
    for key, value in config.get('expected_counts',{}).items():
        require(data['counts'][key]==value,f'{key}数量不符')
    validate(data)
    version=digest(data)
    path=workspace/'versions'/f'{version}.json'
    if not path.exists():atomic_json(path,data,exclusive=True)
    atomic_json(workspace/'current.json',{'schema':'ustc-material-review-current-v1','version':version,'config':str(config_path)})
    return {'ok':True,'workspace':str(workspace),'version':version,'counts':data['counts'],
            'system_write':False,'source_files_checked':len(data['assets'])}
