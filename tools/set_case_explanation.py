#!/usr/bin/env python3
"""原样导入说明正文到案件配置；只更新本地参数，不操作财务系统。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from validate_config import validate


def import_text(case_path, text_path, replace_card_notes=False, version=None):
    case_path, text_path = Path(case_path).resolve(), Path(text_path).resolve()
    text = text_path.read_text(encoding='utf-8').rstrip('\r\n')
    if not text.strip():
        raise ValueError('说明正文不能为空')
    c = validate(json.loads(case_path.read_text(encoding='utf-8')))
    notes = c.setdefault('explanations', {})
    if notes.get('card_notes') and not replace_card_notes:
        raise ValueError('仍有独立公务卡说明；整份定稿须加 --replace-card-notes，避免重复住宿说明')
    notes.update(general=text, source_file=os.path.relpath(text_path, case_path.parent),
                 source_text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                 source_version=version or text_path.name)
    if replace_card_notes:
        notes['card_notes'] = []
    case_path.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'ok': True, 'case': str(case_path), 'source': str(text_path),
            'source_text_sha256': notes['source_text_sha256'], 'characters': len(text),
            'systemWrite': False}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', type=Path, required=True)
    ap.add_argument('--text', type=Path, required=True)
    ap.add_argument('--replace-card-notes', action='store_true')
    ap.add_argument('--version')
    args = ap.parse_args()
    print(json.dumps(import_text(args.case, args.text, args.replace_card_notes, args.version), ensure_ascii=False))
