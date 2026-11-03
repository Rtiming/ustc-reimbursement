"""Checkpointed HTTP applicant batch, using the existing case jobs and acknowledgements."""
import argparse,hashlib,json,os,sys,time
from datetime import datetime,timezone
from pathlib import Path
from assistant_api import AssistantService,atomic_json,read_json
from finance_native_create import ref,checked,frozen

ROOT=Path(__file__).resolve().parents[1]

def manifest(service,reference,anchor):
    from registered_inputs import checked_reference
    from applicant_batch import check_case_binding
    from finance_native_applicant import authority
    from submission import validate_plan
    value=read_json(checked_reference(service,reference));rows=value.get('cases',[])
    if value.get('schema')!='ustc-applicant-http-batch-v1'or value.get('case_id')!=anchor or not rows or len(rows)>100 or len({r['case_id']for r in rows})!=len(rows)or len({r['serial']for r in rows})!=len(rows):raise ValueError('批次须唯一原单、已登记输入及明确锚点案件')
    if anchor not in [r['case_id']for r in rows]:raise ValueError('锚点不在原单批次内')
    for row in rows:
        _,form=check_case_binding(service,row);validate_plan(row['plan'],form);authority(service,row['plan'],form)
        if row['plan']['expected_userid']!=value.get('expected_userid'):raise ValueError('批次账号不一致')
    return value

