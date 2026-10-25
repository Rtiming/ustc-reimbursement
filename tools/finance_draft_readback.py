"""解释建单后的独立原生回读；认证正常不替代建单校验。"""
import re
import copy
import json
import hashlib
from pathlib import Path
from datetime import datetime
from decimal import Decimal


def validate_creation_selection(manifest):
    rows=manifest.get('selected_invoices',[])
    if not rows or len(rows)!=manifest.get('expected_count') or len({r.get('guid')for r in rows})!=len(rows):
        raise ValueError('创建前须先登记本案已核完整票据选择')
    if sum((Decimal(r['amount'])for r in rows),Decimal(0))!=Decimal(manifest['expected_amount']):
        raise ValueError('创建前已核选择金额不闭合')


def verify_unconfigured_creation(manifest,form,native_receipt,readback):
    """仅证明固定脚本在任何原生选票/建单操作前因缺参数拒绝；不处理未知请求。"""
    from select_case_invoices import select
    if form.get('serial') or manifest.get('serial') or not manifest.get('draft_creation_requested'):
        raise ValueError('仅恢复无单号的未配置创建前置拒绝')
    result=native_receipt.get('result',{})
    if native_receipt.get('phase')!='script_completed' or result!={'ok':False,'error':'已核对的票据选择参数不完整'}:
        raise ValueError('原创建不是明确的配置前置拒绝')
    path=Path(native_receipt.get('script',''));raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=native_receipt.get('source_sha256'):raise ValueError('原创建源码SHA改变')
    template=(Path(__file__).parent/'create_daily_draft.js').read_text()
    prefix='(()=>{if(location.origin!=="https://cwzh.ustc.edu.cn"||$.getProjID()!=="WF_YBX")throw Error("财务系统不符");return (('+template+')('
    source=raw.decode()
    if not source.startswith(prefix)or not source.endswith('));})()'):raise ValueError('原创建不是固定已核脚本')
    frozen=json.loads(source[len(prefix):-7])
    if frozen.get('selected_invoices') or frozen.get('serial')or frozen.get('draft_creation_requested')or any(frozen.get(k)!=manifest.get(k)for k in ['case_id','expected_count','original_files']):raise ValueError('原创建曾有选择或原始材料已变')
    body=readback.get('result',{})
    if (readback.get('phase')!='script_completed' or not re.fullmatch('[0-9]+',str(readback.get('tab_id','')))
        or readback.get('browser_transport')!=native_receipt.get('browser_transport')
        or datetime.fromisoformat(readback['executed_at_utc'])<=datetime.fromisoformat(native_receipt['executed_at_utc'])
        or body.get('ok')is not True or body.get('projid')!='WF_YBX' or body.get('serial') or body.get('visibleDialogs')
        or 'serial' not in body or 'visibleDialogs'not in body or body.get('scopeComplete')is not True):
        raise ValueError('新独立票池回读未排除单号、弹框或现场改变')
    for row in manifest['original_files']:
        if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256']:raise ValueError('恢复前原件SHA变化')
    selected=select(manifest,readback,form['amount'])
    if not form.get('expected_userid') or body.get('identity',{}).get('userid')!=form['expected_userid']:raise ValueError('独立票池账号与本案恢复账号不符')
    return selected


