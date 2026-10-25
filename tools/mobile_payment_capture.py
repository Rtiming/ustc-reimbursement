"""手机订单/付款凭证的具名采集入口，复用已绑定的android-control客户端。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time

PACKAGES = {'xianyu':'com.taobao.idlefish','alipay':'com.eg.android.AlipayGphone','wechat':'com.tencent.mm'}
MUTATIONS = {'click','tap','text','scroll','swipe','search','back','open'}
BLOCKED = re.compile(r'^(立即付款|确认付款|确认收货|申请退款|再次购买|去评价|发送|转账|删除|免费领)$')


def clean(text):
    return re.sub('[\u200b-\u200f\ufeff\ufffc]','',text or '').strip()


def select_node(snapshot, action, selector, occurrence=None):
    nodes=snapshot['nodes'];kind,value=selector
    choices=[n for n in nodes if n.get('visible') and n.get('enabled') and clean(n.get(kind,''))==clean(value)]
    if action=='text': choices=[n for n in choices if n.get('editable')]
    if action=='scroll': choices=[n for n in choices if n.get('scrollable')]
    if action in ('click','tap'):
        if BLOCKED.fullmatch(clean(value)):raise ValueError('当前凭证采集工具不执行该业务动作')
        by_id={n['id']:n for n in nodes};found=[]
        for label in choices:
            node=label
            for _ in range(8):
                if node.get('clickable'):break
                node=by_id.get(node.get('parent'))
                if node is None:break
            if node and node.get('clickable') and node.get('visible'):found.append(node);continue
            b=label['bounds']
            enclosing=[n for n in nodes if n.get('visible') and n.get('enabled') and n.get('clickable') and n['bounds'][0]<=b[0] and n['bounds'][1]<=b[1] and n['bounds'][2]>=b[2] and n['bounds'][3]>=b[3]]
            enclosing.sort(key=lambda n:(n['bounds'][2]-n['bounds'][0])*(n['bounds'][3]-n['bounds'][1]))
            if enclosing:
                area=lambda n:(n['bounds'][2]-n['bounds'][0])*(n['bounds'][3]-n['bounds'][1])
                if len(enclosing)>1 and area(enclosing[0])==area(enclosing[1]):
                    raise ValueError('相同大小的可点击容器有歧义')
                found.append(enclosing[0])
        choices=list({n['id']:n for n in found}.values())
    if occurrence is not None:
        choices.sort(key=lambda n:(n['bounds'][1],n['bounds'][0]))
        if not 0<=occurrence<len(choices):raise ValueError('occurrence out of range')
        choices=choices[occurrence:occurrence+1]
    if len(choices)!=1:raise ValueError(f'匹配到{len(choices)}个控件，保留现场，不猜位置')
    return choices[0]


def extract_bill(chunks):
    """只从已观察的支付宝详情标签取值，不用列表日期或网站回调日期替代付款时间。"""
    def following(label):
        try:return chunks[chunks.index(label)+1]
        except (ValueError,IndexError):return ''
    amounts=[re.fullmatch(r'支出([\d,]+(?:\.\d{1,2})?)元',x) for x in chunks]
    amounts=[m.group(1).replace(',','') for m in amounts if m]
    if '支付时间' not in chunks or len(set(amounts))!=1:return None
    reference=following('订单号')
    merchant=following('商家订单号')
    return dict(amount=amounts[0],paid_at=following('支付时间'),
                transaction_ref=reference if re.fullmatch(r'\d{15,32}',reference) else '',
                merchant_order_ref=merchant if re.fullmatch(r'[A-Za-z0-9_-]{10,100}',merchant) else '',
                merchant_order_display=merchant,product=following('商品说明'),
                payee=chunks[0],method=following('付款方式'),
                status='交易成功' if '交易成功' in chunks else '交易关闭' if '交易关闭' in chunks else '待核对',
                refund_status='未在本页独立核验',date_source='支付宝详情的支付时间')


def extract_fields(snapshot):
    chunks=list(dict.fromkeys(clean(n.get('text') or n.get('description')) for n in snapshot['nodes'] if n.get('visible') and (n.get('text') or n.get('description'))))
    text='\n'.join(chunks)
    orders=re.findall(r'订单(?:编号|号)\s*[:：]?\s*(\d{15,32})',text)
    times=re.findall(r'(20\d\d[-年]\d{1,2}[-月]\d{1,2}日?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)',text)
    prices=re.findall(r'[¥￥]\s*([\d,]+(?:\.\d{1,2})?)',text)
    scoped=re.findall(r'成交价\s*[¥￥]\s*([\d,]+(?:\.\d{1,2})?)',text)
    return dict(order_ids=list(dict.fromkeys(orders)),date_candidates=list(dict.fromkeys(times)),amount_candidates=list(dict.fromkeys(prices)),order_amount_candidates=list(dict.fromkeys(scoped)),bill=extract_bill(chunks),visible_text=chunks,classification='source_candidates_not_reimbursement')


class RemoteCapture:
    def __init__(self, output, invoke):
        self.output=output;self.invoke=invoke;self.commands=[]

    def save(self,name,data):
        p=self.output/name
        p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');p.chmod(0o600)

    def command(self,capability,arguments,image_name=None):
        plan=self.invoke(['plan',capability,'--args-file','-'],body=json.dumps(arguments,ensure_ascii=False),device='phone')
        payload=plan.get('result',{})
        if plan.get('exit_code')!=0 or payload.get('status')!='planned':
            self.save('failure.json',plan);raise ValueError('plan未就绪，查看failure.json')
        identity=payload['id'];self.commands.append(dict(id=identity,capability=capability,status='planned'))
        self.save('commands.json',self.commands)
        reply=self.invoke(['apply',identity,'--approve'],device='phone')
        # 先保留原ID，再只查该ID；不在超时后重发或更换意图。
        self.commands[-1]['status']=reply.get('result',{}).get('status','unknown');self.save('commands.json',self.commands)
        start=time.monotonic()
        while True:
            command=['result',identity]
            if image_name:command+=['--image-out',str(self.output/image_name)]
            reply=self.invoke(command,device='phone')
            status=reply.get('result',{}).get('status','unknown')
            self.commands[-1]['status']=status;self.save('commands.json',self.commands)
            if status not in ('queued','dispatched'):break
            if time.monotonic()-start>20:break
            time.sleep(0.15)
        self.save(identity+'.json',reply)
        if reply.get('exit_code')!=0 or status!='executed':
            raise ValueError(f'原任务{identity}为{status}；只查原ID，不重放')
        return reply['result']['result']

    def snapshot(self,package):
        snapshot=self.command('ui.snapshot',{'package':package})
        self.save('snapshot.json',snapshot)
        self.save('fields.json',extract_fields(snapshot))
        return snapshot

    def capture(self,package):
        snapshot=self.snapshot(package)
        shot=self.command('ui.screenshot',{'package':package},'screen.jpg')
        source=self.output/'screen.jpg'
        self.save('capture.json',dict(package=package,image=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),bytes=source.stat().st_size,snapshot=snapshot['snapshot'],remote_image_metadata=shot,financial_system_write=False))
        return extract_fields(snapshot)


def remote_invoke():
    # 复用共享技能对客户端路径、源码SHA、依赖和设备的校验，不复制协议或凭据。
    path=Path.home()/'.ai-skills/android-control/scripts/android_remote.py'
    spec=importlib.util.spec_from_file_location('rtime_phone_capture_adapter',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.invoke


def run(args,invoke=None):
    if args.action in MUTATIONS and not args.execute:
        return dict(preview=True,action=args.action,app=args.app,next='明确执行时加--execute；不创建采集目录')
    args.output.mkdir(parents=True,exist_ok=False);args.output.chmod(0o700)
    capture=RemoteCapture(args.output,invoke or remote_invoke());package=PACKAGES[args.app]
    if args.action=='inspect':return capture.capture(package)
    if args.action in ('open','back'):
        result=capture.command('admin.app_launch' if args.action=='open' else 'ui.back',{'package':package})
    elif args.action=='search':
        if args.app!='alipay' or not args.value.strip():raise ValueError('search仅用于支付宝账单的明确搜索词')
        snapshot=capture.snapshot(package)
        editors=[n for n in snapshot['nodes'] if n.get('visible') and n.get('enabled') and n.get('editable')]
        if len(editors)!=1:raise ValueError('当前搜索框不唯一，先进入账单搜索页')
        if not editors[0].get('focused'):
            capture.command('ui.tap',{'package':package,'snapshot':snapshot['snapshot'],'node':editors[0]['id']})
            snapshot=capture.snapshot(package)
            editors=[n for n in snapshot['nodes'] if n.get('visible') and n.get('enabled') and n.get('editable')]
            if len(editors)!=1:raise ValueError('搜索框已变化')
        capture.command('ui.text',{'package':package,'snapshot':snapshot['snapshot'],'node':editors[0]['id'],'text':args.value})
        snapshot=capture.snapshot(package)
        editors=[n for n in snapshot['nodes'] if n.get('visible') and n.get('enabled') and n.get('editable') and n.get('focused')]
        if len(editors)!=1:raise ValueError('输入焦点已变化，不提交搜索')
        result=capture.command('ui.editor_action',{'package':package,'snapshot':snapshot['snapshot'],'node':editors[0]['id'],'action':'SEARCH'})
    else:
        snapshot=capture.snapshot(package)
        selector=('text',args.text) if args.text is not None else ('description',args.description) if args.description is not None else ('resource',args.resource)
        if selector[1] is None:raise ValueError('需要明确的当前控件文字、描述或资源id')
        node=select_node(snapshot,'scroll' if args.action=='swipe' else args.action,selector,args.occurrence)
        values={'package':package,'snapshot':snapshot['snapshot'],'node':node['id']}
        if args.action=='text':values['text']=args.value
        if args.action=='scroll':values['direction']=args.direction
        if args.action=='swipe':
            if args.direction not in ('up','down','left','right'):raise ValueError('swipe方向需要up/down/left/right')
            values['direction']=args.direction
        result=capture.command('ui.'+args.action,values)
    capture.save('action.json',result)
    return dict(action=args.action,status='executed',output=str(args.output))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','open','back','click','tap','text','scroll','swipe','search'])
    parser.add_argument('--app',choices=PACKAGES,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--text');parser.add_argument('--description');parser.add_argument('--resource')
    parser.add_argument('--value',default='');parser.add_argument('--direction',choices=['forward','backward','up','down','left','right'],default='forward')
    parser.add_argument('--occurrence',type=int);parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    try:
        result=run(args)
    except (ValueError,KeyError,OSError) as error:parser.exit(2,str(error)+'\n')
    # UI正文仅保留私有材料，控制台输出有限候选及可追溯位置。
    print(json.dumps({k:v for k,v in result.items() if k!='visible_text'},ensure_ascii=False))


if __name__=='__main__':main()
