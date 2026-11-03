"""Versioned distribution contracts for offline readers and successor operators.

Public material bundles are allowlisted projections. Operator handoffs preserve the
complete journal, use relative original paths, and carry only the review runtime.
Neither format transfers a running session or submits anything to finance.
"""
import copy
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from material_review_model import asset_path, canonical, digest, load, now, require, sha, validate
from material_review_store import events, state

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCHEMA = 'ustc-material-review-package-v1'
PUBLIC_PROGRESS = {
    'replied': '已收到答复，等待经办复核；无需重复答复原问题。',
    'waiting_materials': '已登记后续补件安排，等待真实材料；已交内容无需重复提交。',
    'before_submit': '已转为提交前待办；请经办核对剩余条件，勿重复索取已采用的答复。',
    'hold': '此事项暂缓，待有新的处理安排后继续。',
}


def safe_name(value):
    return re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(value)).strip(' .')[:65] or '未命名'


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    # Spreadsheet software must not interpret material labels as formulas.
    def cell(value):
        value = str(value if value is not None else '')
        return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    csv.writer(stream).writerows([[cell(v) for v in row] for row in rows])
    return stream.getvalue().encode('utf-8-sig')


class Package:
    """Write once into a temporary archive; publish only after every source passes."""
    def __init__(self, workspace, mode, version, revision, selected):
        self.directory = Path(workspace)/'exports'
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.package-', suffix='.zip', dir=self.directory)
        os.close(fd)
        self.temp = Path(name)
        self.zip = zipfile.ZipFile(self.temp, 'w', zipfile.ZIP_DEFLATED)
        self.manifest = {'schema': PACKAGE_SCHEMA, 'mode': mode, 'source_version': version,
                         'event_revision': revision, 'created_at': now(), 'item_ids': selected,
                         'files': [], 'omitted': [], 'messages_sent': False, 'system_write': False}
        self.names = set()

    def add(self, name, payload, **metadata):
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and '\\' not in name,
                '导出成员路径无效')
        require(name not in self.names, '导出成员重名')
        self.names.add(name)
        info = zipfile.ZipInfo(name)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = ((0o100755 if name.endswith('.command') else 0o100644) << 16)
        self.zip.writestr(info, payload)
        self.manifest['files'].append({'name': name, 'sha256': hashlib.sha256(payload).hexdigest(),
                                       'bytes': len(payload), **metadata})

    def finish(self):
        self.zip.writestr('manifest.json', canonical(self.manifest))
        self.zip.close()
        identity = sha(self.temp)
        label = '材料浏览包' if self.manifest['mode'] == 'materials' else '经办接续包'
        output = self.directory/f'{label}-{identity[:20]}.zip'
        os.replace(self.temp, output)
        return output, self.manifest

    def close(self):
        self.zip.close()
        self.temp.unlink(missing_ok=True)


def select_issues(data, current, selected, issue_ids=None):
    """Default to actionable public issues; explicit selections fail closed."""
    allowed = {q['id'] for q in data['issues'].values()
               if q['item_id'] in selected and q['sendable']
               and current['issues'][q['id']]['status'] != 'reviewed'}
    if issue_ids is None:
        return {qid for qid in allowed if current['issues'][qid]['status'] == 'pending'}
    require(isinstance(issue_ids, (list, tuple)) and issue_ids and
            all(isinstance(q, str) and q in allowed for q in issue_ids),
            '所选问题已复核、不可对外反馈或不在事项范围内；请刷新后重新选择')
    return set(issue_ids)


