#!/usr/bin/env python3
"""公开Git检出中同步派生入口；只处理分发清单声明的别名，不处理任何用户资料。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_distribution import ALIASES, collect_sources


def prepare(root, *, execute=False):
    root = Path(root).resolve()
    # 私有开发工作树的入口有独立职责，禁止把公开说明覆盖到那里。
    marker = root / 'data/distribution.json'
    if not marker.is_file() or not (root / '.git').exists():
        raise ValueError('仅允许在公开版Git检出中运行；私有工作树请使用build_distribution.py')
    members = collect_sources(root)
    changed = []
    for target in ALIASES:
        path = root / target
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)):
            raise ValueError('公开入口不可是链接：' + target)
        if not path.exists() or path.read_bytes() != members[target]:
            changed.append(target)
    if execute:
        for target in changed:
            path = root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(members[target])
    return {'ok': True, 'execute': execute, 'changed': changed, 'private_data_touched': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1], execute=args.execute), ensure_ascii=False, indent=2))
