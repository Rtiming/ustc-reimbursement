#!/usr/bin/env python3
"""Prepare a new contact-only batch version through the formal planner; no case/UI writes.

CLI: --batch-index INDEX --contact-review REVIEW (repeatable) --output-dir NEW_DIR
     --library-root LIBRARY --profile-id REGISTERED_ID --as-of YYYY-MM-DD
Legacy indexes use their adjacent Mac归一化材料输入.json / Mac办理计划.json.
New indexes may declare source_input/source_plan as {path, sha256} instead.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from reimbursement_standards import _pairs, iso_date, safe_path


SCHEMA = 'ustc-contact-amendments-v1'
DETAILS = ('student_id', 'employee_id', 'phone', 'wechat', 'qq', 'email')
ROLES = {'responsible': 'responsible_person', 'filler': 'form_filler'}
STATUSES = {'确认': 'confirmed', '明确空白': 'confirmed_blank', '未辨明': 'pending'}
PROTECTED_FORM = ('expense_ids', 'invoice_ids', 'invoice_count', 'invoice_face_total',
                  'claim_amount', 'candidate_claim_total', 'summary', 'payments', 'export')


class ContactPreparationError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def _fail(code, detail):
    raise ContactPreparationError(code, detail)


def _path(service, value):
    if not isinstance(value, (str, Path)) or not Path(value).is_absolute():
        _fail('absolute_path_required', 'Use explicit local absolute material paths')
    path = Path(value)
    for root in service.material_roots:
        root = Path(root).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            return root
        return safe_path(root, relative.as_posix())
    _fail('material_root', 'A source/output is outside the authorized material roots')


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _read(service, path, expected=None):
    path = _path(service, path)
    if path.suffix.lower() != '.json' or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        _fail('invalid_json_source', 'Expected an authorized JSON file of at most 16MiB')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        _fail('source_sha_mismatch', 'A frozen input/plan SHA no longer matches')
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                       parse_constant=lambda _: _fail('invalid_json', 'Non-finite JSON value'))
    if not isinstance(value, dict):
        _fail('invalid_json_source', 'Expected a JSON object')
    return value, {'path': str(path), 'sha256': digest}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    return {'path': str(path), 'sha256': _digest(path)}


def _binding(plan, source, case_id, profile_id):
    selection = plan.get('rule_selection', {})
    evidence = plan.get('input_evidence', {})
    if (evidence != source or selection.get('case_id') != case_id
            or selection.get('profile_id') != profile_id or selection.get('status') != 'ready'):
        _fail('frozen_plan_binding', 'The frozen formal plan must bind this exact input, case and profile')


def _context(plan, plan_ref, case_id, profile_id):
    """Recover only unique scope constraints already matched by the old formal selection."""
    candidates = {}
    for rule in plan['rule_selection'].get('applied_rules', []):
        for key, value in rule.get('scope', {}).items():
            if key == 'all' or key == 'excluded_business' or value in (None, []):
                continue
            if key == 'included_business':
                key = 'business'
            values = value if isinstance(value, list) else [value]
            if any(not isinstance(x, str) or not x for x in values):
                _fail('scope_context_unknown', 'Cannot recover a scalar matched rule scope')
            candidates[key] = set(values) & candidates.get(key, set(values))
    context = {'case_id': case_id, 'selected_profile_ids': [profile_id],
               'selection_basis': 'Explicit profile selection; matched frozen formal plan '
                                  + plan_ref['path'] + '#sha256=' + plan_ref['sha256']}
    for key, values in candidates.items():
        if len(values) != 1:
            _fail('scope_context_unknown', 'Matched frozen scopes do not identify one context value')
        value = next(iter(values))
        if key == 'case_id' and value != case_id:
            _fail('scope_case_mismatch', 'Matched rule case differs from this input')
        context[key] = value
    return context


def _sequence(document):
    values = document.get('export_sequences')
    if (not isinstance(values, list) or len(values) != 1
            or type(values[0]) is not int or values[0] <= 0):
        _fail('sequence_ambiguous', 'This contact preparation requires one stable sequence per document')
    return values[0]


def _empty(value):
    return value is None or value == ''


def _blank(role):
    fields = [role.get(key, {}) for key in ('name', 'student_id', 'phone')]
    return (role.get('cell_explicitly_blank') is not False
            and all(x.get('status') == '明确空白' and _empty(x.get('value')) for x in fields)
            and all(x.get('field_present') is not False for x in fields))


def _selected(record):
    responsible, filler = record.get('responsible_person', {}), record.get('filler', {})
    if not record.get('original_sheet_source'):
        selected = None
    elif _blank(responsible):
        selected = None if _blank(filler) else 'filler'
    elif responsible.get('name', {}).get('status') in {'确认', '未辨明'}:
        selected = 'responsible'
    else:
        selected = None
    declared = record.get('selected_contact', {})
    if declared.get('role') != selected:
        _fail('role_selection_conflict', 'Selected role conflicts with responsible-first / explicit-blank evidence')
    if selected:
        role = responsible if selected == 'responsible' else filler
        for key in ('name', 'student_id', 'phone'):
            if declared.get(key) != role.get(key):
                _fail('selected_field_conflict', 'Selected fields must be the exact same-role reviewed fields')
    return selected


def _evidence(service, field, record, verified):
    source = field.get('source', {})
    original = record.get('original_sheet_source')
    if not original:
        if field.get('status') != '未辨明' or not _empty(field.get('value')):
            _fail('missing_original', 'Confirmed/blank fields require an identified original sheet')
        return None
    if (any(source.get(k) != original.get(k) for k in ('path', 'sha256', 'page'))
            or not re.fullmatch(r'[a-f0-9]{64}', str(source.get('sha256', '')))
            or type(source.get('page')) is not int or source['page'] < 1):
        _fail('field_source_binding', 'Every field must identify the same original SHA and positive page')
    path = _path(service, source['path'])
    expected = source['sha256']
    if verified.get(str(path)) != expected:
        if not path.is_file() or _digest(path) != expected:
            _fail('original_sha_mismatch', 'Reviewed original sheet bytes have changed')
        verified[str(path)] = expected
    return {**source, 'path': str(path)}


def _ref(source):
    return source['path'] + '#sha256=' + source['sha256'] + '&page=' + str(source['page'])


def _same_original_ref(value, source):
    if not isinstance(value, str):
        return False
    path, marker, tail = value.partition('#sha256=')
    return (bool(marker) and tail[:64] == source['sha256']
            and Path(path) == Path(source['path']))


def _channels(record, selected):
    """The review schema's extra channels belong only to its selected role."""
    if not selected:
        return {}
    details = record.get('contact_channel_details', {})
    result = {k: v for k, v in details.items() if k in ('wechat', 'qq', 'email')}
    aggregate = record.get('other_explicit_contact_channels', {})
    if aggregate.get('status') == '明确空白' and aggregate.get('value') in (None, [], ''):
        for key in ('wechat', 'qq', 'email'):
            result.setdefault(key, {**aggregate, 'value': None})
    elif aggregate.get('status') == '确认':
        for row in aggregate.get('value', []):
            if not isinstance(row, dict) or row.get('type') not in ('wechat', 'qq', 'email'):
                _fail('invalid_contact_channel', 'Extra contact channel lacks a supported explicit type')
            key = row['type']
            candidate = {**aggregate, 'value': row.get('value'), 'raw': row.get('value')}
            if key in result and result[key].get('value') != candidate['value']:
                _fail('contact_channel_conflict', 'Same-role contact channel evidence conflicts')
            result.setdefault(key, candidate)
    return result


