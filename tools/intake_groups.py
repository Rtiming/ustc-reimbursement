"""把同一收件包明确分给不同活动；原件共用且范围冻结，不推定金额或审批授权。"""
import hashlib
import json
from pathlib import Path

from assistant_contract import APIError,CASE_PROFILES
from intake_review import _source,bound_file


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def run(service,case_id,intake_job_id,groups=None,expected_revision=None,execute=False):
    from assistant_api import read_json,atomic_json
    _,paths=service.resolve(case_id);case=read_json(paths['case']);manifest=read_json(paths['invoices'])
    if case.get('material_context',{}).get('intake_group'):
        raise APIError('intake_group_origin','请回到原收件批次维护活动分组，不在子活动中再次拆分来源')
    if read_json(paths['form']).get('serial')or manifest.get('draft_creation_requested'):
        raise APIError('intake_group_claim','已开始建单的材料范围不能通过收件分组改写，请走原单修正')
    inventory,receipt,rows,refs=_source(service,case_id,intake_job_id)
    group_artifacts=[a for a in service.result(case_id,intake_job_id).get('artifacts',[])if a['name']=='groups.json']
    candidates=[]
    if len(group_artifacts)==1:
        data=read_json(service.artifact(case_id,intake_job_id,group_artifacts[0]['artifact_id']))
        if data.get('case_id')!=case_id:raise APIError('intake_group_source','目录分组不属于本次收件')
        candidates=data.get('groups',[])
    output={'ok':True,'schema':'ustc-intake-groups-v1','case_id':case_id,'intake_job_id':intake_job_id,
            'revision':service.revision(case_id),'materials':[{'material_id':r['material_id'],'name':r['member_chain'],
             'directory_candidate':r.get('group_id'),'kind':r['kind']}for r in rows],
            'directory_candidates':candidates,'system_write':False,
            'note':'目录名称只是分组候选。逐件明确所属活动后建立独立活动；未分配材料保留原批次，不自动混入任何单据。'}
    plan_path=service.root/'.local/intake-group-plans'/case_id/(intake_job_id+'.json')
    if groups is None:
        if execute:raise APIError('intake_group_input','执行须明确各活动及其材料')
        if plan_path.exists():output['existing_plan']=read_json(plan_path)
        return output
    if not isinstance(groups,list)or not 1<=len(groups)<=100 or type(execute)is not bool:
        raise APIError('intake_group_input','请明确1至100个活动分组')
    available={r['material_id']for r in rows};selected=set();plans=[]
    default=(case.get('material_context',{}).get('profiles')or['student_activity'])[0]
    for group in groups:
        if not isinstance(group,dict)or set(group)-{'title','material_ids','profile'}:
            raise APIError('intake_group_input','分组仅接受活动名称、场景和材料身份')
        title=group.get('title');ids=group.get('material_ids');profile=group.get('profile',default)
        if (not isinstance(title,str)or not 1<=len(title.strip())<=160 or profile not in CASE_PROFILES
                or not isinstance(ids,list)or not ids or any(not isinstance(i,str)for i in ids)
                or len(ids)!=len(set(ids))or set(ids)-available or set(ids)&selected):
            raise APIError('intake_group_scope','活动须有名称和已收材料；同一材料不能重复分给两个活动')
        selected.update(ids)
        scope={'parent_case_id':case_id,'intake_job_id':intake_job_id,'material_ids':sorted(ids),'source_refs':refs}
        target='group_'+digest(scope)[:24]
        plans.append({'case_id':target,'title':title.strip(),'profile':profile,'source':scope})
    frozen={'groups':plans,'unassigned_material_ids':sorted(available-selected),'source_refs':refs}
    fingerprint=digest(frozen)
    output.update(plan=frozen,plan_sha256=fingerprint,unassigned_count=len(available-selected))
    if not execute:return output
    plan_path.parent.mkdir(parents=True,exist_ok=True)
    from material_review_launch import launch_lock
    with launch_lock(plan_path):
        if plan_path.exists():
            saved=read_json(plan_path)
            if saved.get('fingerprint')!=fingerprint:raise APIError('intake_group_plan_conflict','此批已有不同的分组计划。保留原活动及范围，不重复建立另一批活动。')
        else:
            if expected_revision!=service.revision(case_id):raise APIError('revision_conflict','收件资料已变化，请重新核对分组')
            if service.active_jobs(case_id)or (paths['state'].parent/'pending_write.json').exists():raise APIError('case_busy','原任务尚未结束，先接续原任务')
            saved={'schema':'ustc-intake-group-plan-v1','fingerprint':fingerprint,'plan':frozen,'status':'in_progress','completed_cases':[]}
            atomic_json(plan_path,saved)
        for group in plans:
            target=group['case_id'];source={**group['source'],'plan_sha256':fingerprint}
            try:
                _,target_paths=service.resolve(target)
                existing=read_json(target_paths['case'])
                if existing.get('material_context',{}).get('intake_group')==source:continue
                # 新案建立与关联来源间中断时，只接续本计划中的完全空白活动。
                form=read_json(target_paths['form']);invoices=read_json(target_paths['invoices'])
                if (existing.get('description')!=group['title']or existing.get('materials')or form.get('serial')or form.get('amount')or form.get('payments')or invoices.get('original_files')):
                    raise APIError('intake_group_target','分组目标已有不同内容，保留原状并核对原计划')
            except APIError as error:
                if error.code!='case_not_found':raise
                service.new_case(target,profile=group['profile'],title=group['title'])
            service.update_case(target,'case',{'material_context':{'intake_group':source}},service.revision(target))
            saved['completed_cases'].append(target);atomic_json(plan_path,saved)
        saved.update(status='completed');atomic_json(plan_path,saved)
    output.update(saved=True,created_cases=[{'case_id':r['case_id'],'title':r['title'],'material_count':len(r['source']['material_ids'])}for r in plans],
                  next_action={'tool':'reimbursement_cases','arguments':{},'label':'进入各活动分别核对材料'})
    return output
