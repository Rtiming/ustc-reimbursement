"""保存已请求、旧预览为空时，仅允许在另一原生标签独立找回原单。"""
import hashlib
import json
from pathlib import Path
import re


def qualify(service, case_id, pending, target_tab_id, reference):
    from assistant_api import APIError, read_json
    from finance_backend import transport_identity, NM_NAMESPACE
    from finance_session import native_inventory
    def reject():
        raise APIError('saved_payment_reopen', '须有原支付保存请求、之后的同号空预览及另一原生标签；原pending保留')
    _, paths = service.resolve(case_id)
    form = read_json(paths['form'])
    if (not isinstance(reference, dict) or set(reference) != {'job_id','artifact_id','serial','expected_userid'}
            or pending.get('case_id') != case_id or pending.get('operation') != 'step:payments'
            or pending.get('serial') != form.get('serial') or reference['serial'] != form.get('serial')
            or pending.get('status') != 'await_independent_readback'):
        reject()
    path = service.artifact(case_id, reference['job_id'], reference['artifact_id'])
    job = read_json(service.job_folder(case_id, reference['job_id'])/'request.json')
    observed = read_json(path); body = observed.get('result', {})
    if (job.get('operation') != 'read_system' or job.get('parameters',{}).get('mode','readback') != 'readback'
            or job.get('case_id') != case_id or observed.get('phase') != 'script_completed'
            or body.get('ok') is not True or body.get('projid') != 'WF_YBX'
            or body.get('serial') != form['serial'] or body.get('identity',{}).get('userid') != reference['expected_userid']
            or body.get('visibleInputs') != [] or body.get('visibleDialogs') != [] or body.get('node') not in ['',None]):
        reject()
    frames = body.get('frames', [])
    if len(frames) != 1: reject()
    frame = frames[0]; proof = frame.get('proof_inventory', {})
    if (frame.get('id') != 'iframepage_print' or frame.get('visibleText','missing').strip()
            or frame.get('fields') != [] or frame.get('controls') != []
            or proof.get('schema') != 'ustc-native-proof-inventory-v1' or proof.get('serial') != form['serial']
            or proof.get('frame_visible') is not True or proof.get('document_ready_state') != 'complete'
            or proof.get('preview_complete') is not False or proof.get('preview_serials') != []
            or proof.get('scan_complete') is not True or proof.get('truncated') is not False
            or not proof.get('sections') or any(proof['sections'].values())):
        reject()
    folder = Path(str(pending.get('output_directory',''))).resolve()
    if not folder.is_relative_to(paths['state'].parent.resolve()/'receipts'): reject()
    saved_path = folder/'02_save-payments.json'; saved = read_json(saved_path)
    expected = {'ok':False,'action':'save_payments','serial':form['serial'],'saveRequested':True,
                'execution_status':'unknown_verify_before_retry','error_code':'payment_save_readiness_timeout',
                'remittanceNormalization':[],'requiresReadback':True}
    if (saved.get('schema') != 'ustc-edge-operation-v2' or saved.get('phase') != 'script_completed'
            or saved.get('result') != expected or saved.get('tab_id') == target_tab_id
            or observed.get('tab_id') != saved.get('tab_id')
            or job.get('parameters',{}).get('tab_id') != saved.get('tab_id')
            or observed.get('browser_transport') != saved.get('browser_transport')
            or not saved.get('executed_at_utc') or observed.get('executed_at_utc','') <= saved['executed_at_utc']
            or saved['executed_at_utc'] <= pending.get('started_at_utc','')):
        reject()
    script = Path(str(saved.get('script','')))
    if not script.is_absolute() or hashlib.sha256(script.read_bytes()).hexdigest() != saved.get('source_sha256'): reject()
    source = script.read_text()
    invocation = re.search(r'\nreturn await ReimbursementAutomation\.savePayments\((\{[^\n]*\}),false\);\s*\}\)\(\)\s*$', source)
    library = (Path(__file__).parent/'browser_automation.js').read_text()
    if not invocation or source[:invocation.start()] != '(async()=>{\n'+library: reject()
    requested = json.loads(invocation.group(1))
    if any(requested.get(k) != form.get(k) for k in ['case_id','serial','amount','payments']): reject()
    journal = read_json(folder/'run_journal.json')
    commands = journal.get('commands', [])
    if (journal.get('case_id') != case_id or journal.get('serial') != form['serial'] or journal.get('executed') is not True
            or journal.get('failed_action') != 2 or len(commands) != 3
            or [x.get('index') for x in journal.get('completed', [])] != [0,1]
            or [command[2]for command in commands] != ['navigate','transfers','save-payments']
            or commands[2][1:3] != [str(Path(__file__).parent/'finance_cli.py'),'save-payments']
            or '--case' not in commands[2] or commands[2][commands[2].index('--case')+1] != str(paths['form'])):
        reject()
    transport = transport_identity(); inventory = native_inventory()
    namespace = inventory.get('tab_id_namespace'); origin = saved.get('browser_transport', {})
    targets = [t for t in inventory.get('tabs',[]) if t.get('tab_id') == target_tab_id
               and t.get('window_id') == transport.get('window_id') and t.get('path') == 'https://cwzh.ustc.edu.cn/YBX/main.jsp']
    if (transport.get('backend') != 'edge_native_messaging' or transport.get('platform') != 'darwin'
            or origin.get('backend') != 'edge_native_messaging' or origin.get('platform') != 'darwin'
            or not isinstance(namespace,str) or not NM_NAMESPACE.fullmatch(namespace)
            or namespace != transport.get('tab_id_namespace') or namespace != origin.get('tab_id_namespace')
            or inventory.get('tab_inventory_complete') is not True or inventory.get('tab_inventory_scope') != 'extension_visible_profile_tabs'
            or len(targets) != 1 or target_tab_id not in inventory.get('all_tab_ids', [])):
        reject()
    pending_path=paths['state'].parent/'pending_write.json'
    return {'classification':'saved_payment_blank_preview_independent_new_tab_read',
            'case_id':case_id,'serial':form['serial'],'original_tab_id':saved['tab_id'],'target_tab_id':target_tab_id,
            'original_save_receipt':str(saved_path),'original_save_sha256':hashlib.sha256(saved_path.read_bytes()).hexdigest(),
            'empty_preview':{'job_id':reference['job_id'],'artifact_id':reference['artifact_id'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
            'original_pending_path':str(pending_path),'original_pending_sha256':hashlib.sha256(pending_path.read_bytes()).hexdigest(),
            'original_pending_preserved':True,'save_replayed':False,'payment_saved_verified':False}