def run(c,output):
    service=AssistantService(c['workspace'],c['material_roots']);value=manifest(service,c['manifest'],c['case_id']);fingerprint=c['manifest']['sha256'];rows=value['cases']
    root=service.root/'.local/applicant_batches'/fingerprint;root.mkdir(parents=True,exist_ok=True)
    state_path=root/'state.json';state=read_json(state_path)if state_path.exists()else{'schema':'ustc-applicant-http-batch-state-v1','manifest':c['manifest'],'cases':{},'runs':[]}
    if not c['execute']:
        return{'ok':True,'state':'preview','case_id':c['case_id'],'document_count':len(rows),'business_transport':'native_http','manifest_sha256':fingerprint,'system_write':False,'performance_target_seconds':300}
    if state['manifest']!=c['manifest']:raise ValueError('原批次输入变化')
    # Freeze first-completion timing; later status checks must not inflate submission time.
    for entry in state['cases'].values():
        if entry.get('status')in {'submitted_verified','already_submitted_verified'}and 'submission_seconds'not in entry:
            seconds=entry.get('active_seconds',0)
            entry['submission_seconds']=seconds if seconds+0.001>=sum(t['seconds']for t in entry['timings'])else None
    state.pop('error',None)
    started=time.monotonic();run={'started_at':datetime.now(timezone.utc).isoformat(),'completed':False,'seconds':None};state['runs'].append(run)
    if state.get('waiting_since'):
        wait=(datetime.now(timezone.utc)-datetime.fromisoformat(state.pop('waiting_since'))).total_seconds();state['human_wait_seconds']=state.get('human_wait_seconds',0)+wait
    def checkpoint():atomic_json(state_path,state)
    def job(cid,op,params,label,execute=False):
        entry=state['cases'].setdefault(cid,{'case_id':cid,'jobs':{},'timings':[]});request='applicant-http-'+fingerprint[:12]+'-'+hashlib.sha256(cid.encode()).hexdigest()[:8]+'-'+label
        at=time.monotonic();j=service.enqueue(cid,op,params,request,execute);entry['jobs'][label]=j['job_id'];checkpoint()
        while True:
            r=service.result(cid,j['job_id'])
            if r['status']not in {'queued','running'}:break
            if time.monotonic()-at>300:raise ValueError('原后台任务仍在运行，保留job，不重发')
            time.sleep(.3)
        entry['timings'].append({'label':label,'job_id':j['job_id'],'status':r['status'],'seconds':round(time.monotonic()-at,6)});checkpoint()
        artifact=next((a for a in r.get('artifacts',[])if a['name']=='output.json'),None)
        if artifact:
            envelope=read_json(service.artifact(cid,j['job_id'],artifact['artifact_id']));body=envelope.get('result',envelope)
        else:body=r
        if r['status']!='completed'or r.get('exit_code')not in {None,0}or body.get('ok')is False:raise ValueError('原任务未完成，只恢复原job：'+j['job_id'])
        return body,{'job_id':j['job_id'],'artifact_id':artifact['artifact_id']if artifact else None}
    def acknowledge(cid,proof,outcome,label):
        job(cid,'record_result',{'kind':'ack-write',**proof,'outcome':outcome,'note':'原单本人批次：同号同金额同流程实例，申请人508节点及当前账号日志独立HTTP回读；旧短信停点未应用时只登记未应用，不重放。'},label)
    def sms_handoff(row,entry,wake):
        # This is a human interface, never an OTP sender/reader or an approval retry.
        from finance_native_applicant import authority
        from applicant_batch import check_case_binding
        _,form=check_case_binding(service,row)
        params={'action':'applicant-verification-open','tab_id':c['tab_id'],'plan':row['plan'],'authority':authority(service,row['plan'],form)}
        opened,proof=job(row['case_id'],'access',params,'sms-open-'+str(len(state['runs'])),True)
        entry['verification_handoff']={'result':opened,'receipt':proof};checkpoint()
        if opened.get('state')=='already_verified':return False
        if opened.get('state')!='verification_required'or opened.get('verification_window_visible')is not True:raise ValueError('未取得原单短信界面回执，保留原页')
        if sys.platform in {'darwin','win32'} and value.get('show_verification_interface',True):
            from registered_bridge import show_verification,status
            entry['verification_interface']=show_verification(c['tab_id'],tab_id_namespace=status()['tab_id_namespace'],expected_window_id=wake['window_id'],serial=row['serial'],expected_userid=value['expected_userid'])
        return True
    results=[];reason=None;active_entry=None;case_started=None
    try:
        for row in rows:
            cid=row['case_id'];entry=state['cases'].setdefault(cid,{'case_id':cid,'jobs':{},'timings':[]});begin=time.monotonic();active_entry=entry;case_started=begin;plan=row['plan'];base={'plan':plan,'tab_id':c['tab_id']}
            # Native page recovery once per case; the job/runtime owners still guard every dispatch.
            wake,_=job(cid,'access',{'action':'wake-interface','tab_id':c['tab_id']},'wake-'+str(len(state['runs'])),True)
            os.environ['REIMBURSEMENT_EDGE_WINDOW_ID']=str(wake['window_id'])
            pending=service.overview(cid).get('unverified_write')
            if pending:
                _,paths=service.resolve(cid);pending=read_json(paths['state'].parent/'pending_write.json')
                if pending.get('verification',{}).get('schema')=='ustc-native-applicant-v1':
                    original=Path(pending['verification']['input']['path']).parent.name
                    recovery_label=original+'-'+str(len(state['runs']))
                    observed,proof=job(cid,'submission',{**base,'action':'applicant-recover','job_id':original},'recover-'+recovery_label)
                    outcome='saved'if observed['completed']else'not_applied';acknowledge(cid,proof,outcome,'recover-ack-'+recovery_label)
                    if observed['completed']:entry['status']='submitted_verified';entry['readback']=proof
                elif pending.get('operation')=='submission:applicant-agree':
                    observed,proof=job(cid,'submission',{**base,'action':'applicant-inspect'},'legacy-read')
                    acknowledge(cid,proof,'not_applied','legacy-ack')
                else:raise ValueError('本案存在其他未核写入，保留原任务：'+cid)
            inspect_label='inspect-'+str(len(state['runs']))
            observed,proof=job(cid,'submission',{**base,'action':'applicant-inspect'},inspect_label)
            if observed['completed']:
                entry.update(status='already_submitted_verified',readback=proof,node=observed['original'].get('workflow_node_id',observed['original']['bill']['t.node_id']))
            else:
                if observed.get('verification_required')and sms_handoff(row,entry,wake):
                    entry.update(status='verification_required',readback=proof);state['waiting_since']=datetime.now(timezone.utc).isoformat();reason='verification_required';break
                generation=entry.get('generation',1);label='agree-'+str(generation)
                saved,save_job=job(cid,'submission',{**base,'action':'applicant-agree'},label,True);entry['save_job']=save_job;checkpoint()
                if saved.get('action')=='applicant-http-inspect'and saved.get('verification_required'):
                    entry.update(status='verification_required',generation=generation+1);sms_handoff(row,entry,wake);state['waiting_since']=datetime.now(timezone.utc).isoformat();reason='verification_required';break
                observed,proof=job(cid,'submission',{**base,'action':'applicant-readback',**save_job},'readback-'+str(generation))
                outcome='saved'if observed['completed']else'not_applied';acknowledge(cid,proof,outcome,'ack-'+str(generation))
                if not observed['completed']:
                    entry.update(status='verification_required',generation=generation+1);sms_handoff(row,entry,wake);reason='verification_required';state['waiting_since']=datetime.now(timezone.utc).isoformat();break
                entry.update(status='submitted_verified',readback=proof,node=observed['original'].get('workflow_node_id',observed['original']['bill']['t.node_id']))
            entry['active_seconds']=round(entry.get('active_seconds',0)+time.monotonic()-begin,6)
            if 'submission_seconds'not in entry:entry['submission_seconds']=entry['active_seconds']if entry['active_seconds']+0.001>=sum(t['seconds']for t in entry['timings'])else None
            active_entry=None;results.append(dict(entry));checkpoint()
        run['completed']=reason is None
    except Exception as e:
        reason='original_job_requires_recovery';state['error']=str(e)
    finally:
        if active_entry is not None:active_entry['active_seconds']=round(active_entry.get('active_seconds',0)+time.monotonic()-case_started,6)
        run['seconds']=round(time.monotonic()-started,6);run['stopped_at']=datetime.now(timezone.utc).isoformat();checkpoint()
    verified=[r for r in state['cases'].values()if r.get('status')in {'submitted_verified','already_submitted_verified'}]
    performed=[r for r in verified if any(k.startswith('agree-')for k in r['jobs'])and r.get('active_seconds')is not None]
    for entry in state['cases'].values():
        entry['observed_job_seconds']=round(sum(t['seconds']for t in entry['timings']),6)
        entry['timing_complete']=entry.get('active_seconds',0)+0.001>=entry['observed_job_seconds']
    complete_times=[r['submission_seconds']for r in performed if r.get('submission_seconds')is not None]
    elapsed=sum(r['seconds']for r in state['runs']if r['seconds']is not None)
    report={'ok':reason is None,'state':reason or'completed','case_id':c['case_id'],'business_transport':'native_http','document_count':len(rows),'submitted_count':len(verified),'results':list(state['cases'].values()),'elapsed_seconds':elapsed,'human_wait_seconds':state.get('human_wait_seconds',0),'mean_completed_submission_seconds':sum(r['active_seconds']for r in performed)/len(performed)if performed else None,'measured_new_submissions':len(performed),'performance_target_seconds':300,'within_target':elapsed<=300 if reason is None else None,'timing_complete':all(r['seconds']is not None for r in state['runs']),'report_path':str(output),'checkpoint':str(state_path),'manifest_sha256':fingerprint,'approval_scope':'current_applicant_only','created_claims':0,'leadership_approval_completed':False,'payment_completed':False,'error':state.get('error')}
    report.update(mean_completed_submission_seconds=sum(complete_times)/len(complete_times)if complete_times else None,measured_new_submissions=len(complete_times),min_completed_submission_seconds=min(complete_times)if complete_times else None,max_completed_submission_seconds=max(complete_times)if complete_times else None,
        excluded_incomplete_timing_cases=[r['case_id']for r in performed if r.get('submission_seconds')is None],
        failed_job_observations=sum(t['status']!='completed'for e in state['cases'].values()for t in e['timings']),
        batch_wall_elapsed_seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(state['runs'][0]['started_at'])).total_seconds(),
        timing_scope='automated_batch_invocations_including_recovery; excludes human wait and between-run debugging; per-case means exclude incomplete timers',
        next_action={'tool':'reimbursement_applicant_batch','arguments':{k:c[k]for k in ['case_id','manifest','tab_id','request_id','execute']},'requires_user_input':reason=='verification_required','instruction':'在学校原窗口填写验证码后，重跑同一批次；先回读，已完成原单自动跳过。'if reason=='verification_required'else'继续同一批次，原任务未知时只恢复回执。'}if reason else None)
    report['unmeasured_existing_completed_cases']=[r['case_id']for r in verified if not any(k.startswith('agree-')for k in r['jobs'])]
    report['coordinator_within_target']=report['within_target']
    report['within_target']=report['within_target']if len(complete_times)==len(rows)and report['timing_complete']else None
    checkpoint()
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();c=read_json(a.input);report=run(c,a.output);atomic_json(a.output,report);print(json.dumps({k:report[k]for k in ['ok','state','submitted_count','elapsed_seconds']},ensure_ascii=False));raise SystemExit(0 if report['ok']or report['state']=='verification_required'else 2)