def classify_draft_readback(result, invoices):
    if result.get('ok') is not True or result.get('projid')!='WF_YBX':
        raise ValueError('建单回读不是成功的财务原生观察')
    serial=str(result.get('serial') or '').strip()
    dialogs=result.get('visibleDialogs',[])
    prompts=[d.get('text','') for d in dialogs if '有错误信息' in d.get('text','') and '请手工标注或再次认证后再前往报销' in d.get('text','')]
    if prompts:
        if serial or len(prompts)!=1:raise ValueError('建单错误与单号上下文矛盾，保持待核')
        medical=re.findall(r'医/([0-9]{8})/([0-9]{10})(?![0-9])',prompts[0])
        vat=re.findall(r'(?:普|专)/全电票/([0-9]{20})(?![0-9])',prompts[0])
        lines=[x.strip() for x in prompts[0].splitlines() if re.search(r'(?:医|普|专|铁|飞)/',x)]
        if len(lines)!=len(medical)+len(vat) or any(not re.fullmatch(r'(?:医/[0-9]{8}/[0-9]{10}|(?:普|专)/全电票/[0-9]{20})',x) for x in lines):
            raise ValueError('建单错误包含未适配票种或不完整行，不能部分放行')
        expected=invoices.get('selected_invoices',[])
        matched=[]
        for code,number in medical+[(None,n) for n in vat]:
            hits=[e for e in expected if e.get('number')==number and (code is None or e.get('type')=='MEDICAL' and e.get('invoice_code')==code)]
            if len(hits)!=1:raise ValueError('错误列表含非本案或不唯一的票，不能认作本次建单拒绝')
            matched.append({'invoice_code':code,'invoice_number':number,'guid':hits[0]['guid'],'amount':hits[0]['amount']})
        if not matched or len({e['guid'] for e in matched})!=len(matched):raise ValueError('建单拦截未取得完整且唯一的票号')
        return {'schema':'ustc-draft-readback-v1','state':'blocked_by_invoice_metadata','claim_created':False,'serial':None,
                'blocking_count':len(matched),'blocking_amount':format(sum(Decimal(str(e['amount'])) for e in matched),'.2f'),
                'blocking_invoices':matched,'native_prompt':prompts[0],'certification_does_not_prove_claim_ready':True,
                'next':'按原件核对列明票据的原生手工标注或再次认证，不重复建单、不循环重传'}
    if serial and re.fullmatch(r'[0-9]+',serial):
        return {'schema':'ustc-draft-readback-v1','state':'serial_observed_requires_binding','serial':serial,'claim_created':None,
                'next':'按本案bind-serial及真实表单独立核验，单号出现不证明已保存、提交或付款'}
    return {'schema':'ustc-draft-readback-v1','state':'unknown','claim_created':None,'serial':None,
            'next':'未取得单号或明确拒绝，保留原创建意图，只读恢复，不重复创建'}


