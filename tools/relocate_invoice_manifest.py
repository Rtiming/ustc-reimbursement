#!/usr/bin/env python3
"""换电脑后重新绑定素材根目录；只生成路径参数，不搬移或修改原件。"""
import argparse
import hashlib
import json
from pathlib import Path


def relocate(manifest, old_root, new_root):
    old_root, new_root = Path(old_root), Path(new_root).resolve()
    updated = json.loads(json.dumps(manifest))
    for item in updated['original_files']:
        try:
            relative = Path(item['path']).relative_to(old_root)
        except ValueError as exc:
            raise ValueError('原件不在指定旧根目录：' + item['path']) from exc
        target = new_root / relative
        if not target.is_file():
            raise ValueError('新设备缺原件：' + str(target))
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('新设备原件SHA不一致：' + str(target))
        item['path'] = str(target)
    updated['source_root'] = str(new_root)
    return updated


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--old-root', type=Path, required=True)
    ap.add_argument('--new-root', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    m = relocate(json.loads(args.manifest.read_text(encoding='utf-8')), args.old_root, args.new_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(m, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'files_verified': len(m['original_files']), 'output': str(args.output),
                      'originals_changed': False, 'system_write': False}, ensure_ascii=False))
