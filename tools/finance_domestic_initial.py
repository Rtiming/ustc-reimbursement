"""原生国内差旅建单自动生成补助后的只读归属核对；不重放创建。"""
import json,re
from pathlib import Path
from datetime import datetime
from finance_native_create import read,ref,checked,checked_text,browser,verify_used
ROOT=Path(__file__).resolve().parents[1]
KIND='native_domestic_initial_with_automatic_allowance'

def serial_from_original(c,created):
    if c.get('bill_type')!='2'or created.get('create_dispatched')is not True or created.get('case_id')!=c['case_id']or created.get('expected_userid')!=c['expected_userid']or created.get('token')!=c['token']:raise ValueError('仅核对原国内差旅创建，不接收指定新单号')
    messages=[x['raw']for x in created.get('marker',{}).get('native_responses',[])if x.get('phase')=='create_requested']
    if len(messages)!=1 or not messages[0].startswith(('pass:local(','passlocal(')):raise ValueError('原创建未取得可核的原生接受正文')
    raw=messages[0];serials=re.findall(r'(?:local\(|;)0-g0Serialno:(\d+)(?=;|\))',raw,re.I)
    if len(serials)!=1 or not re.search(r'(?:local\(|;)0-bx_type:2(?=;|\))',raw,re.I):raise ValueError('原创建未唯一返回国内差旅单号')
    return serials[0]

def reader(c,envelope,extra,after):
    script=Path(envelope['script']);expected='(async()=>{'+checked_text(c['adapter'])+';return await reimbursementBusinessApi('+json.dumps({**c,**extra},ensure_ascii=False)+');})()'
    if envelope.get('phase')!='script_completed'or envelope.get('tab_id')!=c['tab_id']or ref(script)['sha256']!=envelope.get('source_sha256')or script.read_text()!=expected or datetime.fromisoformat(envelope['dispatch_intent_at_utc'])<=datetime.fromisoformat(after):raise ValueError('国内差旅须原创建后固定源码的独立完整回读')
    b=envelope.get('result',{})
    if b.get('ok')is not True or b.get('complete')is not True or b.get('case_id')!=c['case_id']or b.get('expected_userid')!=c['expected_userid']or b.get('system_write')is not False:raise ValueError('国内差旅独立读取不完整')
    return b

def verify(c,created,created_at,proof):
    from validate_config import money
    serial=serial_from_original(c,created);listing=reader(c,checked(proof['bills']),{'action':'bills-list'},created_at)
    if listing.get('account_scope')!='current_applicant'or len(listing['rows'])!=listing.get('total_records'):raise ValueError('须完整本人清单')
    hits=[r for r in listing['rows']if r.get('t.serial_no')==serial]
    if len(hits)!=1 or hits[0].get('t.bx_type')!='2'or hits[0].get('t.status')!='-99'or hits[0].get('t.lc_finish')!='F':raise ValueError('原新单不是本人未提交国内差旅')
    amount=hits[0]['t.total_amt'].replace(',','');amount=format(money(amount),'.2f')
    if money(amount)<money(c['expected_amount']):raise ValueError('新单金额小于已核原票合计')
    actual={**c,'expected_amount':amount};used=checked(proof['used']);verified=verify_used(actual,created,used,created_at,reconciled_serial=serial)
    contract=proof['travel_contracts']
    if contract!=read(ROOT/'data/finance_business_contracts.json')['domains']['travel']:raise ValueError('国内差旅补助读取合同变化')
    travel=reader(actual,checked(proof['travel']),{'action':'bill-read','domain':'travel','serial':serial,'contracts':contract},created_at)
    sections=travel['sections']
    if len(sections)!=1 or sections[0].get('funcno')!='1714'or sections[0].get('complete')is not True:raise ValueError('原生自动补助清单不完整')
    rows=sections[0]['rows'];keys=[(x['t.sno'],x['t.city'],x['t.startdate'])for x in rows]
    if len(set(keys))!=len(keys)or any(x.get('t.serial_no')!=serial for x in rows):raise ValueError('补助明细存在重复或不同单号')
    automatic=sum((money(x['t.total_amt'].replace(',',''))for x in rows),money('0'))
    if automatic+money(c['expected_amount'])!=money(amount):raise ValueError('原票合计加原生自动补助未与新单总额闭合')
    return{**verified,'amount':amount,'invoice_amount':c['expected_amount'],'initial_native_allowance':format(automatic,'.2f'),'requires_domestic_allowance_correction':True,'native_bill_type':'2'}

def reconcile(c,created,created_at,folder):
    from validate_config import money
    serial=serial_from_original(c,created);folder=Path(folder);listing=folder/'native-domestic-initial-bills.json'
    value=read(listing)if listing.exists()else browser(c,'reimbursementBusinessApi',listing,{'action':'bills-list'})
    hits=[r for r in value.get('result',{}).get('rows',[])if r.get('t.serial_no')==serial]
    if len(hits)!=1:raise ValueError('原创建单号未在本人完整清单唯一定位，不重建')
    amount=format(money(hits[0]['t.total_amt'].replace(',','')),'.2f');actual={**c,'expected_amount':amount};used=folder/'native-domestic-initial-used.json';travel=folder/'native-domestic-initial-travel.json';contract=read(ROOT/'data/finance_business_contracts.json')['domains']['travel']
    if not used.exists():browser(actual,'reimbursementBusinessApi',used,{'action':'bill-read','domain':'used_invoices','serial':serial,'contracts':c['used_contracts']})
    if not travel.exists():browser(actual,'reimbursementBusinessApi',travel,{'action':'bill-read','domain':'travel','serial':serial,'contracts':contract})
    proof={'kind':KIND,'serial':serial,'bills':ref(listing),'used':ref(used),'travel':ref(travel),'travel_contracts':contract,'creation_replayed':False}
    return proof,verify(c,created,created_at,proof),ref(used)
