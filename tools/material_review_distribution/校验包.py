"""Verify the initial distribution without dependencies or access to sender paths."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 'ustc-material-review-package-v1':
        raise ValueError('不支持的导出包版本')
    names = set()
    for entry in manifest['files']:
        name = entry['name']
        relative = PurePosixPath(name)
        path = (root/name).resolve()
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or not path.is_relative_to(root) or name in names:
            raise ValueError('成员路径越界或重复: '+name)
        names.add(name)
        if not path.is_file() or path.stat().st_size != entry['bytes']:
            raise ValueError('文件缺失或大小变化: '+name)
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                hasher.update(chunk)
        if hasher.hexdigest() != entry['sha256']:
            raise ValueError('文件SHA变化: '+name)
    return {'ok': True, 'mode': manifest['mode'], 'files': len(names),
            'source_version': manifest['source_version'], 'event_revision': manifest['event_revision']}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(Path(__file__).resolve().parent), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
