"""Build per-bill simplex attachment packets using the existing coverage/payment/print owners."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path, PureWindowsPath
import pymupdf as fitz

from assistant_api import atomic_json, read_json, APIError
from batch_workflow import _json_path, _verified_file
from reimbursement_batch_plan import safe_filename
import material_coverage
import payment_evidence
from print_packet import load_plan, Packet, digest, PROJECT


def snapshot_plan(path, plan):
    """索引更新不能抹掉现有PDF所引用的计划。"""
    path=Path(path)
    if path.exists():
        old=read_json(path);old_path=path.parent/'snapshots'/(digest(path)+'.json')
        if not old_path.exists():atomic_json(old_path,old)
    atomic_json(path,plan)
    snapshot=path.parent/'snapshots'/(digest(path)+'.json')
    if not snapshot.exists():atomic_json(snapshot,plan)


def render_fingerprint(plan, profile):
    content={k:v for k,v in plan.items() if k!='source_binding'}
    return hashlib.sha256(json.dumps({'plan':content,'profile':profile,
        'renderer_sha256':digest(PROJECT/'tools/print_packet.py')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def reuse_packet(item, plan, profile, receipt):
    old=read_json(receipt);target=Path(item['pdf']);path=Path(item['plan'])
    if old.get('pdf_sha256')!=digest(target):raise ValueError('已有PDF字节已改变，未覆盖')
    current_sha=digest(path);fingerprint=render_fingerprint(plan,profile)
    if old.get('render_fingerprint'):
        matches=old['render_fingerprint']==fingerprint
    else:
        snapshot=path.parent/'snapshots'/(old['plan_sha256']+'.json')
        original=read_json(snapshot) if snapshot.is_file() and digest(snapshot)==old['plan_sha256'] else None
        matches=old['plan_sha256']==current_sha or (original is not None and
            {k:v for k,v in original.items() if k!='source_binding'}=={k:v for k,v in plan.items() if k!='source_binding'})
    if not matches:raise ValueError('已有PDF的材料或打印配置不同，未覆盖')
    root=(path.parent/plan['source_root']).resolve()
    for section in plan['sections']:
        for source in section['items']:
            if source.get('kind')=='source':
                file=(root/source['path']).resolve()
                if not file.is_relative_to(root) or digest(file)!=source['sha256']:raise ValueError('原材料字节改变，不能复用旧PDF')
    if any(old.get(k)!=item.get(k) for k in ('case_id','serial','amount')):raise ValueError('原PDF案件或金额归属改变')
    record={**old,**item,'plan_sha256':current_sha,'render_fingerprint':fingerprint,'reused':True,
            'pdf_generated_from_plan_sha256':old.get('pdf_generated_from_plan_sha256',old['plan_sha256'])}
    archived=receipt.parent/'.receipts'/(digest(receipt)+'.json')
    if not archived.exists():atomic_json(archived,old)
    atomic_json(receipt,record)
    return record


def prepare_packets(service, *, batch_index, material_map, source_root, output_root, auxiliary_bindings):
    index_path=_json_path(service,str(batch_index));index=read_json(index_path)
    mapping_path=_json_path(service,str(material_map));mapping=read_json(mapping_path)
    bindings_path=_json_path(service,str(auxiliary_bindings));bindings=read_json(bindings_path)
    root=Path(source_root).resolve();out=Path(output_root).resolve()
    if not any(root.is_relative_to(r) and out.is_relative_to(r) for r in service.material_roots):raise APIError('packet_root','材料及输出须在已授权资料根内')
    groups={g['group_id']:g for g in mapping['groups']};extras={e['case_id']:e['items'] for e in bindings.get('cases',[])}
    plans=[];inventory_cache={}
    for row in index['documents']:
        case=row['case_id'];_,paths=service.resolve(case);form=read_json(paths['form']);manifest=read_json(paths['invoices'])
        official=read_json(_verified_file(service,row['official_plan'],row['official_plan_sha256']))
        forms=[f for d in official['plan']['documents']for f in d['forms']]
        if len(forms)!=1 or forms[0]['claim_amount']!=form['amount'] or forms[0]['summary']!=row['summary']:raise ValueError('材料计划与本案已核金额/摘要不符')
        wanted=forms[0];invoices={i['invoice_id']:i for d in official['plan']['documents']for i in d['invoices']}
        expenses=[e for d in official['plan']['documents']for e in d['expenses'] if e['expense_id'] in wanted['expense_ids']]
        claims={}
        for e in expenses:
            ids=e['invoice_ids']
            if len(ids)==1:claims[invoices[ids[0]]['invoice_number']]=e['claim_amount']
            elif Decimal(e['claim_amount'])==sum((Decimal(invoices[i]['face_amount'])for i in ids),Decimal(0)):
                claims.update({invoices[i]['invoice_number']:invoices[i]['face_amount'] for i in ids})
            else:raise ValueError('多票减报不能猜分配')
        if set(claims)!={s['number'] for s in manifest['selected_invoices']} or sum(map(Decimal,claims.values()))!=Decimal(form['amount']):raise ValueError('逐票核定金额与本单不闭合')
        if Decimal(manifest['expected_amount'])!=Decimal(form['amount']):
            from finance_expense_amount import verify_case_reductions
            verify_case_reductions(service,case,form,manifest,expenses,invoices,read_json(paths['case']).get('material_context',{}).get('native_reduction_receipts'))
        if len(form['payments'])!=1:raise ValueError('附件批处理须明确逐票收款绑定；当前适配为每单唯一收款人')
        planned_payments=wanted['payments']
        if (not planned_payments or any(any(p.get(k)!=form['payments'][0].get(k)for k in ('name','mode'))for p in planned_payments)
                or sum((Decimal(p['amount'])for p in planned_payments),Decimal(0))!=Decimal(form['payments'][0]['amount'])):
            raise ValueError('材料收款归属与已核正式计划不一致')
        group=groups[row['document_id']];additional=extras.get(case,[])
        selected_shas={f['sha256'] for f in manifest['original_files']}
        if {f['sha256']for f in group['files']}!=selected_shas|{a['sha256']for a in additional}:raise ValueError('活动组仍有未分类材料，不能遗漏：'+case)
        folder=out/'材料计划'/case;folder.mkdir(parents=True,exist_ok=True)
        def relative(path):return str(Path(path).resolve().relative_to(root))
        files_by_sha={s['source_sha256']:s for s in manifest['selected_invoices']}
        items=[];by_number={};source_archives={}
        for i,f in enumerate(manifest['original_files'],1):
            file=service.material_path(f['path']);number=files_by_sha[f['sha256']]['number']
            if digest(file)!=f['sha256']:raise ValueError('原票SHA变化')
            with fitz.open(file) as pdf:count=len(pdf)
            if count!=1:raise ValueError('多页原票须先按已核页级关系配置，不能自动当一票')
            id_='I'+str(i).zfill(3);by_number[number]=id_
            items.append({'id':id_,'kind':'source','role':'invoice','label':number,'path':relative(file),'sha256':f['sha256'],'page':0,
                          'amount':claims[number],'invoice_face_amount':files_by_sha[f['sha256']]['amount'],'invoice_number':number,
                          'payee':form['payments'][0]['name'],'payment_mode':form['payments'][0]['mode']})
            archive=Path(f.get('source_archive') or root/'01_原件/微信电子包'/group['source_zip'])
            if not archive.is_file():archive=root/'01_原件/微信电子包'/PureWindowsPath(str(archive)).name
            source_archives[relative(archive)]=f.get('source_archive_sha256')
        records=[]
        for i,a in enumerate(additional,1):
            file=service.material_path(a['path'])
            if digest(file)!=a['sha256']:raise ValueError('辅助材料SHA变化')
            targets=[by_number[n]for n in a['invoice_numbers']]
            item={'id':'A'+str(i).zfill(3),'kind':'source','role':a['role'],'label':a['label'],'path':relative(file),'sha256':a['sha256'],'page':0,'invoice_ids':targets,'payee':form['payments'][0]['name']}
            # Keep an auxiliary page immediately after the final linked invoice.
            pos=max(i for i,x in enumerate(items)if x['id']in targets)+1;items.insert(pos,item)
            if a['role']=='payment_proof':records.append({**a['payment_record'],'id':'PAY'+str(i),'material_ids':[item['id']],
                'allocations':[{'invoice_id':by_number[n],'amount':v}for n,v in a['allocations'].items()]})
        coverage_sources=sorted(source_archives)
        for name,expected in source_archives.items():
            if expected and digest(root/name)!=expected:raise ValueError('原压缩包SHA变化')
        key=tuple((p,digest(root/p))for p in coverage_sources)
        if key not in inventory_cache:inventory_cache[key]=material_coverage.inventory({'coverage_sources':coverage_sources},root)
        assets=inventory_cache[key];item_by_asset={x['sha256']+':p'+str(x['page']):x for x in items}
        resolutions={};reviewed={}
        for asset in assets:
            aid=asset['asset_id'];item=item_by_asset.get(aid)
            if item:resolutions[aid]={'state':'included','material_ids':[item['id']]}
            elif asset['kind']=='archive':resolutions[aid]={'state':'container','reason':'原包已递归展开，逐页归属见本清单'}
            else:resolutions[aid]={'state':'excluded','reason':'同原包其他活动材料，不在本单经确认的活动组和选票范围；原件完整保留'}
            if item:reviewed[aid]={'type':'bank_receipt' if item['role']=='payment_proof' else 'invoice' if item['role']=='invoice' else 'other',
                                  'reason':'已核正式发票' if item['role']=='invoice' else next(a['review_basis']for a in additional if a['sha256']==item['sha256'])}
            else:reviewed[aid]={'type':'other','reason':'本单范围外的其他活动材料，未作为本单付款证明；归属按冻结材料对应表'}
        invoice_copy={**manifest,'original_files':[{**f,'path':relative(f['path'])}for f in manifest['original_files']]}
        atomic_json(folder/'invoices.json',invoice_copy);atomic_json(folder/'form.json',form)
        coverage={'case_id':case,'serial':form['serial'],'coverage_sources':coverage_sources,'assets':assets,'resolutions':resolutions}
        atomic_json(folder/'coverage.json',coverage)
        plan={'case_id':case,'serial':form['serial'],'amount':form['amount'],'title':row['summary'],'source_root':os.path.relpath(root,folder),
              'invoice_manifest':relative(folder/'invoices.json'),'form_config':relative(folder/'form.json'),'coverage_manifest':relative(folder/'coverage.json'),
              'coverage_sources':coverage_sources,'paper_material_policy':'list_only','paper_materials':[],
              'payment_evidence':{'profiles':['student_activity'],'assembly_mode':'attach_available','reviewed_assets':reviewed,'records':records},
              'sections':[{'title':row['summary'],'items':items}],
              'source_binding':{'batch_index_sha256':digest(index_path),'material_map_sha256':digest(mapping_path),'auxiliary_bindings_sha256':digest(bindings_path)}}
        plan=payment_evidence.arrange(plan);path=folder/'plan.json';snapshot_plan(path,plan)
        target=out/'附件PDF'/row['business_date']/(safe_filename(str(row['index'])+' '+row['summary'])+'.pdf')
        plans.append({'sequence':row['index'],'case_id':case,'serial':form['serial'],'plan':str(path),'pdf':str(target),'invoice_count':len(claims),'amount':form['amount']})
    result={'schema':'ustc-material-packet-batch-v1','source_root':str(root),'plans':plans,'system_write':False,'approval_completed':False}
    atomic_json(out/'附件批次计划.json',result);return result


def build_packets(batch_plan, *, progress=None):
    result={'schema':'ustc-material-packet-batch-result-v1','packets':[],'issues':[],'system_write':False}
    profile=read_json(PROJECT/'data/printing_profile.json')
    for item in batch_plan['plans']:
        path=Path(item['plan']);target=Path(item['pdf']);receipt=target.with_suffix('.manifest.json')
        try:
            plan=read_json(path)
            if target.exists():
                result['packets'].append(reuse_packet(item,plan,profile,receipt));continue
            plan,root=load_plan(path)
            packet=Packet(plan,root,profile);packet.build();target.parent.mkdir(parents=True,exist_ok=True)
            packet.doc.save(target,garbage=4,deflate=True);pages=len(packet.doc);packet.doc.close()
            record={**item,'pages':pages,'pdf_sha256':digest(target),'plan_sha256':digest(path),'simplex':True,'official_information_page_included':False,
                    'render_fingerprint':render_fingerprint(plan,profile),'reused':False,
                    'materials':packet.records,'payment_audit':payment_evidence.audit_plan(plan,read_json(root/plan['coverage_manifest']))}
            atomic_json(receipt,record);result['packets'].append(record)
            if progress:progress({'event':'attachment_pdf_ready','sequence':item['sequence'],'pages':pages})
        except (ValueError,OSError,KeyError)as error:result['issues'].append({'case_id':item['case_id'],'error':str(error)})
    result.update(ok=not result['issues'],pdf_count=len(result['packets']),page_count=sum(p['pages']for p in result['packets']),
                  reused_count=sum(p.get('reused') is True for p in result['packets']))
    return result


def run_registered_packets(service,cfg,*,progress=None):
    from registered_inputs import checked_reference
    pointer=read_json(_json_path(service,cfg['current_input_pointer']))
    index=checked_reference(service,{'path':pointer['current_30_index'],'sha256':pointer['current_30_index_sha256']})
    aux=checked_reference(service,cfg['auxiliary_bindings'])
    out=Path(cfg['output_root']);out.mkdir(parents=True,exist_ok=True)
    plan=prepare_packets(service,batch_index=index,material_map=cfg['material_map'],source_root=cfg['source_root'],output_root=out,auxiliary_bindings=aux)
    missing=sum(not Path(p['pdf']).exists()for p in plan['plans'])
    if missing:
        runtime=read_json(service.root/'.local/authoring_runtime.json')
        marker=out/'材料计划'/'pdf-operation-started.json'
        if not marker.exists():
            subprocess.run([runtime['node'],runtime['pdf_marker'],'--operation-kind','create','--expected-output-count',str(missing),'--output-format','pdf'],check=True)
            atomic_json(marker,{'operation':'create','format':'pdf','expected_count':missing})
    result=build_packets(plan,progress=progress)
    atomic_json(out/'附件PDF结果.json',result)
    return {'ok':result['ok'],'pdf_count':result['pdf_count'],'page_count':result['page_count'],'issues':result['issues'],
            'directory':str(out/'附件PDF'),'report':str(out/'附件PDF结果.json'),'financial_approval':False}
