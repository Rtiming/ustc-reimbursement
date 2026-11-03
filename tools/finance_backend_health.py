"""核对固定YBX后端和首页入口；不重新登录、不导航、不重发业务。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time


def check(case_id, tab_id, expected_userid, output):
    from edge_runtime import browser_lock
    from finance_backend import backend, mac_target, transport_identity
    from finance_access import write
    from operator_support import stamp
    if (not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id) or not re.fullmatch(r'\d+',tab_id)
            or not re.fullmatch(r'[A-Za-z0-9]{2,40}',expected_userid)):
        raise ValueError('须明确本案、原生tab及账号')
    if output.exists(): raise ValueError('保留原后端核验，不覆盖')
    if backend() != 'edge_native_messaging':raise ValueError('后端核验只使用原生桥，不转模拟点击或AppleEvent')
    from finance_http import source as http_source
    source=http_source('health')
    script='(async()=>{'+source+'\nreturn JSON.stringify(await reimbursementBackendHealth('+json.dumps(expected_userid)+'));})()'
    base={'schema':'ustc-finance-backend-health-v1','case_id':case_id,'tab_id':tab_id,
          'browser_transport':transport_identity(),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
          'observed_at_utc':stamp(),'system_write':False}
    started=time.monotonic()
    with browser_lock():
        if sys.platform=='darwin':
            from mac_edge_bridge import evaluate
            raw=evaluate(script,tab_id,access=True,timeout=8,**mac_target())
        else:
            from windows_edge_bridge import evaluate
            raw=evaluate(script,tab_id,access=True,timeout=8)
    result=json.loads(raw)
    if result.get('system_write') is not False or result.get('business_transport')!='native_http':
        raise ValueError('后端核验回执合同不符')
    report={**base,**result,'elapsed_seconds':round(time.monotonic()-started,3)}
    write(output,report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['case-id','tab-id','expected-userid']:p.add_argument('--'+key,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=check(a.case_id,a.tab_id,a.expected_userid,a.output)
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
