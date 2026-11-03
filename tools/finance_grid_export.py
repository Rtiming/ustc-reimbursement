"""固定HTTP原生清单导出及逐单逐列回读；文件保留学校原始字节。"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
FIELDS=['t.serial_no','t.req_date','t.uni_prj_code','t.abst','t.status_name','t.total_amt','t.real_info','t.req_mobile']
CAPTIONS=['报销单号','填报时间','经费项目号','摘要','状态','报销金额','实际报销人信息','报销人电话']


def frozen(path,value):
    data=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    if path.exists():
        if path.read_text()!=data:raise ValueError('原导出输入发生变化，不覆盖')
    else:
        with path.open('x',encoding='utf-8',newline='\n')as f:f.write(data)
        path.chmod(0o600)


def command(service,request,folder):
    from assistant_api import read_json
    p=request['parameters'];job=p['source_job_id']
    source_folder=service.job_folder(request['case_id'],job);prior=read_json(source_folder/'request.json')
    status=service.result(request['case_id'],job)
    if p['action']=='bills-export-recover':
        if prior['operation']!='business' or prior['parameters'].get('action')!='bills-export' or status['status']in {'queued','running'}:
            raise ValueError('仅恢复已停止的同案原生导出任务')
        config=read_json(source_folder/'native-grid-export-input.json');begin=read_json(source_folder/'grid-begin.json')
        if (config.get('case_id')!=request['case_id'] or config.get('expected_userid')!=p['expected_userid'] or config.get('tab_id')!=p['tab_id']
                or begin.get('phase')!='script_completed' or begin.get('result',{}).get('ok')is not True
                or begin['result'].get('session_token')!=config.get('session_token')):
            raise ValueError('原导出尚无明确已生成的同账号文件，只恢复原marker')
        frozen(folder/'recovery-source.json',{'job_id':job,'input_sha256':hashlib.sha256((source_folder/'native-grid-export-input.json').read_bytes()).hexdigest(),
            'begin_sha256':hashlib.sha256((source_folder/'grid-begin.json').read_bytes()).hexdigest(),'no_regeneration':True})
        return [service.python,str(ROOT/'tools/finance_grid_export.py'),'--input',str(source_folder/'native-grid-export-input.json'),'--output',str(folder/'output.json')]
    if prior['operation']!='business' or prior['parameters'].get('action')!='bills-list' or status['status']!='completed':
        raise ValueError('原生导出须引用同案已完成的完整HTTP清单')
    matches=[a for a in status['artifacts']if a['name']=='output.json']
    if len(matches)!=1:raise ValueError('原清单回执不唯一')
    path=service.artifact(request['case_id'],job,matches[0]['artifact_id']);envelope=read_json(path);body=envelope.get('result',{})
    if (envelope.get('phase')!='script_completed' or body.get('ok')is not True or body.get('complete')is not True or body.get('action')!='bills-list'
            or body.get('expected_userid')!=p['expected_userid'] or body.get('business_transport')!='native_http'):
        raise ValueError('原生导出来源不是同账号完整清单')
    rows=body['rows'];all_rows=rows;export_format='EXCEL';references={'source_job_id':job,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if (len(rows)!=body.get('total_records') or len({r.get('t.serial_no')for r in rows})!=len(rows)
            or any(not re.fullmatch(r'\d+',r.get('t.serial_no','')) or not r.get('t.REQ_INFO','').startswith(p['expected_userid']+'-')for r in rows)):
        raise ValueError('原清单集合或申请人身份不完整')
    try:
        from registered_inputs import load_definition,checked_reference
        cfg,_,_,_=load_definition(service,request['case_id'])
    except Exception as error:
        if getattr(error,'code',None)!='registered_workflow_missing':raise
        cfg={}
    if cfg.get('list_export_manifest'):
        manifest_path=checked_reference(service,cfg['list_export_manifest']);manifest=read_json(manifest_path)
        if manifest.get('expected_userid')!=p['expected_userid'] or manifest.get('format')not in {'EXCEL','PDF'}:
            raise ValueError('当前原生HTTP列表导出接受已登记本人EXCEL/PDF范围')
        export_format=manifest['format']
        serials=[]
        for case in manifest['cases']:
            form=read_json(service.resolve(case['case_id'])[1]['form'])
            if form['serial']!=case['serial'] or form.get('expected_userid')not in (None,p['expected_userid']):raise ValueError('登记导出范围身份变化')
            serials.append(case['serial'])
        if len(set(serials))!=len(serials):raise ValueError('登记导出范围重复')
        rows=[r for r in rows if r['t.serial_no']in serials]
        if len(rows)!=len(serials):raise ValueError('完整来源清单缺少登记导出单据')
        references['manifest_sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if not rows or len(rows)>500:raise ValueError('原生清单导出须有1至500单明确范围')
    scope_ids={r['t.serial_no']for r in rows}
    if len(rows)==len(all_rows):scope_query='all'
    elif len(rows)<=25:scope_query='serials'
    elif scope_ids=={r['t.serial_no']for r in all_rows if r['t.status']=='10' and r['t.lc_finish']=='T' and r['t.status_name']=='已预约待投递'}:scope_query='ready_for_delivery'
    else:raise ValueError('该导出范围超过学校25单筛选预算且不等于全部或完整待投递集合；未发起导出')
    config={'schema':'ustc-native-grid-export-v1','case_id':request['case_id'],'expected_userid':p['expected_userid'],
        'tab_id':p['tab_id'],'session_token':hashlib.sha256((request['case_id']+':'+request['request_id']).encode()).hexdigest()[:32],
        'scope':rows,'scope_query':scope_query,'sources':references,'export_format':export_format}
    frozen(folder/'native-grid-export-input.json',config)
    from finance_http import source
    code=source('grid-export');adapter=folder/'native-grid-export-adapter.js'
    if adapter.exists():
        if adapter.read_text()!=code:raise ValueError('原导出源码发生变化，不覆盖')
    else:adapter.write_text(code,encoding='utf-8',newline='\n');adapter.chmod(0o600)
    return [service.python,str(ROOT/'tools/finance_grid_export.py'),'--input',str(folder/'native-grid-export-input.json'),'--output',str(folder/'output.json')]


def validate_rows(actual,expected):
    if len(actual)!=len(expected):raise ValueError('原生导出行数与冻结范围不符')
    by_id={r['t.serial_no']:r for r in expected};seen=set()
    if len(by_id)!=len(expected):raise ValueError('原始单号重复')
    for row in actual:
        if len(row)!=len(FIELDS) or not isinstance(row[0],str) or row[0]not in by_id or row[0]in seen:raise ValueError('原生导出列数、单号或集合不符')
        seen.add(row[0]);target=by_id[row[0]]
        for field,value in zip(FIELDS,row):
            want=target[field]
            # 原生XLS会把<br />规范为<br>；仅统一项目号换行表示，不改字段内容。
            if field=='t.uni_prj_code':
                want=re.sub(r'<br\s*/?>','\n',want,flags=re.I).replace('\r\n','\n')
                value=re.sub(r'<br\s*/?>','\n',value,flags=re.I).replace('\r\n','\n')
            if value!=want:raise ValueError('原生导出字段与冻结清单不同：'+field)
    return {'records':len(seen),'verified_cells':len(seen)*len(FIELDS),'all_serials_and_values_match':True}


def validate_xls(path,expected):
    import xlrd
    with xlrd.open_workbook(path,on_demand=True)as book:
        if book.nsheets!=1:raise ValueError('原生清单不是唯一工作表')
        sheet=book.sheet_by_index(0)
        if sheet.row_values(0)!=CAPTIONS:raise ValueError('原生清单表头不符')
        return validate_rows([sheet.row_values(i)for i in range(1,sheet.nrows)],expected)


def validate_pdf(path,expected):
    import pymupdf
    actual=[]
    with pymupdf.open(path)as document:
        for page in document:
            for table in page.find_tables().tables:
                for row in table.extract():
                    if len(row)!=len(FIELDS):continue
                    values=[value or '' for value in row]
                    if re.fullmatch(r'\d+',values[0]):actual.append(values)
    # PDF表格换行由排版产生；只在同一字段内规范显示空白，仍逐单逐列比较。
    clean=lambda value:re.sub(r'\s+','',re.sub(r'<br\s*/?>','',value,flags=re.I))
    normalized=[{k:clean(row[k])for k in FIELDS}for row in expected]
    return validate_rows([[clean(value)for value in row]for row in actual],normalized)


def run(input_path,output,runtime=None):
    config=json.loads(input_path.read_text());folder=input_path.parent
    if config.get('schema')!='ustc-native-grid-export-v1':raise ValueError('原生导出输入合同不符')
    # 派发前确认消费者可用，不能先下载后才发现缺依赖。
    if config['export_format']=='EXCEL':import xlrd
    else:import pymupdf
    from finance_http import source
    adapter=folder/'native-grid-export-adapter.js';code=adapter.read_text()
    if code!=source('grid-export'):raise ValueError('原导出源码已变化，只恢复原任务')
    runtime=runtime or subprocess.run
    def call(action,**extra):
        suffix=action+('-'+str(extra['offset'])if 'offset'in extra else '')
        receipt=folder/('grid-'+suffix+'.json');script=receipt.with_suffix('.js')
        c={**config,'action':action,**extra};text='(async()=>{\n'+code+'\nreturn await reimbursementGridExportApi('+json.dumps(c,ensure_ascii=False)+');})()'
        if receipt.exists():
            old=json.loads(receipt.read_text())
            if script.read_text()!=text or old.get('source_sha256')!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('原导出子任务源码不同')
            if old.get('phase')=='script_completed' and old.get('result',{}).get('ok')is True:return old['result']
            if (action=='begin' or old.get('phase')!='not_dispatched' or old.get('operation_kind')!='read'
                    or old.get('result',{}).get('injection_acknowledged')is not False
                    or 'tab_window_mismatch'not in old.get('result',{}).get('error','')):
                raise ValueError('原导出子任务未知，只恢复原marker')
            receipt=folder/('grid-'+suffix+'-after-binding.json')
            if receipt.exists():
                recovered=json.loads(receipt.read_text())
                if (recovered.get('phase')!='script_completed' or recovered.get('result',{}).get('ok')is not True
                        or recovered.get('source_sha256')!=hashlib.sha256(text.encode()).hexdigest()):raise ValueError('原绑定恢复子任务尚未确认')
                return recovered['result']
        else:script.write_text(text,encoding='utf-8',newline='\n');script.chmod(0o600)
        from registered_page_owner import RegisteredPageOwner
        RegisteredPageOwner.before_child_dispatch(config['tab_id'])
        result=runtime([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),'--output',str(receipt),
            '--tab-id',config['tab_id'],'--operation-kind','read','--quiet'])
        if result.returncode:raise ValueError('原导出子任务失败，保留原marker，不重发')
        envelope=json.loads(receipt.read_text());r=envelope.get('result',{})
        if envelope.get('phase')!='script_completed' or r.get('ok')is not True:raise ValueError('原导出子任务不完整')
        return r
    metadata=call('begin');data=bytearray()
    if not isinstance(metadata.get('size'),int)or not 512<=metadata['size']<=64*1024*1024:raise ValueError('原生导出文件长度不符')
    for offset in range(0,metadata['size'],131072):
        chunk=call('chunk',offset=offset,length=min(131072,metadata['size']-offset));raw=base64.b64decode(chunk['base64'],validate=True)
        if chunk.get('offset')!=offset or len(raw)!=chunk.get('chunk_size') or hashlib.sha256(raw).hexdigest()!=chunk.get('chunk_sha256') or chunk.get('sha256')!=metadata['sha256']:raise ValueError('原生导出分块SHA或身份不符')
        data.extend(raw)
    if len(data)!=metadata['size']or hashlib.sha256(data).hexdigest()!=metadata['sha256']:raise ValueError('原生导出整件SHA不符')
    target=folder/('bills.xls'if config['export_format']=='EXCEL'else'bills.pdf')
    if target.exists():
        if target.read_bytes()!=data:raise ValueError('原生导出原件已改变')
    else:target.write_bytes(data);target.chmod(0o600)
    checked=(validate_xls if config['export_format']=='EXCEL'else validate_pdf)(target,config['scope']);finished=call('finish')
    if finished.get('sha256')!=metadata['sha256']:raise ValueError('原生导出结束回执身份不同')
    frozen(output,{'ok':True,'complete':True,'state':'native_grid_export_verified','case_id':config['case_id'],
        'business_transport':'native_http','system_write':False,'financial_write':False,'format':config['export_format'],
        'file':str(target.resolve()),'sha256':metadata['sha256'],'sources':config['sources'],'verification':checked,
        'original_bytes_preserved':True,'browser_dialog_opened':False})
    return 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.input,a.output))
