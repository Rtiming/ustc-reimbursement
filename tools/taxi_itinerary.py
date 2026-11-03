"""只读解析滴滴/高德PDF行程表，按页面坐标保留换行起终点，不推定报销用途。"""
import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import safe_xml as ET


def compact(value):
    return re.sub(r'\s+', '', value)


def parse_bbox(xml, text):
    # Poppler生成的XHTML可能含DOCTYPE；实体与外部资源仍一律拒绝。
    root = ET.fromstring(xml, allow_dtd=True)
    ns = {'h': 'http://www.w3.org/1999/xhtml'}
    date_range = re.search(r'(?:行程起止日期|行程时间)\s*[:：]\s*(\d{4})-\d{2}-\d{2}.*?(?:至|~)', text)
    year = date_range.group(1) if date_range else None
    expected = re.search(r'(?:共计|共)\s*(\d+)\s*(?:笔|单)', text)
    total = re.search(r'合计\s*([\d,]+\.\d{2})\s*元', text)
    trips = []
    issues = []
    for page_no, page in enumerate(root.findall('.//h:page', ns), 1):
        words = [{'text': w.text or '', 'x': (float(w.attrib['xMin'])+float(w.attrib['xMax']))/2,
                  'y': (float(w.attrib['yMin'])+float(w.attrib['yMax']))/2,
                  'xmin': float(w.attrib['xMin']), 'xmax': float(w.attrib['xMax']), 'ymin': float(w.attrib['yMin'])}
                 for w in page.findall('.//h:word', ns)]
        headers = {}
        for w in words:
            label = compact(w['text'])
            for key in ['序号', '车型', '车型类别', '上车时间', '城市', '起点', '终点', '里程', '金额', '备注', '服务商']:
                if label == key or (key in ['里程', '金额'] and label.startswith(key+'[')):
                    headers.setdefault(key, w)
        required = ['序号', '上车时间', '城市', '起点', '终点', '金额']
        if not all(k in headers for k in required):
            issues.append(f'PAGE_{page_no}_TABLE_HEADER_NEEDS_REVIEW')
            continue
        columns = sorted(headers, key=lambda k: headers[k]['x'])
        boundaries = [(headers[columns[i]]['x']+headers[columns[i+1]]['x'])/2 for i in range(len(columns)-1)]
        # 上车时间栏比其他栏宽，以后一个栏左边缘为界，避免日期被切到城市。
        def bounds(key):
            pos = columns.index(key)
            return (boundaries[pos-1] if pos else 0, boundaries[pos] if pos<len(boundaries) else float(page.attrib['width']))
        seq_left, seq_right = bounds('序号')
        header_bottom = max(headers[k]['y'] for k in required)+6
        anchors = sorted([w for w in words if seq_left <= w['x'] < seq_right and w['y'] > header_bottom and re.fullmatch(r'[1-9]\d{0,2}', w['text'])], key=lambda w:w['y'])
        for index, anchor in enumerate(anchors):
            top = (anchors[index-1]['y']+anchor['y'])/2 if index else header_bottom
            bottom = (anchor['y']+anchors[index+1]['y'])/2 if index+1<len(anchors) else anchor['y']+30
            cells = {}
            for key in columns:
                left, right = bounds(key)
                selected = sorted([w for w in words if top < w['y'] <= bottom and left <= w['x'] < right], key=lambda w:(round(w['y']/3),w['xmin']))
                cells[key] = compact(''.join(w['text'] for w in selected))
            time = re.search(r'(\d{4}-)?(\d{2}-\d{2})\s*(\d{2}:\d{2})', cells['上车时间'])
            amount = re.search(r'(-?\d[\d,]*\.\d{2})', cells['金额'])
            row = {'page': page_no, 'sequence': int(anchor['text']), 'time_raw': cells['上车时间'],
                   'departure_time': ((time.group(1) or (year+'-' if year else ''))+time.group(2)+'T'+time.group(3)) if time else None,
                   'city': cells['城市'], 'start_location': cells['起点'], 'end_location': cells['终点'],
                   'amount': amount.group(1).replace(',', '') if amount else None,
                   'mileage_km': cells.get('里程') or None, 'vehicle': cells.get('车型'),
                   'provider': cells.get('服务商') or ('滴滴' if '滴滴' in text else '高德'), 'purpose_status': '待本人核对'}
            if not time or not amount or not row['start_location'] or not row['end_location']:
                issues.append(f'ROW_{row["sequence"]}_INCOMPLETE')
            loc = row['start_location']+row['end_location']
            row['location_hint'] = ('医院相关地点' if '医院' in loc else '中小学相关地点' if '中学' in loc or '小学' in loc else '社区相关地点' if '社区' in loc else '餐饮相关地点' if any(k in loc for k in ['烧烤','餐厅','饭店']) else '校区或其他地点')
            trips.append(row)
    count = int(expected.group(1)) if expected else None
    declared = total.group(1).replace(',', '') if total else None
    computed = sum((Decimal(r['amount']) for r in trips if r['amount'] is not None), Decimal('0'))
    if count is None or count != len(trips): issues.append('TRIP_COUNT_MISMATCH_OR_UNKNOWN')
    if declared is None or computed != Decimal(declared): issues.append('TRIP_AMOUNT_MISMATCH_OR_UNKNOWN')
    if year is None: issues.append('DATE_YEAR_NEEDS_REVIEW')
    if len({r['sequence'] for r in trips}) != len(trips): issues.append('DUPLICATE_SEQUENCE')
    close_routes = []
    for i, a in enumerate(trips):
        for b in trips[i+1:]:
            if a['start_location'] == b['start_location'] and a['end_location'] == b['end_location'] and a['departure_time'] and b['departure_time']:
                try: delta = abs((datetime.fromisoformat(a['departure_time'])-datetime.fromisoformat(b['departure_time'])).total_seconds())
                except ValueError: continue
                if delta<=600: close_routes.append([a['sequence'], b['sequence']])
    return {'ok': not issues, 'trips': trips, 'declared_trip_count': count, 'declared_total': declared,
            'computed_total': str(computed), 'issues': issues, 'close_time_same_route_pairs': close_routes,
            'purpose_note': '地点提示及近时同路线仅供复核，不确认用途、不删除行程、不核定可报金额。'}


def read_itinerary(path):
    path = Path(path).resolve()
    if path.suffix.lower() != '.pdf' or path.stat().st_size > 30*1024*1024:
        raise ValueError('行程入口仅接收不超过30MiB的PDF；扫描表暂需人工核对')
    def read(args):
        p = subprocess.run(['pdftotext', *args, str(path), '-'], capture_output=True, text=True, timeout=45, check=True)
        return p.stdout
    result = parse_bbox(read(['-bbox-layout']), read(['-layout']))
    result.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), system_write=False, parser_version='0.1.0')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('paths', nargs='+', type=Path); ap.add_argument('--output', type=Path, required=True)
    args=ap.parse_args()
    if args.output.resolve() in [p.resolve() for p in args.paths] or args.output.exists():raise ValueError('输出须为新的JSON，不能覆盖来源或已有结果')
    reports=[read_itinerary(p) for p in args.paths]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'ok':all(r['ok'] for r in reports),'reports':reports},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'files':len(reports),'trip_rows':sum(len(r['trips']) for r in reports),'all_consistent':all(r['ok'] for r in reports)},ensure_ascii=False))


if __name__ == '__main__':main()
