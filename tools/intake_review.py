"""把已归档的原件与人工核对连接到案件候选；不选票、不写财务。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from assistant_contract import APIError
from case_invoice_sources import CANDIDATE_STATUS, _verified_sha

IMAGE_KINDS = {'png','jpg','jpeg','webp','tif','tiff'}


def _source(service, case_id, job_id):
    from assistant_api import read_json
    _,case_paths=service.resolve(case_id)
    group=read_json(case_paths['case']).get('material_context',{}).get('intake_group')
    source_case=case_id
    # ???????????????????????????????????
    # ???????service.result(case_id, job_id)???????????????
    if group and group.get('intake_job_id') != job_id:
        group = None
    if group:
        if group.get('parent_case_id')==case_id:
            raise APIError('intake_group_source','请使用此活动明确绑定的原收件批次')
        source_case=group['parent_case_id']
        parent=read_json(service.resolve(source_case)[1]['case'])
        if parent.get('material_context',{}).get('intake_group'):raise APIError('intake_group_source','不接受嵌套分组来源')
    result = service.result(source_case, job_id)
    if result.get('status') != 'completed' or result.get('exit_code') != 0:
        raise APIError('intake_review_source', '材料整理尚未完整成功；先读取原任务结果')
    loaded = {}
    refs = {}
    for name in ['material_inventory.json', 'receipt.json']:
        found = [r for r in result.get('artifacts', []) if r.get('name') == name]
        if len(found) != 1:
            raise APIError('intake_review_source', '原归档任务缺少完整材料清单或回执')
        row = found[0]
        path, value, request = service._typed_case_artifact(source_case, job_id, row['artifact_id'], 'material_intake', [None])
        if request.get('execute') is not True or request.get('parameters', {}).get('request', {}).get('case_id') != source_case:
            raise APIError('intake_review_source', '必须引用本案已实际执行的材料归档任务')
        loaded[name] = value
        refs[name] = {'artifact_id': row['artifact_id'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    receipt = loaded['receipt.json']
    if receipt.get('case_id') != source_case or receipt.get('status') != 'completed':
        raise APIError('intake_review_source', '归档回执案件或完成状态不符')
    inventory = loaded['material_inventory.json']
    records = inventory.get('files')
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise APIError('intake_review_source', '原材料清单不完整')
    rows = [row for row in records if row.get('kind') != 'archive']
    ids = [row.get('material_id') for row in rows]
    if any(not isinstance(i, str) or not re.fullmatch('mat-[a-f0-9]{16}', i) for i in ids) or len(ids) != len(set(ids)):
        raise APIError('intake_review_source', '材料身份不唯一或缺失')
    if group:
        selected=group.get('material_ids')
        if (not isinstance(selected,list)or not selected or len(selected)!=len(set(selected))or set(selected)-set(ids)
                or group.get('source_refs')!=refs):raise APIError('intake_group_source','分组范围或原收件回执已改变')
        plan=read_json(service.root/'.local/intake-group-plans'/source_case/(job_id+'.json'))
        fingerprint=hashlib.sha256(json.dumps(plan.get('plan'),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        matching=[g for g in plan.get('plan',{}).get('groups',[])if g.get('case_id')==case_id]
        if (plan.get('fingerprint')!=fingerprint or group.get('plan_sha256')!=fingerprint or len(matching)!=1
                or matching[0]['source']!={k:v for k,v in group.items()if k!='plan_sha256'}):
            raise APIError('intake_group_source','活动范围与原确认分组计划不一致')
        rows=[row for row in rows if row['material_id']in selected]
    return inventory, receipt, rows, refs


def bound_file(service, case_id, job_id, material_id):
    inventory, receipt, rows, refs = _source(service, case_id, job_id)
    found = [row for row in rows if row['material_id'] == material_id]
    if len(found) != 1:
        raise APIError('intake_review_material', '所选材料不在本次归档清单中')
    row = found[0]
    roots = {r['snapshot']: r for r in receipt.get('originals', [])}
    if row.get('root_source') not in roots:
        raise APIError('intake_review_material', '材料未绑定本案归档原件')
    origin = roots[row['root_source']]
    path = Path(row['working_copy']) if '!/' in row.get('source_chain', '') else Path(origin['snapshot'])
    _verified_sha(path, row['sha256'], service.material_roots)
    pages = row.get('page_count') or (1 if row.get('kind') in IMAGE_KINDS else 0)
    return path, row, origin, pages, refs


def run(service, case_id, intake_job_id=None, action='inspect', records=None,
        expected_revision=None, confirmation_note=None, execute=False, offset=0, limit=50, material_id=None, page=1):
    if action not in {'batches','inspect', 'preview', 'register'} or type(execute) is not bool:
        raise APIError('intake_review_action', '仅支持查看归档材料或登记已核结果')
    if action=='batches' or (action=='inspect' and intake_job_id is None):
        if execute or records is not None:raise APIError('intake_review_action','查找已有收件批次不接受执行或核对内容')
        from assistant_api import read_json
        service.resolve(case_id)
        batches=[]
        group=read_json(service.resolve(case_id)[1]['case']).get('material_context',{}).get('intake_group')
        if group:
            _,_,group_rows,_=_source(service,case_id,group['intake_job_id'])
            batches.append({'intake_job_id':group['intake_job_id'],'status':'completed','created_at':None,
                'file_count':len(group_rows),'file_names':[Path(row['filename']).name for row in group_rows[:8]],
                'next_action':{'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,'intake_job_id':group['intake_job_id']},'label':'核对此活动的分组材料'}})
        for path in (service.jobs/case_id).glob('*/request.json'):
            request=read_json(path)
            if request.get('operation')!='material_intake' or request.get('execute')is not True:continue
            result=service.result(case_id,path.parent.name)
            item={'intake_job_id':path.parent.name,'status':result['status'],'created_at':request.get('queued_at_utc'),
                  'file_count':None,'file_names':[]}
            receipts=[a for a in result.get('artifacts',[])if a.get('name')=='receipt.json']
            if len(receipts)==1:
                receipt=read_json(service.artifact(case_id,path.parent.name,receipts[0]['artifact_id']))
                if receipt.get('case_id')==case_id:
                    originals=receipt.get('originals',[])
                    item.update(file_count=len(originals),file_names=[r.get('relative_path')or Path(r['path']).name for r in originals[:8]])
            item['next_action']={'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,'intake_job_id':path.parent.name},'label':'继续核对本批材料'} if result['status']=='completed' else {
                'tool':'reimbursement_task_view','arguments':{'case_id':case_id,'job_id':path.parent.name},'label':'查看原整理任务'}
            batches.append(item)
        batches.sort(key=lambda row:row.get('created_at')or'',reverse=True)
        return {'ok':True,'case_id':case_id,'batches':batches,'system_write':False,
                'next_action':batches[0]['next_action']if len(batches)==1 else{'kind':'select_batch','reason':'按来源名称及时间选择本次要接续的批次'}}
    if action == 'preview':
        if execute or records is not None or type(page) is not int or page < 1:
            raise APIError('intake_review_page', '预览只接受实际材料及正整数页码，不接受登记或执行')
        path, row, _, count, refs = bound_file(service, case_id, intake_job_id, material_id)
        proof = {'ok':True,'case_id':case_id,'intake_job_id':intake_job_id,'material_id':material_id,
                 'source_sha256':row['sha256'],'source_path':str(path),'source':refs,'system_write':False,'page':page}
        review_action={'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,'intake_job_id':intake_job_id,
            'action':'register','records':[{'material_id':material_id}],'expected_revision':service.revision(case_id),'execute':False},
            'needed_inputs':['records[0].role：invoice发票/payment付款凭证/supporting辅助材料/other待分类',
                '发票需填写对照原件核实的invoice_records：每项number为完整20位票号，page为实际页码；非发票不填写',
                'confirmation_note：本次对照原件核对依据'],'requires_source_review':True,'label':'按原件填写本材料核对结果'}
        proof['review_action']=review_action
        proof['next_action']={'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,'intake_job_id':intake_job_id,
            'action':'preview','material_id':material_id,'page':page+1},'label':'查看原件下一页'}if page<count else review_action
        native_pages = _source(service,case_id,intake_job_id)[0].get('pages',[])
        native_text = '\n'.join(p.get('native_text','') for p in native_pages if p.get('occurrence_id')==row['occurrence_id'] and p.get('page')==page)
        proof.update(native_text_excerpt=native_text[:4000],native_text_truncated=len(native_text)>4000,
                     text_is_review_candidate=True)
        if row['kind'] in IMAGE_KINDS | {'pdf'}:
            if page > count:
                raise APIError('intake_review_page','页码超出原件范围')
            from material_review_server import preview
            from platform_runtime import private_path
            raw = preview({'roots':[str(p) for p in service.material_roots]},
                {'path':str(path),'sha256':row['sha256'],'suffix':'.'+row['kind'],'pages':count}, service.root, page)
            output = service.root/'.local/intake-previews'/case_id
            output.mkdir(parents=True,exist_ok=True);private_path(output)
            sha = hashlib.sha256(raw).hexdigest()
            image = output/(sha+'.jpg')
            if image.exists():
                if hashlib.sha256(image.read_bytes()).hexdigest()!=sha:
                    raise APIError('intake_preview_changed','预览文件已改变，保留原件并检查本地状态')
            else:
                with image.open('xb') as stream:
                    private_path(image);stream.write(raw)
            return {**proof,'preview_path':str(image),'preview_sha256':sha,'content_type':'image/jpeg'}
        if row['kind'] in {'txt','csv'} and page == 1:
            try: value=path.read_text(encoding='utf-8-sig')
            except UnicodeDecodeError:
                raise APIError('intake_preview_encoding','文本不是UTF-8，请查看原文件或另提供可读取副本；原件未修改') from None
            return {**proof,'text':value[:20000],'text_truncated':len(value)>20000,'content_type':'text/plain'}
        return {**proof,'preview_unavailable':True,'reason':'此格式请通过本机原软件查看，原件与来源SHA已核对'}
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise APIError('intake_review_page', '分页范围为非负起点和1至100条')
    inventory, receipt, rows, refs = _source(service, case_id, intake_job_id)
    _, paths = service.resolve(case_id)
    from assistant_api import read_json
    case = read_json(paths['case'])
    revision = service.revision(case_id)
    existing = case.get('materials', [])
    source = {'intake_job_id': intake_job_id, 'artifacts': refs}
    if action == 'inspect':
        if execute or records is not None:
            raise APIError('intake_review_action', '查看材料不接受执行或核对内容')
        result = []
        for row in rows[offset:offset+limit]:
            pages = [page for page in inventory.get('pages', []) if page.get('occurrence_id') == row['occurrence_id']]
            candidates = [{'number': number, 'page': page['page']} for page in pages for number in page.get('invoice_candidates', [])]
            registered = [m for m in existing if m.get('intake_review', {}).get('intake_job_id') == intake_job_id
                          and m.get('intake_review', {}).get('material_id') == row['material_id']]
            result.append({'material_id': row['material_id'], 'name': Path(row['filename']).name,
                           'kind': row['kind'], 'page_count': row.get('page_count') or (1 if row['kind'] in IMAGE_KINDS else 0),
                           'sha256': row['sha256'], 'bytes': row['bytes'], 'invoice_candidates': candidates,
                           'registered_review': registered[0] if len(registered) == 1 else None,
                           'preview_action':{'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,'intake_job_id':intake_job_id,
                               'action':'preview','material_id':row['material_id'],'page':1},'label':'对照本份原件'}})
        next_row=next((r for r in result if not r['registered_review']),None)
        return {'ok': True, 'case_id': case_id, 'revision': revision, 'source': source,
                'total_materials': len(rows), 'offset': offset, 'rows': result,
                'can_split_activities': not bool(case.get('material_context',{}).get('intake_group')),
                'has_more': offset+limit < len(rows), 'system_write': False,
                'group_action':None if case.get('material_context',{}).get('intake_group')else{'tool':'reimbursement_intake_groups',
                    'arguments':{'case_id':case_id,'intake_job_id':intake_job_id},'label':'将本批材料分给多个活动'},
                'next_page_action':{'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,'intake_job_id':intake_job_id,
                    'offset':offset+limit,'limit':limit}}if offset+limit<len(rows)else None,
                'next_action':next_row['preview_action']if next_row else ({'tool':'reimbursement_intake_review','arguments':{'case_id':case_id,
                    'intake_job_id':intake_job_id,'offset':offset+limit,'limit':limit},'label':'继续核对下一页材料'}if offset+limit<len(rows)else
                    {'tool':'reimbursement_case_journey','arguments':{'case_id':case_id},'label':'查看本案下一步'}),
                'note':'预览后按原件填写分类、完整票号及页码；机器候选不是已核结论。'}
    if (not isinstance(records, list) or not records or len(records) > 100
            or len({r.get('material_id') for r in records if isinstance(r, dict)}) != len(records)):
        raise APIError('intake_review_records', '一次登记1至100份不同材料')
    if not isinstance(confirmation_note, str) or not confirmation_note.strip() or len(confirmation_note) > 2000:
        raise APIError('intake_review_confirmation', '请记录本次对照原件核对的依据')
    if expected_revision != revision:
        raise APIError('revision_conflict', '活动资料已改变，请重新查看材料再确认')
    prepared = []
    for review in records:
        if not isinstance(review, dict) or set(review)-{'material_id','role','invoice_records'}:
            raise APIError('intake_review_records', '核对仅接受材料身份、类别及票号页码')
        role = review.get('role')
        if role not in {'invoice','payment','supporting','other'}:
            raise APIError('intake_review_role', '请选择发票、付款凭证、辅助材料或待分类')
        path, row, origin, page_count, _ = bound_file(service, case_id, intake_job_id, review['material_id'])
        invoice_records = review.get('invoice_records', [])
        if role == 'invoice':
            if not isinstance(invoice_records, list) or not invoice_records:
                raise APIError('intake_review_invoice', '发票须确认完整票号及对应页码')
            for item in invoice_records:
                if (not isinstance(item, dict) or set(item) != {'number','page'}
                        or not isinstance(item['number'], str) or not re.fullmatch('[0-9]{20}', item['number'])
                        or type(item['page']) is not int or not 1 <= item['page'] <= page_count):
                    raise APIError('intake_review_invoice', '普通电子发票须20位完整票号及实际页码；其他票据沿对应专用流程核对，不改写票号')
            if len({(r['number'],r['page']) for r in invoice_records}) != len(invoice_records):
                raise APIError('intake_review_invoice', '同一票号页码重复')
        elif invoice_records:
            raise APIError('intake_review_invoice', '非发票材料不能带入发票记录')
        entry = {'name': Path(row['filename']).name, 'path': str(path), 'sha256': row['sha256'],
                 'status': CANDIDATE_STATUS if role == 'invoice' else 'source_registered_pending_review',
                 'type': role, 'role': {'payment':'payment_proof','invoice':'invoice','supporting':'supporting','other':'other'}[role],
                 'intake_review': {**source, 'material_id': row['material_id'], 'confirmation_note': confirmation_note.strip()},
                 **({'invoice_records': invoice_records} if role == 'invoice' else {})}
        if '!/' in row.get('source_chain',''):
            entry.update(source_archive=origin['snapshot'], source_archive_sha256=origin['sha256'], source_chain=row['source_chain'])
        old = [m for m in existing if m.get('intake_review', {}).get('material_id') == row['material_id']
               and m.get('intake_review', {}).get('intake_job_id') == intake_job_id]
        originals = read_json(paths['invoices']).get('original_files', [])
        if old and any(o.get('sha256') == row['sha256'] for o in originals) and old[0].get('invoice_records', []) != invoice_records:
            raise APIError('intake_review_bound_invoice', '该原件已进入票源登记；票号变化须走原单修正，不能在材料页直接替换')
        prepared.append(entry)
    identities = {m['intake_review']['material_id'] for m in prepared}
    retained = [m for m in existing if not (m.get('intake_review',{}).get('intake_job_id') == intake_job_id
                and m.get('intake_review',{}).get('material_id') in identities)]
    result = {'ok': True, 'case_id': case_id, 'revision': revision, 'source': source,
              'reviewed_count': len(prepared), 'invoice_candidate_count': sum(m['type']=='invoice' for m in prepared),
              'system_write': False, 'selected_invoices_changed': False, 'form_amount_changed': False,
              'preview': not execute, 'materials': prepared}
    if execute:
        saved = service.update_case(case_id, 'case', {'materials': retained+prepared}, expected_revision)
        result.update(revision=saved['revision'], input_receipt=saved['input_receipt'], saved=True)
    if not execute:
        result['next_action'] = {'tool':'reimbursement_intake_review','arguments':{
            'case_id':case_id,'intake_job_id':intake_job_id,'action':'register','records':records,
            'expected_revision':revision,'confirmation_note':confirmation_note,'execute':True},
            'requires_user_confirmation':True,'reason':'核对本次材料范围和票号后才登记；此预览尚未保存'}
    elif result['invoice_candidate_count']:
        key=hashlib.sha256(json.dumps(prepared,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        result['next_action'] = {'tool':'reimbursement_invoice_sources','arguments':{
            'case_id':case_id,'action':'manifest','request_id':'review-sources-'+key},
            'reason':'核对已确认的候选票源；不进行学校选票或审批'}
    else:
        result['next_action'] = {'kind':'human_review','reason':'辅助材料已登记，票据和金额仍按实际资料补齐'}
    return result
