"""HTTP票池与本案冻结GUID集：只修改本地manifest，财务建单是独立动作。"""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import re
import sys

SCHEMA='ustc-native-case-selection-v1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_context(paths,request):
    from assistant_api import read_json
    from invoice_selection import sha
    m,f=read_json(paths['invoices']),read_json(paths['form'])
    if request['grid']!='53' or f.get('system_mode','daily')!='daily' or f.get('serial')or m.get('serial')or m.get('draft_creation_requested')or f.get('review',{}).get('workflow_started'):
        raise ValueError('冻结选择仅适配未建单日常53；已有单号按逐票更正接口处理')
    who=request.get('expected_userid')
    if not isinstance(who,str)or not re.fullmatch(r'[A-Za-z0-9]{2,40}',who):raise ValueError('冻结选择须明确当前财务账号')
    if m.get('case_id')!=request['case_id'] or f.get('case_id')!=request['case_id'] or any(d.get('expected_userid')not in (None,'',who)for d in [m,f]):
        raise ValueError('本案、来源与财务账号不同')
    guids=[x.get('guid')for x in m.get('selected_invoices',[])]
    if any(not isinstance(g,str)or not re.fullmatch(r'[A-Fa-f0-9]{32}',g)for g in guids)or len(set(guids))!=len(guids):
        raise ValueError('本案原冻结GUID集不完整或重复')
    return {'source':'local_case_manifest','unbound':True,'case_id':request['case_id'],'guids':sorted(guids),
        'manifest_path':str(paths['invoices']),'manifest_sha256':sha(paths['invoices']),'form_sha256':sha(paths['form']),
        'server_selection_observed':False}


def snapshot(request,paths,output,provided=None):
    from assistant_api import read_json
    from invoice_selection import run_browser,write,body,sha
    from finance_invoice_verification import full_grid_http_script
    from finance_invoice_reconcile import grid_scope
    context=local_context(paths,request)
    source=Path(provided)if provided else Path(output).with_name(Path(output).stem+'-http.json')
    raw=read_json(source)if provided else run_browser(full_grid_http_script('53',request['expected_userid'],request['case_id']),request,source)
    b,g,scope=grid_scope(raw,'53',request['expected_userid'])
    if raw.get('tab_id')!=request['tab_id'] or b.get('case_id')!=request['case_id'] or not scope['complete']or b.get('business_transport')!='native_http':raise ValueError('冻结选择须完整HTTP票池，不能以首屏或控件状态替代')
    if local_context(paths,request)!=context:raise ValueError('读取票池期间本案冻结选择发生变化')
    result={'schema':SCHEMA,'tab_id':request['tab_id'],'source_runtime':{'path':str(source.resolve()),'sha256':sha(source)},
        'result':{**b,'mode':'case_manifest_selection','case_selection':context,'display_evidence':False}}
    write(output,result);return result


def verify_plan(plan):
    from invoice_selection import sha,load,preview
    if plan.get('business_transport')!='native_http' or plan.get('selection_source')!='local_case_manifest':raise ValueError('不是本案冻结选择计划')
    refs=plan['evidence']
    for key in ['request','case_manifest','snapshot']:
        if sha(refs[key]['path'])!=refs[key]['sha256']:raise ValueError('原冻结选择输入变化')
    if any(sha(r['path'])!=r['sha256']for r in refs['audit_receipts']):raise ValueError('原票面审核变化')
    snap=load(refs['snapshot']['path']);native=snap.get('source_runtime',{})
    if not native.get('path')or sha(native['path'])!=native.get('sha256'):raise ValueError('原HTTP票池回执变化')
    rebuilt=preview(load(refs['request']['path']),load(refs['case_manifest']['path']),snap,[load(r['path'])for r in refs['audit_receipts']])
    if {k:v for k,v in plan.items()if k not in {'evidence','prepared_at_utc'}}!=rebuilt:raise ValueError('冻结选择计划与原输入重建不同')
    return plan


