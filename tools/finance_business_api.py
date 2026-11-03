"""固定底层业务接口的参数与脚本owner；不接受任意URL、JS或过程名。"""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ('approval-context', 'approval-history', 'approval-log', 'approval-filters', 'approvals-list', 'bills-list', 'bill-read', 'reference-bill-read', 'staff-lookup', 'invoices-list', 'recognition-list', 'invoice-inventory', 'upload-prepare', 'create-prepare', 'definitions', 'print-coverage', 'basic-prepare', 'payments-prepare', 'notes-prepare', 'pre-review-prepare', 'bills-export', 'bills-export-recover', 'categories-prepare', 'category-candidates', 'funding-prepare', 'expenses-prepare')
DOMAINS = ('bills', 'details', 'basic', 'funding', 'payments', 'categories', 'submission', 'approval', 'invoices', 'recognition', 'invoice_edit', 'remuneration', 'notes', 'pre_review', 'used_invoices','invoice_allocations','travel')
READ_DOMAINS = ('basic', 'funding', 'payments', 'categories', 'submission', 'remuneration', 'notes', 'pre_review', 'used_invoices','invoice_allocations','travel')
REFERENCE_SUMMARY_DOMAINS = ('basic', 'funding', 'payments', 'categories', 'submission', 'notes', 'pre_review')


def valid_notes_xtype(value, serial=None):
    if not isinstance(value,str) or len(value)>120:return False
    parts=value.split('&')
    if len(parts)!=2 or not all(re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff|]+',p)for p in parts):return False
    if parts[0]==parts[1] and '|'not in parts[0]:return True
    return parts[1]=='CHECK_DW' and bool(re.fullmatch(r'CHECK_DW\|[0-9]{1,20}',parts[0])) and (serial is None or parts[0]=='CHECK_DW|'+serial)


def validate(parameters):
    if not isinstance(parameters, dict) or set(parameters) - {'action', 'domain', 'expected_userid', 'tab_id', 'source_job_id', 'print_job_id', 'reference_serial', 'staff_query', 'notes_xtype'}:
        raise ValueError('底层业务接口只接受登记参数')
    if parameters.get('action') not in ACTIONS:
        raise ValueError('未知底层业务动作')
    if any(not isinstance(parameters.get(k), str) for k in ('expected_userid', 'tab_id')) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', parameters['expected_userid']) or not re.fullmatch(r'\d+', parameters['tab_id']):
        raise ValueError('须明确当前账号及财务标签')
    if parameters['action'] == 'definitions':
        if parameters.get('domain') not in DOMAINS:
            raise ValueError('须选已登记业务域')
    elif parameters['action'] in {'bill-read', 'reference-bill-read'}:
        if parameters.get('domain') not in READ_DOMAINS and not (parameters['action']=='reference-bill-read' and parameters.get('domain')=='archive-summary'):
            raise ValueError('单据读取须选已登记读取业务域：' + '、'.join(READ_DOMAINS))
    elif parameters.get('domain') is not None:
        raise ValueError('清单查询不接受业务域参数')
    if parameters['action'] in {'reference-bill-read','approval-log','approval-context'}:
        if (not isinstance(parameters.get('source_job_id'), str)
                or not re.fullmatch(r'j_[a-f0-9]{24}', parameters['source_job_id'])
                or not isinstance(parameters.get('reference_serial'), str)
                or not re.fullmatch(r'[0-9]{1,20}', parameters['reference_serial'])
                or parameters.get('print_job_id') is not None):
            raise ValueError('参考单读取须同案完整本人清单任务及清单内单号')
    elif parameters['action'] in {'bills-export','bills-export-recover'}:
        if not isinstance(parameters.get('source_job_id'),str) or not re.fullmatch(r'j_[a-f0-9]{24}',parameters['source_job_id']) or parameters.get('print_job_id')is not None:
            raise ValueError('原生清单导出须引用同案完整bills-list任务')
    elif parameters['action'] == 'print-coverage':
        if any(not isinstance(parameters.get(k), str) or not re.fullmatch(r'j_[a-f0-9]{24}', parameters[k]) for k in ('source_job_id', 'print_job_id')):
            raise ValueError('覆盖核验须引用本案完整清单及打印任务')
    elif any(parameters.get(k) is not None for k in ('source_job_id', 'print_job_id')):
        raise ValueError('此动作不接受来源任务参数')
    if parameters['action'] not in {'reference-bill-read','approval-log','approval-context'} and parameters.get('reference_serial') is not None:
        raise ValueError('只有参考单读取接受reference_serial，不能覆盖本案单号')
    if parameters['action']=='staff-lookup':
        q=parameters.get('staff_query')
        if not isinstance(q,str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}',q) or not re.search(r'[0-9]',q) or re.match(r'^(PB|SA|SB|BA)[0-9]',q,re.I):
            raise ValueError('教师查询须明确教工号，不接学生号、通配符或过程名')
    elif parameters.get('staff_query')is not None:
        raise ValueError('只有教师只读查询接受staff_query')
    if parameters.get('notes_xtype') is not None:
        if parameters['action'] != 'bill-read' or parameters.get('domain') != 'notes' or not valid_notes_xtype(parameters['notes_xtype']):
            raise ValueError('事项定位仅用于notes只读，须有效类型与同类标识')
    return parameters


