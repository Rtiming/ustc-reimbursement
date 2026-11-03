"""日常发票HTTP上传：冻结来源、原pending、分块、一次保存及独立库存回读。"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='ustc-native-http-upload-v1'


def upload_sources(paths, verification):
    """每个源文件可有多张票；医疗财政票必须明确代码和号码，不能只匹配短号码。"""
    from assistant_api import read_json
    rows=read_json(paths['invoices'])['original_files'];result=[];seen=set()
    offset=verification['batch']['offset']
    for row,source in zip(rows[offset:offset+verification['batch']['count']],verification['source_invoices']):
        numbers=source['invoice_numbers'];declared=row.get('invoice_identities')
        if declared is None:
            declared=[{'kind':'vat','code':'','number':n}if re.fullmatch(r'[0-9]{20}',n)else
                      {'kind':'medical','code':row.get('invoice_code'),'number':n}for n in numbers]
        if not isinstance(declared,list)or not declared or len(declared)>100:raise ValueError('原件须有1至100张已核实票据身份')
        identities=[]
        for item in declared:
            if not isinstance(item,dict)or set(item)!={'kind','code','number'}:raise ValueError('票据身份仅kind/code/number')
            kind,code,number=(item[k]for k in ['kind','code','number'])
            if not (kind=='vat'and code==''and isinstance(number,str)and re.fullmatch(r'[0-9]{20}',number)or
                    kind=='medical'and isinstance(code,str)and re.fullmatch(r'[0-9]{8}',code)and isinstance(number,str)and re.fullmatch(r'[0-9]{10}',number)):
                raise ValueError('只支持20位发票号或医疗8位代码加10位号码')
            key=(kind,code,number)
            if key in seen:raise ValueError('上传原件含重复票据身份')
            seen.add(key);identities.append(dict(item))
        if [x['number']for x in identities]!=numbers:raise ValueError('逐票身份顺序须与原件票号清单完全相同')
        result.append({**source,'invoice_identities':identities})
    return result


def source_pool_rows(rows,identity):
    from invoice_identity import source_matches
    source={'invoice_number_candidates':[identity['number']],'invoice_code':identity['code']}
    return [r for r in rows if source_matches(r,source)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen(path,value):
    text=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    if path.exists():
        if path.read_text()!=text:raise ValueError('原HTTP上传输入或回执发生变化，不覆盖')
    else:
        with path.open('x',encoding='utf-8',newline='\n')as f:f.write(text)
        path.chmod(0o600)


def command(service,request,folder,*,prepare_only=False,expected_userid=None):
    from assistant_api import read_json
    from reimburse import freeze_upload_batch,guard_invoice_entry
    from finance_http import source
    p=request['parameters'];action=p.get('action')
    _,paths=service.resolve(request['case_id'])
    form=read_json(paths['form']);manifest=read_json(paths['invoices'])
    if action=='upload-resume':
        if set(p)-{'action','tab_id','job_id'}:raise ValueError('上传恢复只接受原job和tab')
        original=service.job_folder(request['case_id'],p.get('job_id'));prior=read_json(original/'request.json')
        status=service.result(request['case_id'],p['job_id'])
        if prior['operation']!='invoice' or prior['parameters'].get('action')!='upload' or prior.get('execute')is not True or status['status']in {'queued','running'}:
            raise ValueError('只恢复本案已经停止的原HTTP上传任务')
        c=read_json(original/'native-upload-input.json')
        if c['tab_id']!=p.get('tab_id') or c['case_id']!=request['case_id']:
            raise ValueError('原上传tab或案件不同')
        frozen(folder/'upload-recovery-source.json',{'original_job_id':p['job_id'],'input':str(original/'native-upload-input.json'),
            'input_sha256':sha(original/'native-upload-input.json'),'no_replay':True})
        return [service.python,str(ROOT/'tools/finance_native_upload.py'),'--input',str(original/'native-upload-input.json'),
            '--output',str(folder/'output.json'),'--resume',*(['--preview']if not request['execute']else[])]
    if action!='upload' or set(p)-{'action','start','count','tab_id'}:raise ValueError('日常HTTP上传只接受本案批次参数')
    offset=p.get('start',0);count=p.get('count',10)
    if type(offset)is not int or offset<0 or type(count)is not int or not 1<=count<=10:raise ValueError('上传批次为1至10份原件')
    if not prepare_only:
        guard_invoice_entry(form,manifest,'upload',None)
        if form.get('review',{}).get('workflow_started') or form.get('system_mode','daily')!='daily':raise ValueError('只适配未建单日常原件上传')
    # 校验资料根和来源复核后才读取字节；不能先读取未授权路径再报错。
    for row in manifest['original_files'][offset:offset+count]:
        service.material_path(row['path'])
        visual=row.get('visual_check')
        if row.get('ocr_issues') and not (visual in ['verified','user_confirmed']if isinstance(visual,str)else isinstance(visual,dict)and visual.get('status')=='verified'):
            raise ValueError('原件OCR异常尚未复核')
    explicit=expected_userid or form.get('expected_userid') or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')
    verification=freeze_upload_batch(paths,request['case_id'],offset,count,expected_userid=explicit)
    batch=verification['batch'];sources=upload_sources(paths,verification)
    if form.get('expected_userid')not in (None,'',batch['expectedUserid']) or os.environ.get('REIMBURSEMENT_EXPECTED_USERID')not in (None,batch['expectedUserid']):
        raise ValueError('本案、manifest和会话账号不符')
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}',batch['expectedUserid']):raise ValueError('须登记本案明确账号')
    if request['execute'] and (not isinstance(p.get('tab_id'),str) or not re.fullmatch(r'\d+',p['tab_id'])):raise ValueError('实际上传须明确当前tab')
    contract=json.loads((ROOT/'data/finance_upload_contract.json').read_text())
    if any(f['size']>contract['max_file_bytes']for f in batch['files']):raise ValueError('单份原件超过本适配5MiB预算，未暂存或上传')
    domain=json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']
    state=read_json(paths['state']);last=state.get('last_native_upload_batch',{})
    c={'schema':SCHEMA,'identity_contract':'code-number-v2','case_id':request['case_id'],'bundle':str(paths['form'].parent/'operator.json'),
        'form_sha256':sha(paths['form']),'manifest_sha256':sha(paths['invoices']),'offset':offset,
        'batch':batch,'sources':sources,'identity_source':verification.get('identity_source','manifest.expected_userid'),
        'tab_id':p.get('tab_id'),'expected_userid':batch['expectedUserid'],
        'upload_contract':contract,'pool_contracts':domain['invoices'],'request_id':request['request_id'],
        'token':hashlib.sha256((request['case_id']+':'+request['request_id']).encode()).hexdigest()[:32],
        'prior_reconciled_token':last.get('token')if last.get('schema')==SCHEMA and last.get('independent_http_readback')is True else None,
        'prior_reconciled_without_upload':last.get('schema')==SCHEMA and last.get('no_upload_dispatched')is True and last.get('independent_http_readback')is True,
        'execute':request['execute']}
    c['upload_execution_eligible']=not (form.get('serial')or manifest.get('serial')or manifest.get('draft_creation_requested')or form.get('review',{}).get('workflow_started'))and form.get('system_mode','daily')=='daily'
    # resolve()给出的bundle是正本，不假定operator.json固定位置。
    c['bundle']=str(service.resolve(request['case_id'])[0])
    adapter=folder/'native-upload-adapter.js';text=source('upload')
    if adapter.exists():
        if adapter.read_text()!=text:raise ValueError('原上传适配器源码变化')
    else:adapter.write_text(text,encoding='utf-8',newline='\n');adapter.chmod(0o600)
    c['adapter_sha256']=sha(adapter);frozen(folder/'native-upload-input.json',c)
    return [service.python,str(ROOT/'tools/finance_native_upload.py'),'--input',str(folder/'native-upload-input.json'),'--output',str(folder/'output.json')]


def preparation_command(service,request,folder):
    """固定只读检查第一批真实原件；已入池原件应被明确拒绝，不产生pending。"""
    from assistant_api import read_json
    synthetic={**request,'execute':False,'parameters':{'action':'upload','start':0,'count':10,'tab_id':request['parameters']['tab_id']}}
    command(service,synthetic,folder,prepare_only=True,expected_userid=request['parameters']['expected_userid'])
    c=read_json(folder/'native-upload-input.json')
    if c['expected_userid']!=request['parameters']['expected_userid']:raise ValueError('本案上传manifest账号与当前请求不同')
    code=(folder/'native-upload-adapter.js').read_text()
    script=folder/'upload-readonly-prepare.js';text='(async()=>{\n'+code+'\nreturn await reimbursementUploadApi('+json.dumps({**c,'action':'prepare'},ensure_ascii=False)+');})()'
    if script.exists():
        if script.read_text()!=text:raise ValueError('原上传只读检查源码变化')
    else:script.write_text(text,encoding='utf-8',newline='\n');script.chmod(0o600)
    return [service.python,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(folder/'output.json'),
        '--tab-id',c['tab_id'],'--operation-kind','read','--quiet']


def run(input_path,output,*,resume=False,preview=False,runtime=None):
    from operator_support import bundle
    from assistant_api import read_json
    from reimburse import begin_write,freeze_upload_batch,guard_invoice_entry
    from finance_http import source
    c=read_json(input_path);folder=input_path.parent
    if c.get('schema')!=SCHEMA:raise ValueError('未知HTTP上传输入')
    _,paths=bundle(c['bundle']);form=read_json(paths['form']);manifest=read_json(paths['invoices'])
    guard_invoice_entry(form,manifest,'upload',None)
    adapter=folder/'native-upload-adapter.js';code=adapter.read_text()
    if (sha(adapter)!=c['adapter_sha256'] or hashlib.sha256(source('upload').encode()).hexdigest()!=c['adapter_sha256']
            or sha(paths['form'])!=c['form_sha256'] or sha(paths['invoices'])!=c['manifest_sha256']
            or c['upload_contract']!=json.loads((ROOT/'data/finance_upload_contract.json').read_text())
            or c['pool_contracts']!=json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['invoices']):raise ValueError('原HTTP上传源码、固定合同或案件输入漂移，只保留原任务')
    current=freeze_upload_batch(paths,c['case_id'],c['offset'],c['batch']['count'],expected_userid=c['expected_userid'])
    if current['batch']!=c['batch'] or (upload_sources(paths,current)if c.get('identity_contract')=='code-number-v2'else current['source_invoices'])!=c['sources']:raise ValueError('原件SHA或批次身份变化')
    if not c['execute'] or preview:
        frozen(output,{'ok':True,'state':'upload_resume_preview'if resume else'upload_preview','batch':c['batch'],
            'system_write':False,'business_transport':'native_http','requires_independent_readback':True});return 0
    pending=paths['state'].parent/'pending_write.json'
    if resume:
        previous=read_json(pending);v=previous.get('verification',{})
        if v.get('schema')!=SCHEMA or v.get('input')!=str(input_path) or v.get('input_sha256')!=sha(input_path):raise ValueError('须恢复原HTTP上传pending，不创建新意图')
    elif pending.exists():raise ValueError('已有未核写入，只恢复原任务')
    runtime=runtime or subprocess.run
    def call(action,label,**extra):
        receipt=folder/(label+'.json');script=receipt.with_suffix('.js')
        text='(async()=>{\n'+code+'\nreturn await reimbursementUploadApi('+json.dumps({**c,'action':action,**extra},ensure_ascii=False)+');})()'
        if receipt.exists():
            old=read_json(receipt)
            if script.read_text()!=text or old.get('source_sha256')!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('原上传子阶段源码变化')
            if old.get('phase')=='script_completed':
                if old.get('result',{}).get('ok')is not True:
                    if(resume and action=='start'and label=='upload-start'and old.get('result',{}).get('error')=='UPLOAD_API_PENDING_ORIGINAL'
                       and not list(folder.glob('file-*-upload.json'))and not (folder/'upload-save.json').exists()):
                        # Only browser-local staging was rejected. The fixed JS
                        # still refuses any unreconciled prior registry; no byte
                        # upload/save has been dispatched for this original job.
                        return call('start','upload-start-after-release',**extra)
                    if resume and action=='save':return call('status','upload-save-status')
                    raise ValueError('原上传子阶段明确失败，不能重发')
                return old['result']
            if not resume:raise ValueError('原上传子阶段尚未确认，只恢复原marker')
            from registered_page_owner import RegisteredPageOwner
            RegisteredPageOwner.before_child_dispatch(c['tab_id'])
            if (old.get('phase')=='not_dispatched' and old.get('result',{}).get('injection_acknowledged')is False
                    and 'tab_window_mismatch'in old.get('result',{}).get('error','')):
                after=folder/(label+'-after-binding.json')
                if not after.exists():
                    result=runtime([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(after),
                        '--tab-id',c['tab_id'],'--operation-kind','write'if action in {'upload','save'}else'browser-local','--quiet'])
                    if result.returncode:raise ValueError('原未派发阶段绑定恢复仍失败，不再次尝试')
                proof=read_json(after)
                if proof.get('phase')!='script_completed' or proof.get('source_sha256')!=old['source_sha256'] or proof.get('result',{}).get('ok')is not True:
                    raise ValueError('原绑定恢复阶段尚未确认，不重新派发')
                return proof['result']
            recovered=folder/(label+'-original-marker.json')
            if recovered.exists():body=read_json(recovered)['result']
            else:
                from finance_backend import assert_original_transport
                from edge_runtime import read_marker
                assert_original_transport(old)
                if old.get('phase')=='not_dispatched':
                    raise ValueError('原上传子阶段明确未派发，保留其回执等待专用恢复，不注入同一写脚本')
                body=read_marker(old['result_marker'],c['tab_id'],timeout=45)
                frozen(recovered,{'result':body,'original_receipt_sha256':sha(receipt),'script_reexecuted':False,
                    'observed_at_utc':datetime.now(timezone.utc).isoformat()})
            if not isinstance(body,dict) or body.get('ok')is not True:
                if action=='save' and isinstance(body,dict) and body.get('ok')is False:return call('status','upload-save-status')
                raise ValueError('原上传marker未证明完成，禁止重传或重存')
            return body
        with script.open('x',encoding='utf-8',newline='\n')as stream:stream.write(text)
        script.chmod(0o600)
        from registered_page_owner import RegisteredPageOwner
        RegisteredPageOwner.before_child_dispatch(c['tab_id'])
        result=runtime([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(receipt),
            '--tab-id',c['tab_id'],'--operation-kind','write'if action in {'upload','save'}else'browser-local'if action in {'start','append','finish'}else'read','--quiet'])
        if result.returncode:raise ValueError('原上传子任务失败或未知；保留原pending，不重传')
        envelope=read_json(receipt);body=envelope.get('result',{})
        if envelope.get('phase')!='script_completed' or body.get('ok')is not True:raise ValueError('原上传子任务未确认')
        return body
    prepared=call('prepare','upload-prepare')
    if (prepared.get('complete')is not True or prepared.get('batch')!=c['batch'] or prepared.get('sources')!=c['sources']
            or prepared.get('definition_sha256')!=c['upload_contract']['definition_sha256']):raise ValueError('原上传预检不完整')
    if not resume:
        begin_write(paths,form,'invoice:http-upload',folder,{'schema':SCHEMA,'input':str(input_path),'input_sha256':sha(input_path),
            'prepare':str(folder/'upload-prepare.json'),'prepare_sha256':sha(folder/'upload-prepare.json'),
            'save_receipt':str(folder/'upload-save.json'),'form_sha256':c['form_sha256']})
    call('start','upload-start',expected_plan={k:prepared[k]for k in ['batch','sources','original_guids','definition_sha256']})
    for index,file in enumerate(c['sources']):
        data=Path(file['path']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=file['sha256']:raise ValueError('分块前原件SHA变化')
        for offset in range(0,len(data),131072):
            raw=data[offset:offset+131072]
            call('append',f'file-{index}-append-{offset}',index=index,offset=offset,base64=base64.b64encode(raw).decode(),chunk_sha256=hashlib.sha256(raw).hexdigest())
        call('upload',f'file-{index}-upload',index=index)
    saved=call('save','upload-save')
    frozen(output,{**saved,'action':'upload-save','complete':False,'state':'upload_save_dispatched_independent_inventory_required',
        'save_receipt':str(folder/'upload-save.json'),'financial_claim_created':False,'requires_independent_readback':True})
    if saved.get('phase')=='save_accepted':call('finish','upload-finish')
    return 0


def verify_ack(paths,previous,read,outcome):
    from assistant_api import read_json
    from reimburse import freeze_upload_batch
    v=previous['verification'];c=read_json(v['input']);prepared=read_json(v['prepare'])['result']
    if (v.get('schema')!=SCHEMA or previous.get('operation')!='invoice:http-upload' or sha(v['input'])!=v['input_sha256']
            or sha(v['prepare'])!=v['prepare_sha256'] or sha(paths['form'])!=v['form_sha256'] or sha(paths['invoices'])!=c['manifest_sha256']
            or sha(Path(v['input']).parent/'native-upload-adapter.js')!=c['adapter_sha256']):raise ValueError('原HTTP上传冻结证据变化')
    current=freeze_upload_batch(paths,previous['case_id'],c['offset'],c['batch']['count'],expected_userid=c['expected_userid'])
    if current['batch']!=c['batch'] or (upload_sources(paths,current)if c.get('identity_contract')=='code-number-v2'else current['source_invoices'])!=c['sources']:raise ValueError('原HTTP上传来源变化')
    r=read.get('result',{})
    if (read.get('phase')!='script_completed' or r.get('ok')is not True or r.get('complete')is not True or r.get('action')!='invoice-inventory'
            or r.get('case_id')!=c['case_id'] or r.get('expected_userid')!=c['expected_userid'] or r.get('business_transport')!='native_http'
            or r.get('system_write')is not False or r.get('identity',{}).get('userid')!=c['expected_userid']):raise ValueError('须为本案本人完整独立HTTP库存回读')
    grids={g['funcno']:g for g in r.get('grids',[])}
    if len(r.get('grids',[]))!=2 or set(grids)!={'53','113'} or any(g.get('complete')is not True or len(g.get('rows',[]))!=g.get('records')for g in grids.values()):raise ValueError('须完整票池及识别明细，不接受首屏')
    if outcome=='not_applied':
        return verify_before_upload(c,v,read)
    save_path=Path(v['save_receipt']);save=read_json(save_path)
    code=(Path(v['input']).parent/'native-upload-adapter.js').read_text()
    text='(async()=>{\n'+code+'\nreturn await reimbursementUploadApi('+json.dumps({**c,'action':'save'},ensure_ascii=False)+');})()'
    if (save_path!=Path(v['input']).parent/'upload-save.json' or save_path.with_suffix('.js').read_text()!=text
            or save.get('source_sha256')!=hashlib.sha256(text.encode()).hexdigest()):raise ValueError('原保存源码或阶段身份不同')
    if (save.get('phase')=='not_dispatched' and save.get('result',{}).get('injection_acknowledged')is False
            and 'tab_window_mismatch'in save.get('result',{}).get('error','')):
        after=read_json(save_path.with_name('upload-save-after-binding.json'))
        if after.get('phase')!='script_completed' or after.get('source_sha256')!=save['source_sha256']:
            raise ValueError('原保存明确未派发后的首次派发尚未确认')
        save=after
    if save.get('phase')!='script_completed':
        recovered=read_json(save_path.with_name('upload-save-original-marker.json'))
        if recovered.get('script_reexecuted')is not False or recovered.get('original_receipt_sha256')!=sha(save_path) or not isinstance(recovered.get('result'),dict):
            raise ValueError('未知原保存尚未取得原marker完成证明')
        save={**save,'phase':'script_completed','result':recovered['result'],'executed_at_utc':recovered['observed_at_utc']}
    saved=save.get('result',{})
    if saved.get('save_dispatched')is not True:
        status_path=save_path.with_name('upload-save-status.json');status=read_json(status_path)
        status_text='(async()=>{\n'+code+'\nreturn await reimbursementUploadApi('+json.dumps({**c,'action':'status'},ensure_ascii=False)+');})()'
        if (status.get('phase')!='script_completed' or status.get('source_sha256')!=hashlib.sha256(status_text.encode()).hexdigest()
                or status_path.with_suffix('.js').read_text()!=status_text or not status.get('executed_at_utc')
                or not save.get('executed_at_utc') or datetime.fromisoformat(status['executed_at_utc'])<datetime.fromisoformat(save['executed_at_utc'])):
            raise ValueError('原保存失败后须只读原上传状态，不能重发保存')
        save={**save,'executed_at_utc':status['executed_at_utc']};saved=status.get('result',{})
    if (save.get('phase')!='script_completed' or saved.get('token')!=c['token'] or saved.get('batch')!=c['batch']
            or saved.get('save_dispatched')is not True or not save.get('executed_at_utc')
            or not read.get('dispatch_intent_at_utc') or datetime.fromisoformat(read['dispatch_intent_at_utc'])<=datetime.fromisoformat(save['executed_at_utc'])):
        raise ValueError('须在原保存脚本结束后新发库存回读，未知原marker先恢复')
    uploaded=saved.get('files',[])
    if len(uploaded)!=len(c['sources']):raise ValueError('缺少本批全部filecode')
    accounted=[];codes=set();guids=set()
    for source,file in zip(c['sources'],uploaded):
        code=file.get('filecode','')
        if file.get('name')!=source['name'] or file.get('sha256')!=source['sha256'] or file.get('phase')!='upload_received' or not re.fullmatch(r'[A-Fa-f0-9]{32}',code) or code.lower()in codes:raise ValueError('原上传filecode、文件名或SHA不完整')
        codes.add(code.lower())
        identities=source.get('invoice_identities')or[{'kind':'vat','code':'','number':n}for n in source['invoice_numbers']]
        errors=[x for x in grids['113']['rows']if x.get('t.show_filename')==source['name'] and str(x.get('t.filesequcode','')).lower()==code.lower()]
        if len(errors)>1:raise ValueError('本批识别错误filecode重复')
        recognized=[];unresolved=[]
        for identity in identities:
            pool=source_pool_rows(grids['53']['rows'],identity)
            if len(pool)>1:raise ValueError('同一票据身份对应多个池GUID')
            if not pool:
                unresolved.append(identity);continue
            row=pool[0];guid=row.get('t.tk_guid','')
            if (not re.fullmatch(r'[A-Fa-f0-9]{32}',guid) or guid.lower()in {x.lower()for x in prepared['original_guids']} or guid.lower()in guids
                    or row.get('t.tk_mod_flag')!='NORMAL' or row.get('t.auth_rst_desc')!='正常(已认证)'):
                raise ValueError('本批认证GUID或状态未完整确认')
            guids.add(guid.lower());recognized.append({'native_guid':guid,'invoice_number':identity['number'],'invoice_identity':identity})
        if unresolved and not errors:raise ValueError('原件仍有未归账票据，只能继续只读查询')
        if errors:
            message=errors[0].get('t.show_err_msg')
            if not isinstance(message,str) or not message.strip():raise ValueError('本批仍在识别，只能继续只读查询')
            entry={'name':source['name'],'sha256':source['sha256'],'filecode':code,'state':'recognition_error','error':message,
                   'recognized_invoices':recognized,'unresolved_identities':unresolved,'partial_recognition':bool(recognized)}
        else:
            entry={'name':source['name'],'sha256':source['sha256'],'filecode':code,'state':'pool_authenticated',
                   'recognized_invoices':recognized,'buyer_fields_verified':False,'claim_amount_verified':False,'official_tax_receipt_verified':False}
            if len(recognized)==1:entry.update(native_guid=recognized[0]['native_guid'],invoice_number=recognized[0]['invoice_number'])
        accounted.append(entry)
    if c.get('identity_contract')=='code-number-v2':
        originals={g.lower()for g in prepared['original_guids']}
        current_guids={str(row.get('t.tk_guid','')).lower()for row in grids['53']['rows']}
        if not originals.issubset(current_guids):raise ValueError('上传前原票池记录消失，须先核对并发变更')
        added=[str(row.get('t.tk_guid','')).lower()for row in grids['53']['rows']if str(row.get('t.tk_guid','')).lower()not in originals]
        if len(added)!=len(set(added))or set(added)!=guids:raise ValueError('本批出现未登记票据或并发入池，不能宣称原件逐票归账')
    has_errors=any(x['state']=='recognition_error'for x in accounted)
    if outcome!=('needs_correction'if has_errors else'saved'):raise ValueError('识别错误须needs_correction，已认证用saved；未知不得清锁')
    return {'schema':SCHEMA,'phase':'http_save_reconciled_with_errors'if has_errors else'http_save_reconciled','token':c['token'],
        'batch':c['batch'],'accounted_files':accounted,'independent_http_readback':True,'saved':not has_errors}


def verify_before_upload(c,verification,read):
    """只有所有已准备脚本均已停止且仅为只读/本地阶段，才认定从未上传。"""
    from assistant_api import read_json
    folder=Path(verification['input']).parent;code=(folder/'native-upload-adapter.js').read_text()
    scripts=[p for p in folder.glob('*.js')if p.name!='native-upload-adapter.js']
    if not scripts or not read.get('dispatch_intent_at_utc'):raise ValueError('缺少上传派发前完整停止证明')
    prefix='(async()=>{\n'+code+'\nreturn await reimbursementUploadApi('
    for script in scripts:
        text=script.read_text()
        if not text.startswith(prefix) or not text.endswith(');})()'):raise ValueError('原上传目录含未核脚本，不能判未上传')
        config=json.loads(text[len(prefix):-len(');})()')])
        if config.get('action')not in {'prepare','start','append'} or any(config.get(k)!=value for k,value in c.items()):raise ValueError('已经准备上传或保存请求，不能判未执行')
        receipt=read_json(script.with_suffix('.json'))
        if (receipt.get('source_sha256')!=hashlib.sha256(text.encode()).hexdigest()
                or receipt.get('phase')not in {'script_completed','not_dispatched'}
                or receipt.get('phase')=='not_dispatched' and receipt.get('result',{}).get('injection_acknowledged')is not False
                or not receipt.get('executed_at_utc') or datetime.fromisoformat(read['dispatch_intent_at_utc'])<=datetime.fromisoformat(receipt['executed_at_utc'])):
            raise ValueError('原本地脚本仍未知，先恢复原marker，不解锁')
    return {'schema':SCHEMA,'phase':'http_upload_not_applied','token':c['token'],'batch':c['batch'],
        'no_upload_dispatched':True,'independent_http_readback':True,'saved':False}




def validate_error_binding(binding,case_id,expected_userid):
    """从原HTTP上传的不可变ACK证书核错误队列与原件，不凭文件名猜SHA。"""
    from assistant_api import read_json
    refs=[binding.get('acknowledgement',{}),binding.get('input',{})]
    if binding.get('schema')!='ustc-native-upload-error-binding-v1':raise ValueError('原上传关联类型不符')
    for ref in refs:
        if not Path(ref.get('path','')).is_file()or sha(ref['path'])!=ref.get('sha256'):raise ValueError('原上传关联证据SHA变化')
    ack,c=read_json(refs[0]['path']),read_json(refs[1]['path'])
    v=ack.get('verification',{});cert=ack.get('upload_reconciliation',{})
    if (ack.get('case_id')!=case_id or ack.get('operation')!='invoice:http-upload' or ack.get('outcome')!='needs_correction'
        or ack.get('status')!='operator_readback_recorded' or v.get('schema')!=SCHEMA or cert.get('schema')!=SCHEMA
        or cert.get('independent_http_readback')is not True or v.get('input')!=refs[1]['path']or v.get('input_sha256')!=refs[1]['sha256']
        or c.get('schema')!=SCHEMA or c.get('case_id')!=case_id or c.get('expected_userid')!=expected_userid
        or c.get('batch',{}).get('caseId')!=case_id or c.get('batch',{}).get('expectedUserid')!=expected_userid or c.get('batch')!=cert.get('batch')):
        raise ValueError('原上传ACK不是同案本人已独立归账的识别错误')
    errors=[f for f in cert.get('accounted_files',[])if f.get('state')=='recognition_error'and f.get('filecode')==binding.get('filecode')
            and f.get('name')==binding.get('filename')and f.get('sha256')==binding.get('source_sha256')]
    sources=[f for f in c.get('sources',[])if f.get('sha256')==binding.get('source_sha256')and f.get('name')==binding.get('filename')
             and f.get('invoice_numbers')==binding.get('invoice_numbers',[binding.get('invoice_number')])]
    if len(errors)!=1 or len(sources)!=1 or sha(sources[0]['path'])!=binding['source_sha256']:
        raise ValueError('原filecode、文件名、票号或原件SHA未唯一关联')
    if errors[0].get('partial_recognition')is True:raise ValueError('合并原件已部分入池，禁止整份重识别；先拆分并核对剩余票据')
    return binding


def acknowledged_error_bindings(service,case_id,expected_userid):
    from assistant_api import read_json
    _,paths=service.resolve(case_id);state=read_json(paths['state'])
    refs=state.get('native_upload_acknowledgements',[])
    if not isinstance(refs,list)or len(refs)>500:raise ValueError('本案上传ACK历史不完整或超出预算')
    result=[]
    for ref in refs:
        path=Path(ref.get('path','')).resolve()
        if not any(path.is_relative_to(r.resolve())for r in [service.jobs/case_id,paths['state'].parent/'receipts']):
            raise ValueError('上传ACK不属于本案正式工作目录')
        if not path.is_file()or sha(path)!=ref.get('sha256'):raise ValueError('原上传ACK SHA变化')
        ack=read_json(path);cert=ack.get('upload_reconciliation',{})
        if cert.get('schema')!=SCHEMA or ack.get('outcome')!='needs_correction':continue
        v=ack.get('verification',{});c=read_json(v['input'])
        for file in cert.get('accounted_files',[]):
            if file.get('state')!='recognition_error':continue
            candidates=[s for s in c['sources']if s['sha256']==file['sha256']and s['name']==file['name']]
            if len(candidates)!=1:raise ValueError('原上传错误来源不唯一')
            if file.get('partial_recognition')is True or len(candidates[0]['invoice_numbers'])!=1:continue
            binding={'schema':'ustc-native-upload-error-binding-v1','filecode':file['filecode'],'filename':file['name'],
                'source_sha256':file['sha256'],'invoice_number':candidates[0]['invoice_numbers'][0],
                'invoice_numbers':candidates[0]['invoice_numbers'],
                'acknowledgement':ref,'input':{'path':v['input'],'sha256':v['input_sha256']}}
            validate_error_binding(binding,case_id,expected_userid);result.append(binding)
    unique={}
    for b in result:
        key=b['filecode']
        if key in unique and unique[key]!=b:raise ValueError('原上传filecode关联冲突')
        unique[key]=b
    return list(unique.values())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--resume',action='store_true');p.add_argument('--preview',action='store_true')
    a=p.parse_args();raise SystemExit(run(a.input,a.output,resume=a.resume,preview=a.preview))