def _amend(service, document, record, verified):
    result = copy.deepcopy(document)
    clarification = result.get('clarification')
    if not isinstance(clarification, dict):
        _fail('clarification_required', 'Keep the existing normalized clarification object')
    selected = _selected(record)
    channels = _channels(record, selected)
    gaps, warnings, sources = [], [], []
    for review_role, target_role in ROLES.items():
        reviewed = record.get('responsible_person' if review_role == 'responsible' else 'filler', {})
        target = clarification.get(target_role)
        if not isinstance(target, dict):
            _fail('role_input_missing', 'Normalized input must retain both original role objects')
        name = reviewed.get('name', {})
        if (not isinstance(name, dict) or name.get('status') not in STATUSES
                or name.get('status') == '确认' and (not isinstance(name.get('value'), str) or not name['value'].strip())):
            _fail('invalid_contact_name_review', 'The same-role name must have an explicit valid review status/value')
        source = _evidence(service, name, record, verified)
        if source:
            sources.append(source)
            if not any(_same_original_ref(ref, source) for ref in target.get('source_refs', [])):
                _fail('role_original_mismatch', 'Reviewed original is not a frozen source of this same contact role')
        if name.get('status') == '确认' and target.get('name') is not None and target['name'] != name.get('value'):
            _fail('contact_name_conflict', 'Reviewed contact name differs from the frozen same-role name')
        if target.get('blank') is True and not _blank(reviewed):
            _fail('contact_blank_conflict', 'Frozen role marked blank lacks whole-role blank review evidence')
        if _blank(reviewed) and (target.get('name') is not None
                                or target.get('status') == 'confirmed' and target.get('blank') is not True):
            _fail('contact_blank_conflict', 'Do not alter frozen name/blank/status during detail preparation')
        if target.get('status') != 'confirmed' or (not target.get('name') and target.get('blank') is not True):
            gaps.append({'field': target_role, 'code': 'FROZEN_ROLE_NOT_CONFIRMED',
                         'detail': 'Original contact name/blank/status stays unchanged and is not promoted by detail evidence',
                         'blocking': review_role == selected})
        if name.get('status') == '未辨明':
            gaps.append({'field': target_role + '.name', 'code': 'CONTACT_NAME_UNRESOLVED',
                         'detail': 'Original name is unresolved; it is not a blank-role fallback',
                         'blocking': review_role == selected})
        for key in DETAILS:
            field = reviewed.get(key)
            if field is None and review_role == selected:
                field = channels.get(key)
            if field is None:
                gaps.append({'field': target_role + '.' + key, 'code': 'not_reviewed',
                             'detail': 'No field evidence; omitted without declaring blank or adding a pending gate',
                             'blocking': False})
                continue
            if not isinstance(field, dict) or field.get('status') not in STATUSES:
                _fail('invalid_field_status', 'Only explicit confirmed / blank / unresolved field statuses are supported')
            source = _evidence(service, field, record, verified)
            status, value = field['status'], field.get('value')
            if status == '确认':
                if (not isinstance(value, str) or not value.strip()
                        or re.search(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]', value)):
                    _fail('invalid_contact_value', 'Confirmed contact values must be original nonempty single-line strings')
            elif status == '明确空白' and not _empty(value):
                _fail('blank_value_conflict', 'A blank field cannot contain a value')
            else:
                value = None
            if target.get(key) is not None and target[key] != value:
                _fail('frozen_detail_conflict', 'Do not replace a conflicting frozen contact detail')
            target[key] = value
            target[key + '_status'] = STATUSES[status]
            target[key + '_source_refs'] = [_ref(source)] if source else []
            if source:
                sources.append(source)
                target['source_refs'] = list(dict.fromkeys([*target.get('source_refs', []), _ref(source)]))
            if status == '未辨明':
                gaps.append({'field': target_role + '.' + key, 'code': 'CONTACT_DETAIL_UNRESOLVED',
                             'detail': 'Written/attributed value remains unresolved; no guessed value or blank',
                             'blocking': review_role == selected})
            if key == 'phone' and value is not None and not re.fullmatch(r'\d{11}', value):
                warnings.append({'code': 'PHONE_NONSTANDARD_FORMAT', 'field': target_role + '.phone',
                                 'detail': 'Original confirmed value preserved verbatim; format/dialability not verified'})
    # All fields outside these two detail objects remain exactly the source input.
    expected = copy.deepcopy(document)
    for role in ROLES.values():
        expected['clarification'][role] = result['clarification'][role]
        for key in ('name', 'status', 'blank'):
            if result['clarification'][role].get(key) != document['clarification'][role].get(key):
                _fail('contact_identity_changed', 'Contact name/blank/status may not be rewritten')
    if result != expected:
        _fail('non_contact_change', 'Only normalized contact detail fields may change')
    unique = {json.dumps(s, sort_keys=True): s for s in sources}
    return result, gaps, warnings, list(unique.values())


def _formal(service, source, target, old, old_ref, library_root, profile_id, as_of, profile_sha):
    context = _context(old, old_ref, json.loads(Path(source['path']).read_text())['case_id'], profile_id)
    receipt = service.prepare_batch_file(str(library_root), profile_id, context, as_of,
                                         source['path'], str(target))
    plan, evidence = _read(service, target, receipt.get('output_sha256'))
    if (plan.get('input_evidence') != source or receipt.get('input_evidence') != source
            or plan.get('rule_selection', {}).get('profile_sha256') != profile_sha
            or plan.get('system_write') is not False):
        _fail('formal_plan_binding', 'Formal plan input/profile/read-only evidence did not match')
    return plan, evidence


def _one_form(plan, document_id):
    docs = (plan.get('plan') or {}).get('documents', [])
    found = [d for d in docs if d.get('document_id') == document_id]
    if not found or not found[0].get('forms'):
        return None
    if len(found) != 1 or len(found[0]['forms']) != 1:
        _fail('form_scope_ambiguous', 'The indexed case must bind exactly one formal planned form')
    return found[0]['forms'][0]


def prepare_contact_amendment(service, *, batch_index, contact_reviews, output_dir,
                              library_root, profile_id, as_of):
    """Create a fresh master + per-indexed-case planning version, never a finance action."""
    iso_date(as_of)
    if not isinstance(contact_reviews, (list, tuple)) or not contact_reviews:
        _fail('contact_reviews_required', 'Give the frozen contact review files explicitly')
    index, index_ref = _read(service, batch_index)
    base = Path(index_ref['path']).parent
    input_decl = index.get('source_input', {'path': str(base / 'Mac归一化材料输入.json')})
    plan_decl = index.get('source_plan', {'path': str(base / 'Mac办理计划.json')})
    data, data_ref = _read(service, input_decl['path'], input_decl.get('sha256'))
    old, old_ref = _read(service, plan_decl['path'], plan_decl.get('sha256'))
    if data.get('schema') != 'ustc-reimbursement-batch-input-v1' or data.get('policy'):
        _fail('normalized_input_required', 'Use the original policy-free normalized input')
    _binding(old, data_ref, data.get('case_id'), profile_id)
    output = _path(service, output_dir)
    if output.exists():
        _fail('output_exists', 'Use a new output directory; never overwrite or resume partial output')
    library = _path(service, library_root)
    profile = service.standards_read(str(library), profile_id)
    profile_sha = profile['sha256']
    reviews, refs, verified = {}, [], {}
    for path in contact_reviews:
        review, ref = _read(service, path)
        if review.get('schema') != 'zhouxiang-contact-recheck-v1' or not isinstance(review.get('records'), list):
            _fail('contact_review_schema', 'Use the frozen field-level contact review schema')
        refs.append(ref)
        for position, row in enumerate(review['records']):
            gid = row.get('group_id')
            if not isinstance(gid, str) or gid in reviews:
                _fail('duplicate_contact_review', 'Each document must have one unambiguous review record')
            reviews[gid] = (row, {**ref, 'json_pointer': '/records/' + str(position)})
    documents = data.get('documents', [])
    ids = [d.get('document_id') for d in documents]
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(reviews):
        _fail('review_coverage', 'Contact reviews must cover exactly the master normalized documents')
    amended = copy.deepcopy(data)
    details = {}
    for number, doc in enumerate(documents):
        gid = doc['document_id']; row, ref = reviews[gid]
        if row.get('sequence') != _sequence(doc):
            _fail('review_sequence_mismatch', 'Document and stable original sequence differ')
        updated, gaps, warnings, evidence = _amend(service, doc, row, verified)
        amended['documents'][number] = updated
        details[gid] = {'gaps': gaps, 'warnings': warnings, 'evidence_refs': evidence, 'review_refs': [ref]}
    indexed, sequences, case_ids = [], set(), set()
    for row in index.get('documents', []):
        gid, seq = row.get('document_id'), row.get('index')
        if gid not in reviews or seq != reviews[gid][0].get('sequence') or seq in sequences:
            _fail('index_binding', 'Index document/sequence is missing, repeated or inconsistent')
        sequences.add(seq)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', str(row.get('case_id', ''))):
            _fail('index_case_required', 'Each indexed row must retain its explicit case identity')
        if row['case_id'] in case_ids:
            _fail('duplicate_index_case', 'Do not prepare multiple amendments to the same case')
        case_ids.add(row['case_id'])
        if any(not re.fullmatch(r'[a-f0-9]{64}', str(row.get(key, '')))
               for key in ('normalized_input_sha256', 'official_plan_sha256')):
            _fail('frozen_sha_required', 'Every indexed single input and formal plan needs its frozen SHA')
        single, single_ref = _read(service, row['normalized_input'], row.get('normalized_input_sha256'))
        single_old, single_old_ref = _read(service, row['official_plan'], row.get('official_plan_sha256'))
        if (single.get('case_id') != row['case_id'] or len(single.get('documents', [])) != 1
                or single['documents'][0].get('document_id') != gid or single.get('policy')):
            _fail('single_input_binding', 'Indexed input must bind only this original case/document')
        _binding(single_old, single_ref, row['case_id'], profile_id)
        updated, gaps, warnings, evidence = _amend(service, single['documents'][0], reviews[gid][0], verified)
        single_new = copy.deepcopy(single); single_new['documents'][0] = updated
        indexed.append((row, single_new, single_old, single_old_ref, gaps, warnings, evidence, single_ref))
    if not indexed:
        _fail('empty_index', 'The formal index must contain cases')
    output.mkdir(parents=True, mode=0o700)
    new_input = _write(output / 'normalized_input.json', amended)
    new_plan, new_plan_ref = _formal(service, new_input, output / 'official_plan.json', old, old_ref,
                                       library, profile_id, as_of, profile_sha)
    new_index = copy.deepcopy(index)
    amendments = []
    for pos, (row, single, previous, previous_ref, gaps, warnings, evidence, single_ref) in enumerate(indexed):
        item = output / 'items' / str(row['index']).zfill(3)
        source = _write(item / 'normalized_input.json', single)
        plan, plan_ref = _formal(service, source, item / 'official_plan.json', previous, previous_ref,
                                library, profile_id, as_of, profile_sha)
        before, after = _one_form(previous, row['document_id']), _one_form(plan, row['document_id'])
        if not before:
            _fail('original_form_missing', 'The frozen indexed plan has no unique original form')
        if any(row.get(k) != before.get(k) for k in ('summary', 'claim_amount', 'remarks')):
            _fail('index_frozen_form_binding', 'Frozen index summary/claim/remarks differs from its original formal plan')
        if after:
            if any(before.get(k) != after.get(k) for k in PROTECTED_FORM):
                _fail('planned_financial_change', 'Replanning changed a protected summary/invoice/payment/amount/export field')
            if (row.get('summary') != after.get('summary') or row.get('claim_amount') != after.get('claim_amount')):
                _fail('index_financial_binding', 'Index summary/claim must equal its frozen formal planned form')
        else:
            gaps.append({'field': 'formal_plan', 'code': 'FORM_NOT_PRODUCED', 'detail': 'Formal owner returned no form', 'blocking': True})
        after_remarks = after.get('remarks') if after else None
        new_row = copy.deepcopy(row)
        new_row.update(remarks=after_remarks, normalized_input=source['path'], normalized_input_sha256=source['sha256'],
                       official_plan=plan_ref['path'], official_plan_sha256=plan_ref['sha256'])
        new_index['documents'][pos] = new_row
        amendments.append({'sequence': row['index'], 'document_id': row['document_id'], 'case_id': row['case_id'],
                           'serial': row.get('serial'), 'before_remarks': row.get('remarks'), 'after_remarks': after_remarks,
                           'before_remarks_source': 'frozen_index_not_live',
                           'ready_for_controlled_remarks_update': bool(after_remarks) and not any(g.get('blocking') for g in gaps),
                           'normalized_input': source, 'official_plan': plan_ref, 'evidence_refs': evidence,
                           'review_refs': details[row['document_id']]['review_refs'], 'warnings': warnings, 'gaps': gaps,
                           'financial_fields_unchanged': True, 'form_modified': False})
    new_index.update(source_input=new_input, source_plan=new_plan_ref,
                     previous_version=index.get('version'), version=output.name,
                     rules_profile_revision=profile['profile']['revision'],
                     rules_profile_sha256=profile_sha, planning_only=True, case_modified=False, system_write=False)
    new_index_ref = _write(output / 'batch_index.json', new_index)
    manifest = {'schema': SCHEMA, 'status': 'prepared_local_only', 'system_write': False, 'form_modified': False,
                'allowed_form_fields': ['t-cl_abst'], 'batch_index': index_ref, 'source_input': data_ref,
                'source_plan': old_ref, 'contact_reviews': refs, 'new_input': new_input, 'new_plan': new_plan_ref,
                'new_index': new_index_ref, 'library_root': str(library), 'profile': {'profile_id': profile_id,
                    'revision': profile['profile']['revision'], 'sha256': profile_sha}, 'as_of': as_of,
                'amendments': amendments, 'gaps': [{'document_id': gid, **gap} for gid, value in details.items() for gap in value['gaps']],
                'warnings': [{'document_id': gid, **warning} for gid, value in details.items() for warning in value['warnings']],
                'source_single_inputs': [x[7] for x in indexed],
                'source_single_plans': [x[3] for x in indexed],
                'planning_status': new_plan.get('status'), 'planning_needed_inputs': new_plan.get('needed_inputs', []),
                'source_originals_reverified': [{'path': path, 'sha256': digest} for path, digest in sorted(verified.items())],
                'current_pointer_modified': False, 'browser_started': False, 'pdf_generated': False,
                'native_identity_approval_submission_or_payment_verified': False}
    for path, digest in verified.items():
        if _digest(Path(path)) != digest:
            _fail('original_sha_mismatch', 'Original source drifted during preparation; output is incomplete, not published')
    saved = _write(output / 'contact_amendments.json', manifest)
    return {'ok': True, 'status': 'prepared_local_only', 'manifest': saved, 'new_index': new_index_ref,
            'document_count': len(documents), 'indexed_case_count': len(amendments),
            'controlled_remarks_candidates': sum(x['ready_for_controlled_remarks_update'] for x in amendments),
            'system_write': False, 'form_modified': False}


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--batch-index', required=True)
    value.add_argument('--contact-review', action='append', required=True)
    value.add_argument('--output-dir', required=True)
    value.add_argument('--library-root', required=True)
    value.add_argument('--profile-id', required=True)
    value.add_argument('--as-of', required=True)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    from assistant_api import AssistantService
    # Explicit batch/review locations establish the material workspace, not a whole drive.
    parents = [str(Path(p).absolute().parent) for p in [args.batch_index, *args.contact_review]]
    common = Path(os.path.commonpath(parents))
    if common == Path(common.anchor):
        _fail('material_root', 'Inputs must share a bounded material workspace')
    service = AssistantService(material_roots=[common, Path(args.library_root)])
    result = prepare_contact_amendment(service, batch_index=args.batch_index, contact_reviews=args.contact_review,
        output_dir=args.output_dir, library_root=args.library_root, profile_id=args.profile_id, as_of=args.as_of)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'status': 'blocked', 'code': getattr(error, 'code', 'invalid_input'),
                          'detail': str(error), 'system_write': False}, ensure_ascii=False))
        sys.exit(2)
