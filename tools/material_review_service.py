"""Discover registered local review workspaces through the formal CLI/MCP.

The registry is local routing metadata, never a second material/event store.
Only an explicit absolute workspace under a configured material root is used.
"""
from pathlib import Path

from material_review_model import atomic_json, digest, load, now, read_json, require
from material_review_store import state

ACTIONS = ('list', 'register', 'summary', 'start', 'open', 'status', 'doctor', 'stop')


def authorized(service, workspace):
    require(isinstance(workspace, str) and Path(workspace).is_absolute(), '请明确工作台的绝对路径')
    path = Path(workspace).resolve()
    require(any(path.is_relative_to(Path(root).resolve()) for root in service.material_roots), '工作台须位于已配置的material-root内')
    return path


def feedback(data, current):
    """Issue progress is independent of financial workflow progress."""
    from material_review_package import PUBLIC_PROGRESS
    rows, counts = [], {}
    for issue in data['issues'].values():
        entry = current['issues'][issue['id']]
        status = entry['status']
        counts[status] = counts.get(status, 0) + 1
        item = data['items'][issue['item_id']]
        copy = issue.get('feedback', {}) if status == 'pending' else {}
        next_action = entry.get('next_action') or PUBLIC_PROGRESS.get(status)
        if status == 'reviewed' and not next_action:
            next_action = '材料已复核，无需重复答复。'
        rows.append({'issue_id': issue['id'], 'item_id': item['id'], 'title': copy.get('context') or issue['title'],
                     'item_title': item['title'], 'status': status, 'owner': issue.get('owner', ''),
                     'next_action': next_action or copy.get('response') or copy.get('request') or issue.get('request', ''),
                     'bill_number': item.get('bill_number'), 'system_write': False})
    return {'revision': current['revision'], 'counts': counts, 'rows': rows,
            'open_count': len(rows)-counts.get('reviewed', 0), 'system_write': False}


def run(service, action='list', workspace=None, port=8766):
    require(action in ACTIONS, '不支持的工作台动作')
    require(type(port) is int and 0 <= port <= 65535, '端口须为0至65535的整数')
    registry = service.root/'.local'/'review-workspaces'
    if action == 'list':
        require(workspace is None, 'list不接受workspace，请用summary查看单个工作台')
        rows = []
        for entry in sorted(registry.glob('*.json')):
            info = read_json(entry)
            try:
                path = authorized(service, info['workspace'])
            except ValueError:
                continue  # A narrower material-root must not expose another workspace.
            try:
                data, version = load(path)
                rows.append({'workspace': str(path), 'title': data['title'], 'version': version,
                             'counts': data['counts'], 'available': True})
            except (ValueError, OSError, KeyError) as exc:
                rows.append({'workspace': str(path), 'available': False, 'error': str(exc)})
        return {'ok': True, 'workspaces': rows, 'system_write': False,
                'next_action': '选定workspace后open；未登记的已有工作台先register，不自动重建或移动案件'}
    if action == 'open' and workspace is None:
        rows = run(service, 'list')['workspaces']
        available = [row for row in rows if row['available']]
        if len(available) != 1 or len(rows) != 1:
            return {'ok': False, 'workspaces': rows, 'system_write': False,
                    'next_action': '未登记唯一可用工作台；安装时指定--review-workspace，或从list中选定workspace后open'}
        workspace = available[0]['workspace']
    path = authorized(service, workspace)
    data, version = load(path)
    if action in ('register', 'start', 'open'):
        atomic_json(registry/(digest(str(path))+'.json'), {'workspace': str(path), 'registered_at': now()})
    if action in ('register', 'summary'):
        from material_review_workflow import summary
        current = state(path, data)
        return {'ok': True, 'workspace': str(path), 'title': data['title'], 'version': version,
                'feedback': feedback(data, current), 'workflow': summary(path, data, current), 'system_write': False}
    from material_review_launch import launch, status, doctor, stop
    if action in ('start', 'open'):
        return launch(path, port, action == 'open')
    return {'status': status, 'doctor': doctor, 'stop': stop}[action](path)


def deployment(service, workspaces, *, check_only=False):
    """Explicit existing workspaces only; prevalidate all before registering any."""
    rows = []
    for workspace in workspaces:
        path = authorized(service, workspace)
        data, version = load(path)
        current = state(path, data)
        rows.append({'workspace': str(path), 'title': data['title'], 'version': version,
                     'revision': current['revision']})
    registered = {row['workspace'] for row in run(service, 'list')['workspaces']}
    if check_only:
        require(all(row['workspace'] in registered for row in rows), '工作台尚未登记；请重跑原安装入口')
    else:
        for row in rows:
            registry = service.root/'.local'/'review-workspaces'
            atomic_json(registry/(digest(row['workspace'])+'.json'),
                        {'workspace': row['workspace'], 'registered_at': now()})
    return {'ok': True, 'workspaces': rows, 'system_write': False, 'browser_started': False}
