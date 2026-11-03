"""Single-file reader export, with issue navigation and self-contained evidence.

Uses the existing PyMuPDF/Pillow material runtime. Source originals and financial
records are never changed; embedded pages are view copies, not submission forms.
"""
import io
import os
import tempfile
from pathlib import Path

from material_review_model import asset_path, load, now, require, sha, validate, paper_contexts, PAPER_RELATION_LABELS
from material_review_store import events, state
from material_review_package import PUBLIC_PROGRESS, select_issues

READABLE = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}


def source_dimensions(data, asset, workspace, page_number):
    """排版只需尺寸；按EXIF交换宽高，不为取尺寸解码、复制整张照片。"""
    path = asset_path(data, asset, workspace)
    if asset['suffix'] == '.pdf':
        import fitz
        with fitz.open(path) as source:
            rect = source[page_number-1].rect
            return rect.width, rect.height
    from PIL import Image
    with Image.open(path) as source:
        width, height = source.size
        if source.getexif().get(274) in (5, 6, 7, 8):
            width, height = height, width
        return width, height


def evidence_image(data, asset, workspace, page_number, size, box=None, highlight=None):
    """按最终纸面尺寸从原件渲染；PDF先裁区域，避免放大网页缩略图。"""
    import fitz
    from PIL import Image, ImageOps, ImageDraw
    from material_review_model import validate_region
    for region in (box, highlight):
        if region is not None: validate_region({'box':region, 'basis':'PDF查看定位'})
    path = asset_path(data, asset, workspace)
    rotation = asset.get('rotation', 0)
    if asset['suffix'] == '.pdf':
        with fitz.open(path) as source:
            page = source[page_number-1]
            clip = page.rect
            if box:
                x, y, w, h = box
                clip = fitz.Rect(x*clip.width, y*clip.height, (x+w)*clip.width, (y+h)*clip.height)
            width, height = clip.width, clip.height
            if rotation in (90, 270): width, height = height, width
            scale = min(64, 3*min(size[0]/width, size[1]/height))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale), clip=clip, colorspace=fitz.csRGB, alpha=False)
            image = Image.frombytes('RGB', (pix.width,pix.height), pix.samples)
    else:
        with Image.open(path) as original: image = ImageOps.exif_transpose(original).convert('RGB')
        if box:
            x,y,w,h=box; width,height=image.size
            image=image.crop((round(x*width),round(y*height),round((x+w)*width),round((y+h)*height)))
    if highlight:
        x,y,w,h=highlight; width,height=image.size
        ImageDraw.Draw(image).rectangle((x*width,y*height,(x+w)*width,(y+h)*height),
                                       outline=(210,44,44),width=max(2,round(width/350)))
    if rotation: image=image.rotate(rotation,expand=True)
    image.thumbnail((max(1,round(size[0]*3)),max(1,round(size[1]*3))),Image.Resampling.LANCZOS)
    payload=io.BytesIO()
    if asset['suffix'] in {'.jpg','.jpeg'}:
        image.save(payload,'JPEG',quality=95,subsampling=0)
    else:
        image.save(payload,'PNG')
    return payload.getvalue()


