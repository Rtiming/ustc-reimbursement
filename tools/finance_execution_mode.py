"""本案界面执行模式；不授予金额变更、未知请求重放或其他角色审批权限。"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

FINANCE_OPERATIONS = {'access','read_system','stage','invoice','submission','correction',
                      'invoice_verification','invoice_selection','invoice_retry','invoice_edit',
                      'expense_amount','pdf_export'}

def validate_mode(value, case_id):
    if value is None:return {'mode':'silent'}
    if not isinstance(value,dict) or value.get('mode') not in {'silent','full_control'}:
        raise ValueError('执行模式仅支持silent或full_control')
    allowed={'schema','mode','case_id','authorized_by','source','restore_context','inherited_from'}
    if set(value)-allowed or value.get('case_id') != case_id or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id):
        raise ValueError('执行模式须绑定本案，不能继承他案授权')
    if value.get('schema') != 'ustc-execution-mode-v1' or type(value.get('restore_context',True)) is not bool:
        raise ValueError('执行模式格式或恢复开关不合法')
    if 'inherited_from' in value and (not isinstance(value['inherited_from'],str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',value['inherited_from']) or value['inherited_from']==case_id):
        raise ValueError('继承模式须绑定其他明确批次')
    if value['mode']=='full_control' and (value.get('authorized_by')!='user' or not isinstance(value.get('source'),str) or not value['source'].strip()):
        raise ValueError('全权限模式须登记用户直接授权来源')
    return value

def preflight_ready(request, health):
    if health.get('state') in {'main_readable','preview_ready'}:return True
    params=request.get('parameters',{});plan=params.get('plan',{});observed=health.get('observation',{})
    # 本人审批页的附件预览可以为空；同号508及唯一原生同意按钮才允许交给原审批适配器校验。
    return (health.get('state')=='preview_not_loaded' and request.get('operation')=='submission'
            and params.get('action')=='applicant-agree' and bool(plan.get('serial'))
            and observed.get('project')=='WF_YBX' and observed.get('serial')==plan['serial']
            and bool(plan.get('expected_userid')) and observed.get('userid')==plan['expected_userid']
            and observed.get('node')=='508' and observed.get('activeRequests')==0
            and observed.get('applicantAgreementVisible')is True)

def prepare_focus(request, case_path, job_folder):
    from assistant_api import atomic_json, read_json
    from finance_backend import transport_identity
    case=read_json(case_path)
    if case.get('case_id')!=request['case_id']:raise ValueError('界面模式资料不属于当前案件')
    mode=validate_mode(case.get('material_context',{}).get('execution_mode'),request['case_id'])
    params=request.get('parameters',{});tab=params.get('tab_id')
    if (mode['mode']!='full_control' or request['operation'] not in FINANCE_OPERATIONS or not tab
            or params.get('action') in {'restore-context','new-interface','recover-interface'}
            or (not request.get('execute') and request['operation'] not in {'read_system','access','invoice_verification'})):
        return None
    if transport_identity().get('backend')!='edge_applescript':
        return {'mode':'full_control','state':'native_backend_preserves_background','foreground_requested':False}
    from finance_session import native_inventory
    from edge_read_probe import activate_tab
    observed=native_inventory();hits=[t for t in observed['tabs'] if t['tab_id']==str(tab)]
    if len(hits)!=1 or not hits[0]['path'].startswith(('https://cwzh.ustc.edu.cn/','https://id.ustc.edu.cn/')):
        raise ValueError('只能激活本次明确的财务或官方登录标签')
    folder=Path(job_folder);session_dir=folder.parents[2]/'finance_execution'/request['case_id'];session_dir.mkdir(parents=True,exist_ok=True)
    state_path=session_dir/'session.json';state=read_json(state_path) if state_path.exists() else None
    authority=hashlib.sha256(json.dumps(mode,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if state and state.get('status')=='active' and state.get('authority_sha256')!=authority:
        raise ValueError('当前界面授权已变，先恢复原上下文')
    if not state or state.get('status')!='active':
        state={'case_id':request['case_id'],'status':'active','before':observed,'owned_tab_ids':[],
               'authority_sha256':authority,'restore_context':mode.get('restore_context',True)}
    if str(tab) not in state['owned_tab_ids']:state['owned_tab_ids'].append(str(tab))
    atomic_json(state_path,state)
    record={'mode':'full_control','case_id':request['case_id'],'tab_id':str(tab),'source':mode['source'],
            'state':'activation_intent','system_write':False,'business_replayed':False,'session_path':str(state_path)}
    atomic_json(folder/'execution-mode.json',record)
    already=(observed.get('frontmost_bundle_id')=='com.microsoft.edgemac' and observed['windows']
             and observed['windows'][0].get('active_tab_id')==str(tab))
    if not already:activate_tab(str(tab),foreground=True)
    record.update(state='already_active' if already else 'activated',foreground_requested=not already)
    atomic_json(folder/'execution-mode.json',record)
    action=params.get('action','')
    needs_backend=(request.get('execute') and request['operation']!='access')or(action.startswith('remuneration-')and action!='remuneration-inspect')
    if needs_backend and hits[0]['path']=='https://cwzh.ustc.edu.cn/YBX/main.jsp':
        from finance_session import check_page
        userid=params.get('expected_userid') or params.get('plan',{}).get('expected_userid')
        config=folder.parents[3]/'data/finance_access.json'
        if not userid and config.exists():userid=read_json(config).get('username')
        if not userid:raise ValueError('执行前须明确本机财务登录人')
        health=check_page(request['case_id'],str(tab),userid,folder/'execution-health.json')
        if not preflight_ready(request,health):
            raise ValueError('本案业务命令尚未派发；财务页面健康状态为'+str(health.get('state'))+'，先沿正式登录恢复再核原单')
    return record

def restore(case_path, root):
    from assistant_api import atomic_json, read_json
    from finance_session import native_inventory, restore_active, restore_foreground
    cid=read_json(case_path)['case_id']
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',cid):raise ValueError('案件标识不合法')
    path=Path(root)/'.local/finance_execution'/cid/'session.json'
    if not path.exists():return {'ok':True,'case_id':cid,'state':'no_mode_context','system_write':False}
    state=read_json(path)
    if state.get('case_id')!=cid:raise ValueError('恢复上下文不属于本案')
    if state.get('status')!='active':return {'ok':True,'case_id':cid,'state':'already_restored','system_write':False}
    result={'ok':True,'case_id':cid,'state':'kept_by_case_preference','system_write':False}
    if state.get('restore_context',True):
        result['active_restoration']=restore_active(state['before'],native_inventory(),set(state['owned_tab_ids']))
        result['foreground_restoration']=restore_foreground(state['before'],native_inventory())
        result['state']='restore_observed'
    atomic_json(path.with_name('restore-'+str(time.time_ns())+'.json'),result)
    atomic_json(path,{**state,'status':'restored','restoration':result})
    return result

def inherit_batch_mode(service, parent_case_id, child_case_ids):
    """仅登记清单中的子案承接同源界面授权；保留子案显式模式。"""
    from assistant_api import read_json
    parent=read_json(service.resolve(parent_case_id)[1]['case'])
    if parent.get('case_id')!=parent_case_id:raise ValueError('批次模式资料身份不符')
    mode=validate_mode(parent.get('material_context',{}).get('execution_mode'),parent_case_id)
    ids=list(dict.fromkeys(child_case_ids));prepared=[]
    for cid in ids:
        case=read_json(service.resolve(cid)[1]['case'])
        if case.get('case_id')!=cid:raise ValueError('子案模式资料身份不符')
        existing=case.get('material_context',{}).get('execution_mode')
        if existing is not None and existing.get('inherited_from')!=parent_case_id:continue
        if mode['mode']!='full_control' and existing is None:continue
        inherited={**mode,'schema':'ustc-execution-mode-v1','case_id':cid,'inherited_from':parent_case_id,
                   'source':mode.get('source','批次当前采用默认静默')+'；承接已登记批次'+parent_case_id+'的界面授权'}
        if existing==inherited:continue
        validate_mode(inherited,cid)
        prepared.append((cid,inherited,service.revision(cid)))
    for cid,inherited,revision in prepared:
        service.update_case(cid,'case',{'material_context':{'execution_mode':inherited}},revision)
    return [cid for cid,_,_ in prepared]

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',required=True,type=Path);p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    if a.output.exists():raise ValueError('保留原恢复回执，不覆盖')
    result=restore(a.case,a.root);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
