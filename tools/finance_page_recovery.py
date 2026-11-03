"""公开access唤醒的原生页生命周期；只恢复原文档，不重复业务。"""
import hashlib
import json
import re
from pathlib import Path


def recover_native_page(case_id, request_id, tab_id, output):
    from finance_access import write
    from finance_session import SESSIONS, ENTRY
    from finance_backend import mac_target
    from mac_edge_bridge import inventory, frozen_relocation_options, relocate_finance_tab, wake_finance_tab
    from operator_support import stamp
    if (not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',request_id) or
            not re.fullmatch(r'[0-9]+',tab_id)):
        raise ValueError('恢复原页须有合法案件、稳定请求和标签ID')
    output=Path(output)
    if output.exists():raise ValueError('保留原恢复回执，不覆盖或重新派发')
    cache=SESSIONS/case_id/('wake-native-'+hashlib.sha256(request_id.encode()).hexdigest()+'.json')
    if cache.exists():
        prior=json.loads(cache.read_text())
        if any(prior.get(k)!=v for k,v in {'case_id':case_id,'request_id':request_id,'tab_id':tab_id}.items()):
            raise ValueError('原恢复意图身份不符')
        value={**prior,'reused_original_intent':True}
        write(output,value);return value
    binding=mac_target();before=inventory()
    if before['tab_id_namespace']!=binding['tab_id_namespace']:raise ValueError('连接身份变化，未恢复')
    hits=[t for t in before['tabs'] if t['tab_id']==tab_id and t['window_id']==binding['expected_window_id'] and t['path']==ENTRY]
    if len(hits)!=1:raise ValueError('原财务页不在指定窗口；先盘点，不新开或重放')
    page=hits[0]
    record=dict(schema='ustc-native-page-recovery-v1',case_id=case_id,request_id=request_id,tab_id=tab_id,
        before=before,window_id=page['window_id'],tab_id_namespace=before['tab_id_namespace'],
        started_at_utc=stamp(),ok=False,state='intent_recorded',system_write=False,
        business_replayed=False,navigation_requested=False,foreground_requested=False)
    write(cache,record)
    if page.get('discarded') is True:
        record.update(state='document_discarded',next='原文档已被浏览器丢弃；保留未知业务，按原单独立核对，不能重放保存')
    elif page.get('frozen') is False:
        record.update(ok=True,state='already_awake',page_mutation=False,next='原页可调度；独立核对账号、后端和原单，不代表业务已保存')
    elif page.get('frozen') is not True:
        record.update(state='freeze_state_unknown',next='浏览器未提供明确冻结状态；保留原页并检查桥接能力')
    else:
        try:
            options=frozen_relocation_options(before,tab_id,page['window_id'])
            record['selection_binding']=options
            record['state']='recovery_dispatched';write(cache,record)
            if page.get('active') is True:
                try:
                    recovered=wake_finance_tab(tab_id,tab_id_namespace=before['tab_id_namespace'],expected_window_id=page['window_id'])
                except (OSError,ValueError,RuntimeError,TimeoutError) as wake_error:
                    # Explicit completed wake failure is different from an unknown
                    # transport outcome. Relocate only after a fresh same-tab check;
                    # the native bridge additionally requires an isolated window.
                    if not str(wake_error).endswith('frozen_tab_not_woken'):raise
                    fresh=inventory();hits=[t for t in fresh['tabs'] if t['tab_id']==tab_id and t['window_id']==page['window_id'] and t['path']==ENTRY]
                    if fresh['tab_id_namespace']!=before['tab_id_namespace'] or len(hits)!=1 or hits[0].get('active')is not True or hits[0].get('frozen')is not True or hits[0].get('discarded')is True:raise ValueError('唤醒失败后原页状态变化，不迁移')
                    record.update(state='isolated_relocation_dispatched',completed_wake_failure=str(wake_error),fallback_inventory=fresh);write(cache,record)
                    recovered=relocate_finance_tab(tab_id,tab_id_namespace=before['tab_id_namespace'],expected_window_id=page['window_id'])
            else:
                recovered=relocate_finance_tab(tab_id,tab_id_namespace=before['tab_id_namespace'],expected_window_id=page['window_id'],**options)
            record.update(result=recovered,window_id=recovered['window_id'])
            # 窗口变化先落盘；后续观察失败也不能回到旧窗口重做迁移。
            write(cache,record)
            after=inventory();record['after']=after
            matches=[t for t in after['tabs'] if t['tab_id']==tab_id and t['window_id']==record['window_id'] and t['path']==ENTRY]
            record['ok']=bool(after['tab_id_namespace']==before['tab_id_namespace'] and len(matches)==1 and
                              matches[0].get('frozen') is False and matches[0].get('discarded') is not True)
            record['state']='original_page_recovered' if record['ok'] else 'original_page_still_unavailable'
            record['next']='沿返回窗口读回原任务及原单；恢复页面不等于保存成功'
        except (OSError,ValueError,RuntimeError,TimeoutError) as error:
            record.update(state='recovery_unknown',error=str(error)[:500],
                          next='保留原意图，只盘点同一tab及读取桥回执；不再次迁移、不重放业务')
    write(cache,record);write(output,record);return record