def reference_context(service, request):
    """从已核完整清单选本人参考单；不登记成当前案件单号或写入来源。"""
    from validate_config import money
    import hashlib
    parameters = request['parameters']
    case_id, job_id = request['case_id'], parameters['source_job_id']
    result = service.result(case_id, job_id)
    if result.get('status') != 'completed' or result.get('exit_code') != 0:
        raise ValueError('参考单来源任务须完整成功')
    artifacts = [a for a in result.get('artifacts', []) if a.get('name') == 'output.json']
    if len(artifacts) != 1:
        raise ValueError('参考单来源须唯一正式JSON回执')
    path, envelope, prior = service._typed_case_artifact(
        case_id, job_id, artifacts[0]['artifact_id'], 'business', ['bills-list'])
    value = envelope.get('result', {})
    who = parameters['expected_userid']
    rows = value.get('rows')
    if (prior.get('execute') is not False or prior.get('parameters', {}).get('expected_userid') != who
            or envelope.get('phase') != 'script_completed' or value.get('ok') is not True
            or value.get('action') != 'bills-list' or value.get('complete') is not True
            or value.get('case_id') != case_id or value.get('expected_userid') != who
            or value.get('system_write') is not False or value.get('business_transport') != 'native_http'
            or value.get('account_scope') != 'current_applicant' or not isinstance(rows, list)
            or type(value.get('total_records')) is not int or value['total_records'] != len(rows)):
        raise ValueError('参考单来源须同案同账号完整原生本人清单')
    serials = [r.get('t.serial_no') for r in rows]
    if (any(not isinstance(s, str) or not re.fullmatch(r'[0-9]+', s) for s in serials)
            or len(set(serials)) != len(serials)
            or any(not isinstance(r.get('t.REQ_INFO'), str) or not r['t.REQ_INFO'].startswith(who + '-') for r in rows)):
        raise ValueError('参考单来源存在重复或非本人单据')
    target = [r for r in rows if r['t.serial_no'] == parameters['reference_serial']]
    if len(target) != 1:
        raise ValueError('参考单号不在已核本人清单')
    amount = target[0].get('t.total_amt')
    if not isinstance(amount, str) or not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\.[0-9]{2}', amount):
        raise ValueError('参考单清单金额格式异常')
    return dict(action='bill-read', serial=parameters['reference_serial'],
                expected_amount=str(money(amount.replace(',', ''))),
                reference_bill={'summary': target[0].get('t.abst'), 'business_date': target[0].get('t.req_date'),
                                'sequence': serials.index(parameters['reference_serial']) + 1,
                                'bill_type': target[0].get('t.bx_type')},
                reference_only=True, reference_source={'job_id': job_id, 'artifact_id': artifacts[0]['artifact_id'],
                    'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})


def command(service, request, folder):
    parameters = validate(request['parameters'])
    if request['execute']:
        raise ValueError('此底层查询动作不接受execute')
    if parameters['action']in {'categories-prepare','category-candidates','funding-prepare','expenses-prepare'}:
        from finance_native_stage import freeze_input
        bundle,paths=service.resolve(request['case_id'])
        stage='categories'if parameters['action']=='category-candidates'else parameters['action'].removesuffix('-prepare')
        path=freeze_input(bundle,paths,{**request,'parameters':{'stage':stage,'tab_id':parameters['tab_id']}},folder)
        config=json.loads(path.read_text());config['expected_userid']=parameters['expected_userid']
        from finance_http import source
        method='reimbursementCategoriesCandidates'if parameters['action']=='category-candidates'else'reimbursement'+stage.title()+'Prepare'
        script='(async()=>{\n'+source(stage)+'\nreturn await '+method+'('+json.dumps(config,ensure_ascii=False)+');\n})()'
        target=folder/'categories-read.js'
        if target.exists():
            if target.read_text()!=script:raise ValueError('原分类读取源码漂移，不覆盖')
        else:
            target.write_text(script,encoding='utf-8',newline='\n');target.chmod(0o600)
        return[service.python,str(ROOT/'tools/edge_runtime.py'),str(target),'--output',str(folder/'output.json'),'--tab-id',config['tab_id'],'--operation-kind','read','--quiet']
    if parameters['action']=='upload-prepare':
        from finance_native_upload import preparation_command
        return preparation_command(service,request,folder)
    if parameters['action']=='create-prepare':
        from finance_native_create import command as create_command
        return create_command(service,request,folder,inspect_only=True)
    if parameters['action']in {'bills-export','bills-export-recover'}:
        from finance_grid_export import command as export_command
        return export_command(service,request,folder)
    if parameters['action'] == 'print-coverage':
        inputs, sources = {}, {}
        for key, operation in [('source_job_id', 'business'), ('print_job_id', 'print_batch')]:
            job = parameters[key]
            source_folder = service.job_folder(request['case_id'], job)
            source_request = json.loads((source_folder / 'request.json').read_text())
            status = service.result(request['case_id'], job)
            if source_request['operation'] != operation or status['status'] != 'completed':
                raise ValueError('原任务类型或完成状态不同')
            artifacts = [a for a in status['artifacts'] if a['name'] == 'output.json']
            if len(artifacts) != 1:
                raise ValueError('原任务输出不唯一')
            artifact = service.artifact(request['case_id'], job, artifacts[0]['artifact_id'])
            value = json.loads(artifact.read_text())
            if key == 'source_job_id':
                if source_request['parameters']['action'] != 'bills-list' or value.get('phase') != 'script_completed':
                    raise ValueError('须引用完整原生清单回执')
                value = value['result']
            inputs['snapshot' if key == 'source_job_id' else 'print_report'] = value
            sources[key] = {'job_id': job, 'artifact': str(artifact), 'sha256': artifacts[0]['sha256']}
        from material_intake import reject_links
        delivery = inputs['print_report']['delivery']
        files = [d['pdf'] for d in delivery['binding']['documents'] if d.get('pdf')]
        files += [delivery['receipt'], delivery['merged_pdf']]
        for filename in files:
            path = Path(filename)
            reject_links(path)
            if not path.is_absolute() or not any(path.resolve().is_relative_to(root) for root in service.material_roots):
                raise ValueError('打印文件不在本机授权资料根')
        data = {**inputs, 'expected_userid': parameters['expected_userid'], 'sources': sources}
        path = folder / 'coverage-input.json'
        encoded = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        if path.exists() and path.read_text() != encoded:
            raise ValueError('同一覆盖核验来源发生变化')
        if not path.exists():
            path.write_text(encoded,encoding='utf-8',newline='\n'); path.chmod(0o600)
        return [service.python, str(ROOT / 'tools/print_coverage.py'), '--input', str(path), '--output', str(folder / 'output.json')]
    config = {**parameters, 'case_id': request['case_id']}
    if parameters['action']in {'approval-log','approval-context'}:
        from finance_approval_read import source_context
        config['approval_target']=source_context(service,request)
        config['contracts']=[c for c in json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['submission'] if c['funcno'] in ({'109','1816'} if parameters['action']=='approval-context' else {'1816'})]
        config['approval_history_contracts']=json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']['approval_history']
    if parameters['action']=='staff-lookup':
        config['basic_contract']=json.loads((ROOT/'data/finance_basic_contract.json').read_text())
    if parameters['action'] == 'reference-bill-read':
        config.update(reference_context(service, request))
        domains = json.loads((ROOT / 'data/finance_business_contracts.json').read_text())['domains']
        if parameters['domain']=='archive-summary':
            config['reference_summary_contracts']={name:domains[name] for name in REFERENCE_SUMMARY_DOMAINS}
        else:
            config['contracts'] = domains[parameters['domain']]
    if parameters['action'] in {'approvals-list','approval-history'}:
        config['contracts'] = json.loads((ROOT / 'data/finance_business_contracts.json').read_text())['domains']['approval_history' if parameters['action']=='approval-history' else 'approval']
    if parameters['action'] in {'invoices-list','recognition-list'}:
        config['contracts'] = json.loads((ROOT / 'data/finance_business_contracts.json').read_text())['domains']['recognition' if parameters['action']=='recognition-list' else 'invoices']
    if parameters['action']=='invoice-inventory':
        domains=json.loads((ROOT/'data/finance_business_contracts.json').read_text())['domains']
        config['contracts']={name:domains[name]for name in ['invoices','recognition']}
    if parameters['action'] in {'bill-read', 'basic-prepare', 'payments-prepare', 'notes-prepare', 'pre-review-prepare'}:
        from validate_config import load_context, money
        _, paths = service.resolve(request['case_id'])
        form = load_context(paths['form'])
        if form.get('expected_userid') not in (None, '', parameters['expected_userid']):
            raise ValueError('本案账号与请求不同')
        config.update(serial=form['serial'], expected_amount=str(money(form['amount']).quantize(money('0.01'))))
        if parameters.get('notes_xtype') is not None and not valid_notes_xtype(parameters['notes_xtype'],form['serial']):
            raise ValueError('事项定位不属于本案原单')
        domain = parameters['action'].removesuffix('-prepare').replace('-','_') if parameters['action'].endswith('-prepare') else parameters['domain']
        config['contracts'] = json.loads((ROOT / 'data/finance_business_contracts.json').read_text())['domains'][domain]
        if parameters['action'] == 'basic-prepare':
            from validate_config import load_basic
            config['basic'] = load_basic(paths['form'])['basic']
            config['basic_contract'] = json.loads((ROOT / 'data/finance_basic_contract.json').read_text())
    if parameters['action'] == 'notes-prepare':
        from validate_config import load
        config['explanations'] = load(paths['form']).get('explanations')
        config['notes_contract'] = json.loads((ROOT / 'data/finance_notes_contract.json').read_text())
    if parameters['action']=='payments-prepare':
        from validate_config import load
        config['payments']=load(paths['form'])['payments']
        config['payments_contract']=json.loads((ROOT/'data/finance_payments_contract.json').read_text())
        if any(p.get('mode')=='corporate_card'for p in config['payments']):config['card_contract']=json.loads((ROOT/'data/finance_card_contract.json').read_text())
        if any(p.get('mode')=='corporate_transfer'for p in config['payments']):config['corporate_contract']=json.loads((ROOT/'data/finance_corporate_contract.json').read_text())
    if parameters['action'] == 'pre-review-prepare':
        from validate_config import load
        config['pre_review'] = load(paths['form']).get('pre_review',[])
        config['pre_review_contract'] = json.loads((ROOT / 'data/finance_pre_review_contract.json').read_text())
    from finance_http import source
    prepares={'basic-prepare':('basic','reimbursementBasicPrepare'), 'payments-prepare':('payments','reimbursementPaymentsPrepare'), 'notes-prepare':('notes','reimbursementNotesPrepare'),
              'pre-review-prepare':('pre-review','reimbursementPreReviewPrepare')}
    adapter, method = prepares.get(parameters['action'],('business','reimbursementBusinessApi'))
    if parameters['action']=='staff-lookup':adapter,method='staff','reimbursementStaffLookup'
    invocation = method + '(' + json.dumps(config, ensure_ascii=False) + ')'
    if parameters['action']=='definitions' and parameters['domain']=='submission':
        contract=json.loads((ROOT/'data/finance_applicant_contract.json').read_text())
        invocation='(async()=>{const value=await '+invocation+';const expected='+json.dumps(contract)+';const text=value.native_applicant_checks.before_sp_yzm;'\
            'if(typeof text!=="string")throw Error("APPLICANT_VERIFICATION_GUARD_MISSING");'\
            'const hash=[...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(text)))].map(n=>n.toString(16).padStart(2,"0")).join("");'\
            'if(hash!==expected.verification_guard_sha256)throw Error("APPLICANT_VERIFICATION_GUARD_CHANGED");'\
            'const requests=[];const h=createReimbursementHttp('+json.dumps(config['expected_userid'])+', {prefix:"APPLICANT_VERIFICATION",requests,timeout:15000,allowed:["applicantVerificationStatus"]});'\
            'const reply=await h.post("activation_ifsend.action",{userid:'+json.dumps(config['expected_userid'])+'});'\
            'if(!reply||typeof reply.status!=="string")throw Error("APPLICANT_VERIFICATION_STATUS_UNKNOWN");'\
            'return{...value,applicant_verification:{native_status:reply.status,verified:reply.status==="yyz",code_requested:false,approval_dispatched:false},verification_requests:requests};})()'
    if parameters['action']=='definitions' and parameters['domain']=='basic':
        invocation='(async()=>{const value=await '+invocation+';const requests=[];const h=createReimbursementHttp('+json.dumps(config['expected_userid'])+', {prefix:"BASIC_OPTIONS",requests,timeout:15000,allowed:["binding"]});'\
            'const return_points=ReimbursementHttpValues.formRows(await h.post("common_bindSQLDataBackend.action",{sqlid:"ab0cd169c9bf010dab8bdb1b54457eca",cvstr:"",projid:"WF_YBX"},"text"));return{...value,return_points,source_requests:requests};})()'
    if parameters['action']=='definitions' and parameters['domain']=='invoices':
        # 上传保存后的原生入口及OCR回调只读留证，不执行页面动作或重发识别。
        names=list(json.loads((ROOT/'data/finance_retry_contract.json').read_text())['ocr_callbacks'])+['pdf_v2','pdfInvoice_succCallBack','pdfInvoice_failCallBack']
        _, upload_paths=service.resolve(request['case_id'])
        pending=upload_paths['state'].parent/'pending_write.json'
        upload_files=[]
        if pending.is_file():
            prior=json.loads(pending.read_text())
            if prior.get('verification',{}).get('schema')=='ustc-native-http-upload-v1':
                from finance_upload_recognition import original
                original_input=Path(prior['verification']['input'])
                upload_config,upload_files=original(upload_paths,original_input)
                if upload_config['expected_userid']!=config['expected_userid']:raise ValueError('原上传与当前查询账号不同')
        invocation='(async()=>{const value=await '+invocation+';const requests=[];const h=createReimbursementHttp('+json.dumps(config['expected_userid'])+', {prefix:"UPLOAD_SOURCE",requests,timeout:15000,allowed:["definition","binding"]});'\
            'const entry=await h.post("loadDefinition.action?type=W",{winno:"9",projid:"WF_YBX"});'\
            'const manual=await h.post("loadDefinition.action?type=W",{winno:"61",projid:"WF_YBX"});'\
            'if(manual.funcs.find(f=>String(f.funcno)==="86")?.formFields.find(f=>f.fieldname==="t.inv_type")?.binding_data!=="@$af9dbd21084129048c30825811dfe916|")throw Error("MANUAL_INVOICE_TYPES_CHANGED");'\
            'const manual_invoice_types=ReimbursementHttpValues.formRows(await h.post("common_bindSQLDataBackend.action",{sqlid:"af9dbd21084129048c30825811dfe916",cvstr:"",projid:"WF_YBX"},"text"));'\
            'const supplementary_definitions=[];for(const winno of ["551","288","301","428","353","354","867","868","41","1492","1991","128","1525","352"]){supplementary_definitions.push({winno,definition:await h.post("loadDefinition.action?type=W",{winno,projid:"WF_YBX"})});}'\
            'const nonvat_fee_catalog=ReimbursementHttpValues.formRows(await h.post("common_bindSQLDataBackend.action",{sqlid:"1b04a53e315a2cd15ce3c7887f92f3b7",cvstr:"#0-tk_type#::FEEOTHER",projid:"WF_YBX"},"text"));'\
            'const uploaded_source_readback=[];for(const f of '+json.dumps(upload_files,ensure_ascii=False)+'){'\
            'h.guard();const u=new URL("fileSystem_getAction.action",location.href);u.search=new URLSearchParams({seq:f.filecode,minFile:"0",type:"."+f.name.split(".").at(-1),projid:"WF_YBX"});'\
            'const response=await fetch(u.href,{credentials:"same-origin",redirect:"error",signal:AbortSignal.timeout(15000)});const bytes=new Uint8Array(await response.arrayBuffer());h.guard();'\
            'const sha=[...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(n=>n.toString(16).padStart(2,"0")).join("");'\
            'uploaded_source_readback.push({filecode:f.filecode,name:f.name,http_status:response.status,size:bytes.length,sha256:sha,source_sha256:f.sha256,source_bytes_match:response.ok&&sha===f.sha256});}'\
            'return{...value,native_invoice_entry:entry,native_invoice_manual:manual,manual_invoice_types,nonvat_fee_catalog,supplementary_definitions,uploaded_source_readback,native_ajax_source:String(window.jQuery.ajax),native_ajax_datafilter_source:String(window.jQuery.ajaxSettings?.dataFilter),'\
            'native_recognition_timings:performance.getEntriesByType("resource").filter(e=>e.name===location.origin+"/YBX/thirdPartyInterface.action").slice(-20).map(e=>({duration:e.duration,initiatorType:e.initiatorType,transferSize:e.transferSize,responseStatus:e.responseStatus})),ocr_callback_sources:Object.fromEntries('+json.dumps(names)+'.map(name=>[name,String(name==="callInterface"?window.jQuery.I?.callInterface:window[name])])),source_requests:requests};})()'
    if parameters['action'] == 'reference-bill-read':
        if parameters['domain']=='archive-summary':
            invocation = '(async()=>{const c='+json.dumps(config,ensure_ascii=False)+';const domains=[];for(const domain of '+json.dumps(REFERENCE_SUMMARY_DOMAINS)+'){'\
                'const value=await reimbursementBusinessApi({...c,domain,contracts:c.reference_summary_contracts[domain]});'\
                'if(value.ok!==true||value.complete!==true||value.serial!==c.serial||value.expected_userid!==c.expected_userid||value.system_write!==false)throw Error("REFERENCE_SUMMARY_INCOMPLETE");'\
                'domains.push({domain,result:value});}return{ok:true,complete:true,action:"reference-bill-read",reference_only:true,'\
                'case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:"native_http",system_write:false,reference_source:c.reference_source,domains};})()'
        else:
            invocation += '.then(value => ({...value, reference_only:true, reference_source:' + json.dumps(config['reference_source'], ensure_ascii=False) + '}))'
    script = '(async()=>{\n' + source(adapter) + '\nreturn await ' + invocation + ';\n})()'
    path = folder / 'business-api.js'
    if path.exists():
        if path.read_text() != script:
            raise ValueError('同一任务脚本发生变化，不覆盖或重放')
    else:
        path.write_text(script,encoding='utf-8',newline='\n')
        path.chmod(0o600)
    return [service.python, str(ROOT / 'tools/edge_runtime.py'), str(path), '--output', str(folder / 'output.json'),
            '--tab-id', parameters['tab_id'], '--operation-kind', 'read', '--quiet']