def readback(plan,paths,observed,expected_manifest_sha=None):
    from invoice_selection import grid_state,sha,PROTECTED_FIELDS,text
    b,g,rows,selected=grid_state(observed,'53')
    ctx=b['case_selection'];differences=[]
    if ctx['form_sha256']!=plan['form_sha256']:raise ValueError('本案表单在冻结选择后变化')
    for guid in plan['after_guids']:
        matches=[r for _,r in rows if r.get('t.tk_guid')==guid]
        if len(matches)!=1:raise ValueError('冻结选择中的GUID不在完整本人票池')
        original=plan['bindings'][guid]['native_row']
        differences.extend({'guid':guid,'field':key}for key in PROTECTED_FIELDS if text(matches[0].get(key))!=text(original.get(key)))
    ok=selected==plan['after_guids'] and not differences and (not expected_manifest_sha or sha(paths['invoices'])==expected_manifest_sha)
    return {'ok':ok,'case_id':plan['case_id'],'business_transport':'native_http','selection_source':'local_case_manifest',
        'selected_guids':selected,'expected_guids':plan['after_guids'],'field_differences':differences,
        'manifest_sha256':sha(paths['invoices']),'system_write':False,'selection_write':False,'draft_created':False,'submitted':False}


def apply(plan,plan_path,bundle,output,request_id,*,recover=None):
    from operator_support import bundle as load_bundle
    from assistant_api import read_json,atomic_json
    from invoice_selection import sha,write,preview,money
    _,paths=load_bundle(bundle);verify_plan(plan)
    output=Path(output);intent_path=Path(recover)if recover else output.parent/'selection-intent.json'
    if (paths['state'].parent/'pending_write.json').exists():raise ValueError('存在未核财务写入，先恢复原任务')
    if recover:
        intent=read_json(intent_path)
        if intent.get('schema')!=SCHEMA or intent.get('case_id')!=plan['case_id']or intent.get('plan_sha256')!=sha(plan_path)or Path(intent['bundle']).resolve()!=Path(bundle).resolve():
            raise ValueError('本地选择恢复未绑定原案件和计划')
    else:
        fresh_path=output.with_name(output.stem+'-before.json');fresh=snapshot(plan['request'],paths,fresh_path)
        refs=plan['evidence'];current=preview(plan['request'],read_json(refs['case_manifest']['path']),fresh,[read_json(r['path'])for r in refs['audit_receipts']])
        for key in ['identity','before_guids','after_guids','target_guids','bindings','case_manifest_sha256','form_sha256']:
            if current[key]!=plan[key]:raise ValueError('执行前HTTP票池、本案或票面审核变化：'+key)
        m=read_json(paths['invoices']);f=read_json(paths['form']);updated=deepcopy(m)
        source_manifest=read_json(refs['case_manifest']['path'])
        from invoice_selection import numbers
        selected=[]
        for guid in plan['after_guids']:
            binding=plan['bindings'][guid];r=binding['native_row'];number=binding['invoice_number']
            exposed=number in numbers(r)
            selected.append({'guid':guid,'number':number,'type':r['t.tk_type'],'amount':str(money(r['t.tk_total_amt'])),
                'source_sha256':binding['source_sha256'],'number_exposed':exposed,'native_code_abst':r['t.tk_code_abst'],
                'native_display_abst':r.get('t.tk_display_abst'),'native_person_id':r.get('t.tk_buysno'),
                'binding_verified':not exposed,**({'invoice_code':r.get('t.tk_code')}if r['t.tk_type']=='MEDICAL'else{})})
        selected_shas={x['source_sha256']for x in selected};known={x.get('sha256')for x in updated.get('original_files',[])}
        updated.setdefault('original_files',[]).extend(deepcopy(s)for s in source_manifest['original_files']if s['sha256']in selected_shas and s['sha256']not in known)
        updated.update(selected_invoices=selected,selected_count=len(selected),expected_count=f.get('expected_invoice_count'),
            selected_amount=str(sum((money(x['amount'])for x in selected),money('0'))),expected_amount=f['amount'],
            selection_requires_review=False,native_selection={'schema':SCHEMA,'plan':str(Path(plan_path).resolve()),
                'plan_sha256':sha(plan_path),'source_manifest':refs['case_manifest'],'audit_receipts':refs['audit_receipts'],
                'request_id':request_id,'financial_write':False})
        from invoice_selection import binding as check_binding,flatten_audits
        audits=flatten_audits([read_json(r['path'])for r in refs['audit_receipts']])
        try:
            for guid in plan['after_guids']:
                row=plan['bindings'][guid]['native_row']
                check_binding(row,source_manifest,audits,require_eligible=True)
                if row['t.tk_mod_flag']!='NORMAL' or row.get('t.auth_rst_desc')!='正常(已认证)' or row.get('t.serial_no'):
                    raise ValueError('保留记录仍待核实')
            updated['selection_requires_review']=False
        except ValueError:
            updated['selection_requires_review']=True

        after=(json.dumps(updated,ensure_ascii=False,indent=2)+'\n').encode()
        intent={'schema':SCHEMA,'case_id':plan['case_id'],'bundle':str(Path(bundle).resolve()),'plan':str(Path(plan_path).resolve()),
            'plan_sha256':sha(plan_path),'manifest':str(paths['invoices']),'before_sha256':sha(paths['invoices']),
            'after_sha256':digest(after),'after':updated,'request_id':request_id,'financial_write':False}
        write(intent_path,intent)
    pointer=paths['state'].parent/'native_selection_intents'/(intent['plan_sha256']+'.json')
    if pointer.exists():
        ref=read_json(pointer)
        if ref!={'path':str(intent_path.resolve()),'sha256':sha(intent_path)}:raise ValueError('该冻结计划已有原意图，须recover原任务')
    else:
        write(pointer,{'path':str(intent_path.resolve()),'sha256':sha(intent_path)})
    current=sha(paths['invoices'])
    wrote=False
    if current==intent['before_sha256']:
        if recover:
            fresh=snapshot(plan['request'],paths,output.with_name(output.stem+'-before.json'))
            refs=plan['evidence'];rebuilt=preview(plan['request'],read_json(refs['case_manifest']['path']),fresh,[read_json(r['path'])for r in refs['audit_receipts']])
            if any(rebuilt[k]!=plan[k]for k in ['identity','before_guids','after_guids','bindings','case_manifest_sha256','form_sha256']):raise ValueError('原本地意图恢复前数据变化')
        if local_context(paths,plan['request'])['form_sha256']!=plan['form_sha256']:raise ValueError('原本地写入前表单已改变')
        atomic_json(paths['invoices'],intent['after']);wrote=True
    elif current!=intent['after_sha256']:raise ValueError('本案manifest已被其他编辑更改，不覆盖')
    if sha(paths['invoices'])!=intent['after_sha256']:raise ValueError('本地选择原子保存未核实')
    after_path=output.with_name(output.stem+'-after.json');observed=snapshot(plan['request'],paths,after_path)
    verified=readback(plan,paths,observed,intent['after_sha256'])
    result={**verified,'local_selection_write':wrote,'local_selection_persisted':True,'original_intent':str(intent_path),'request_id':intent['request_id'],
            'recovered_original_local_intent':bool(recover),'financial_action_dispatched':False}
    write(output,result);return result


def native_plan_request(request):
    from assistant_api import AssistantService
    if not request.get('workspace'):return False
    service=AssistantService(request['workspace'],request.get('material_roots'),request.get('python'))
    p=request['parameters']
    try:
        _,plan,_=service._typed_case_artifact(request['case_id'],p.get('preview_job_id'),p.get('preview_artifact_id'),'invoice_selection',['preview'])
        return plan.get('business_transport')=='native_http'and plan.get('selection_source')=='local_case_manifest'and plan.get('apply_ready')is True
    except (ValueError,KeyError,TypeError,OSError):return False


if __name__=='__main__':
    import argparse
    from assistant_api import read_json
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--intent',type=Path,required=True)
    ap.add_argument('--intent-sha256',required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if digest(args.intent.read_bytes())!=args.intent_sha256 or args.output.exists():raise ValueError('原本地选择意图SHA变化或输出已存在')
    intent=read_json(args.intent);plan=read_json(intent['plan'])
    result=apply(plan,Path(intent['plan']),intent['bundle'],args.output,intent['request_id'],recover=args.intent)
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['ok']else 2)
