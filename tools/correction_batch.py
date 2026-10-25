"""一个发票分组的串行修改、独立回读和核验；失败保留pending，不重放。"""
import json
import subprocess
import sys
from pathlib import Path
from correction import script, expected_update, verify_update, verify_group


def apply_group(paths, c, plan, group, tab_id, out):
    from reimburse import begin_write, acknowledge, ROOT
    from operator_support import write_json
    if group not in plan['expected_groups']: raise ValueError('未配置发票分组')
    out.mkdir(exist_ok=False)
    number=0
    def action(name, key='', kind=''):
        nonlocal number
        number+=1
        src=out/(f'{number:02}_{name}.js');receipt=src.with_suffix('.json')
        src.write_text(script(c,plan,name,key,group,kind))
        result=subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(src),'--output',str(receipt),'--tab-id',tab_id,'--quiet'],capture_output=True,text=True)
        if result.returncode:raise ValueError('原生动作未完成，保留原回执先recover/readback：'+str(receipt)+' '+result.stdout[-400:])
        return receipt
    def read(name):
        receipt=out/(name+'.json')
        result=subprocess.run([sys.executable,str(ROOT/'tools/invoice_cli.py'),'read','--manifest',str(paths['invoices']),'--output',str(receipt),'--tab-id',tab_id],capture_output=True,text=True)
        if result.returncode:raise ValueError('独立回读未完成，不继续写入：'+str(receipt))
        return receipt,json.loads(receipt.read_text())
    policy=plan['policy'];wc_key='!P!'.join([policy['subject'],policy['project'],policy['budget']])
    others=[f['code'] for f in plan['funding'] if f['code']!=policy['project']]
    if len(others)!=1:raise ValueError('本适配器需要唯一的其他经费项目')
    subject=plan['remainder_subjects'][group]
    ww_key='!P!'.join([subject,others[0],policy['budget']])
    action('open')
    # 先清理旧团委分担，原生控件才能接受新的其他经费余额。
    stages=[(wc_key,'clear'),(ww_key,'life-ww' if group=='生产生活服务' else 'ww'),(wc_key,'wc')]
    for index,(key,kind) in enumerate(stages):
        action('select',key)
        _,before=read(f'{index}_before')
        expected=expected_update(before,plan,group,key,kind)
        try:
            verify_update(before,plan,key,expected)
            write_json(out/(f'{index}_already_matches.json'), {'ok':True,'action_replayed':False,'reason':'当前完整绑定已符合本步骤'})
            continue
        except ValueError:
            pass
        begin_write(paths,c,'correction:update:'+group,out)
        action('update',key,kind)
        receipt,after=read(f'{index}_after')
        verify_update(after,plan,key,expected)
        acknowledge(paths,receipt,'独立逐票比较目标经费全部明细金额及其他绑定不变：'+group+'/'+kind,'saved')
    receipt,closed=read('closed_before_save')
    verified=verify_group(closed,plan,group);write_json(out/'verified_before_save.json',verified)
    begin_write(paths,c,'correction:save:'+group,out)
    action('save')
    receipt,saved=read('after_save')
    previews=[f for f in saved['result'].get('frames',[]) if ('报销单号：'+c['serial']) in f.get('visibleText','')]
    if not previews or any(g.get('visible') for g in saved['result'].get('grids',[]) if g['id'] in ['gridWF_YBX_305','gridWF_YBX_1669']) or '确认保存拆分结果？' in saved['result'].get('visibleText',''):
        raise ValueError('保存后同单号预览未恢复，保留pending回查')
    acknowledge(paths,receipt,'逐票闭合核验后原生保存，已返回同单号预览；再打开核对持久绑定','saved')
    action('open')
    receipt,persisted=read('persisted_bindings')
    verified=verify_group(persisted,plan,group);verified['readback']=str(receipt.resolve())
    write_json(out/'verified_saved.json',verified)
    action('return')
    return verified
