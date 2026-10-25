"""逐页盘点本案原始材料，递归展开ZIP；不执行、不改写原件。"""
from __future__ import annotations

import hashlib
import io
import json
import argparse
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

import pymupdf as fitz
from PIL import Image


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def archive_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode('cp437')
    except UnicodeEncodeError:
        return info.filename
    for encoding in ['utf-8', 'gb18030']:
        try:
            return raw.decode(encoding)
        except UnicodeError:
            pass
    return info.filename


def inventory(plan, root):
    root = Path(root).resolve()
    assets = []
    total = 0

    def inspect(data, source, depth=0):
        nonlocal total
        total += len(data)
        if total > 1024 * 1024 * 1024 or depth > 5:
            raise ValueError('材料归档过大或嵌套超过5层，须分组核对')
        sha = digest_bytes(data)
        suffix = Path(source).suffix.lower()
        if suffix == '.zip':
            assets.append(dict(asset_id=sha + ':file', source=source, sha256=sha, kind='archive'))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set()
                for info in archive.infolist():
                    name = archive_name(info)
                    if info.is_dir() or '__MACOSX' in name or PurePosixPath(name).name == '.DS_Store':
                        continue
                    if name in names:
                        raise ValueError('压缩包中有同名材料，须先区分版本：' + source + '!/' + name)
                    names.add(name)
                    if info.file_size > 100 * 1024 * 1024:
                        raise ValueError('单份压缩材料超过100MB，须单独核对')
                    inspect(archive.read(info), source + '!/' + name, depth + 1)
        elif suffix == '.pdf':
            with fitz.open('pdf', data) as doc:
                if doc.needs_pass or not len(doc):
                    raise ValueError('PDF加密或无页面，不能声明核对完成：' + source)
                for i, page in enumerate(doc):
                    assets.append(dict(asset_id=f'{sha}:p{i}', source=source, sha256=sha,
                        kind='pdf_page', page=i, text=page.get_text()[:5000]))
        elif suffix in ['.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff']:
            with Image.open(io.BytesIO(data)) as scan:
                if getattr(scan, 'n_frames', 1) != 1:
                    raise ValueError('多帧扫描图须转为多页PDF后逐页核对：' + source)
            assets.append(dict(asset_id=sha + ':p0', source=source, sha256=sha, kind='image', page=0))
        else:
            assets.append(dict(asset_id=sha + ':file', source=source, sha256=sha, kind='working_file'))

    paths = set()
    for relative in plan['coverage_sources']:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError('覆盖检查源不存在或不在活动根内：' + relative)
        paths.update(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else paths.add(path)
    for path in sorted(paths):
        if path.name == '.DS_Store':
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError('覆盖检查不得通过软链接读取活动根外材料：' + str(path))
        inspect(path.read_bytes(), str(path.relative_to(root)))
    return assets


def validate(plan, root):
    root = Path(root).resolve()
    report_path = (root / plan['coverage_manifest']).resolve()
    if not report_path.is_relative_to(root):
        raise ValueError('覆盖清单须位于本活动资料根内')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('case_id') != plan['case_id'] or str(report.get('serial')) != str(plan['serial']):
        raise ValueError('覆盖清单与本案的案件/单号不一致')
    if report.get('coverage_sources') != plan['coverage_sources']:
        raise ValueError('覆盖检查源范围与清单不一致，重新核对，不得缩小范围绕过缺项')
    actual = inventory(plan, root)
    identity = lambda a: (a['source'], a['asset_id'])
    if {identity(a) for a in actual} != {identity(a) for a in report['assets']}:
        raise ValueError('原始材料范围已改变或压缩包未完整展开，重新做覆盖核对')
    items = {r['id']: r for s in plan['sections'] for r in s['items']}
    printed = {f"{r['sha256']}:p{r.get('page', 0)}": r['id'] for r in items.values() if r['kind'] == 'source'}
    if len(printed) != sum(r['kind'] == 'source' for r in items.values()):
        raise ValueError('同一材料页的不同副本重复打印')
    resolutions = report['resolutions']
    paper = {r['id']: r for r in plan.get('paper_materials', [])}
    paper_assets = {a['asset_id']: r['id'] for r in paper.values() for a in r.get('source_assets', [])}
    if plan.get('paper_material_policy', 'list_only') == 'list_only' and (any(k in printed for k in paper_assets) or any(r['kind'] == 'original_slot' for r in items.values())):
        raise ValueError('本案纸质材料仅出清单，不得打印扫描件或原件留位页')
    for asset in actual:
        resolution = resolutions.get(asset['asset_id'], {})
        state = resolution.get('state')
        if state not in ['included', 'represented_by', 'excluded', 'container', 'paper_list']:
            raise ValueError('原始材料尚未说明收录或排除：' + asset['source'])
        refs = resolution.get('material_ids', [])
        if state == 'included' and printed.get(asset['asset_id']) not in refs:
            raise ValueError('已收录材料没有实际打印页绑定：' + asset['source'])
        if state in ['included', 'represented_by'] and (not refs or any(ref not in items and ref not in paper for ref in refs)):
            raise ValueError('覆盖关系引用了不存在的打印材料：' + asset['source'])
        if state in ['represented_by', 'excluded', 'container', 'paper_list'] and not resolution.get('reason'):
            raise ValueError('未收录原件必须说明原因：' + asset['source'])
        if state == 'container' and asset['kind'] != 'archive':
            raise ValueError('普通材料不能当已展开压缩包排除')
        if state == 'paper_list' and paper_assets.get(asset['asset_id']) not in resolution.get('paper_ids', []):
            raise ValueError('纸质材料没有清单绑定：' + asset['source'])
        if state == 'paper_list' and any(ref not in paper for ref in resolution.get('paper_ids', [])):
            raise ValueError('纸质材料引用了不存在的清单项：' + asset['source'])
    if not set(printed).issubset({a['asset_id'] for a in actual}):
        raise ValueError('打印材料原件不在实际覆盖检查范围内')
    if any(asset_id not in resolutions or resolutions[asset_id].get('state') != 'included' for asset_id in printed):
        raise ValueError('打印材料未纳入原始材料覆盖检查')
    return dict(source_occurrences=len(actual), unique_assets=len({a['asset_id'] for a in actual}),
                archive_occurrences=sum(a['kind'] == 'archive' for a in actual),
                paper_list_items=len(paper),
                dispositions=dict(Counter(resolutions[k]['state'] for k in {a['asset_id'] for a in actual})),
                unreviewed=0, manifest=str(report_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true', help='重新读取原件，验证已审核的覆盖清单')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    root = (args.plan.resolve().parent / plan['source_root']).resolve()
    if args.check:
        print(json.dumps(validate(plan, root), ensure_ascii=False, indent=2))
        return
    if args.output is None or args.output.exists():
        raise ValueError('盘点须指定不存在的--output；新盘点不覆盖已审核清单')
    assets = inventory(plan, root)
    report = dict(case_id=plan['case_id'], serial=plan['serial'],
                  coverage_sources=plan['coverage_sources'], assets=assets,
                  resolutions={a['asset_id']: {'state': 'unreviewed'} for a in assets})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(args.output.resolve()), source_occurrences=len(assets),
                          unique_assets=len(report['resolutions']), review_required=True), ensure_ascii=False))


if __name__ == '__main__':
    main()
