#!/usr/bin/env python3
"""由同案占用证据生成联系清单；不访问浏览器、不释放预约、不发送消息。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from checklist_delivery import validate_manifest, write_issue_summary
from finance_invoice_reconcile import grid_scope, invoice_number
from finance_surface import parse_recognition_error

SCHEMA = 'ustc-reservation-followup-input-v1'
NUMBER = re.compile(r'[0-9]{20}')
UID = re.compile(r'[A-Za-z0-9_.@-]{1,80}')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def body(value):
    for _ in range(4):
        if not isinstance(value, dict):
            raise ValueError('回执须为对象')
        if isinstance(value.get('result'), dict):
            value = value['result']
        else:
            return value
    raise ValueError('回执嵌套未支持')


def amount(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
        raise ValueError('金额须为非负元字符串或null')
    return f'{Decimal(value):.2f}'


def total(rows):
    values = [r['amount'] for r in rows]
    return None if None in values else f'{sum((Decimal(v) for v in values), Decimal(0)):.2f}'


def build_report(config):
    if config.get('schema') != SCHEMA or not re.fullmatch(r'[A-Za-z0-9_-]+', str(config.get('case_id', ''))):
        raise ValueError('须明确同案schema和case_id')
    userid = config.get('expected_userid')
    if not isinstance(userid, str) or not UID.fullmatch(userid):
        raise ValueError('须明确本案当前账号')
    root = Path(config['source_root']).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('source_root须为案件资料目录')
    evidence = {}

    def reference(ref):
        raw = Path(ref.get('path', ''))
        if not raw.is_absolute():
            raise ValueError('证据路径须为绝对路径')
        path = raw.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('证据或原件不在本案资料根内')
        sha = ref.get('sha256')
        if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha) or digest(path) != sha:
            raise ValueError('证据或原件SHA变化')
        item = {'path': str(path), 'sha256': sha}
        evidence[str(path)] = item
        return path, item

    aliases = {}
    for c in config.get('operator_confirmations', []):
        ids = c.get('operator_ids', [])
        if (c.get('user_confirmed') is not True or not isinstance(c.get('name'), str) or not c['name'].strip()
                or not c.get('basis') or not c.get('evidence') or not isinstance(ids, list) or not ids
                or len(set(ids)) != len(ids) or any(not isinstance(v, str) or not UID.fullmatch(v) for v in ids)):
            raise ValueError('同人确认须有姓名、工号、用户确认依据和证据')
        refs = [reference(x)[1] for x in c['evidence']]
        identity_key = 'confirmed:' + ','.join(sorted(ids))
        for uid in ids:
            if uid in aliases and (aliases[uid]['name'] != c['name'] or aliases[uid]['key'] != identity_key):
                raise ValueError('同一工号的姓名或确认范围矛盾')
            aliases[uid] = {'name': c['name'], 'key': identity_key, 'basis': c['basis'], 'evidence': refs}

    refs = config.get('reports')
    if not isinstance(refs, list) or not refs:
        raise ValueError('须提供已有逐票报告')
    invoices, observations, source_coverage, sha_numbers = {}, [], [], {}
    for ref in refs:
        path, source_ref = reference(ref)
        report = json.loads(path.read_text(encoding='utf-8'))
        if (report.get('schema') != 'ustc-finance-invoice-reconcile-v1' or report.get('case_id') != config['case_id']
                or report.get('expected_userid') != userid):
            raise ValueError('逐票报告不是同案或账号不一致')
        scopes = report.get('query_scopes', [])
        if not isinstance(scopes, list) or any(not isinstance(s, dict) for s in scopes):
            raise ValueError('逐票报告查询范围格式未支持')
        observed = next((s.get('observed_at') for s in scopes if str(s.get('grid')) == '113'), None) or report.get('generated_at_utc')
        source_coverage.append({'source': source_ref, 'complete': report.get('complete') is True,
                                'observed_at': observed, 'invoice_count': len(report.get('invoices', []))})
        for i in report.get('invoices', []):
            reservations = []
            for error in i.get('full_recognition_errors', []):
                reservations.extend((r, error) for r in parse_recognition_error(error)['reservations'])
            if i.get('reservation_status') == 'reported_reserved' and not reservations:
                raise ValueError('有占用标记但完整错误无法解析，须先核对')
            if not reservations:
                continue
            n = i.get('invoice_number')
            if not isinstance(n, str) or not NUMBER.fullmatch(n):
                raise ValueError('占用票号须为完整20位字符串')
            source_path, original = reference({'path': i['source_path'], 'sha256': i['source_sha256']})
            if source_path.suffix.lower() not in {'.pdf', '.ofd', '.jpg', '.jpeg', '.png'}:
                raise ValueError('原票载体未支持')
            value, person = amount(i.get('original_total_amount_display_only')), i.get('person_display')
            if original['sha256'] in sha_numbers and sha_numbers[original['sha256']] != n:
                raise ValueError('同一原件SHA对应多个不同票号，须先核对来源')
            sha_numbers[original['sha256']] = n
            if not isinstance(person, str) or not person.strip():
                raise ValueError('原票归属缺失')
            if n in invoices and (invoices[n]['person'] != person or invoices[n]['amount'] != value):
                raise ValueError('同票号的人员或金额矛盾')
            item = invoices.setdefault(n, {'invoice_number': n, 'person': person, 'amount': value,
                'sources': [], 'issue_date': i.get('issue_date'), 'paired_invoice_number': i.get('paired_invoice_number'),
                'version_role': i.get('version_role'), 'pool_guids': []})
            if original not in item['sources']:
                item['sources'].append(original)
            for match in i.get('pool_matches', []):
                guid = match.get('native_guid')
                if guid and guid not in item['pool_guids']:
                    item['pool_guids'].append(guid)
            for reservation, error in reservations:
                observations.append({'invoice_number': n, **reservation, 'full_error': error,
                                     'observed_at': observed, 'report': source_ref})

    pool_rows, pool_ref = [], None
    if config.get('current_pool'):
        path, pool_ref = reference(config['current_pool'])
        _, grid, scope = grid_scope(json.loads(path.read_text()), '53', userid)
        if not scope['complete']:
            raise ValueError('当前本人票池范围不完整，不能作未见依据')
        pool_rows = grid['rows']
    used = defaultdict(list)
    for ref in config.get('used_queries', []):
        path, query_ref = reference(ref)
        query = body(json.loads(path.read_text()))
        if (query.get('ok') is not True or query.get('mode') != 'native_used_invoice_number_query'
                or query.get('identity', {}).get('userid') != userid or query.get('conditionRestored') is not True):
            raise ValueError('已使用查询失败、身份不符或条件未恢复')
        for item in query.get('results', []):
            n, count, rows = item.get('invoice_number'), item.get('records'), item.get('rows')
            if (not isinstance(n, str) or not NUMBER.fullmatch(n) or item.get('complete') is not True
                    or type(count) is not int or not isinstance(rows, list) or count != len(rows)
                    or any(invoice_number(row)[0] != n for row in rows)):
                raise ValueError('已使用逐票范围或完整号码未核实')
            used[n].append({'records': count, 'observed_at': item.get('observedAt'), 'evidence': query_ref})

    by_reservation = {}
    for obs in observations:
        uid = obs['operator_id']
        # 同工号的掩码展示改变仍是同一预约行；无工号时不能据掩码合并人。
        identity = ('id', uid) if uid else ('masked', obs['operator_masked'])
        key = (obs['reservation_number'], identity, obs['invoice_number'])
        row = by_reservation.setdefault(key, {**invoices[obs['invoice_number']],
            'reservation_number': obs['reservation_number'], 'operator_id': uid, 'operator_masked': obs['operator_masked'],
            'operator_masks': [], 'operation_date': obs['operation_date'], 'observations': []})
        if obs['operator_masked'] not in row['operator_masks']:
            row['operator_masks'].append(obs['operator_masked'])
        if obs not in row['observations']:
            row['observations'].append(obs)
    rows = list(by_reservation.values())
    for row in rows:
        alias = aliases.get(row['operator_id'])
        row['contact_name'] = alias['name'] if alias else row['operator_masked'] + '（实名待确认）'
        row['contact_key'] = alias['key'] if alias else 'id:' + row['operator_id'] if row['operator_id'] else 'unknown:' + row['reservation_number']
        row['identity_confirmation'] = alias
        hits = [r for r in pool_rows if invoice_number(r)[0] == row['invoice_number'] or r.get('t.tk_guid') in row['pool_guids']]
        row['current_own_pool'] = '本人当前池已见，先核对当前状态' if hits else '本人当前完整池未见' if pool_ref else '本次未查本人池'
        queries = used.get(row['invoice_number'], [])
        row['own_used_queries'] = queries
        row['own_used_status'] = '本人已用查询有记录，先核既往报销' if any(x['records'] for x in queries) else '本人本次已用查询未见' if queries else '本次未查已用'
        row['next_action'] = '先核本人当前或既往记录，不能直接要求重复释放' if hits or any(x['records'] for x in queries) else '请原经办核是否已报/付款；若未报且可释放，再按系统允许方式处理'
        row['release_status'] = row['reimbursement_status'] = row['payment_status'] = 'unknown'
    contacts = defaultdict(list)
    for row in rows:
        contacts[row['contact_key']].append(row)
    groups = []
    for key, items in contacts.items():
        unique = {x['invoice_number']: x for x in items}
        groups.append({'contact_key': key, 'contact_name': items[0]['contact_name'],
            'operator_ids': sorted({x['operator_id'] for x in items if x['operator_id']}),
            'reservations': sorted({x['reservation_number'] for x in items}), 'invoice_count': len(unique),
            'amount': total(unique.values()), 'invoice_numbers': list(unique)})
    return {'schema': 'ustc-reservation-followup-v1', 'case_id': config['case_id'], 'expected_userid': userid,
        'source_root': str(root), 'sources': list(evidence.values()), 'source_coverage': source_coverage,
        'all_input_reports_complete': all(s['complete'] for s in source_coverage), 'current_pool': pool_ref,
        'invoice_count': len(invoices), 'invoice_reservation_rows': len(rows), 'contact_count': len(groups),
        'reservation_count': len({x['reservation_number'] for x in rows}), 'amount': total(invoices.values()),
        'contacts': groups, 'rows': rows, 'system_write': False, 'messages_sent': False,
        'reservation_released': False, 'claim_amount_calculated': False}


def sheet(name, columns, rows, note):
    return {'name': name, 'title': name, 'note': note,
            'columns': [{'key': k, 'label': k, 'type': t, 'width': w} for k, t, w in columns], 'rows': rows}


def write_package(report, output):
    """按已生成成员白名单打包，不递归装入案件日志或凭据。"""
    names = ['预约占用联系清单.xlsx', '问题总结.html', 'README.md']
    names += [x['draft'] for x in report['contacts']]
    names += sorted({s['copy'] for row in report['rows'] for s in row['sources']})
    for row in report['rows']:
        for source in row['sources']:
            if digest(output / source['copy']) != source['sha256']:
                raise ValueError('打包前原票副本SHA变化')
    target = output / '预约占用联系包.zip'
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(output / name, name)
    return {'path': str(target), 'sha256': digest(target), 'members': names}


def build(config, output, node=None, module_root=None):
    output = Path(output).resolve()
    report = build_report(config)
    if not output.is_relative_to(Path(report['source_root'])):
        raise ValueError('输出须在本案资料根内')
    output.mkdir(parents=True, exist_ok=False)
    (output / '原票').mkdir()
    for row in report['rows']:
        for source in row['sources']:
            suffix = '_' + source['sha256'] if len({s['sha256'] for s in row['sources']}) > 1 else ''
            source['copy'] = '原票/' + row['invoice_number'] + suffix + Path(source['path']).suffix.lower()
            dest = output / source['copy']
            if not dest.exists():
                shutil.copyfile(source['path'], dest)
            if digest(dest) != source['sha256']:
                raise ValueError('原票副本SHA不符')
    issues, details, versions = [], [], []
    by_group = defaultdict(list)
    for row in report['rows']:
        by_group[(row['reservation_number'], row['operator_id'])].append(row)
        details.append({'费用人员': row['person'], '完整发票号码': row['invoice_number'], '票面含税金额': row['amount'],
            '预约单号': row['reservation_number'], '系统工号': row['operator_id'], '确认联系人': row['contact_name'],
            '占用证据时间': '\n'.join(str(o['observed_at'] or '未记载') for o in row['observations']),
            '本人待报池': row['current_own_pool'], '本人已用查询': row['own_used_status'], '下一步': row['next_action'],
            '原票副本': '\n'.join(dict.fromkeys(s['copy'] for s in row['sources'])), '完整错误原文': '\n'.join(dict.fromkeys(o['full_error'] for o in row['observations']))})
        if row['paired_invoice_number']:
            versions.append({'费用人员': row['person'], '联系票号': row['invoice_number'], '关联旧票号': row['paired_invoice_number'],
                             '票面含税金额': row['amount'], '版本': row['version_role'], '处理': '仅联系所列占用票号；旧票只作版本对照，不重复累计报销'})
    for index, ((serial, uid), rows) in enumerate(by_group.items(), 1):
        issues.append({'问题编号': f'R{index:02}', '状态': '请原经办核对当前状态', '人员/费用': rows[0]['contact_name'],
            '涉及金额': total(rows), '问题': f'预约{serial}，系统工号{uid or "未取得"}，涉及{len(rows)}张票',
            '已确认依据': '；'.join(dict.fromkeys(x['current_own_pool'] + '；' + x['own_used_status'] for x in rows)),
            '下一步': '先核是否已报或付款；确认未报且可释放后由原经办处理。历史报错不等于本次已释放。'})
    for index, contact in enumerate(report['contacts'], 1):
        contact['draft'] = f'联系文字{index:02}.txt'
        lines = [f'联系人：{contact["contact_name"]}；系统工号：{"、".join(contact["operator_ids"])}',
                 '请帮忙核对以下票据是否已经报销或付款。如果只是预约占用、尚未报销，且系统允许释放，烦请处理；若已报，请告知对应单号及状态，避免重复报销。', '']
        for row in report['rows']:
            if row['contact_key'] == contact['contact_key']:
                lines.append(f'预约{row["reservation_number"]}｜{row["person"]}｜票号{row["invoice_number"]}｜{row["amount"] or "金额待核"}元')
        lines += ['', '附件为对应原票及完整清单。历史占用、本次本人查询和原经办实际处理结果需分别核对。']
        (output / contact['draft']).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    note = f'现有证据涉及{report["contact_count"]}个联系人、{report["reservation_count"]}个预约、{report["invoice_count"]}张不同票号，联系票面额{report["amount"] or "待核"}元；不是申请额。历史占用与本次本人查询分列，未发送消息或释放预约。'
    if not report['all_input_reports_complete']:
        note += ' 输入报告有不完整范围，仅列已证实占用，不能认定已查全。'
    summaries = [{'联系人': c['contact_name'], '系统工号': '、'.join(c['operator_ids']), '预约单号': '、'.join(c['reservations']),
        '不同票号数': c['invoice_count'], '联系票面额': c['amount'], '联系文字': c['draft'], '状态': '待用户联系核对，未发送/未释放'} for c in report['contacts']]
    plan = {'schema': 'ustc-checklist-delivery-v1', 'case_id': report['case_id'], 'title': '他人预约占用联系清单',
        'note': note, 'system_write': False, 'sources': report['sources'], 'sheets': [
            sheet('问题总结', [('问题编号','text',14),('状态','text',26),('人员/费用','text',24),('涉及金额','money',18),('问题','text',44),('已确认依据','text',44),('下一步','text',44)], issues, note),
            sheet('联系人汇总', [('联系人','text',22),('系统工号','text',24),('预约单号','text',28),('不同票号数','number',18),('联系票面额','money',18),('联系文字','text',26),('状态','text',42)], summaries, note),
            sheet('逐票联系明细', [(k, 'money' if k=='票面含税金额' else 'text', 62 if k=='完整错误原文' else 42 if k in ['占用证据时间','下一步'] else 28) for k in (details[0] if details else ['完整发票号码'])], details, '同票号多份证据只计一张；不同载体SHA保留。本人未见不代表他人未报，是否已报/付款/释放保持待核。'),
            sheet('新旧票对照', [('费用人员','text',22),('联系票号','text',28),('关联旧票号','text',28),('票面含税金额','money',18),('版本','text',18),('处理','text',60)], versions, '此表是联系明细的版本关系，不另加金额。') ]}
    plan = validate_manifest(plan)
    plan_path = output / '清单表格交付计划.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    write_issue_summary(plan, output)
    for filename, rows in [('逐票联系明细.csv', details), ('联系人汇总.csv', summaries)]:
        with (output / filename).open('x', encoding='utf-8-sig', newline='') as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    table_link = '[Excel清单](预约占用联系清单.xlsx)' if node else '[表格交付计划](清单表格交付计划.json)，Excel尚未生成'
    (output / 'README.md').write_text('# 他人预约占用联系清单\n\n' + note + '\n\n[问题总结](问题总结.html) · ' + table_link + '\n\n先核是否已报或付款，确认未报且可释放后由原经办处理。原票在“原票”目录，联系人文字按确认身份归并；原始工号和预约号分别保留。\n\n' + '\n'.join(f'- [{c["contact_name"]}的联系文字]({c["draft"]})' for c in report['contacts']) + '\n', encoding='utf-8')
    result = {'ok': True, 'invoice_count': report['invoice_count'], 'contact_count': report['contact_count'],
              'reservation_count': report['reservation_count'], 'amount': report['amount'], 'workbook_created': False,
              'system_write': False, 'messages_sent': False, 'reservation_released': False}
    if node:
        command = [sys.executable, str(Path(__file__).with_name('checklist_delivery.py')), '--input', str(plan_path),
                   '--output', str(output / '预约占用联系清单.xlsx'), '--node', str(node), '--qa-dir', str(output / '表格版式核验')]
        if module_root:
            command += ['--module-root', str(module_root)]
        subprocess.run(command, check=True)
        result['workbook_created'] = True
        result['package'] = write_package(report, output)
    (output / '预约占用联系证据.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / '生成回执.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--node')
    parser.add_argument('--module-root', type=Path)
    args = parser.parse_args()
    try:
        if args.module_root and not args.node:
            raise ValueError('module-root须与已配置node一起提供')
        print(json.dumps(build(json.loads(args.input.read_text(encoding='utf-8')), args.output_dir, args.node, args.module_root), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, '联系清单生成失败；保留已有产物，不代表预约已释放：' + str(exc) + '\n')


if __name__ == '__main__':
    main()