def _public_data(data, current, selected, available, issue_ids=None):
    """No internal notes, payee allocations, finance records or host source paths."""
    out = {'schema': data['schema'], 'title': data['title']+' · 材料浏览包',
           'batch_id': data['batch_id'], 'roots': [], 'sources': {}, 'system_snapshot_at': None,
           'items': {}, 'assets': {}, 'expenses': {}, 'issues': {}, 'system_write': False}
    chosen = select_issues(data, current, selected, issue_ids)
    for iid in selected:
        source = data['items'][iid]
        item = {k: copy.deepcopy(source.get(k)) for k in
                ('id', 'title', 'club', 'face_total', 'paper_total', 'material_type')}
        item.update(asset_ids=[a for a in available if iid in available[a]['item_ids']],
                    paper_ids=[a for a in source.get('paper_ids', []) if a in available],
                    paper_relations=[copy.deepcopy(r) for r in source.get('paper_relations',[]) if r['asset_id'] in available],
                    expense_ids=[], issue_ids=[], history=[], prior_reply=None,
                    queue='材料核对', conclusion='按清单查看材料与待补说明', claim_total=None,
                    bill_number='', finance_status='此包不含财务办理记录', finance_observed_at='未提供')
        out['items'][iid] = item
        for eid in source['expense_ids']:
            original = data['expenses'][eid]
            expense = {k: copy.deepcopy(original[k]) for k in
                       ('id', 'item_id', 'label', 'face_amount', 'display_amount', 'invoice_numbers', 'association', 'review_note', 'reply_needed', 'candidate_amount') if k in original}
            expense.setdefault('label', eid)
            expense.setdefault('invoice_numbers', [])
            expense.setdefault('association', '原核对清单')
            expense.update(payments=[], refs=[r for r in original.get('refs', []) if r['asset_id'] in available])
            out['expenses'][eid] = expense
            item['expense_ids'].append(eid)
        for qid in source['issue_ids']:
            original = data['issues'][qid]
            if qid not in chosen:
                continue
            issue = {k: copy.deepcopy(original.get(k)) for k in
                     ('id', 'item_id', 'title', 'request', 'known', 'requirements', 'location_note', 'tone')}
            issue.update(queue='待补说明', owner='请按问题编号答复', sendable=True, refs=[],
                         targets=[r for r in original['targets']+current['issues'][qid]['links'] if r['asset_id'] in available])
            if original.get('feedback'):
                text = original['feedback']
                issue['feedback'] = {k:copy.deepcopy(text[k]) for k in
                                     ('project','request','context','response') if k in text}
                if 'evidence' in text:
                    issue['feedback']['evidence'] = [copy.deepcopy(r) for r in text['evidence'] if r['asset_id'] in available]
                issue.update(title=text['request'], known=text['context'],
                             request=text.get('response') or text['request'])
            out['issues'][qid] = issue
            item['issue_ids'].append(qid)
    out['assets'] = available
    out['counts'] = {name: len(out[name]) for name in ('items', 'assets', 'expenses', 'issues')}
    empty = {'status': 'pending', 'links': [], 'events': [], 'stale_events': []}
    records = {'revision': current['revision'], 'attachments': {}, 'history': [],
               'issues': {key: copy.deepcopy(empty) for key in list(out['issues'])+['item:'+i for i in selected]}}
    # Preserve progress without publishing private replies or operator notes.
    for qid, issue in out['issues'].items():
        status = current['issues'][qid]['status']
        records['issues'][qid]['status'] = status
        if status in PUBLIC_PROGRESS:
            records['issues'][qid]['next_action'] = PUBLIC_PROGRESS[status]
            if issue.get('feedback'):
                issue['feedback'].update(request=PUBLIC_PROGRESS[status], response='')
            issue['known'] = ('原始问题记录（请结合当前进度查看）：' +
                              (issue.get('known') or ''))
            issue['request'] = PUBLIC_PROGRESS[status] + '\n原始缺项：' + issue['request']
    return out, records


