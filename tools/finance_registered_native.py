"""已登记单据串行接续只调用正式HTTP owner，不进入旧点击/页面读取通道。"""
from __future__ import annotations
import hashlib,json,time
from pathlib import Path
from assistant_api import APIError,atomic_json,read_json
from finance_bill_state import state as bill_state
from submission import view_plan

def no_change(stage,p):
    if stage=='categories':return all(all(next(r for r in p['original']['categories']if r['group_key']==key)[k]==value for k,value in target.items())for key,target in p['changes'].items())
    if stage=='funding':return not p.get('changes') # 原生funding准备已核完整预算树与全部已确认经费。
    return not p.get('changes')and not any(b.get('selection_required')for b in p.get('card_bindings',[]))and not any(b.get('resolve_required')for b in p.get('corporate_bindings',[]))

def run_registered_native(service,session,items,authorization,phase,progress=None):
    auth=read_json(authorization);folder=session.folder/('native-'+phase+'-'+str(time.time_ns()));folder.mkdir();report={'schema':'ustc-registered-native-run-v1','ok':True,'case_id':session.case,'phase':phase,'state':'running','results':[],'report_path':str(folder/'report.json'),'final_applicant_approval_executed':False,'sms_sent':False,'system_write_requested':False}
    def checkpoint():
        report['held_count']=sum(r.get('status','').startswith('held_')for r in report['results']);report['applicant_ready_count']=sum(r.get('status')in {'applicant_ready_verified','already_applicant_ready_verified'}for r in report['results']);report['filled_count']=sum(r.get('status')=='draft_filled_native_verified'for r in report['results']);report['ok']=not report['held_count'];atomic_json(folder/'report.json',report)
    def job(case,op,params,label,execute=False,intent=None):
        result,body=session.job(case,op,params,label,execute=execute,request_id=intent)
        art=next(a for a in result['artifacts']if a['name']=='output.json');return result,body,art
    def write(case,op,params,label,baseline):
        digest=hashlib.sha256(json.dumps({'params':params,'baseline':baseline},ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
        report['system_write_requested']=True;checkpoint();return job(case,op,params,label,True,'native-registered-'+label+'-'+digest)
    def ack(case,job_result,art,outcome):
        return job(case,'record_result',{'kind':'ack-write','job_id':job_result['job_id'],'artifact_id':art['artifact_id'],'outcome':outcome,'note':'原生HTTP原任务结束后完整独立读取；核同单/GUID/金额、原有字段与当前节点；未执行本人同意或短信'},'native-ack')
    for item in items:
        case=item['case_id'];_,paths=service.resolve(case);form=read_json(paths['form']);row={'case_id':case,'sequence':item['sequence'],'serial':form.get('serial'),'amount':form.get('amount')};report['results'].append(row);checkpoint()
        try:
            if service.active_jobs(case):raise APIError('native_registered_active','先读取本案原后台任务')
            if (paths['state'].parent/'pending_write.json').exists():raise APIError('native_registered_pending','本案有原写入未核，按overview的原marker恢复入口接续，不生成新写入')
            if not form.get('serial'):raise APIError('native_registered_missing_serial','先沿已审原票的upload/selection/create合同建单并bind-serial，再回到本批次，不猜用其他单号')
            if not item.get('ready_for_draft'):row.update(status='held_inputs',issues=item.get('needed_inputs',[]));checkpoint();continue
            params={'expected_userid':session.cfg['expected_userid'],'tab_id':session.state['tab_id']};plan=view_plan(form,params['expected_userid'],str(authorization))
            choices={a['project']:a for a in auth.get('department_approvers',[])}
            if choices and all(f['code']in choices for f in form['funding']):plan['department_approvers']=[choices[f['code']]for f in form['funding']]
            read_job,body,read_art=job(case,'submission',{'action':'workflow-inspect','plan':plan,'tab_id':params['tab_id']},'native-initial-read')
            classified=bill_state(body['original']['bill'])
            if classified!='draft':
                current=body['original']['bill'];status={'ready_for_delivery':'already_approved_ready_verified','paid':'already_paid_verified'}.get(classified,'already_applicant_ready_verified'if current.get('t.node_id')=='508'and current.get('t.uni_key')=='U'+form['serial']else'already_in_approval_verified'if classified=='workflow_active'else'held_existing_workflow');row.update(status=status,observed_status=current.get('t.status_name'),native_evidence={'job_id':read_job['job_id'],'artifact_id':read_art['artifact_id'],'sha256':read_art['sha256']});checkpoint();continue
            if phase=='fill':
                stages=['funding','basic']+(['categories']if form.get('expense_categories')else[])+['expenses','payments']+(['notes']if form.get('explanations')else[])+(['pre-review']if form.get('pre_review')else[])
                row['stage_checks']={}
                for stage in stages:
                    prepared_job,prepared,prepared_art=job(case,'business',{'action':stage+'-prepare',**params},'native-prepare-'+stage)
                    row['stage_checks'][stage]={'prepare_job_id':prepared_job['job_id'],'artifact_id':prepared_art['artifact_id'],'sha256':prepared_art['sha256'],'already_matches':no_change(stage,prepared)};checkpoint()
                    if no_change(stage,prepared):continue
                    write(case,'stage',{'stage':stage,'tab_id':params['tab_id']},'stage-'+stage,prepared)
                    reading,_,art=job(case,'business',{'action':'categories-prepare',**params}if stage=='categories'else{'action':'bill-read','domain':{'funding':'categories','basic':'basic','expenses':'categories','payments':'payments','notes':'notes','pre-review':'pre_review'}[stage],**params},'native-stage-readback-'+stage)
                    ack(case,reading,art,'saved');row['stage_checks'][stage]['readback']={'job_id':reading['job_id'],'artifact_id':art['artifact_id'],'sha256':art['sha256']};checkpoint()
                row.update(status='draft_filled_native_verified',ready_for_material_delivery=False,workflow_started=False,approval_completed=False,payment_completed=False);checkpoint();continue
            if body['native_error_present']:row.update(status='held_materials',issues=['学校原生确认正文仍有材料错误，先按原票用途和明白纸逐项补齐']);checkpoint();continue
            if body['original']['options']['print']!='N'or any(p['contract']and p['selected']!='N'for p in body['original']['options']['proofs']):
                write(case,'submission',{'action':'options','plan':plan,'tab_id':params['tab_id']},'submission-options',body['original']['options']);reading,_,art=job(case,'submission',{'action':'inspect','plan':plan,'tab_id':params['tab_id']},'native-options-readback');ack(case,reading,art,'saved')
            if form.get('review',{}).get('ordinary_promise_authorized')is not True:row.update(status='held_promise_acknowledgement',issues=['须确认本案普通报销承诺；最终本人同意和短信仍由本人完成'],promise_sha256=body['promise_sha256']);checkpoint();continue
            plan['promise_sha256']=body['promise_sha256'];prior=read_json(paths['state']).get('last_native_workflow_action',{});reuse=False
            if prior.get('operation')=='next'and prior.get('outcome')=='staged'and prior.get('source_input'):
                from finance_native_create import checked
                old_input=checked(prior['source_input']);old_job=Path(prior['source_input']['path']).parent.name
                if old_input.get('plan')==plan and old_input['form']['sha256']==hashlib.sha256(paths['form'].read_bytes()).hexdigest():
                    next_job=service.result(case,old_job);next_art=next(a for a in next_job['artifacts']if a['name']=='output.json');reuse=True
            if not reuse:
                next_job,_,_=write(case,'submission',{'action':'next','plan':plan,'tab_id':params['tab_id']},'submission-next',body['original']);next_art=next(a for a in next_job['artifacts']if a['name']=='output.json')
            reading,fresh,art=job(case,'submission',{'action':'workflow-inspect'if reuse else'workflow-readback','plan':plan,'tab_id':params['tab_id'],'job_id':next_job['job_id'],'artifact_id':next_art['artifact_id']},'native-next-readback')
            if not reuse:ack(case,reading,art,'staged')
            next_body=read_json(service.artifact(case,next_job['job_id'],next_art['artifact_id']))['result']
            if any(str(v or'').strip()for v in next_body.get('messages',{}).values()):row.update(status='held_native_information',issues=next_body['messages']);checkpoint();continue
            action='department'if fresh['environment'].get('0-buss_var_list')else'promise';write_job,_,write_art=write(case,'submission',{'action':action,'plan':plan,'tab_id':params['tab_id'],'job_id':next_job['job_id'],'artifact_id':next_art['artifact_id']},'submission-'+action,fresh['original'])
            reading,_,art=job(case,'submission',{'action':'workflow-readback','plan':plan,'tab_id':params['tab_id'],'job_id':write_job['job_id'],'artifact_id':write_art['artifact_id']},'native-workflow-readback');ack(case,reading,art,'saved');row.update(status='applicant_ready_verified',native_evidence={'job_id':reading['job_id'],'artifact_id':art['artifact_id'],'sha256':art['sha256']});checkpoint()
        except(APIError,ValueError,KeyError,OSError,StopIteration)as error:
            pending=(paths['state'].parent/'pending_write.json').exists();row.update(status='held_original_pending'if pending else'held_native_contract',issues=[str(error)]);checkpoint()
            if pending:break
        if progress:progress({k:v for k,v in row.items()if k in {'case_id','sequence','serial','status','issues'}})
    report['state']='completed'if report['ok']else'held';checkpoint();return report
