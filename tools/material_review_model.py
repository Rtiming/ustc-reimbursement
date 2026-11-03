"""Shared, offline material-review contracts; no finance operations."""
import hashlib
import json
import os
import re
import tempfile
import math
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SCHEMA = 'ustc-material-review-v1'

class ReviewError(ValueError):
    pass


def require(value, message):
    if not value:
        raise ReviewError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.review-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temp, path)  # Atomic publication; never overwrite a competing revision.
        else:
            os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def in_roots(path, roots):
    path = Path(path).resolve()
    require(any(path.is_relative_to(Path(root).resolve()) for root in roots), '文件不在本工作台登记的材料根目录中')
    return path


def reference(value):
    if not isinstance(value, str):
        return None
    path, _, fragment = value.partition('#')
    match = re.search(r'sha256=([a-f0-9]{64})', fragment)
    page = re.search(r'(?:^|&)page=(\d+)', fragment)
    return {'path': path, 'sha256': match.group(1) if match else None,
            'page': int(page.group(1)) if page else 1}


def refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ('source_ref', 'source_refs', '材料来源'):
                for raw in child if isinstance(child, list) else [child]:
                    parsed = reference(raw)
                    if parsed:
                        yield parsed
            elif isinstance(child, (dict, list)):
                yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def load(workspace):
    workspace = Path(workspace).resolve()
    current = read_json(workspace/'current.json')
    require(re.fullmatch(r'[a-f0-9]{64}', current.get('version', '')), '工作台版本号无效')
    data = read_json(workspace/'versions'/f"{current['version']}.json")
    require(data.get('schema') == SCHEMA and digest(data) == current['version'], '工作台快照SHA不匹配')
    return data, current['version']


def asset_path(data, asset, workspace=None):
    if asset.get('attachment'):
        require(workspace is not None, '补件需要工作台目录')
        path = in_roots(Path(workspace)/attachment_relative_path(asset['path']), [Path(workspace)/'attachments'])
    elif data.get('storage') == 'portable':
        require(workspace is not None, '接续包需要工作台目录')
        portable_path(asset['path'])
        path = in_roots(Path(workspace)/asset['path'], [Path(workspace)/'originals'])
    else:
        path = in_roots(asset['path'], data['roots'])
    require(path.is_file(), '原件未找到，请核对材料根或重新构建')
    require(sha(path) == asset['sha256'], '原件SHA变化，拒绝展示过期索引；请核对新版本')
    return path


def attachment_relative_path(value):
    """兼容旧Windows事件的分隔符；保持历史字节，仍只允许补件子目录。"""
    require(isinstance(value, str) and bool(value), '补件相对路径无效')
    path = PurePosixPath(value.replace('\\', '/'))
    require(not path.is_absolute() and ':' not in value and '..' not in path.parts
            and len(path.parts) == 2 and path.parts[0] == 'attachments', '补件路径越界')
    return path


def portable_path(value):
    """Portable snapshots only address originals below their own workspace."""
    require(isinstance(value,str) and bool(value), '接续包材料路径无效')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '\\' not in value and ':' not in value
            and '..' not in path.parts and bool(path.parts) and path.parts[0] == 'originals', '接续包材料路径越界')
    return path


def validate_region(ref):
    if 'box' not in ref:
        return
    box = ref['box']
    require(isinstance(box, (list, tuple)) and len(box) == 4, '局部标注须为四个归一化坐标')
    require(all(type(v) in (int, float) and math.isfinite(v) for v in box), '局部标注坐标须为有限数值')
    x, y, w, h = box
    require(0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1-x+1e-9 and 0 < h <= 1-y+1e-9, '标注区域越界')
    require(isinstance(ref.get('basis'), str) and bool(ref['basis'].strip()), '局部标注必须有核实依据')


PAPER_RELATION_LABELS = {
    'same_scope': '同一费用范围',
    'different_scope': '同活动不同费用，不能替代',
    'unverified': '已收明白纸，费用范围待核',
    'paper_only': '已收明白纸，仍缺费用或领取明细',
}