def _lists(package, data, records=None):
    package.add('清单/事项清单.csv', csv_bytes([
        ['事项编号', '事项', '社团', '票面合计', '纸面登记', '问题编号', '材料份数']]+[
        [i['id'], i['title'], i.get('club'), i.get('face_total'), i.get('paper_total'),
         '；'.join(i['issue_ids']), len(i['asset_ids'])] for i in data['items'].values()]))
    package.add('清单/材料目录.csv', csv_bytes([
        ['SHA256', '原文件名', '类别', '页数', '事项编号', '包内文件']]+[
        [a['sha256'], a['label'], a['kind'], a['pages'], '；'.join(a['item_ids']), a['path']]
        for a in data['assets'].values()]))
    package.add('清单/问题清单.csv', csv_bytes([
        ['问题编号', '事项编号', '问题', '具体所需答复', '完成条件', '对应材料SHA及页码', '材料处理状态']]+[
        [q['id'], q['item_id'], q['title'], q['request'], '；'.join(q['requirements']),
         '；'.join(r['asset_id']+':'+str(r['page']) for r in q['targets']),
         records['issues'][q['id']]['status'] if records else '待补说明'] for q in data['issues'].values()]))
    if records:
        package.add('清单/处理记录.csv', csv_bytes([
            ['记录版本', '问题或事项编号', '动作', '记录人', '记录时间', '原文', '补件SHA']]+[
            [e['revision'], e.get('issue_id'), e.get('action'), e['actor'], e['recorded_at'],
             e.get('text'), e.get('attachment', {}).get('sha256')] for e in records['history']]))


def _offline_view(package, workspace, data, version, current, selected, issue_ids=None):
    from material_review_server import preview
    combined = {**data['assets'], **current['attachments']}
    available = {}
    related = {}
    for iid in selected:
        for r in data['items'][iid].get('paper_relations',[]):
            related.setdefault(r['asset_id'],set()).add(iid)
    for aid, asset in combined.items():
        if not set(asset['item_ids']) & set(selected) and aid not in related:
            continue
        if (not set(asset['item_ids']) <= set(selected) and aid not in related) or asset['kind'] == '正式打印件':
            package.manifest['omitted'].append({'asset_id': aid, 'label': asset['label'],
                'reason': '含范围外共享材料或正式财务打印件，未自动放入对外浏览包'})
            continue
        path = asset_path(data, asset, workspace)
        name = f"材料/{safe_name(asset['kind'])}/{aid[:16]}_{safe_name(Path(asset['label']).stem)}{asset['suffix']}"
        package.add(name, path.read_bytes(), original_sha256=aid, role='original')
        available[aid] = {k: copy.deepcopy(asset[k]) for k in
                          ('id', 'sha256', 'label', 'suffix', 'kind', 'pages', 'item_ids', 'invoice_numbers')}
        available[aid].update(path=name, origins=[])
        if 'rotation' in asset: available[aid]['rotation'] = asset['rotation']
        if aid in related:
            # 浏览关联不改变费用所有权；原来源事项单独保留，不携带其余事项和内部记录。
            available[aid]['source_item_ids'] = asset['item_ids'][:]
            available[aid]['item_ids'] = sorted((set(asset['item_ids']) & set(selected)) | related[aid])
    public, records = _public_data(data, current, selected, available, issue_ids)
    previews = {}
    targets = [r for q in public['issues'].values()
               for r in q['targets']+q.get('feedback',{}).get('evidence',[]) if r.get('box')]
    for aid, asset in available.items():
        source = combined[aid]
        if source['suffix'] not in {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}:
            continue
        for page in range(1, asset['pages']+1):
            name = f'预览/{aid}-p{page}.jpg'
            package.add(name, preview(data, source, workspace, page), original_sha256=aid, page=page, role='preview')
            previews[f'{aid}:{page}'] = name
        for ref in targets:
            if ref['asset_id'] != aid:
                continue
            key = f"{aid}:{ref['page']}:"+','.join(str(int(v)) if float(v).is_integer() else str(v) for v in ref['box'])
            name = f'预览/{digest(ref)}.jpg'
            if key not in previews:
                package.add(name, preview(data, source, workspace, ref['page'], ref['box']),
                            original_sha256=aid, page=ref['page'], role='crop')
                previews[key] = name
                marked_key = f"{aid}:{ref['page']}:marked:"+','.join(str(int(v)) if float(v).is_integer() else str(v) for v in ref['box'])
                marked_name = f'预览/{digest(ref)}-marked.jpg'
                package.add(marked_name, preview(data, source, workspace, ref['page'], highlight=ref['box']),
                            original_sha256=aid, page=ref['page'], role='marked_preview')
                previews[marked_key] = marked_name
    from material_review_service import feedback
    packet = {'data': public, 'state': records, 'version': version, 'previews': previews,
              'feedback': feedback(public, records),
              'created_at': package.manifest['created_at'], 'omitted': package.manifest['omitted']}
    ui = ROOT/'tools'/'material_review_ui'
    index = (ui/'index.html').read_text().replace('href="/style.css"', 'href="style.css"')
    index = index.replace('<script src="/app.js">', '<script src="data.js"></script><script src="app.js">')
    package.add('index.html', index.encode())
    package.add('data.js', b'window.REVIEW_EXPORT='+canonical(packet)+b';')
    for name in ('app.js', 'style.css'):
        package.add(name, (ui/name).read_bytes())
    _lists(package, public, records)
    package.add('问题反馈.csv', csv_bytes([
        ['问题编号', '事项编号', '社团', '活动', '具体问题', '补充说明（请填写）', '新增文件名（请填写）']]+[
        [q['id'], q['item_id'], public['items'][q['item_id']]['club'],
         public['items'][q['item_id']]['title'], q['request'], '', ''] for q in public['issues'].values()]))
    package.manifest['issue_ids'] = list(public['issues'])
    package.manifest['counts'] = public['counts']
    package.add('使用说明.txt', (
        '解压完整文件夹，双击index.html。无需安装、联网或运行原工作台。\n'
        '保留事项检索、明白纸/费用/材料跳转、问题对照、翻页、放大和原件下载。\n'
        '此包是只读快照，不能保存补件；按问题编号回复，并将新文件交经办人登记，或使用经办接续包。\n'
        '材料按原类别保存，预览是派生图片，材料目录中的文件保留原字节；打印要求另走正式附件工具。\n'
        '清单目录有事项、材料、问题CSV；manifest.json记录文件SHA和未包含材料。\n'
        '范围只说明系统登记归属，原件自身可能包含个人信息；发送前查看实际文件。此工具没有发送。\n'
        '可用Python3运行“校验包.py”检查包内文件是否完整。\n').encode())