def plan_pdf(data, current, selected, issue_ids=None):
    chosen = select_issues(data, current, selected, issue_ids)
    require(chosen, '没有选中待反馈问题，请选择后导出PDF')
    assets = {**data['assets'], **current['attachments']}
    rows, omitted = [], []
    for qid in sorted(chosen, key=lambda q: (len(data['issues'][q]['item_id']), data['issues'][q]['item_id'], q)):
        q = data['issues'][qid]
        item = data['items'][q['item_id']]
        papers = paper_contexts(item)
        declared = {(r['asset_id'],r['page']) for r in item.get('paper_relations',[])}
        focused_refs=q.get('feedback',{}).get('evidence',q['targets']+current['issues'][qid]['links'])
        refs = [dict(r, basis=r.get('basis','问题关联页')) for r in focused_refs+q.get('refs', [])]
        refs += [dict(asset_id=aid, page=p, basis='明白纸对照')
                 for aid in item.get('paper_ids', []) for p in range(1, assets[aid]['pages']+1)]
        refs += [dict(asset_id=r['asset_id'],page=r['page'],basis=r['basis']) for r in papers]
        if not refs:
            refs = [dict(asset_id=a['id'], page=p, basis='事项票据对照')
                    for a in assets.values() if q['item_id'] in a['item_ids'] and a['kind']=='发票'
                    for p in range(1, a['pages']+1)]
        unique = {}
        for ref in refs:
            asset = assets[ref['asset_id']]
            reason = ('材料涉及未选事项' if not set(asset['item_ids']) <= set(selected) and (asset['id'],ref['page']) not in declared
                      else '正式财务打印件不外发' if asset['kind']=='正式打印件'
                      else '此格式无法显示为PDF页面' if asset['suffix'] not in READABLE else '')
            if reason:
                omitted.append(dict(asset_id=asset['id'], page=ref['page'], reason=reason))
                continue
            unique.setdefault((ref['asset_id'], ref['page']), ref)
        # Preserve multiple regions on one page; full-page dedup must not erase annotations.
        focused = {}
        for ref in focused_refs:
            if ref.get('box') and (ref['asset_id'], ref['page']) in unique:
                focused.setdefault((ref['asset_id'], ref['page'], tuple(ref['box'])), ref)
        rows.append({'issue': q, 'item': item, 'refs': list(unique.values()),
                     'visuals': list(focused.values()), 'papers':papers})
    return rows, assets, omitted


def material_code(item, asset):
    """编号来自事项内的原件顺序，筛掉某个问题不会给其余票据重新编号。"""
    papers = item.get('paper_ids', [])
    if asset['id'] in papers:
        return item['id']+'-M'+str(papers.index(asset['id'])+1)
    ids = item.get('asset_ids', [])
    if asset['id'] in ids:
        return item['id']+'-F'+str(ids.index(asset['id'])+1).zfill(2)
    return item['id']+'-补'+asset['id'][:6]


def expense_inventory(data, item, assets, selected):
    """只复用已登记的费用—原件关系；不凭同额或文件名推算实付/申请额。"""
    result = []
    for eid in item.get('expense_ids', []):
        expense = data['expenses'][eid]
        refs = [r for r in expense['refs'] if r['asset_id'] in assets
                and set(assets[r['asset_id']]['item_ids']) <= set(selected)
                and assets[r['asset_id']]['kind'] != '正式打印件']
        if not refs:
            continue
        asset = assets[refs[0]['asset_id']]
        result.append({'expense_id':eid,'asset_id':asset['id'],
                       'code':material_code(item,asset),'label':asset['label'],
                       'invoice_numbers':expense.get('invoice_numbers',[]),
                       'face_amount':expense.get('face_amount'),
                       **{k:expense[k] for k in ('review_note','reply_needed','candidate_amount') if k in expense}})
    return result