def paper_contexts(item):
    """收到某张纸不等于纸、票、申报范围已一致；旧数据只回退到待核。"""
    rows = list(item.get('paper_relations', []))
    explicit = {r['asset_id'] for r in rows}
    rows += [dict(asset_id=aid, page=1, source_item_id=item['id'], relation='unverified',
                  statement='已登记原纸；文件归属不代表费用范围已经核清。', basis='原材料登记')
             for aid in item.get('paper_ids', []) if aid not in explicit]
    return rows


def validate(data):
    require(data.get('schema') == SCHEMA, '不支持的工作台格式')
    for name in ('items', 'assets', 'expenses', 'issues'):
        require(isinstance(data.get(name), dict), f'缺少{name}')
    for key, item in data['items'].items():
        require(key == item['id'], '事项ID不一致')
        require(all(x in data['assets'] for x in item['asset_ids']), '事项材料引用不存在')
        require(all(x in data['expenses'] for x in item['expense_ids']), '事项费用引用不存在')
        require(all(x in data['issues'] for x in item['issue_ids']), '事项问题引用不存在')
        for r in item.get('paper_relations', []):
            require(set(r) == {'asset_id','page','source_item_id','relation','statement','basis'}, '明白纸关系字段无效')
            require(r['relation'] in PAPER_RELATION_LABELS, '明白纸关系类型无效')
            require(r['asset_id'] in data['assets'], '关联明白纸不存在')
            asset = data['assets'][r['asset_id']]
            require(asset['kind'] == '明白纸', '关联背景只能指向明白纸')
            require(r['source_item_id'] in asset.get('source_item_ids', asset['item_ids']), '关联明白纸来源事项不符')
            require(type(r['page']) is int and 1 <= r['page'] <= asset['pages'], '关联明白纸页码越界')
            require(all(isinstance(r[k],str) and 0 < len(r[k].strip()) <= 600 for k in ('statement','basis')), '明白纸关系须有说明和依据')
    for key, asset in data['assets'].items():
        require(key == asset['sha256'] and re.fullmatch('[a-f0-9]{64}', key), '材料ID须为源SHA')
        require(type(asset['pages']) is int and asset['pages'] > 0, '材料页数无效')
        require(all(x in data['items'] for x in asset['item_ids']), '材料事项引用不存在')
        if data.get('storage') == 'portable':
            portable_path(asset['path'])
        else:
            in_roots(asset['path'], data['roots'])
    for collection in ('expenses', 'issues'):
        for row in data[collection].values():
            require(row['item_id'] in data['items'], '记录事项不存在')
            if 'review_note' in row:
                require(isinstance(row['review_note'],str) and len(row['review_note']) <= 600, '费用对照说明无效')
            if 'reply_needed' in row: require(type(row['reply_needed']) is bool, '费用答复标记无效')
            if 'candidate_amount' in row:
                require(isinstance(row['candidate_amount'],str) and re.fullmatch(r'\d+\.\d{2}',row['candidate_amount']), '文件名候选金额无效')
            if 'feedback' in row:
                feedback=row['feedback']
                require(isinstance(feedback,dict) and {'issue_fingerprint','project','request','context'}<=set(feedback)<= {'issue_fingerprint','project','request','context','response','evidence'},'反馈短文格式无效')
                require(feedback['issue_fingerprint']==row.get('fingerprint'),'问题已变化，请重新核对反馈短文')
                for key,limit in [('project',120),('request',600),('context',400)]:
                    require(isinstance(feedback[key],str) and len(feedback[key])<=limit,'反馈短文过长或格式错误')
                require(feedback['project'].strip() and feedback['request'].strip(),'反馈项目及所需材料不能为空')
                require(isinstance(feedback.get('response',''),str) and len(feedback.get('response',''))<=600,'反馈回复格式无效')
                require(isinstance(feedback.get('evidence',[]),list),'反馈特写必须为列表')
            for ref in row.get('refs', []) + row.get('targets', []) + row.get('feedback',{}).get('evidence',[]):
                require(ref['asset_id'] in data['assets'], '证据材料不存在')
                asset = data['assets'][ref['asset_id']]
                require(row['item_id'] in asset['item_ids'], '证据跨事项引用')
                require(type(ref['page']) is int and 1 <= ref['page'] <= asset['pages'], '证据页码越界')
                validate_region(ref)
    return True