def _handoff(package, workspace, data, version, current):
    """Full owner transfer, not a selective journal merge or a public recipient pack."""
    portable = copy.deepcopy(data)
    portable.update(storage='portable', roots=[], sources={},
                    handoff={'source_version': version, 'event_revision': current['revision']})
    portable['source_identities'] = {k: {'sha256': v['sha256']} for k, v in data['sources'].items()}
    for aid, asset in portable['assets'].items():
        path = asset_path(data, data['assets'][aid], workspace)
        asset['path'] = f"originals/{safe_name(asset['kind'])}/{aid}{asset['suffix']}"
        asset['origins'] = []
        package.add('workspace/'+asset['path'], path.read_bytes(), original_sha256=aid, role='original')
    # History is retained byte-for-byte, including its original request and version identities.
    for event in current['history']:
        name = f"events/{event['revision']:08d}.json"
        package.add('workspace/'+name, (Path(workspace)/name).read_bytes(), role='event')
    attachment_paths = {c['attachment']['path']: c['attachment'] for e in current['history']
                        for c in e.get('changes', [e]) if c.get('attachment')}
    for relative, asset in attachment_paths.items():
        path = asset_path(data, asset, workspace)
        from material_review_model import attachment_relative_path
        package.add('workspace/'+attachment_relative_path(relative).as_posix(), path.read_bytes(), original_sha256=asset['sha256'], role='attachment')
    validate(portable)
    portable_version = digest(portable)
    package.add(f'workspace/versions/{portable_version}.json', canonical(portable))
    package.add('workspace/current.json', canonical({'version': portable_version}))
    historical_versions = {e['version'] for e in current['history']} | {version}
    for old_version in historical_versions - {portable_version}:
        require(re.fullmatch('[a-f0-9]{64}', old_version), '事件来源版本无效')
        path = Path(workspace)/'versions'/f'{old_version}.json'
        require(path.is_file(), '历史事件对应的快照缺失')
        raw = path.read_bytes()
        require(digest(json.loads(raw)) == old_version, '历史快照损坏')
        package.add(f'workspace/versions/{old_version}.json', raw, role='historical-snapshot')
    modules = ['model', 'store', 'server', 'export', 'package', 'pdf', 'launch', 'cli', 'workflow', 'service']
    for module in modules:
        name = f'material_review_{module}.py'
        package.add('tools/'+name, (ROOT/'tools'/name).read_bytes(), role='runtime')
    package.add('tools/platform_runtime.py', (ROOT/'tools/platform_runtime.py').read_bytes(), role='runtime')
    for path in sorted((ROOT/'tools'/'material_review_ui').iterdir()):
        if path.is_file():
            package.add('tools/material_review_ui/'+path.name, path.read_bytes(), role='runtime')
    for name in ('review.py', '打开工作台.command', '打开工作台.cmd', '使用说明.txt', '校验包.py'):
        package.add(name, (ROOT/'tools'/'material_review_distribution'/name).read_bytes(), role='runtime')
    package.add('requirements-materials.lock.txt', (ROOT/'requirements-materials.lock.txt').read_bytes())
    # Keep the distributor reusable when the successor exports another handoff.
    for path in sorted((ROOT/'tools'/'material_review_distribution').iterdir()):
        if path.is_file():
            package.add('tools/material_review_distribution/'+path.name, path.read_bytes(), role='runtime')
    listing = copy.deepcopy(portable)
    for aid, asset in current['attachments'].items():
        if aid not in listing['assets']:
            listing['assets'][aid] = copy.deepcopy(asset)
        listing['assets'][aid]['item_ids'] = asset['item_ids']
        for iid in asset['item_ids']:
            if aid not in listing['items'][iid]['asset_ids']:
                listing['items'][iid]['asset_ids'].append(aid)
    for qid, issue in listing['issues'].items():
        issue['targets'] += current['issues'][qid]['links']
    for asset in listing['assets'].values():
        asset['path'] = 'workspace/'+asset['path']
    _lists(package, listing, current)
    package.manifest.update(portable_version=portable_version,
                            counts={**data['counts'], 'all_assets':len(listing['assets']),
                                    'supplement_assets':len(current['attachments'])},
                            scope='完整案件经办接续；含内部记录，仅交指定接手人；旧副本停止写入')


