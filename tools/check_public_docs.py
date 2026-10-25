#!/usr/bin/env python3
"""核对分发中的双语topic、相对链接和安全请求示例；不联网、不修改配置。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit

from assistant_contract import DOCS, DOCS_EN, GUIDE_LANGUAGES, JSON_METHODS, CAPABILITY_INTENTS
from build_distribution import collect_sources


def check_members(members):
    issues = []
    if set(DOCS) != set(DOCS_EN):
        issues.append('Chinese and English topic keys differ')
    for language, mapping in (('zh', DOCS), ('en', DOCS_EN)):
        for topic, name in mapping.items():
            if name not in members or len(members[name]) < 40:
                issues.append(f'{language}/{topic}: missing or empty {name}')
    checked = 0
    for name, raw in members.items():
        if not name.endswith('.md') or name.startswith('distribution/'):
            continue  # distribution内是按ALIASES投影的编辑正本，不以其目录解释发行链接。
        text = re.sub(r'```[^\n]*\n.*?```', '', raw.decode('utf-8'), flags=re.S)
        for link in re.findall(r'\[[^\]]*\]\(([^)\s]+)\)', text):
            url = urlsplit(link.strip('<>'))
            if url.scheme in ('https', 'http', 'mailto') or not url.path:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path)))
            checked += 1
            if url.scheme or target not in members:
                issues.append(f'{name}: unresolved local link {link}')
    for name, raw in members.items():
        if not name.startswith('examples/requests/') or not name.endswith('.json'):
            continue
        try:
            value = json.loads(raw)
            if set(value) - {'method', 'params'} or value.get('method') not in JSON_METHODS:
                raise ValueError('invalid envelope/method')
            if value['method'] not in {'capabilities', 'guide'}:
                raise ValueError('runnable examples must be offline discovery/guidance')
            params = value.get('params', {})
            if not isinstance(params, dict) or params.get('execute') is not None:
                raise ValueError('example is not read-only guidance')
            if value['method'] == 'guide' and (params.get('topic', 'start') not in DOCS or params.get('language', 'zh') not in GUIDE_LANGUAGES):
                raise ValueError('unknown topic/language')
            if value['method'] == 'guide' and set(params) - {'topic', 'language'}:
                raise ValueError('unknown guide parameter')
            if value['method'] == 'capabilities' and (set(params) - {'intent'} or params.get('intent', 'materials') not in CAPABILITY_INTENTS):
                raise ValueError('unknown capability parameter')
        except (ValueError, TypeError, KeyError) as error:
            issues.append(name + ': ' + str(error))
    return {'ok': not issues, 'languages': list(GUIDE_LANGUAGES), 'topics_per_language': len(DOCS),
            'local_links_checked': checked, 'issues': issues, 'filesystem_write': False, 'network_access': False}


def check(root):
    return check_members(collect_sources(root))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = check(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