def export_pdf(workspace, item_ids, issue_ids=None, expected_snapshot=None):
    import fitz
    data, version = load(workspace)
    validate(data)
    current = state(workspace, data)
    require(expected_snapshot is None or expected_snapshot == (version, current['revision']),
            '筛选后材料或答复已更新，请刷新后重新选择')
    require(item_ids and all(i in data['items'] for i in item_ids), 'PDF事项范围无效')
    rows, assets, omitted = plan_pdf(data,current,set(item_ids),issue_ids)
    manifest={'schema':'ustc-material-feedback-pdf-v1','layout':'project-relations-v4','source_version':version,
              'event_revision':current['revision'],'created_at':now(),'item_ids':sorted(set(item_ids)),
              'issue_ids':[r['issue']['id'] for r in rows],'issues':[],'materials':[],'closeups':[],'overviews':[],'annotations':[],
              'omitted':omitted,'messages_sent':False,'system_write':False}
    doc=fitz.open();font=fitz.Font('china-s');font_buffer=font.buffer
    ink=(.12,.18,.27);muted=(.39,.43,.49);accent=(.65,.15,.12)
    left,right=32,563;width=right-left;bottom=795

    def lines(value,size,span):
        result=[]
        for part in value.split('\n'):
            line='';used=0
            for char in part:
                advance=font.text_length(char,fontsize=size)
                if line and used+advance>span:
                    result.append(line);line='';used=0
                line+=char;used+=advance
            result.append(line)
        return result

    def height(value,size,span=width):
        return len(lines(value,size,span))*size*1.45

    def put(page,x,y,value,size=10,color=ink,span=width):
        page.insert_font(fontname='reviewcjk',fontbuffer=font_buffer)
        for n,line in enumerate(lines(value,size,span)):
            page.insert_text((x,y+size*font.ascender+n*size*1.45),line,
                             fontname='reviewcjk',fontsize=size,color=color)
        return y+height(value,size,span)

    # 仅在本次排版内复用不可变尺寸；实际渲染仍重新核验原件SHA。
    dimensions = {}
    def aspect(ref, box=None):
        a=assets[ref['asset_id']];key=(a['id'],ref['page'])
        if key not in dimensions:
            dimensions[key]=source_dimensions(data,a,workspace,ref['page'])
        w,h=dimensions[key]
        if box: w*=box[2];h*=box[3]
        if a.get('rotation',0) in (90,270):w,h=h,w
        return w/h

    toc=[];page=None;y=bottom;destinations={};pending_links=[]

    def new_page(title):
        p=doc.new_page(width=595,height=842)
        put(p,left,17,title,9,muted)
        p.draw_line((left,36),(right,36),color=(.82,.85,.88))
        return p,48

    def evidence_record(ref, qid, code, kind, caption):
        a=assets[ref['asset_id']]
        return {'issue_id':qid,'code':code,'asset_id':a['id'],'original_sha256':a['sha256'],
                'source_page':ref['page'],'box':ref.get('box'),
                'basis':ref.get('basis','同事项已登记原件对应'),'label':a['label'],
                'caption':caption,'kind':a['kind'],'pdf_page':len(doc),'rendering':kind}

    def card_layout(item,ref,regions):
        a=assets[ref['asset_id']]
        relation=next((r for r in paper_contexts(item) if r['asset_id']==a['id'] and r['page']==ref['page']),None)
        source_item=data['items'][relation['source_item_id']] if relation else item
        title=material_code(source_item,a)+' · '+a['label']+' · 第'+str(ref['page'])+'页'
        if relation:title+=' · '+PAPER_RELATION_LABELS[relation['relation']]
        image_h=min(210,390/aspect(ref))
        notes=[str(n+1)+'. '+(r.get('label') or '圈注位置') for n,r in enumerate(regions)]
        if relation:notes.insert(0,relation['statement'])
        if not notes:notes=['本项目已有的'+a['kind']+'，用于对照。']
        body_h=max(image_h,sum(height(note,9,127)+10 for note in notes))
        return title,image_h,body_h,notes,height(title,9)+6+body_h+8

    for row in rows:
        q,item=row['issue'],row['item'];qid=q['id'];copy=q.get('feedback',{})
        project=copy.get('project') or item['title']
        request=copy.get('request') or q['request']
        status=current['issues'][qid]['status']
        if status in PUBLIC_PROGRESS: request=PUBLIC_PROGRESS[status]
        inventory=expense_inventory(data,item,assets,item_ids)
        paper_ids={r['asset_id'] for r in row['papers']}
        paper_refs=[r for r in row['refs'] if r['asset_id'] in paper_ids]
        # 明白纸先交代申报上下文；每张原页只出现一次，多个局部留在同一卡内。
        refs=paper_refs+[r for r in row['refs'] if r not in paper_refs]
        papers=row['papers']
        paper_note=('；'.join(dict.fromkeys(PAPER_RELATION_LABELS[r['relation']] for r in papers))
                    if paper_refs else '已有登记，本次选择未包含（见省略记录）' if papers
                    else '本批未定位到对应明白纸；请提供文件或已交位置')
        amounts=[]
        for key,label in [('paper_total','明白纸登记'),('face_total','已登记票面合计'),('claim_total','当前申请/草稿金额')]:
            if key=='claim_total' and item.get(key)==item.get('paper_total'):continue
            if item.get(key) is not None:amounts.append(label+' '+str(item[key])+'元')
        summary=('；'.join(amounts) or '申请金额尚未核定')
        if inventory: summary+='；'+str(len(inventory))+'笔费用见对应清单'
        intro=[('原清单第'+item['id']+'项 · '+qid,8,muted),
               ('明白纸：'+paper_note+'；'+summary,9,ink)]
        if copy.get('context'):intro.append((copy['context'],9,ink))
        instruction=copy.get('response') if status not in PUBLIC_PROGRESS else None
        intro.append(('请回复：'+instruction if instruction else '需处理：'+request,10,accent))
        heading=height(project,14)+5+sum(height(t,s)+5 for t,s,c in intro)+8
        require(heading<bottom-100,'反馈文字超出页面，请缩短文字')
        # 短清单连同首份明白纸一起排；长清单允许续页，标题不能成为孤立说明。
        reserve=50
        if refs:
            first=refs[0];a=assets[first['asset_id']]
            focused=[r for r in row['visuals'] if r['asset_id']==a['id'] and r['page']==first['page']]
            reserve=card_layout(item,first,focused)[-1]
            # 两份对照材料能放在一页时，一起保留；避免纸与唯一票据被拆开。
            if len(refs)==2:
                reserve=sum(card_layout(item,r,[v for v in row['visuals'] if v['asset_id']==r['asset_id'] and v['page']==r['page']])[-1] for r in refs)
        if inventory:
            if len(inventory)>8:reserve=200
            else:reserve+=45+sum(max(height(e['label'],8,279),height(' / '.join(e['invoice_numbers']) or '未登记',8,126),12)+8 for e in inventory)
        reserve=min(reserve,bottom-48-heading)
        if y+heading+reserve>bottom:page,y=new_page('材料补充与问题对照')
        entry={'issue_id':qid,'item_id':item['id'],'project':project,'pdf_page':len(doc),
               'detail_pages':[len(doc)],'material_pages':[],'paper_codes':list(dict.fromkeys(
                   material_code(data['items'][next(p['source_item_id'] for p in papers if p['asset_id']==r['asset_id'])],assets[r['asset_id']]) for r in paper_refs)),
               'paper_status':'included' if paper_refs else 'omitted' if papers else 'not_located',
               'paper_relations':papers,
               'expense_inventory':inventory}
        manifest['issues'].append(entry);toc.append([1,project+'（原第'+item['id']+'项）',len(doc)])
        y=put(page,left,y,project,14)+5
        for text,size,color in intro:y=put(page,left,y,text,size,color)+5
        y+=8

        def ensure_room(needed):
            nonlocal page,y
            require(needed<=bottom-80,'单份材料内容过长，无法排入反馈页')
            if y+needed>bottom:
                page,y=new_page('第'+item['id']+'项 · '+project+'（续）')
                y=put(page,left,y,'续上页：'+request,9,accent)+10
                require(y+needed<=bottom,'材料与续页说明超出页面')
                entry['detail_pages'].append(len(doc))

        # 先给对照表，接收者无需翻看全包即可指认本项目所有已登记费用。
        if inventory:
            table_rows=[]
            columns=[52,285,132,62]
            for e in inventory:
                description=e['label']
                if e.get('review_note'):description+='\n'+e['review_note']
                if e.get('candidate_amount'):description+='\n文件名候选 '+e['candidate_amount']+'元（待确认）'
                table_rows.append([e['code'],description,' / '.join(e['invoice_numbers']) or '未登记',
                                   str(e['face_amount']) if e['face_amount'] is not None else '待核'])
            def table_heading():
                nonlocal y
                y=put(page,left,y,'费用对应清单（票面金额；实付和拟报金额另核）',9,ink)+4
                page.draw_rect(fitz.Rect(left,y,right,y+19),fill=(.94,.96,.98),color=None)
                x=left
                for value,cw in zip(['材料编号','材料 / 已知信息与待确认内容','完整票号','票面/元'],columns):
                    put(page,x+3,y+3,value,8,muted,cw-6);x+=cw
                y+=19
            ensure_room(65);table_heading()
            for cells,e in zip(table_rows,inventory):
                rh=max(height(value,8,cw-6) for value,cw in zip(cells,columns))+8
                if y+rh>bottom:
                    ensure_room(rh+50);table_heading()
                x=left
                for value,cw in zip(cells,columns):
                    put(page,x+3,y+4,value,8,ink,cw-6);x+=cw
                pending_links.append((page.number,fitz.Rect(left,y,right,y+rh),qid,e['asset_id']))
                page.draw_line((left,y+rh),(right,y+rh),color=(.88,.90,.92));y+=rh
            y+=12

        for ref in refs:
            a=assets[ref['asset_id']]
            relation=next((r for r in papers if r['asset_id']==a['id'] and r['page']==ref['page']),None)
            code=material_code(data['items'][relation['source_item_id']] if relation else item,a)
            crops=[r for r in row['visuals'] if r['asset_id']==a['id'] and r['page']==ref['page']]
            title,overview_h,image_h,notes,ch=card_layout(item,ref,crops)
            ensure_room(ch)
            destinations.setdefault((qid,a['id']),(page.number,y))
            y=put(page,left,y,title,9)+6
            rect=fitz.Rect(left,y,left+overview_h*aspect(ref),y+overview_h)
            # 概览固定原貌，框只画在展示副本上；原件及审批空栏不作改写。
            page.insert_image(rect,stream=evidence_image(data,a,workspace,ref['page'],(rect.width,rect.height)))
            for n,r in enumerate(crops):
                x0,y0,w0,h0=r['box']
                # 先按原图坐标定位，再跟随材料已登记的展示旋转。
                rotation=a.get('rotation',0)
                if rotation==90:x0,y0,w0,h0=y0,1-x0-w0,h0,w0
                elif rotation==180:x0,y0,w0,h0=1-x0-w0,1-y0-h0,w0,h0
                elif rotation==270:x0,y0,w0,h0=1-y0-h0,x0,h0,w0
                mark=fitz.Rect(rect.x0+x0*rect.width,rect.y0+y0*rect.height,
                               rect.x0+(x0+w0)*rect.width,rect.y0+(y0+h0)*rect.height)
                page.draw_rect(mark,color=accent,width=1)
                badge=fitz.Rect(mark.x0,max(rect.y0,mark.y0-12),mark.x0+12,max(rect.y0,mark.y0-12)+12)
                page.draw_rect(badge,fill=(1,1,1),color=accent,width=.5)
                put(page,badge.x0+3,badge.y0,str(n+1),8,accent,10)
                annotation=evidence_record(r,qid,code,'outline_on_overview',r.get('label') or '圈注位置')
                annotation['marker']=n+1;manifest['annotations'].append(annotation)
            overview=evidence_record(dict(ref,box=None),qid,code,'overview_fixed_appearance_216dpi','整体圈注')
            manifest['materials'].append(overview);manifest['overviews'].append(overview.copy())
            cy=y
            for note in notes:cy=put(page,left+404,cy,note,9,ink,127)+10
            if len(doc) not in entry['material_pages']:entry['material_pages'].append(len(doc))
            y+=image_h+8
            page.draw_line((left,y-4),(right,y-4),color=(.88,.90,.92))
        if not refs:
            ensure_room(50)
            y=put(page,left,y,'本次没有可显示的对应材料；缺项不能用其他项目原件代替。',9,muted)+12
        y+=16

    # 对照清单只链接到本文件实际展示的材料，不生成失效的本机路径链接。
    for pno,rect,qid,aid in pending_links:
        if (qid,aid) in destinations:
            target,ty=destinations[(qid,aid)]
            doc[pno].insert_link({'kind':fitz.LINK_GOTO,'from':rect,'page':target,'to':fitz.Point(left,ty)})
    for n,page in enumerate(doc):
        put(page,left,813,'回复请注明项目及材料编号；纸质已交的，请写持有人和位置。',8,muted)
        put(page,505,813,str(n+1)+' / '+str(len(doc)),8,muted,58)
    doc.set_toc(toc)
    doc.set_metadata({'title':'管指委材料补充清单 - '+str(len(rows))+'项',
                      'subject':'项目、明白纸、费用对应清单与整体圈注','creator':'材料核对工作台'})
    directory=Path(workspace)/'exports';directory.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.feedback-',suffix='.pdf',dir=directory);os.close(fd)
    try:
        doc.save(temp,garbage=4,deflate=True);doc.close()
        require(load(workspace)[1]==version and len(events(workspace))==current['revision'],
                '导出期间材料或答复更新，请重新导出')
        identity=sha(temp);out=directory/f'问题反馈-{identity[:20]}.pdf';os.replace(temp,out)
        with fitz.open(out) as check:manifest.update(sha256=identity,bytes=out.stat().st_size,pages=len(check))
        return out,manifest
    finally:
        if not doc.is_closed:doc.close()
        Path(temp).unlink(missing_ok=True)
