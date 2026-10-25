#!/usr/bin/env python3
"""私有规范登记的版本化维护与显式选择；不授予财务写入权限。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from datetime import date
from contextlib import contextmanager
from assistant_contract import GuidanceError, CASE_PROFILES
from pathlib import Path, PureWindowsPath

KINDS = {
    'official_requirement',
    'reviewer_requirement',
    'user_workflow',
    'case_decision',
    'historical_reference',
    'unverified_extract',
}
STATUSES = {'proposed', 'confirmed', 'disputed', 'superseded', 'retired'}
ACTIONS = {'required', 'recommended', 'excluded', 'ordering', 'default'}
SCOPE_KEYS = {
    'institution',
    'organization',
    'responsible_person',
    'business',
    'case_id',
    'expense_type',
    'funding_code',
    'included_business',
    'excluded_business',
    'all',
}


def issue(field, code, detail):
    return {'field': field, 'code': code, 'detail': detail}


def _result(issues, method='standards_read'):
    return {
        'status': 'blocked' if issues else 'ready',
        'issues': issues,
        'needed_inputs': copy.deepcopy(issues),
        'system_write': False,
        'finance_write_authorized': False,
        'next_action': {
            'method': method,
            'action': 'resolve_fields' if issues else 'review_result',
            'fields': sorted({x['field'] for x in issues}),
            'instruction': '补齐所列来源、范围或明确冲突决定后重新读取；不得自行覆盖规则。'
            if issues
            else '核对登记或选择结果；规则登记不授予财务写入权限。',
        },
    }


def text(value):
    return isinstance(value, str) and bool(value.strip())


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('revision须为三段非负整数版本')
    return tuple(map(int, value.split('.')))


def next_version(value):
    parts = version(value)
    return '.'.join(map(str, (*parts[:2], parts[2] + 1)))


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期须为YYYY-MM-DD')
    return date.fromisoformat(value)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('JSON重复键：' + key)
        value[key] = item
    return value


def read_json(path):
    return json.loads(
        Path(path).read_text(encoding='utf-8-sig'),
        object_pairs_hook=_pairs,
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError('非法JSON数值：' + x)),
    )


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
    ).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _refs(value, sources):
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(x, str) and x in sources for x in value)
        and len(value) == len(set(value))
    )


def validate_profile(profile):
    """校验登记结构；空rules兼容既有收集中的专项。"""
    problems = []
    if not isinstance(profile, dict):
        return _result([issue('', 'INVALID_PROFILE', '规范须为对象')])
    for key in ('profile_id', 'title'):
        if not text(profile.get(key)):
            problems.append(issue(key, 'REQUIRED', '缺少专项标识或名称'))
    if type(profile.get('schema_version')) is not int or profile.get('schema_version') != 1:
        problems.append(issue('schema_version', 'UNSUPPORTED_SCHEMA', '仅支持现有版本1'))
    parents = profile.get('extends', [])
    if (
        not isinstance(parents, list)
        or len(parents) > 16
        or any(
            not isinstance(p, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', p) for p in parents
        )
        or len(set(parents)) != len(parents)
        or profile.get('profile_id') in parents
    ):
        problems.append(
            issue('extends', 'INVALID_PARENTS', '父模式须为最多16个不同的登记ID，不能继承自身')
        )
    try:
        version(profile.get('revision'))
    except ValueError as exc:
        problems.append(issue('revision', 'INVALID_VERSION', str(exc)))
    sources = {}
    rows = profile.get('sources', [])
    if not isinstance(rows, list):
        problems.append(issue('sources', 'INVALID_SOURCES', '来源须为数组'))
        rows = []
    for n, source in enumerate(rows):
        field = f'sources[{n}]'
        if not isinstance(source, dict) or not text(source.get('source_id')):
            problems.append(issue(field, 'SOURCE_ID_REQUIRED', '来源须有稳定source_id'))
            continue
        sid = source['source_id']
        if sid in sources:
            problems.append(issue(field, 'DUPLICATE_SOURCE', '来源编号重复'))
        sources[sid] = source
        if not text(source.get('kind')) or not text(source.get('location')):
            problems.append(
                issue(field, 'SOURCE_LOCATION_REQUIRED', '须记录来源类型和原文/消息位置')
            )
        if source.get('sha256') is not None and not re.fullmatch(
            r'[a-f0-9]{64}', str(source['sha256'])
        ):
            problems.append(issue(field + '.sha256', 'INVALID_SHA', '原件SHA256格式不合法'))
    rows = profile.get('rules', [])
    if not isinstance(rows, list):
        problems.append(issue('rules', 'INVALID_RULES', '规则须为数组'))
        rows = []
    ids = set()
    for n, rule in enumerate(rows):
        field = f'rules[{n}]'
        if not isinstance(rule, dict):
            problems.append(issue(field, 'INVALID_RULE', '规则须为对象'))
            continue
        rid = rule.get('rule_id')
        if not text(rid) or rid in ids:
            problems.append(
                issue(field + '.rule_id', 'INVALID_RULE_ID', '当前规则编号须非空且唯一')
            )
        if isinstance(rid, str):
            ids.add(rid)
        try:
            version(rule.get('revision'))
        except ValueError as exc:
            problems.append(issue(field + '.revision', 'INVALID_VERSION', str(exc)))
        if (
            not text(rule.get('statement'))
            or rule.get('kind') not in KINDS
            or rule.get('status') not in STATUSES
        ):
            problems.append(issue(field, 'RULE_METADATA_REQUIRED', '规则正文、类型或状态无效'))
        scope = rule.get('scope')
        if not isinstance(scope, dict) or not scope or set(scope) - SCOPE_KEYS:
            problems.append(
                issue(
                    field + '.scope', 'INVALID_SCOPE', '须显式给出支持的适用范围；全局使用all:true'
                )
            )
        elif scope.get('all') is not None and (scope['all'] is not True or len(scope) != 1):
            problems.append(
                issue(field + '.scope', 'INVALID_GLOBAL_SCOPE', 'all:true不可混合其他范围')
            )
        elif isinstance(scope, dict):
            for key, value in scope.items():
                if key == 'all':
                    continue
                if key in {'included_business', 'excluded_business'}:
                    valid = isinstance(value, list) and all(text(x) for x in value)
                else:
                    valid = (
                        value is None
                        or text(value)
                        or (isinstance(value, list) and bool(value) and all(text(x) for x in value))
                    )
                if not valid:
                    problems.append(
                        issue(
                            field + '.scope.' + key,
                            'INVALID_SCOPE_VALUE',
                            '范围值须为明确文本、文本数组或待核null',
                        )
                    )
        effect = rule.get('effect', {})
        if (
            not isinstance(effect, dict)
            or effect.get('action') not in ACTIONS
            or not text(effect.get('target'))
            or 'value' not in effect
        ):
            problems.append(
                issue(field + '.effect', 'INVALID_EFFECT', '作用须有固定action、target和value')
            )
        if not _refs(rule.get('source_refs'), sources):
            problems.append(
                issue(field + '.source_refs', 'UNRESOLVED_SOURCE', '规则须引用已登记来源')
            )
        if rule.get('status') == 'confirmed':
            confirmation = rule.get('confirmation', {})
            if (
                not isinstance(confirmation, dict)
                or not text(confirmation.get('by'))
                or not text(confirmation.get('at'))
                or not _refs(confirmation.get('source_refs'), sources)
            ):
                problems.append(
                    issue(
                        field + '.confirmation',
                        'CONFIRMATION_REQUIRED',
                        '已确认规则须记录确认者、时间和依据',
                    )
                )
            if rule.get('kind') in {'historical_reference', 'unverified_extract'}:
                problems.append(
                    issue(
                        field + '.kind',
                        'NON_OPERATIVE_KIND',
                        '历史样本/待核提取不能直接成为已确认规则',
                    )
                )
        bounds = []
        for key in ('valid_from', 'valid_until'):
            try:
                bounds.append(iso_date(rule[key]) if rule.get(key) is not None else None)
            except ValueError as exc:
                bounds.append(None)
                problems.append(issue(field + '.' + key, 'INVALID_DATE', str(exc)))
        if all(bounds) and bounds[0] > bounds[1]:
            problems.append(issue(field, 'INVALID_VALIDITY', '生效日期晚于失效日期'))
        for key in ('supersedes', 'exception_to'):
            refs = rule.get(key, [])
            if not isinstance(refs, list):
                problems.append(issue(field + '.' + key, 'INVALID_RELATION', '规则关系须为数组'))
                continue
            for ref in refs:
                if (
                    not isinstance(ref, dict)
                    or not text(ref.get('rule_id'))
                    or not text(ref.get('revision'))
                    or not text(ref.get('reason'))
                    or not _refs(ref.get('source_refs'), sources)
                    or (
                        'profile_id' in ref
                        and (
                            not isinstance(ref['profile_id'], str)
                            or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', ref['profile_id'])
                        )
                    )
                ):
                    problems.append(
                        issue(
                            field + '.' + key,
                            'RELATION_EVIDENCE_REQUIRED',
                            '替代或例外须有精确版本、理由和来源',
                        )
                    )
                elif (
                    ref.get('profile_id', profile['profile_id']) == profile['profile_id']
                    and ref['rule_id'] == rid
                    and ref['revision'] == rule.get('revision')
                ):
                    problems.append(
                        issue(field + '.' + key, 'SELF_REPLACEMENT', '不能替代或例外于自身当前版本')
                    )
    return _result(problems)


def preview_change(profile, request):
    """固定增补、修订、停用动作；旧版本由执行层保存，不删除规则。"""
    check = validate_profile(profile)
    if check['issues']:
        raise ValueError('原规范未通过校验：' + json.dumps(check['issues'], ensure_ascii=False))
    if not isinstance(request, dict) or request.get('action') not in {'add', 'revise', 'retire'}:
        raise ValueError('action仅允许add/revise/retire')
    if not all(text(request.get(k)) for k in ('actor', 'at', 'reason')):
        raise ValueError('变更须有actor、at、reason')
    result = copy.deepcopy(profile)
    new_sources = request.get('sources', [])
    if not isinstance(new_sources, list):
        raise ValueError('sources须为数组')
    source_index = {x['source_id']: x for x in result.get('sources', [])}
    for source in new_sources:
        if not isinstance(source, dict) or not text(source.get('source_id')):
            raise ValueError('新来源缺source_id')
        sid = source['source_id']
        if sid in source_index and source != source_index[sid]:
            raise ValueError('已有来源不可静默改写；使用新source_id')
        source_index[sid] = copy.deepcopy(source)
    result['sources'] = list(source_index.values())
    if not _refs(request.get('source_refs'), source_index):
        raise ValueError('变更须引用已登记的来源')
    rules = {x['rule_id']: x for x in result['rules']}
    action = request['action']
    if action == 'retire':
        rid = request.get('rule_id')
        if rid not in rules:
            raise ValueError('待停用规则不存在')
        old = rules[rid]
        rule = copy.deepcopy(old)
        rule.update(
            status='retired', revision=request.get('revision', next_version(old['revision']))
        )
        rule['supersedes'] = [
            {
                'rule_id': rid,
                'revision': old['revision'],
                'reason': request['reason'],
                'source_refs': request['source_refs'],
            }
        ]
        rule['retirement'] = {
            k: copy.deepcopy(request[k]) for k in ('actor', 'at', 'reason', 'source_refs')
        }
    else:
        rule = copy.deepcopy(request.get('rule'))
        if not isinstance(rule, dict) or not text(rule.get('rule_id')):
            raise ValueError('缺少待登记rule')
        rid = rule['rule_id']
        if action == 'add' and rid in rules:
            raise ValueError('rule_id已存在；修订须用revise')
        if action == 'revise' and rid not in rules:
            raise ValueError('修订的rule_id不存在')
        old = rules.get(rid)
        if old and not any(
            isinstance(x, dict) and x.get('rule_id') == rid and x.get('revision') == old['revision']
            for x in rule.get('supersedes', [])
        ):
            raise ValueError('修订须明确supersedes原规则ID和版本')
    if old and version(rule['revision']) <= version(old['revision']):
        raise ValueError('规则新版本必须递增')
    rules[rid] = rule
    result['rules'] = list(rules.values())
    result['revision'] = request.get('profile_revision', next_version(profile['revision']))
    if version(result['revision']) <= version(profile['revision']):
        raise ValueError('专项新版本必须递增')
    check = validate_profile(result)
    if check['issues']:
        raise ValueError('变更后规范未通过校验：' + json.dumps(check['issues'], ensure_ascii=False))
    return {
        **_result([], 'standards_change'),
        'action': action,
        'profile': result,
        'changes': {
            'rule_id': rid,
            'before_revision': old['revision'] if old else None,
            'after_revision': rule['revision'],
            'profile_revision': result['revision'],
        },
        'system_write': False,
        'finance_write_authorized': False,
    }


def safe_path(root, relative_path):
    """私有目录内只接收相对路径，拒绝链接/重解析点与跨平台逃逸。"""
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not text(relative_path):
        raise ValueError('须给既有资料根目录和相对路径')
    raw = relative_path.replace('\\', '/')
    if PureWindowsPath(raw).drive or raw.startswith('/'):
        raise ValueError('路径须相对于资料根目录')
    parts = raw.split('/')
    if any(
        p in ('', '.', '..')
        or re.search(r'[<>:"|?*\x00-\x1f]', p)
        or p.rstrip('. ') != p
        or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
        for p in parts
    ):
        raise ValueError('拒绝路径逃逸或歧义路径')
    current = root
    for part in parts:
        current = current / part
        if current.exists() or current.is_symlink():
            mode = current.lstat()
            if (
                stat.S_ISLNK(mode.st_mode)
                or getattr(mode, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError('规范路径不能经过链接或重解析点')
    if not current.resolve().is_relative_to(root):
        raise ValueError('规范路径越出资料根目录')
    return current


@contextmanager
def _library_transaction(library_root, intent):
    """新增专项与规则修订共用同一把锁和审计目录，遗留锁不自动删除。"""
    history = safe_path(library_root, '.standards-history')
    history.mkdir(exist_ok=True)
    lock = safe_path(library_root, '.standards-history/write.lock')
    with lock.open('xb') as stream:
        stream.write(encoded(intent))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        audit = safe_path(library_root, '.standards-history/' + uuid.uuid4().hex)
        audit.mkdir()
        yield audit
    finally:
        lock.unlink()


def maintain_profile(library_root, relative_path, request, execute=False, expected_sha256=None):
    if type(execute) is not bool:
        raise ValueError('execute须为布尔值')
    if '.standards-history' in Path(
        library_root
    ).parts or '.standards-history' in relative_path.replace('\\', '/').split('/'):
        raise ValueError('审计快照不可作为规范正本修改')
    path = safe_path(library_root, relative_path)
    if path.suffix.lower() != '.json' or not path.is_file():
        raise ValueError('规范必须为既有JSON文件')
    before = path.read_bytes()
    actual_sha = sha(before)
    if expected_sha256 is not None and expected_sha256 != actual_sha:
        raise ValueError('规范SHA已改变，请重新预览')
    preview = preview_change(read_json(path), request)
    after = encoded(preview['profile'])
    result = {
        **preview,
        'executed': False,
        'path': str(path),
        'before_sha256': actual_sha,
        'after_sha256': sha(after),
        'audit_path': None,
    }
    if not execute:
        return result
    if expected_sha256 is None:
        raise ValueError('真实修改必须提供预览时的expected_sha256')
    temp = None
    with _library_transaction(
        library_root, {'path': relative_path, 'before_sha256': actual_sha}
    ) as audit:
        try:
            if sha(path.read_bytes()) != actual_sha:
                raise ValueError('等待期间规范已变化，请重新预览')
            transaction = audit.name
            receipt = {
                'schema': 'ustc-standard-change-v1',
                'path': relative_path,
                'before_sha256': actual_sha,
                'after_sha256': sha(after),
                'request': request,
                'changes': preview['changes'],
                'system_write': False,
                'finance_write_authorized': False,
            }
            for name, payload in [
                ('before.json', before),
                ('after.json', after),
                ('intent.json', encoded(receipt)),
            ]:
                with (audit / name).open('xb') as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            temp = safe_path(library_root, relative_path + '.' + transaction + '.tmp')
            with temp.open('xb') as handle:
                handle.write(after)
                handle.flush()
                os.fsync(handle.fileno())
            if sha(path.read_bytes()) != actual_sha:
                raise ValueError('写入前规范已变化；保留审计，未覆盖正本')
            os.replace(temp, path)
            if sha(path.read_bytes()) != sha(after):
                raise ValueError('写入回读SHA不符，须核对审计，不可重放')
            with (audit / 'committed.json').open('xb') as handle:
                handle.write(encoded({'after_sha256': sha(after), 'system_write': False}))
            result.update(executed=True, audit_path=str(audit))
            return result
        finally:
            if temp is not None and temp.exists():
                temp.unlink()


def _scope_match(scope, context):
    unknown = []
    for key, expected in scope.items():
        if key == 'all':
            continue
        if key in {'included_business', 'excluded_business'}:
            if not expected:
                continue
            actual = context.get('business')
            if actual is None:
                unknown.append('business')
            elif (actual in expected) != (key == 'included_business'):
                return False, []
        else:
            actual = context.get(key)
            if expected is None or actual is None:
                unknown.append(key)
            elif actual not in (expected if isinstance(expected, list) else [expected]):
                return False, []
    return (None if unknown else True), unknown


def select_rules(profile, context, as_of):
    """选择已确认且明确匹配的规则；不解释任意effect为财务动作。"""
    check = validate_profile(profile)
    if check['issues']:
        return {
            **check,
            'applied_rules': [],
            'skipped_rules': [],
            'finance_write_authorized': False,
        }
    today = iso_date(as_of)
    problems, selected, skipped = [], [], []
    if (
        not isinstance(context, dict)
        or not text(context.get('case_id'))
        or not isinstance(context.get('selected_profile_ids'), list)
        or profile['profile_id'] not in context.get('selected_profile_ids', [])
        or not text(context.get('selection_basis'))
    ):
        return {
            **_result(
                [issue('context', 'EXPLICIT_SELECTION_REQUIRED', '须明确案件、专项选择和适用依据')],
                'standards_select',
            ),
            'applied_rules': [],
            'skipped_rules': [],
        }
    for rule in profile['rules']:
        rid = rule['rule_id']
        if rule['status'] != 'confirmed':
            skipped.append({'rule_id': rid, 'revision': rule['revision'], 'reason': rule['status']})
            continue
        matched, unknown = _scope_match(rule['scope'], context)
        if matched is None:
            problems.append(
                issue(
                    'rules.' + rid + '.scope', 'SCOPE_UNKNOWN', '适用范围待核：' + ','.join(unknown)
                )
            )
            continue
        if not matched:
            skipped.append({'rule_id': rid, 'revision': rule['revision'], 'reason': 'out_of_scope'})
            continue
        if (rule.get('valid_from') and today < iso_date(rule['valid_from'])) or (
            rule.get('valid_until') and today > iso_date(rule['valid_until'])
        ):
            skipped.append(
                {'rule_id': rid, 'revision': rule['revision'], 'reason': 'outside_validity'}
            )
            continue
        selected.append(copy.deepcopy(rule))
    # 替代/例外只有当前范围命中后才消去被替代项；循环关系必须人工澄清。
    identities = {(x['rule_id'], x['revision']) for x in selected}
    edges = {
        (r['rule_id'], r['revision']): {
            (x['rule_id'], x['revision'])
            for key in ('supersedes', 'exception_to')
            for x in r.get(key, [])
            if (x['rule_id'], x['revision']) in identities
        }
        for r in selected
    }

    def cycles(node, path):
        return node in path or any(cycles(target, path | {node}) for target in edges[node])

    if any(cycles(node, set()) for node in edges):
        problems.append(
            issue('rules', 'CYCLIC_REPLACEMENT', '规则替代或例外形成循环，须明确唯一依据')
        )
        selected = []
    else:
        replaced = set().union(*edges.values()) if edges else set()
        selected = [x for x in selected if (x['rule_id'], x['revision']) not in replaced]
    conflicts = set()
    for n, left in enumerate(selected):
        for right in selected[n + 1 :]:
            a, b = left['effect'], right['effect']
            if a['target'] == b['target'] and (a['action'], a['value']) != (
                b['action'],
                b['value'],
            ):
                conflicts.update((left['rule_id'], right['rule_id']))
                problems.append(
                    issue(
                        'effect.' + a['target'],
                        'RULE_CONFLICT',
                        '同一作用项规则冲突：' + left['rule_id'] + ' / ' + right['rule_id'],
                    )
                )
    selected = [x for x in selected if x['rule_id'] not in conflicts]
    return {
        **_result(problems, 'standards_select'),
        'case_id': context['case_id'],
        'as_of': as_of,
        'profile_id': profile['profile_id'],
        'profile_revision': profile['revision'],
        'applied_rules': selected,
        'skipped_rules': skipped,
        'issues': problems,
        'system_write': False,
        'finance_write_authorized': False,
    }


def compose_profiles(profiles, selected_id):
    """显式extends组成一个有效规则集合；规则和来源保留所属模式，不按层级自动覆盖。"""
    ordered, visiting, visited = [], set(), set()

    def visit(pid):
        if pid in visiting:
            raise ValueError('模式extends形成循环')
        if pid in visited:
            return
        if pid not in profiles:
            raise ValueError('父模式未登记或未加载：' + pid)
        if len(visiting) >= 16 or len(visited) >= 64:
            raise ValueError('模式组合超过16层或64个模式')
        profile = profiles[pid]
        checked = validate_profile(profile)
        if checked['issues']:
            raise ValueError('模式结构不完整：' + pid)
        visiting.add(pid)
        for parent in profile.get('extends', []):
            visit(parent)
        visiting.remove(pid)
        visited.add(pid)
        ordered.append(profile)

    visit(selected_id)
    merged = copy.deepcopy(profiles[selected_id])
    merged.update(extends=[], sources=[], rules=[])
    for profile in ordered:
        pid = profile['profile_id']
        # 名字空间避免两个项目的limit、source1等局部ID互相覆盖。
        for source in profile.get('sources', []):
            merged['sources'].append(
                {**copy.deepcopy(source), 'source_id': pid + '/' + source['source_id']}
            )
        for original in profile.get('rules', []):
            rule = copy.deepcopy(original)
            rule.update(
                rule_id=pid + '/' + original['rule_id'],
                origin_profile_id=pid,
                origin_rule_id=original['rule_id'],
            )
            rule['source_refs'] = [pid + '/' + ref for ref in rule['source_refs']]
            if isinstance(rule.get('confirmation'), dict) and 'source_refs' in rule['confirmation']:
                rule['confirmation']['source_refs'] = [
                    pid + '/' + ref for ref in rule['confirmation']['source_refs']
                ]
            for relation in ('supersedes', 'exception_to'):
                for ref in rule.get(relation, []):
                    owner = ref.pop('profile_id', pid)
                    if owner not in visited:
                        raise ValueError('规则替代引用不在本次模式组合中：' + owner)
                    ref['rule_id'] = owner + '/' + ref['rule_id']
                    ref['source_refs'] = [pid + '/' + item for item in ref['source_refs']]
            merged['rules'].append(rule)
    return merged, [p['profile_id'] for p in ordered]


def _library_entry(service, library_root, profile_id=None):
    if not isinstance(library_root, str) or not Path(library_root).is_absolute():
        raise GuidanceError('invalid_library_root', '规范库使用本机绝对路径')
    root = Path(library_root).resolve()
    if not root.is_dir() or not any(root.is_relative_to(p) for p in service.material_roots):
        raise GuidanceError('material_root', '规范库须在启动时明确授权的material-root内')
    registry_path = safe_path(root, 'registry.json')
    if registry_path.stat().st_size > 1024 * 1024:
        raise GuidanceError('registry_too_large', '规范目录超过1MiB，先核对所选目录')
    registry = read_json(registry_path)
    if (
        not isinstance(registry, dict)
        or registry.get('schema_version') != 1
        or not isinstance(registry.get('profiles'), list)
    ):
        raise GuidanceError('invalid_registry', '请选择含schema_version=1和profiles列表的规范库')
    rows = registry['profiles']
    ids = [row.get('profile_id') for row in rows if isinstance(row, dict)]
    if (
        len(ids) != len(rows)
        or any(not isinstance(x, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', x) for x in ids)
        or len(set(ids)) != len(ids)
    ):
        raise GuidanceError('invalid_registry', '专项编号须唯一且为2至64位小写标识')
    if profile_id is None:
        return root, rows, None, None, None
    entry = next((row for row in rows if row['profile_id'] == profile_id), None)
    if entry is None:
        raise GuidanceError('profile_not_found', '专项未登记；先standards_read列出，不猜路径')
    path = safe_path(root, entry.get('path'))
    if path.suffix != '.json' or path.stat().st_size > 1024 * 1024:
        raise GuidanceError('profile_format', '专项须为不超过1MiB的JSON登记')
    raw = path.read_bytes()
    profile = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    profile_sha256 = hashlib.sha256(raw).hexdigest()
    validation = validate_profile(profile)
    if validation['status'] != 'ready' or profile.get('profile_id') != profile_id:
        raise GuidanceError(
            'invalid_profile',
            '专项登记或规则校验未通过：'
            + json.dumps(validation.get('issues', []), ensure_ascii=False),
        )
    return root, entry, path, profile, profile_sha256


def standards_read(service, library_root, profile_id=None):
    root, entry, path, profile, profile_sha256 = _library_entry(service, library_root, profile_id)
    if profile_id is None:
        return {
            'ok': True,
            'profiles': entry,
            'system_write': False,
            'next_action': {
                'tool': 'reimbursement_standards_read',
                'arguments': {'library_root': str(root)},
                'needed_inputs': ['profile_id'],
                'reason': '从列表选择明确专项',
            },
        }
    return {
        'ok': True,
        'profile': profile,
        'sha256': profile_sha256,
        'system_write': False,
        'next_action': {
            'tool': 'reimbursement_standards_select',
            'arguments': {'library_root': str(root), 'profile_id': profile_id},
            'needed_inputs': ['context', 'as_of'],
            'reason': '按本案范围与业务日期筛选已确认规则',
        },
    }


def _empty_profile(profile_id, title, parents, pending_questions):
    """新库与追加专项共用空模板，不在任何入口自动确认规则。"""
    return {
        'schema_version': 1,
        'profile_id': profile_id,
        'title': title,
        'revision': '0.1.0',
        'status': 'collecting',
        'extends': list(parents),
        'scope': {},
        'sources': [],
        'rules': [],
        'pending_questions': list(pending_questions),
    }


def standards_init(
    service, library_root, profile_id, title, mode='student_activity', execute=False
):
    """仅建立全新的私人规范库；模板不包含已确认规则、财务身份或执行授权。"""
    from assistant_api import atomic_json

    if type(execute) is not bool or mode not in CASE_PROFILES:
        raise GuidanceError('invalid_initialization', '须给有效材料场景和布尔execute')
    if (
        not isinstance(profile_id, str)
        or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', profile_id)
        or profile_id == 'common'
        or not isinstance(title, str)
        or not title.strip()
    ):
        raise GuidanceError('invalid_profile', '专项须有名称及唯一ID，common保留给共用规则')
    if not isinstance(library_root, str) or not Path(library_root).is_absolute():
        raise GuidanceError('invalid_library_root', '新规范库须为授权材料根内的绝对路径')
    raw_target = Path(library_root)
    target = raw_target.resolve()
    parent = next((root for root in service.material_roots if target.is_relative_to(root)), None)
    if parent is None:
        raise GuidanceError('material_root', '新规范库不在授权材料根内')
    for segment in (raw_target, *raw_target.parents):
        if segment.is_symlink() and segment.resolve().is_relative_to(parent):
            raise GuidanceError('library_symlink', '规范库路径不能经过资料根内的符号链接')
    if '..' in raw_target.parts:
        raise GuidanceError('library_path', '规范库路径不能包含上级跳转')
    if '.standards-history' in target.parts:
        raise GuidanceError('library_history', '不能在不可变规范历史中建立新库')
    target = safe_path(parent, str(target.relative_to(parent)))
    if target.exists():
        raise GuidanceError('library_exists', '目标已存在，不覆盖旧规范库；使用standards_read继续')
    profiles = []
    for pid, name, parents in [('common', '共用规则', []), (profile_id, title.strip(), ['common'])]:
        profiles.append(
            _empty_profile(
                pid,
                name,
                parents,
                ['登记本单位有效规范及来源', '确认本场景与专项差异；示例不代替真实规则'],
            )
        )
    result = {
        'ok': True,
        'executed': False,
        'library_root': str(target),
        'profile_id': profile_id,
        'mode': mode,
        'profiles': profiles,
        'system_write': False,
        'finance_write_authorized': False,
        'next_action': {
            'tool': 'reimbursement_standards_read',
            'arguments': {'library_root': str(target), 'profile_id': profile_id},
        },
    }
    if not execute:
        return result
    target.mkdir(parents=True, exist_ok=False)
    atomic_json(
        target / 'initialization.json', {'phase': 'writing', 'mode': mode, 'system_write': False}
    )
    registry = {'schema_version': 1, 'profiles': []}
    for profile in profiles:
        relative = 'profiles/' + profile['profile_id'] + '.json'
        atomic_json(target / relative, profile)
        registry['profiles'].append(
            {'profile_id': profile['profile_id'], 'title': profile['title'], 'path': relative}
        )
    atomic_json(target / 'registry.json', registry)
    atomic_json(
        target / 'initialization.json',
        {
            'phase': 'completed',
            'mode': mode,
            'system_write': False,
            'registry_sha256': hashlib.sha256((target / 'registry.json').read_bytes()).hexdigest(),
        },
    )
    return {**result, 'executed': True}


def standards_change(
    service, library_root, profile_id, change, execute=False, expected_sha256=None
):
    from reimbursement_standards import maintain_profile

    root, entry, _, _, _ = _library_entry(service, library_root, profile_id)
    result = maintain_profile(
        root, entry['path'], change, execute=execute, expected_sha256=expected_sha256
    )
    result['next_action'] = {
        'tool': 'reimbursement_standards_read',
        'arguments': {'library_root': str(root), 'profile_id': profile_id},
        'reason': '回读当前规则与版本；规则变更不授权财务写入',
    }
    return result


def standards_add_profile(
    service,
    library_root,
    profile_id,
    title,
    parent_ids=None,
    execute=False,
    expected_registry_sha256=None,
):
    """给既有库追加空专项，预览冻结registry；不修改已有模式或确认规则。"""
    from assistant_api import atomic_json

    root, entries, _, _, _ = _library_entry(service, library_root)
    if '.standards-history' in root.parts:
        raise GuidanceError('library_history', '不可在规范历史快照内追加专项')
    if (
        type(execute) is not bool
        or not isinstance(profile_id, str)
        or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', profile_id)
        or not isinstance(title, str)
        or not title.strip()
    ):
        raise GuidanceError('invalid_profile', '须给有效专项ID、名称和布尔execute')
    parents = [] if parent_ids is None else parent_ids
    if (
        not isinstance(parents, list)
        or len(parents) > 16
        or any(not isinstance(p, str) for p in parents)
        or len(set(parents)) != len(parents)
        or profile_id in parents
    ):
        raise GuidanceError('invalid_parents', '父模式须为不同的登记ID，最多16项，不得包含自身')
    ids = {entry['profile_id'] for entry in entries}
    if profile_id in ids:
        raise GuidanceError('profile_exists', '专项已存在，读取原模式，不覆盖')
    if set(parents) - ids:
        raise GuidanceError('parent_missing', '父模式未登记，先standards_read核对')
    for parent in parents:
        _library_entry(service, library_root, parent)
    registry_path = safe_path(root, 'registry.json')
    before = registry_path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    relative = 'profiles/' + profile_id + '.json'
    path = safe_path(root, relative)
    if path.exists():
        raise GuidanceError('profile_path_exists', '专项路径已存在但未登记，须核对原创建任务')
    profile = _empty_profile(profile_id, title.strip(), parents, ['登记本专项差异及真实来源'])
    result = {
        'ok': True,
        'executed': False,
        'profile': profile,
        'path': str(path),
        'before_registry_sha256': digest,
        'system_write': False,
        'finance_write_authorized': False,
    }
    if not execute:
        return result
    if expected_registry_sha256 != digest:
        raise GuidanceError('registry_changed', '须提供本次预览的registry SHA；变化后重新预览')
    with _library_transaction(
        root, {'operation': 'add_profile', 'profile_id': profile_id}
    ) as audit:
        if registry_path.read_bytes() != before or path.exists():
            raise GuidanceError('registry_changed', '规范库已变化，保留原内容，不创建专项')
        (audit / 'registry-before.json').write_bytes(before)
        atomic_json(audit / 'profile-intent.json', profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(profile, stream, ensure_ascii=False, indent=2)
        if registry_path.read_bytes() != before:
            raise GuidanceError(
                'registry_changed', '创建过程中registry改变；保留新增文件和审计，不覆盖或重试'
            )
        registry = read_json(registry_path)
        registry['profiles'].append(
            {'profile_id': profile_id, 'title': title.strip(), 'path': relative}
        )
        atomic_json(registry_path, registry)
        atomic_json(
            audit / 'committed.json',
            {
                'profile_id': profile_id,
                'registry_sha256': hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            },
        )
        return {**result, 'executed': True, 'audit_path': str(audit)}


def standards_select(service, library_root, profile_id, context, as_of):
    _, _, path, profile, profile_sha256 = _library_entry(service, library_root, profile_id)
    if profile.get('extends'):
        profiles, refs = {}, []

        def load(pid, stack=()):
            if pid in stack or len(stack) >= 16:
                raise GuidanceError('profile_cycle', '模式继承循环或超过16层')
            if pid in profiles:
                return
            if len(profiles) >= 64:
                raise GuidanceError('profile_limit', '模式组合超过64项')
            _, _, source, value, digest = _library_entry(service, library_root, pid)
            profiles[pid] = value
            refs.append(
                {
                    'profile_id': pid,
                    'revision': value['revision'],
                    'path': str(source),
                    'sha256': digest,
                }
            )
            for parent in value.get('extends', []):
                load(parent, (*stack, pid))

        load(profile_id)
        merged, order = compose_profiles(profiles, profile_id)
        result = select_rules(merged, context, as_of)
        selected_sha = next(ref['sha256'] for ref in refs if ref['profile_id'] == profile_id)
        result.update(profile_sha256=selected_sha, profile_sources=refs, composition_order=order)
        return result
    result = select_rules(profile, context, as_of)
    result['profile_sha256'] = profile_sha256
    return result


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('validate')
    inspect.add_argument('--profile', type=Path, required=True)
    select = sub.add_parser('select')
    select.add_argument('--profile', type=Path, required=True)
    select.add_argument('--context', type=Path, required=True)
    select.add_argument('--as-of', required=True)
    change = sub.add_parser('change')
    change.add_argument('--library-root', type=Path, required=True)
    change.add_argument('--profile-path', required=True)
    change.add_argument('--request', type=Path, required=True)
    change.add_argument('--expected-sha256')
    change.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.command == 'validate':
        result = validate_profile(read_json(args.profile))
    elif args.command == 'select':
        result = select_rules(read_json(args.profile), read_json(args.context), args.as_of)
    else:
        result = maintain_profile(
            args.library_root,
            args.profile_path,
            read_json(args.request),
            args.execute,
            args.expected_sha256,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
