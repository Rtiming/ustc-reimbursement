"""Read native Edge dialogs when the finance renderer cannot run JavaScript.

No activation, key, coordinate, press, property write or permission prompt.
Only browser-native dialog text is read; web areas are never traversed.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path

from mac_native_ax import _CoreAX, AXBlocked


def inspect_dialogs(tab_id, window_id, namespace, *, bridge=None, include_finance_web=False):
    if not re.fullmatch(r'[0-9]+',tab_id or '') or not re.fullmatch(r'[0-9]+',window_id or ''):
        raise ValueError('须明确本次财务标签及窗口')
    if bridge is None:
        from mac_edge_bridge import inventory
        bridge=inventory()
    matches=[t for t in bridge.get('tabs',[]) if t.get('tab_id')==tab_id and t.get('window_id')==window_id]
    if (bridge.get('tab_id_namespace')!=namespace or len(matches)!=1
            or matches[0].get('path')!='https://cwzh.ustc.edu.cn/YBX/main.jsp'):
        raise ValueError('只观察本次原生财务主标签所属Edge的原生弹窗')
    result={'schema':'ustc-mac-finance-dialog-observation-v1','tab_id':tab_id,'window_id':window_id,
            'tab_id_namespace':namespace,'system_write':False,'ui_modified':False,'foreground_requested':False,
            'page_script_executed':False,'account_verified':False,'web_areas_read':False,'capture_complete':False,
            'windows':[],'dialogs':[],'omissions':[],'finance_web_text':[]}
    if include_finance_web and sum(t.get('path')=='https://cwzh.ustc.edu.cn/YBX/main.jsp' for t in bridge.get('tabs',[]))!=1:
        raise ValueError('网页诊断要求扩展盘点中只有一个财务主标签，避免读到其他案件窗口')
    p=subprocess.run(['/usr/bin/pgrep','-x','Microsoft Edge'],capture_output=True,text=True,timeout=3)
    ids=p.stdout.split()
    if p.returncode or len(ids)!=1 or not ids[0].isdigit():raise AXBlocked('EDGE_MAIN_PROCESS_NOT_UNIQUE')
    with _CoreAX(budget_seconds=6) as ax:
        app=ax.application(int(ids[0]));count=0
        if ax.pid(app)!=int(ids[0]):raise AXBlocked('EDGE_AX_PID_MISMATCH')
        def walk(element,depth,inside_dialog=False,owner=None,finance_web=False):
            nonlocal count
            count+=1
            if count>300 or depth>14:
                result['omissions'].append({'window_index':owner,'reason':'native_tree_limit'});return
            role=ax.attribute(element,'AXRole');sub=ax.attribute(element,'AXSubrole')
            if role in ('AXTextField','AXSecureTextField','AXTextArea','AXToolbar','AXTabGroup'):return
            if role=='AXWebArea':
                url=ax.attribute(element,'AXURL') or ''
                if not include_finance_web or not re.match(r'^https://cwzh\.ustc\.edu\.cn/YBX/(?:main\.jsp|redirectCustomPage\.action)(?:[?#]|$)',url):return
                finance_web=True;result['web_areas_read']=True
            is_dialog=inside_dialog or role in ('AXSheet','AXDialog','AXAlert') or sub in ('AXDialog','AXSystemDialog') or ax.attribute(element,'AXModal') is True
            if is_dialog:
                entry={'role':role,'subrole':sub,'window_index':owner}
                for key in ('AXTitle','AXDescription','AXValue'):
                    value=ax.attribute(element,key)
                    if isinstance(value,(str,bool,int)) and value!='':entry[key]=value
                result['dialogs'].append(entry)
            if finance_web and role in ('AXStaticText','AXButton','AXHeading'):
                text=ax.attribute(element,'AXValue') or ax.attribute(element,'AXTitle') or ax.attribute(element,'AXDescription')
                if isinstance(text,str) and text.strip():
                    text=re.sub(r'\b[0-9]{12,32}\b',lambda m:m[0][:4]+'****'+m[0][-4:],text)
                    result['finance_web_text'].append({'role':role,'text':text[:600]})
            for child in ax.children(element,'AXChildren',150):walk(child,depth+1,is_dialog,owner,finance_web)
        for i,window in enumerate(ax.children(app,'AXWindows',20)):
            result['windows'].append({'window_index':i,'title':ax.attribute(window,'AXTitle'),'role':ax.attribute(window,'AXRole'),'subrole':ax.attribute(window,'AXSubrole')})
            walk(window,0,False,i)
        result.update(capture_complete=not result['omissions'],node_count=count)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tab-id',required=True);p.add_argument('--window-id',required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--include-finance-web',action='store_true');a=p.parse_args()
    if a.output.exists():raise ValueError('保留旧观察回执，不覆盖')
    try:result=inspect_dialogs(a.tab_id,a.window_id,a.namespace,include_finance_web=a.include_finance_web)
    except (AXBlocked,ValueError,OSError) as error:result={'ok':False,'error':str(error),'ui_modified':False,'capture_complete':False,'system_write':False}
    else:result['ok']=True
    from assistant_api import atomic_json
    atomic_json(a.output,result);print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['ok'] else 2


if __name__=='__main__':raise SystemExit(main())
