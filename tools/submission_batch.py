"""Explicitly authorised serial submission through registered native jobs.

No private browser channel, unchecked click, workflow reset or payment action.
Unknown writes retain the original pending job and stop the shared scene.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from assistant_api import APIError, atomic_json, read_json
from batch_workflow import DraftBatch, inspect_batch, _json_path
from submission import verify_next, verify_promise, verify_department, verify_applicant_ready, verify_applicant_agreement, verify_applicant_prepared_list
from submission import validate_plan, verify_options, verify_missing_supplement_rejection


def business_fingerprint(form):
    stable = {k: v for k, v in form.items() if k != 'review'}
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_authorization(service, path, index, items, userid, prepare_only=False):
    path = _json_path(service, str(path)); value = read_json(path)
    if (value.get('schema') != 'ustc-batch-submission-authorization-v1'
            or value.get('batch_index_sha256') != hashlib.sha256(index.read_bytes()).hexdigest()
            or value.get('expected_userid') != userid or not userid
            or value.get('submit_authorized') is not True or (not prepare_only and value.get('applicant_agreement_authorized') is not True)
            or value.get('approval_opinion') != '同意' or not value.get('source')):
        raise APIError('submission_authorization', '批量提交须有绑定本批SHA、账号及本人同意的明确授权，意见仅支持同意')
    bindings = value.get('cases', [])
    if not isinstance(bindings, list) or any(not isinstance(v, dict) for v in bindings):
        raise APIError('submission_authorization', '授权案件绑定须为完整列表')
    by_case = {v.get('case_id'): v for v in bindings}
    if len(by_case) != len(bindings): raise APIError('submission_authorization', '授权案件不能重复')
    for item in items:
        case = item['case_id']; _, paths = service.resolve(case); form = read_json(paths['form'])
        bound = by_case.get(case, {})
        gate=read_json(paths['case']).get('material_context',{}).get('applicant_agreement_gate',{})
        if not prepare_only and gate.get('status')=='deferred_by_user':
            raise APIError('submission_wait_for_user','用户已要求之后共同批量批准；当前只允许准备到本人待审，先读取最新安排')
        if (bound.get('serial') != form.get('serial') or bound.get('amount') != form.get('amount')
                or bound.get('business_fingerprint') != business_fingerprint(form)
                or bound.get('invoice_manifest_sha256') != hashlib.sha256(paths['invoices'].read_bytes()).hexdigest()):
            raise APIError('submission_binding_changed', '案件、单号、金额、业务输入或原票清单与授权不符：'+case)
    return path, value


class SubmissionBatch(DraftBatch):
    def __init__(self, service, index, scenario, tab, userid, authorization):
        super().__init__(service, index, scenario, tab, userid)
        self.authorization = authorization
        self.key = hashlib.sha256(index.read_bytes()+authorization.read_bytes()).hexdigest()[:16]
        self.folder = service.root/'.local/submission_batches'/self.key
        self.folder.mkdir(parents=True, exist_ok=True)
        self.authorization_sha = hashlib.sha256(authorization.read_bytes()).hexdigest()
        self.index_sha = hashlib.sha256(index.read_bytes()).hexdigest()
        self.bindings = {b['case_id']:b for b in read_json(authorization)['cases']}
        self.fresh_workspace_mode=False

    def verify_existing(self, item):
        try:
            return super().verify_existing(item)
        except APIError as error:
            if error.code != 'batch_verify_options':
                raise
        # 分类变更可能重建合同行。整单字段已先核过，只按本案已确认的
        # 无合同/自行打印设置补齐新行；不是放过其他差项或补写说明。
        case = item['case_id']
        _, paths = self.service.resolve(case)
        form = read_json(paths['form'])
        if (form.get('contract', {}).get('exists') is not False
                or form.get('delivery', {}).get('delegate_finance_print') is not False):
            raise APIError('submission_options_source', '本案缺少明确的合同与打印选项依据')
        plan = self.plan(form, read_json(self.authorization))
        self.submit(case, plan, 'options')
        job, data, art = self.wait_read(case, 'options', lambda d: verify_options(d, form['serial']))
        self.acknowledge(case, 'options', job, art, 'saved', '按已确认合同与打印选项补齐当前原生行并独立核对；未写说明或推进审批。')
        _, fresh = inspect_batch(self.service, self.scenario, self.index, [item['sequence']])
        return super().verify_existing(fresh[0])

    def request(self, *args, **kwargs):
        result = super().request(*args, **kwargs)
        if result.get('status') != 'completed' or result.get('exit_code') != 0:
            raise APIError('submission_original_job', '原任务未完整结束；保留原job、意图及pending，不重放：'+str(result.get('job_id')))
        return result

    def current(self, case, label):
        job = self.request(case, label+'-'+str(time.time_ns()), operation='read_system',
                           parameters={'mode':'readback','tab_id':self.tab})
        data, art = self.artifact(case, job)
        if data.get('identity', {}).get('userid') != self.userid or data.get('projid') != 'WF_YBX':
            raise APIError('submission_identity', '独立回读的账号或财务系统变化')
        return job, data, art

    def wait_read(self, case, label, verify):
        last = None
        for _ in range(12):
            job, data, art = self.current(case, label)
            try:
                verify(data)
                return job, data, art
            except ValueError as error:
                last = error
            dialogs=data.get('visibleDialogs') or []
            unit_dialogs=(dialogs and all(d.get('text','').startswith('选择单位审批人') and {'返回','保存'}.issubset({b.get('btnname') for b in d.get('buttons',[])}) for d in dialogs))
            if dialogs and not unit_dialogs:
                raise APIError('submission_native_dialog', '原生出现校验或提示；保留本单现场及原pending，不重复点击')
            time.sleep(.4)
        raise APIError('submission_readback_pending', str(last)+'；只恢复原请求，不重新提交')

    def acknowledge(self, case, label, job, art, outcome, note):
        return self.request(case, label+'-ack', operation='record_result', parameters={
            'kind':'ack-write','job_id':job['job_id'],'artifact_id':art['artifact_id'],'outcome':outcome,'note':note})

    def plan(self, form, authorization):
        choices = authorization.get('department_approvers', [])
        by_project = {a.get('project'): a for a in choices}
        if len(by_project) != len(choices): raise APIError('submission_approver', '经费审批人不能重复')
        funds = form.get('funding', [])
        if any(f['code'] not in by_project for f in funds):
            raise APIError('submission_approver', '本案经费缺少已确认的单位审批人')
        return {'case_id':form['case_id'],'serial':form['serial'],'amount':form['amount'],
                'summary':form['basic']['fields']['t-abst'],'phone':form['basic']['fields']['t-req_mobile'],
                'contract_exists':False,'delegate_finance_print':False,
                'submit_authorized':True,'applicant_agreement_authorized':authorization.get('applicant_agreement_authorized') is True,'approval_opinion':'同意',
                'source':str(self.authorization),'expected_userid':self.userid,
                'department_approvers':[by_project[f['code']] for f in funds]}

    def submit(self, case, plan, action):
        _, paths = self.service.resolve(case); form = read_json(paths['form']); bound = self.bindings[case]
        if (hashlib.sha256(self.authorization.read_bytes()).hexdigest() != self.authorization_sha
                or hashlib.sha256(self.index.read_bytes()).hexdigest() != self.index_sha
                or business_fingerprint(form) != bound['business_fingerprint']
                or hashlib.sha256(paths['invoices'].read_bytes()).hexdigest() != bound['invoice_manifest_sha256']):
            raise APIError('submission_input_drift','动作前授权、索引、业务输入或原票清单改变，停止原现场')
        label = 'submit-'+action+('-'+str(time.time_ns()) if action == 'status' else '')
        retry=self.folder/case/'next-known-rejection.json'
        if action=='next' and retry.exists():
            proof=read_json(retry)
            if proof.get('outcome')!='not_applied':raise APIError('submission_retry_proof','没有明确未点击的原动作核定')
            label+='-after-'+proof['original_job_id']
        return self.request(case, label, operation='submission', execute=True,
                            parameters={'action':action,'plan':plan,'tab_id':self.tab})

    def ensure_approvers(self, case, plan):
        for _ in range(8):
            job = self.request(case, 'approver-inspect-'+str(time.time_ns()), operation='submission', execute=True,
                               parameters={'action':'inspect','plan':plan,'tab_id':self.tab})
            data, _ = self.artifact(case, job)
            candidates = data.get('departmentCandidates', [])
            if len(candidates) == len(plan['department_approvers']) and all(c.get('options') for c in candidates):
                for expected, actual in zip(plan['department_approvers'], candidates):
                    if (actual.get('project') != expected['project'] or '单位负责人（院级）' not in actual.get('label','')
                            or sum(o.get('id') == expected['id'] and o.get('label') == expected['id']+'('+expected['name']+')' for o in actual['options']) != 1):
                        raise APIError('submission_approver_changed', '原生经费、角色或已确认审批人候选变化')
                return
            time.sleep(.4)
        raise APIError('submission_approver_loading', '原生单位审批人候选未就绪，保留原现场')

    def prior_review(self, item, allow_material_gaps=False):
        """Re-verify the registered original preview, never a completion label."""
        from batch_delivery import _frame
        from finance_open_draft import verify_preview
        case=item['case_id'];_,paths=self.service.resolve(case);form=read_json(paths['form'])
        for path in sorted(self.folder.glob('report-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            for row in read_json(path).get('results',[]):
                checked=row.get('draft_review') or {};e=checked.get('native_evidence') or {}
                if row.get('case_id')!=case or row.get('serial')!=form['serial'] or not e:continue
                artifact=self.service.artifact(case,e.get('job_id'),e.get('artifact_id'))
                if hashlib.sha256(artifact.read_bytes()).hexdigest()!=e.get('sha256'):raise APIError('submission_review_sha','原整单复核回执SHA改变')
                data=verify_preview(read_json(artifact),form['serial'],self.userid,self.tab)
                original=item['_row'];planned=[f for d in read_json(Path(original['official_plan']))['plan']['documents'] for f in d['forms']]
                if len(planned)!=1:raise APIError('submission_review_scope','原计划不是唯一已核报销单')
                frame=_frame(data,form,{**original,'planned':planned[0]},self.userid)
                for stage in ('basic','funding','payments'):self.verify_stage(case,stage,frame)
                if form.get('expense_categories'):self.verify_stage(case,'categories',frame)
                verify_options(data,form['serial'])
                from native_material_requirements import material_gaps
                gaps=material_gaps(frame)
                if gaps and not allow_material_gaps:raise APIError('submission_review_gaps','原整单复核有材料错误，不能按完整复核恢复')
                from finance_category_verify import review_material_prompt
                checked={**checked,'system_evidence_gaps':gaps,'category_review':review_material_prompt(form,frame,gaps)}
                return checked
        raise APIError('submission_review_missing','没有本授权批次的完整原生整单复核；不凭状态标签恢复提交')

    def recover_pending(self, item, plan, pending, recorded_only=False):
        case=item['case_id'];operation=pending.get('operation','');action=operation.removeprefix('submission:')
        if action not in ('next','promise','department','applicant-agree') or operation!='submission:'+action:
            raise APIError('submission_recovery_scope','原pending不属于本入口已适配的提交动作')
        if pending.get('case_id')!=case or pending.get('serial')!=plan['serial']:
            raise APIError('submission_recovery_identity','原pending案件或单号不符')
        value=pending.get('verification',{}).get('save_receipt');source=Path(value or '')
        if source.parent.parent!=self.service.jobs/case or source.name!='output.json':
            raise APIError('submission_recovery_source','原提交必须引用同案注册job的保存请求回执')
        original=read_json(source.parent/'request.json');result=self.service.result(case,source.parent.name)
        matches=[a for a in result.get('artifacts',[]) if a.get('name')=='output.json']
        envelope=read_json(source);body=envelope.get('result',{})
        preclick=(action=='next' and result.get('status')=='failed' and envelope.get('phase')=='script_completed'
                  and body.get('ok') is False and body.get('error')=='摘要、电话、申请总额或原票面合计不符')
        if preclick:
            script=Path(envelope.get('script',''))
            if not script.is_file() or hashlib.sha256(script.read_bytes()).hexdigest()!=envelope.get('source_sha256'):
                raise APIError('submission_recovery_script','原拒绝脚本SHA不符')
            text=script.read_text();segment=text.split('async function next(){',1)[-1].split('async function promise()',1)[0]
            if (segment.count('.click()')!=1 or segment.find("throw Error('摘要、电话、申请总额或原票面合计不符')")<0
                    or segment.find("throw Error('摘要、电话、申请总额或原票面合计不符')")>segment.find('.click()')):
                raise APIError('submission_recovery_dispatch','未证明原拒绝发生于点击之前')
            legacy_body=text.split('async function next(){',1)[1].split('async function promise()',1)[0]
            if hashlib.sha256(legacy_body.encode()).hexdigest()!='40380eb9384f79394aac56df565c552df11b4140091248c39cd546b1767a8646':
                raise APIError('submission_recovery_legacy','原拒绝不是已核定的旧版只读前置检查；保留原意图')
        if ((not preclick and (result.get('status')!='completed' or result.get('exit_code')!=0)) or len(matches)!=1
                or self.service.artifact(case,source.parent.name,matches[0]['artifact_id'])!=source
                or original.get('operation')!='submission' or original.get('execute') is not True
                or original.get('parameters',{}).get('action')!=action
                or original['parameters'].get('tab_id')!=self.tab
                or original['parameters'].get('plan')!=plan):
            raise APIError('submission_recovery_original','原提交请求未明确完成、标签或冻结计划不同；只读原任务，不重放')
        if preclick:
            checked=self.prior_review(item)
            _,paths=self.service.resolve(case);lock=paths['state'].parent/'pending_write.json'
            with self.service.mutex(case):
                if self.service.active_jobs(case) or read_json(lock)!=pending:raise APIError('submission_recovery_changed','原前置拒绝登记前任务或意图改变')
                ack={**pending,'outcome':'not_applied','status':'preclick_refusal_recorded',
                     'proof_kind':'registered_failed_script_before_native_click','original_job_id':source.parent.name,
                     'source_sha256':envelope['source_sha256'],'legacy_next_function_sha256':'40380eb9384f79394aac56df565c552df11b4140091248c39cd546b1767a8646',
                     'system_write':False,'saved_result_claimed':False}
                destination=Path(pending['output_directory'])/'preclick_refusal_ack.json'
                if not destination.resolve().is_relative_to(paths['state'].parent/'receipts'):raise APIError('submission_recovery_path','原拒绝回执目录不属于本案')
                atomic_json(destination,ack)
                state=read_json(paths['state']);state.update(status='submission_preflight_rejected',submission_last_operation='submission:next',
                    submission_preflight_refusal=str(destination),submission_completed=False)
                atomic_json(paths['state'],state);lock.unlink()
            atomic_json(self.folder/case/'next-known-rejection.json',{'original_job_id':source.parent.name,'proof':str(destination),'outcome':'not_applied'})
            return 'preclick-next',checked
        expected={'next':'submission_next','promise':'promise_submitted','department':'save_department_approvers','applicant-agree':'applicant_agree_requested'}
        if body.get('ok') is not True or body.get('action')!=expected[action] or body.get('serial')!=plan['serial'] or body.get('requiresReadback') is not True:
            raise APIError('submission_recovery_original','原提交动作回执不完整或不是已派发的本案动作')
        if action=='next':
            candidates=[]
            for ref_path in sorted((self.folder/case).glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
                saved=read_json(ref_path)
                if saved.get('status')!='completed' or not saved.get('job_id'):continue
                request=read_json(self.service.job_folder(case,saved['job_id'])/'request.json')
                if request.get('operation')!='read_system' or request.get('parameters',{}).get('tab_id')!=self.tab:continue
                try:
                    observed,art=self.artifact(case,saved,envelope=True)
                    if observed.get('executed_at_utc','')<=pending.get('started_at_utc',''):continue
                    data=observed.get('result',{});blocked=verify_missing_supplement_rejection(data,plan)
                except (ValueError,APIError,KeyError):continue
                candidates.append((saved,data,art,blocked));break
            if candidates:job,data,art,blocked=candidates[0]
            elif recorded_only:return None,None
            else:
                job,data,art=self.current(case,'recover-next-classify')
                try:blocked=verify_missing_supplement_rejection(data,plan)
                except ValueError:blocked=None
            if blocked:
                checked=self.prior_review(item,allow_material_gaps=True)
                self.acknowledge(case,'recover-blocked-'+source.parent.name,job,art,'not_applied','原生明确提示缺补充说明；完整同号预览、同金额及空流程节点已核对，没有进入承诺或新流程。')
                if not self.fresh_workspace_mode:
                    self.submit(case,plan,'dismiss-validation')
                    self.wait_read(case,'dismissed-blocker',lambda d: True if not d.get('visibleDialogs') else (_ for _ in ()).throw(ValueError('原缺说明提示仍在')))
                return 'blocked-next',checked
        if recorded_only:return None,None
        checked=self.prior_review(item)
        if action=='applicant-agree':
            self.submit(case,plan,'status')
            verifier=lambda d:verify_applicant_agreement(d,plan['serial'],self.userid,plan['amount'],read_json(self.service.resolve(case)[1]['form'])['basic'].get('teacher_name'))
        else:
            verifier={'next':lambda d:verify_next(d,plan['serial']), 'promise':lambda d:verify_promise(d,plan['serial']),
                      'department':lambda d:verify_applicant_ready(d,plan['serial'],self.userid)}[action]
        job,data,art=self.wait_read(case,'recover-'+action,verifier)
        self.acknowledge(case,'recover-'+source.parent.name,job,art,'staged' if action=='next' else 'saved',
                         '只回读原提交job的实际结果；同案、同标签、冻结计划及原整单字段核对通过，未重新点击。')
        return action,checked

    def recover_read_only_interface(self, case):
        """Close/reopen only after an explicitly authorised read-only failure."""
        from finance_backend import mac_native_enabled, mac_target
        from finance_open_draft import ensure_globally_idle
        from mac_edge_bridge import reopen_page, inventory
        if not mac_native_enabled():
            raise APIError('submission_reopen_backend','本自动恢复仅适用于已接通的Mac原生通道')
        ensure_globally_idle(self.service,case,'',target_tab_id=self.tab)
        target=mac_target(); intent=self.folder/('interface-recovery-'+str(time.time_ns())+'.json')
        atomic_json(intent,{'phase':'reopen_requested','case_id':case,'old_tab_id':self.tab,
                            'window_id':target['expected_window_id'],'business_write_requested':False})
        opened=reopen_page(close_tab_id=self.tab,window_id=target['expected_window_id'])
        self.tab=str(opened['new_tab_id']); atomic_json(intent,{'phase':'reopened','receipt':opened,'business_write_requested':False})
        deadline=time.monotonic()+15
        while True:
            loaded=[t for t in inventory().get('tabs',[]) if t.get('tab_id')==self.tab and t.get('window_id')==str(target['expected_window_id'])]
            if len(loaded)==1 and loaded[0].get('loading') is False:break
            if time.monotonic()>deadline:raise APIError('submission_reopen_loading','已重开标签仍在加载；保留开页回执，不重复开页')
            time.sleep(.25)
        job=self.request(case,'reopened-access-'+str(time.time_ns()),operation='access',execute=True,
                         parameters={'action':'ensure','tab_id':self.tab})
        body,_=self.artifact(case,job); envelope,_=self.artifact(case,job,envelope=True)
        if body.get('state')!='ready':raise APIError('submission_reopen_access','已重开原界面但登录未就绪；不提交、不重放')
        actual=envelope.get('finance_tab_id') or next((m.get('tab_id') for m in job.get('messages',[]) if m.get('state')=='ready' and m.get('tab_id')),None) or envelope.get('tab_id')
        found=[t for t in inventory().get('tabs',[]) if t.get('tab_id')==str(actual) and t.get('window_id')==str(target['expected_window_id'])]
        if len(found)!=1 or found[0].get('path')!='https://cwzh.ustc.edu.cn/YBX/main.jsp':
            raise APIError('submission_reopen_target','重开后未唯一取得实际财务主标签')
        self.tab=str(actual);self.current(case,'reopened-identity')
        result={'phase':'ready','receipt':opened,'actual_tab_id':self.tab,'access_job_id':job['job_id'],'business_write_requested':False}
        atomic_json(intent,result);return {**result,'receipt_path':str(intent)}


def run_submissions(service, *, scenario, batch_index, authorization, tab_id=None, expected_userid=None,
                    sequences=None, execute=False, progress=None, _interface_recovered=False, prepare_only=False,workspace=None):
    if type(execute) is not bool: raise APIError('submission_execute','execute须为布尔值')
    index, items = inspect_batch(service, scenario, batch_index, sequences)
    auth_path, auth = validate_authorization(service, authorization, index, items, expected_userid,prepare_only=prepare_only)
    report = {'schema':'ustc-submission-batch-v1','ok':True,'phase':'prepare-submit' if prepare_only else 'submit','scenario':scenario,
              'batch_index':str(index),'execute':execute,'results':[], 'issues':[],
              'system_write_requested':False,'approval_completed':False,'payment_completed':False,'pdf_exported':0,
              'submitted_count':0,'already_submitted_count':0,'held_count':0,'applicant_ready_count':0}
    if not execute:
        return {**report,'state':'preview_only','documents':[{k:v for k,v in i.items() if not k.startswith('_')} for i in items]}
    if not tab_id: raise APIError('submission_tab', '执行须给当前原生财务标签')
    runner = SubmissionBatch(service,index,scenario,tab_id,expected_userid,auth_path)
    runner.fresh_workspace_mode=workspace is not None
    report_path = runner.folder/('report-'+str(time.time_ns())+'.json'); report['report_path'] = str(report_path)

    def checkpoint(row=None):
        report['submitted_count'] = sum(r.get('status') == 'submitted_verified' for r in report['results'])
        report['already_submitted_count'] = sum(r.get('status') == 'already_in_approval_verified' for r in report['results'])
        report['held_count'] = sum(r.get('status','').startswith('held_') for r in report['results'])
        report['applicant_ready_count'] = sum(r.get('status') in ('applicant_ready_verified','already_applicant_ready_verified') for r in report['results'])
        atomic_json(report_path,report)
        atomic_json(runner.folder/'current.json',{'report_path':str(report_path),'sha256':hashlib.sha256(report_path.read_bytes()).hexdigest()})
        if progress and row: progress({k:v for k,v in row.items() if k in ('sequence','case_id','serial','status','issues')})

    pending_cases=[i for i in items if service.overview(i['case_id']).get('unverified_write')]
    if len(pending_cases)>1:raise APIError('submission_multiple_pending','批次有多个原写入待核，保留各原任务，不自动切换共享现场')
    if pending_cases:items=pending_cases+[i for i in items if i not in pending_cases]
    for item in items:
        case = item['case_id']; _, paths = service.resolve(case); form = read_json(paths['form'])
        row = {'sequence':item['sequence'],'case_id':case,'serial':form.get('serial'),'amount':form.get('amount')}
        report['results'].append(row); checkpoint()
        try:
            if workspace and case in workspace.initial_states:
                row.update(workspace.initial_states[case]);checkpoint(row);continue
            pending=service.overview(case).get('unverified_write');resume_after=None;checked=None
            if workspace:
                if not pending and not item['ready_for_draft']:
                    row.update(status='held_inputs',issues=item['needed_inputs']);checkpoint(row);continue
                runner.tab=workspace.begin_case(case)
            if pending:
                # Full private pending contains the registered source references;
                # the overview deliberately exposes a reduced view.
                original_pending=read_json(paths['state'].parent/'pending_write.json')
                plan=runner.plan(form,auth)
                resume_after,checked=runner.recover_pending(item,plan,original_pending)
                row['draft_review']=checked
                if resume_after=='blocked-next':
                    row.update(status='held_materials',issues=checked['system_evidence_gaps']);checkpoint(row);continue
            job, data, art = runner.current(case,'initial')
            current_serial=data.get('serial')
            list_visible=any(g.get('visible') and str(g.get('funcno')) in ('101','105') for g in data.get('grids', []))
            visible_case=next((c for c,b in runner.bindings.items() if b.get('serial')==current_serial),None) if current_serial and not list_visible else None
            clean_home=not current_serial and str(data.get('currentPage'))=='6' and all(label in data.get('visibleText','') for label in ('草稿','审批中','退回'))
            if not workspace and not resume_after and (visible_case or clean_home):
                source_case=visible_case or case;_,source_paths=service.resolve(source_case)
                runner.submit(source_case,runner.plan(read_json(source_paths['form']),auth),'status')
                job,data,art=runner.current(case,'initial-in-progress')
            try:
                verify_applicant_agreement(data,form['serial'],expected_userid,form['amount'],form['basic'].get('teacher_name'))
                row.update(status='already_in_approval_verified',native_evidence={'job_id':job['job_id'],'artifact_id':art['artifact_id'],'sha256':art['sha256']})
                checkpoint(row); continue
            except ValueError:
                pass
            if prepare_only:
                try:
                    verify_applicant_prepared_list(data,form['serial'],expected_userid,form['amount'])
                    row.update(status='already_applicant_ready_verified',native_evidence={'job_id':job['job_id'],'artifact_id':art['artifact_id'],'sha256':art['sha256']})
                    checkpoint(row);continue
                except ValueError:pass
            if not resume_after and not item['ready_for_draft']:
                row.update(status='held_inputs',issues=item['needed_inputs']);checkpoint(row);continue
            if not resume_after and any(g.get('visible') and str(g.get('funcno')) in ('101','105') for g in data.get('grids', [])):
                runner.request(case,'home-'+str(time.time_ns()),method='invoice_verification',action='view',tab_id=tab_id,
                               view='home',expected_userid=expected_userid)
                time.sleep(.5)
            if not resume_after:checked = runner.verify_existing(item)
            row['draft_review'] = checked
            if checked.get('system_evidence_gaps'):
                category = checked.get('category_review') or {}
                row.update(status='held_category_review' if category.get('review_required') else 'held_materials',
                           category_review=category,issues=checked['system_evidence_gaps']);checkpoint(row);continue
            current = service.overview(case,include_parameters=True)
            if not current['parameters']['form']['review'].get('ordinary_promise_authorized'):
                service.update_case(case,'form',{'review':{'ordinary_promise_authorized':True}},current['revision'])
            form = read_json(paths['form']); plan = runner.plan(form,auth)
            steps=[
                ('next',lambda d:verify_next(d,form['serial']),'staged'),
                ('promise',lambda d:verify_promise(d,form['serial']),'saved'),
                ('department',lambda d:verify_applicant_ready(d,form['serial'],expected_userid),'saved')]
            if resume_after in ('next','promise','department'):steps=steps[('next','promise','department').index(resume_after)+1:]
            for action, verify, outcome in steps:
                if action == 'department': runner.ensure_approvers(case,plan)
                row['phase'] = action; report['system_write_requested'] = True; checkpoint()
                runner.submit(case,plan,action)
                job,data,art = runner.wait_read(case,action,verify)
                runner.acknowledge(case,action,job,art,outcome,'本批显式授权；原生'+action+'后独立同号回读通过，未代后续审批或付款。')
            if prepare_only:
                row.update(status='applicant_ready_verified',native_evidence={'job_id':job['job_id'],'artifact_id':art['artifact_id'],'sha256':art['sha256']})
                if not workspace:
                    runner.submit(case,plan,'applicant-return')
                    runner.submit(case,plan,'status')
                checkpoint(row);continue
            row['phase'] = 'applicant-agree';checkpoint();runner.submit(case,plan,'applicant-agree')
            def departed(data):
                if data.get('serial') or any(str(f.get('funcno'))=='109' and any(i.get('id')=='formWF_YBX_109_t-remark' for i in data.get('visibleInputs',[])) for f in data.get('forms', [])):
                    raise ValueError('申请人页面尚未离开')
                return True
            runner.wait_read(case,'agree-departed',departed)
            runner.submit(case,plan,'status')
            job,data,art=runner.wait_read(case,'agree-status',lambda d:verify_applicant_agreement(d,form['serial'],expected_userid,form['amount'],form['basic'].get('teacher_name')))
            runner.acknowledge(case,'applicant-agree',job,art,'saved','同号同金额已离开508申请人进入后续审批；状态和当前账号独立回读通过，未代领导审批或付款。')
            row.update(status='submitted_verified',native_evidence={'job_id':job['job_id'],'artifact_id':art['artifact_id'],'sha256':art['sha256']});checkpoint(row)
        except (APIError,ValueError,OSError,KeyError) as error:
            pending=service.overview(case).get('unverified_write')
            # A concrete native rejection can be reconciled and held separately.
            # Unknown writes still stop the shared financial sequence.
            if pending and pending.get('operation')=='submission:next' and getattr(error,'code','')=='submission_native_dialog':
                try:
                    form=read_json(paths['form']);plan=runner.plan(form,auth)
                    recovered,review=runner.recover_pending(item,plan,read_json(paths['state'].parent/'pending_write.json'))
                    if recovered=='blocked-next':
                        row.update(status='held_materials',draft_review=review,issues=review['system_evidence_gaps']);checkpoint(row);continue
                except (APIError,ValueError,OSError,KeyError) as recovery_error:
                    error=recovery_error
                pending=service.overview(case).get('unverified_write')
            transport_failure=any(token in str(error).lower() for token in ('timeout','财务桥','原生桥','transport_unavailable'))
            if not pending and not row.get('phase') and auth.get('allow_interface_reopen') is True and not _interface_recovered and transport_failure:
                row.update(status='held_read_only_interface',issues=[str(error)]);checkpoint(row)
                try:recovered=runner.recover_read_only_interface(case)
                except (APIError,ValueError,OSError,RuntimeError) as recovery_error:
                    row.update(status='held_interface_recovery',issues=[str(error),str(recovery_error)])
                    report.update(ok=False,state='interface_recovery_needs_readback');checkpoint(row);break
                resumed=run_submissions(service,scenario=scenario,batch_index=batch_index,authorization=authorization,
                    tab_id=runner.tab,expected_userid=expected_userid,sequences=sequences,execute=True,progress=progress,_interface_recovered=True,prepare_only=prepare_only)
                resumed['recoveries']=[{'original_report':str(report_path),**recovered},*resumed.get('recoveries',[])]
                atomic_json(Path(resumed['report_path']),resumed)
                return resumed
            row.update(status='held_original_task' if pending else 'held_review',issues=[str(error)],error_code=getattr(error,'code','submission_error'))
            report.update(ok=False,state='original_task_needs_readback' if pending else 'review_stopped');checkpoint(row)
            # Even a read failure may leave a native editor in flight. Do not
            # drive the shared browser to the next case after an unknown scene.
            break
    else:
        report['state']='completed_with_holds' if report['held_count'] else 'completed'
    checkpoint(); return report
