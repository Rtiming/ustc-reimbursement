"""按完整学校清单核对正式打印交付；只做身份、集合、SHA及合并结构检查。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from finance_bill_state import index_bills, state as bill_state


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(snapshot, report, expected_userid):
    indexed = index_bills(snapshot, expected_userid)
    required, returned, unclassified = {}, [], []
    for serial, row in indexed.items():
        status, classification = row['t.status'], bill_state(row)
        item = {'serial': serial, 'status': status, 'status_name': row['t.status_name'], 'summary': row['t.abst']}
        if classification == 'ready_for_delivery':
            required[serial] = item
        elif classification == 'returned_after_approval':
            returned.append(item)
        elif classification == 'unknown':
            unclassified.append(item)
    delivery = report['delivery']
    if report.get('business_transport') != 'native_http' or type(delivery['binding']['finance_only']) is not bool:
        raise ValueError('须为明确附件选项的真实接口交付')
    documents = [r for r in delivery['binding']['documents'] if r.get('pdf')]
    serials = [r['serial'] for r in documents]
    if len(set(serials)) != len(serials):
        raise ValueError('交付包含重复单号')
    for row in documents:
        if sha(row['pdf']) != row['pdf_sha256']:
            raise ValueError('逐单PDF已变化：' + row['serial'])
    receipt_dir = Path(delivery['receipt']).parent
    for item in delivery['artifacts']:
        if Path(item['name']).name != item['name'] or sha(receipt_dir / item['name']) != item['sha256']:
            raise ValueError('交付文件SHA不符')
    merged = delivery.get('merged_pdf')
    if not merged:
        raise ValueError('覆盖核验须有合并PDF')
    if Path(merged).parent != receipt_dir or not any(item['name'] == Path(merged).name for item in delivery['artifacts']):
        raise ValueError('合并PDF不在原交付SHA清单')
    # 不读取文字、不渲染版面：逐源页只核拼接后的内容流与页尺寸/方向、书签边界。
    import fitz
    page_index, bookmarks = 0, []
    with fitz.open(merged) as combined:
        for row in documents:
            bookmarks.append([1, row['serial'] + ' ' + row['summary'], page_index + 1])
            with fitz.open(row['pdf']) as original:
                for page in original:
                    if page_index >= combined.page_count:
                        raise ValueError('合并PDF缺页')
                    target = combined[page_index]
                    if (hashlib.sha256(page.read_contents()).digest() != hashlib.sha256(target.read_contents()).digest() or
                            page.mediabox != target.mediabox or page.cropbox != target.cropbox or page.rotation != target.rotation):
                        raise ValueError('合并页内容流或方向尺寸不符：' + row['serial'])
                    page_index += 1
        if page_index != combined.page_count or combined.get_toc() != bookmarks:
            raise ValueError('合并PDF页数或单号书签边界不符')
    missing, extra = sorted(set(required) - set(serials)), sorted(set(serials) - set(required))
    ok = not missing and not extra and not unclassified
    return {'schema': 'ustc-print-coverage-v1', 'ok': ok, 'state': 'complete' if ok else 'coverage_gap',
            'scope': '本账号审批结束且已预约待投递；已支付、草稿、本人待审批和缺附件退回分别排除',
            'expected_userid': expected_userid, 'observed_at': snapshot['observed_at'],
            'account_documents': len(indexed), 'required_count': len(required), 'included_count': len(serials),
            'missing_serials': missing, 'extra_serials': extra, 'unclassified_statuses': unclassified,
            'returned_after_approval': returned, 'required': list(required.values()),
            'merged_pdf': merged, 'merged_pdf_sha256': sha(merged), 'merged_pages': page_index,
            'source_bytes_verified': True, 'merged_structure_verified': True, 'content_audit': False,
            'business_transport': 'native_http', 'system_write': False, 'browser_actions_executed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('覆盖结果不覆盖已有回执')
    data = json.loads(args.input.read_text())
    result = verify(data['snapshot'], data['print_report'], data['expected_userid'])
    result['sources'] = data['sources']
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('ok', 'state', 'account_documents', 'required_count', 'included_count', 'missing_serials')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