def prepare_rejected_draft_resume(manifest, form, case_directory, current_output=None):
    """仅接续本案最后一次已独立回读的明确拒绝；旧创建意图始终保留。"""
    root=Path(case_directory).resolve()
    if form.get('case_id')!=manifest.get('case_id') or form.get('serial') or manifest.get('serial') or not manifest.get('draft_creation_requested'):
        raise ValueError('只接续本案无单号的原创建请求')
    pending=root/'pending_write.json'
    if pending.exists():
        active=json.loads(pending.read_text())
        if current_output is None or active.get('case_id')!=manifest['case_id'] or active.get('operation')!='invoice:create-draft' or Path(active.get('output_directory','')).resolve()!=Path(current_output).resolve():
            raise ValueError('原写入尚未独立核对，不能再次建单')
    recovery=manifest.get('unconfigured_creation_recovery')
    if recovery:
        receipt=Path(recovery['original_receipt']);read_path=Path(recovery['readback_receipt']);ack_path=receipt.parent/'operator_readback_ack.json'
        if Path(manifest.get('creation_receipt','')).resolve()!=receipt.resolve():raise ValueError('已有新的创建意图，不得再次复用旧配置拒绝证明')
        if not receipt.resolve().is_relative_to(root/'receipts')or not read_path.resolve().is_relative_to(root/'receipts'):
            raise ValueError('恢复依据不属于本案')
        if any(hashlib.sha256(p.read_bytes()).hexdigest()!=recovery[k]for p,k in [(receipt,'original_receipt_sha256'),(read_path,'readback_sha256')]):raise ValueError('恢复依据SHA改变')
        selected=verify_unconfigured_creation(manifest,{**form,'expected_userid':recovery.get('expected_userid')},json.loads(receipt.read_text()),json.loads(read_path.read_text()))
        ack=json.loads(ack_path.read_text())
        if (ack.get('outcome')!='not_applied' or ack.get('status')!='operator_readback_recorded'
            or ack.get('case_id')!=manifest['case_id'] or ack.get('serial') or ack.get('operation')!='invoice:create-draft'
            or Path(ack.get('output_directory','')).resolve()!=receipt.parent or ack.get('readback_receipt')!=str(read_path)
            or selected.get('selected_invoices')!=manifest.get('selected_invoices')):
            raise ValueError('原前置拒绝未登记或已核选择改变')
        result=copy.deepcopy(manifest);result['draft_creation_requested']=False
        result['resuming_rejected_creation']={**recovery,'acknowledgement':str(ack_path),'ack_sha256':hashlib.sha256(ack_path.read_bytes()).hexdigest(),'rejection':{'state':'local_precondition_rejected_before_native_action'}}
        return result
    receipt=Path(manifest.get('creation_receipt','')).resolve()
    if not receipt.is_relative_to(root/'receipts') or receipt.name!='invoice.json':raise ValueError('原创建回执须在本案receipts目录')
    ack_path=receipt.parent/'operator_readback_ack.json'
    ack=json.loads(ack_path.read_text())
    if ack.get('case_id')!=manifest['case_id'] or ack.get('serial') or ack.get('operation')!='invoice:create-draft' or ack.get('status')!='operator_readback_recorded' or ack.get('outcome') not in ['needs_correction','not_applied'] or Path(ack.get('output_directory','')).resolve()!=receipt.parent:
        raise ValueError('原创建未登记明确拒绝，不允许再次建单')
    read_path=Path(ack.get('readback_receipt','')).resolve()
    if not read_path.is_relative_to(root/'receipts'):raise ValueError('独立拒绝回读须来自本案receipts')
    observed=json.loads(read_path.read_text())
    if datetime.fromisoformat(observed['executed_at_utc'])<=datetime.fromisoformat(ack['started_at_utc']):raise ValueError('不能用旧回读放行新创建')
    rejected=classify_draft_readback(observed.get('result',{}),manifest)
    if rejected['state']!='blocked_by_invoice_metadata':raise ValueError('原创建结果未知或已出现单号，禁止重放')
    native_receipt=json.loads(receipt.read_text())
    original=native_receipt.get('result',{})
    if original.get('action')!='create_daily_draft_requested' or original.get('case_id')!=manifest['case_id'] or original.get('count')!=manifest.get('expected_count') or Decimal(str(original.get('total')))!=Decimal(str(manifest.get('expected_amount'))):
        raise ValueError('原创建范围/合计与当前冻结清单不符')
    script=Path(native_receipt['script']).read_bytes()
    if hashlib.sha256(script).hexdigest()!=native_receipt['source_sha256']:raise ValueError('原创建脚本SHA变化')
    source=script.decode('utf-8');matches=list(re.finditer(r'\n\}\)\s*\)\(',source))
    if len(matches)!=1:raise ValueError('原创建冻结参数格式不符')
    frozen,end=json.JSONDecoder().raw_decode(source[matches[0].end():])
    if source[matches[0].end()+end:]!='));})()' or any(frozen.get(k)!=manifest.get(k) for k in ['case_id','expected_count','expected_amount','selected_invoices']):
        raise ValueError('原创建逐票范围改变，不能沿旧拒绝接续')
    result=copy.deepcopy(manifest)
    result['draft_creation_requested']=False  # 仅给原生校验的单次副本，不清除案件标记。
    result['resuming_rejected_creation']={'previous_creation_receipt':str(receipt),'acknowledgement':str(ack_path),
        'ack_sha256':hashlib.sha256(ack_path.read_bytes()).hexdigest(),'readback_receipt':str(read_path),
        'readback_sha256':hashlib.sha256(read_path.read_bytes()).hexdigest(),'rejection':rejected}
    return result