def export_bundle(workspace, item_ids=None, mode='materials', issue_ids=None, expected_snapshot=None):
    require(mode in ('materials', 'handoff'), '导出类型无效')
    data, version = load(workspace)
    validate(data)
    current = state(workspace, data)
    require(expected_snapshot is None or expected_snapshot == (version, current['revision']),
            '筛选后材料或答复已更新，请刷新后重新选择')
    selected = sorted(set(item_ids or []), key=lambda s: (len(s), s))
    if mode == 'handoff':
        require(not selected and issue_ids is None, '经办接续包含完整事件链，不支持局部事项或问题选择')
        selected = list(data['items'])
    require(selected and all(i in data['items'] for i in selected), '必须明确有效的材料导出范围')
    package = Package(workspace, mode, version, current['revision'], selected)
    try:
        if mode == 'materials':
            _offline_view(package, workspace, data, version, current, selected, issue_ids)
            package.add('校验包.py', (ROOT/'tools'/'material_review_distribution'/'校验包.py').read_bytes())
        else:
            _handoff(package, workspace, data, version, current)
        # A package is a frozen revision; never quietly mix in concurrent changes.
        require(load(workspace)[1] == version and len(events(workspace)) == current['revision'],
                '导出期间材料或反馈更新，请重试以生成一致快照')
        return package.finish()
    finally:
        package.close()
