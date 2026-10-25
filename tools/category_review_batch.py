"""串行读取登记批次的原生分类候选；不选择科目、不保存财务字段。"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from assistant_api import AssistantService, APIError, atomic_json, read_json
from batch_workflow import DraftBatch, verify_invoice_scope
from finance_category_verify import category_rows
from registered_session import RegisteredSession
from registered_inputs import load_definition
from edge_runtime import file_lock


def run(service, case_id, sequences, execute=False):
    cfg, index, auth, _ = load_definition(service, case_id)
    rows = [r for r in read_json(index)['documents'] if r['index'] in sequences]
    if {r['index'] for r in rows} != set(sequences):
        raise APIError('category_sequences', '分类核对序号不属于当前登记批次')
    if not execute:
        return {'ok':True, 'cases':[r['case_id'] for r in rows], 'system_write':False, 'browser_access':False}
    out = service.root/'.local/category_reviews'/case_id/str(time.time_ns())
    out.mkdir(parents=True)
    result = {'ok':True, 'system_write':False, 'results':[], 'report_path':str(out/'report.json')}
    with file_lock(service.root/'.local/workflow-orchestrator.lock'):
        for registered in read_json(index)['documents']:
            current = service.overview(registered['case_id'])
            if current.get('unverified_write') or current.get('active_jobs'):
                raise APIError('category_original_task', '批次原任务尚未核实，不新开或切换分类现场')
        session = RegisteredSession(service, case_id, cfg, index, auth)
        session.ensure()
        for row in rows:
            case = row['case_id']; paths = service.resolve(case)[1]; form = read_json(paths['form'])
            overview = service.overview(case)
            if overview.get('unverified_write') or overview.get('active_jobs'):
                raise APIError('category_original_task', '先核原任务，分类核对不改变未知现场')
            if form.get('review', {}).get('workflow_started'):
                result['results'].append({'case_id':case,'sequence':row['index'],'state':'workflow_exists_not_edited'})
                continue
            tab = session.begin_case(case)
            job, data = session.job(case, 'read_system', {'mode':'open-draft', 'serial':form['serial'],
                'expected_userid':cfg['expected_userid'], 'tab_id':tab}, 'categories-original')
            frames = [f for f in data.get('frames',[]) if f.get('id') == 'iframepage_print']
            if len(frames) != 1 or data.get('serial') != form['serial'] or data.get('identity',{}).get('userid') != cfg['expected_userid']:
                raise APIError('category_identity', '当前原单或账号未核实')
            frame = frames[0]; verify_invoice_scope(form, read_json(paths['invoices']), frame)
            runner = DraftBatch(service,index,cfg['scenario'],tab,cfg['expected_userid'])
            for stage in ['basic','funding','payments']:runner.verify_stage(case,stage,frame)
            item = {'case_id':case, 'sequence':row['index'], 'serial':form['serial'], 'preview_job_id':job['job_id'],
                    'state':'candidates_observed_not_selected', 'groups':[]}
            folder = out/case; folder.mkdir()
            for number, current in enumerate(category_rows(frame)):
                observed = None
                for action in ['invoice-group-open','invoice-group-inspect','invoice-group-close']:
                    target = folder/(str(number)+'-'+action+'.json')
                    command = [service.python,str(service.root/'tools/finance_cli.py'),action,'--case',str(paths['form']),
                               '--tab-id',tab,'--output',str(target)]
                    if action == 'invoice-group-open':command += ['--group',current['group']]
                    process = subprocess.run(command,capture_output=True,text=True,timeout=150)
                    if process.returncode:
                        raise APIError('category_read_failed', '原分类读取未完成；不选择、不保存，查看 '+str(target))
                    if action == 'invoice-group-inspect':
                        native = read_json(target)
                        while isinstance(native.get('result'),dict):native=native['result']
                        observed = {'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                                    'candidates':[r['row'] for r in native['rows']]}
                item['groups'].append({'current':current,'native_candidates':observed})
            result['results'].append(item); atomic_json(out/'report.json',result)
            print(json.dumps({'sequence':row['index'],'state':item['state'],'groups':len(item['groups'])},ensure_ascii=False),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-id',required=True)
    parser.add_argument('--sequences',required=True)
    parser.add_argument('--execute',action='store_true',help='只读原生候选，不选择或保存科目')
    args=parser.parse_args()
    sequences=[int(v) for v in args.sequences.split(',')]
    result=run(AssistantService(),args.case_id,sequences,args.execute)
    print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False))


if __name__ == '__main__':main()
