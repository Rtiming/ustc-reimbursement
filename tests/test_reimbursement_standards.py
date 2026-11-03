"""规范版本、适用范围及本地维护边界；全部使用虚构资料。"""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from reimbursement_standards import (maintain_profile, preview_change, read_json,
                                     safe_path, select_rules, validate_profile)


def profile():
    return {'schema_version': 1, 'profile_id': 'demo', 'title': '合成专项', 'revision': '0.1.0',
            'status': 'collecting', 'inherits': ['common'], 'scope': {}, 'rules': [],
            'sources': [{'source_id': 'user-1', 'kind': 'user_instruction', 'location': '合成消息', 'date': '2026-10-22'}]}


def rule():
    return {'rule_id': 'summary.contact', 'revision': '1.0.0', 'statement': '底部联系人放摘要',
            'kind': 'user_workflow', 'status': 'confirmed', 'scope': {'institution': '合成学校'},
            'effect': {'action': 'required', 'target': 'summary.contact_location', 'value': 'abstract'},
            'source_refs': ['user-1'], 'confirmation': {'by': 'user', 'at': '2026-10-22', 'source_refs': ['user-1']},
            'valid_from': None, 'valid_until': None, 'supersedes': [], 'exception_to': []}


def change():
    return {'action': 'add', 'actor': 'user', 'at': '2026-10-22', 'reason': '合成规则登记',
            'source_refs': ['user-1'], 'rule': rule()}


def context():
    return {'case_id': 'demo-case', 'selected_profile_ids': ['demo'], 'selection_basis': '合成材料明确匹配',
            'institution': '合成学校'}


class StandardsTests(unittest.TestCase):
    def test_empty_collecting_profile_is_compatible(self):
        self.assertEqual(validate_profile(profile())['status'], 'ready')
        self.assertEqual(select_rules(profile(), context(), '2026-10-22')['applied_rules'], [])

    def test_add_is_pure_and_revision_increases(self):
        old = profile()
        result = preview_change(old, change())
        self.assertEqual(old['rules'], [])
        self.assertEqual(result['profile']['revision'], '0.1.1')
        self.assertFalse(result['finance_write_authorized'])
        self.assertEqual(len(select_rules(result['profile'], context(), '2026-10-22')['applied_rules']), 1)

    def test_confirmation_and_source_are_required(self):
        for field, value in [('confirmation', None), ('source_refs', ['missing']), ('kind', 'unverified_extract')]:
            p = profile()
            r = rule()
            r[field] = value
            p['rules'] = [r]
            self.assertEqual(validate_profile(p)['status'], 'blocked', field)

    def test_no_automatic_profile_binding(self):
        p = preview_change(profile(), change())['profile']
        result = select_rules(p, {'case_id': 'demo-case', 'institution': '合成学校'}, '2026-10-22')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['applied_rules'], [])

    def test_unconfirmed_rules_never_apply(self):
        for status in ['proposed', 'disputed', 'retired', 'superseded']:
            p = preview_change(profile(), change())['profile']
            p['rules'][0]['status'] = status
            result = select_rules(p, context(), '2026-10-22')
            self.assertEqual(result['applied_rules'], [])
            self.assertEqual(result['skipped_rules'][0]['reason'], status)

    def test_missing_scope_is_blocked_but_different_scope_is_skipped(self):
        p = preview_change(profile(), change())['profile']
        c = context()
        del c['institution']
        self.assertEqual(select_rules(p, c, '2026-10-22')['status'], 'blocked')
        c['institution'] = '另一所合成学校'
        result = select_rules(p, c, '2026-10-22')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['applied_rules'], [])

    def test_validity_is_business_date_not_file_time(self):
        p = preview_change(profile(), change())['profile']
        p['rules'][0]['valid_until'] = '2026-09-30'
        self.assertEqual(select_rules(p, context(), '2026-10-22')['applied_rules'], [])
        self.assertEqual(len(select_rules(p, context(), '2026-09-30')['applied_rules']), 1)

    def test_conflict_does_not_choose_latest_or_strictest(self):
        p = preview_change(profile(), change())['profile']
        other = rule()
        other['rule_id'] = 'summary.other'
        other['effect']['value'] = 'remarks'
        p['rules'].append(other)
        result = select_rules(p, context(), '2026-10-22')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['applied_rules'], [])
        self.assertEqual(result['issues'][0]['code'], 'RULE_CONFLICT')

    def test_revise_requires_exact_predecessor_and_increasing_version(self):
        p = preview_change(profile(), change())['profile']
        request = change()
        request['action'] = 'revise'
        request['rule']['revision'] = '1.0.1'
        with self.assertRaisesRegex(ValueError, 'supersedes'):
            preview_change(p, request)
        request['rule']['supersedes'] = [{'rule_id': 'summary.contact', 'revision': '1.0.0', 'reason': '修订', 'source_refs': ['user-1']}]
        new = preview_change(p, request)
        self.assertEqual(new['profile']['rules'][0]['revision'], '1.0.1')
        self.assertEqual(p['rules'][0]['revision'], '1.0.0')
        request['rule']['revision'] = '1.0.0'
        with self.assertRaisesRegex(ValueError, '递增'):
            preview_change(p, request)

    def test_retire_preserves_record_with_new_revision(self):
        p = preview_change(profile(), change())['profile']
        request = {k: v for k, v in change().items() if k != 'rule'}
        request.update(action='retire', rule_id='summary.contact')
        result = preview_change(p, request)['profile']
        self.assertEqual(len(result['rules']), 1)
        self.assertEqual(result['rules'][0]['status'], 'retired')
        self.assertEqual(result['rules'][0]['supersedes'][0]['revision'], '1.0.0')
        self.assertEqual(select_rules(result, context(), '2026-10-22')['applied_rules'], [])

    def test_source_is_immutable(self):
        request = change()
        request['sources'] = [{'source_id': 'user-1', 'kind': 'user_instruction', 'location': '篡改来源'}]
        with self.assertRaisesRegex(ValueError, '来源不可'):
            preview_change(profile(), request)

    def test_explicit_exception_applies_only_in_its_own_scope(self):
        p = preview_change(profile(), change())['profile']
        other = rule()
        other.update(rule_id='case.exception', scope={'case_id': 'demo-case'})
        other['effect']['value'] = 'alternate_abstract_order'
        other['exception_to'] = [{'rule_id': 'summary.contact', 'revision': '1.0.0',
                                  'reason': '仅本案已确认例外', 'source_refs': ['user-1']}]
        p['rules'].append(other)
        result = select_rules(p, context(), '2026-10-22')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual([x['rule_id'] for x in result['applied_rules']], ['case.exception'])
        another = context() | {'case_id': 'different-case'}
        self.assertEqual([x['rule_id'] for x in select_rules(p, another, '2026-10-22')['applied_rules']], ['summary.contact'])

    def test_circular_replacement_is_blocked(self):
        p = preview_change(profile(), change())['profile']
        other = rule()
        other['rule_id'] = 'other-rule'
        other['supersedes'] = [{'rule_id': 'summary.contact', 'revision': '1.0.0', 'reason': '合成替代', 'source_refs': ['user-1']}]
        p['rules'][0]['supersedes'] = [{'rule_id': 'other-rule', 'revision': '1.0.0', 'reason': '合成反向替代', 'source_refs': ['user-1']}]
        p['rules'].append(other)
        result = select_rules(p, context(), '2026-10-22')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['applied_rules'], [])
        self.assertIn('CYCLIC_REPLACEMENT', [x['code'] for x in result['issues']])

    def test_self_replacement_and_bad_scope_are_rejected(self):
        p = preview_change(profile(), change())['profile']
        p['rules'][0]['scope'] = {'included_business': '活动'}
        self.assertEqual(validate_profile(p)['status'], 'blocked')
        p['rules'][0]['scope'] = {'all': True}
        p['rules'][0]['supersedes'] = [{'rule_id': 'summary.contact', 'revision': '1.0.0', 'reason': '错误自替代', 'source_refs': ['user-1']}]
        self.assertEqual(validate_profile(p)['status'], 'blocked')

    def test_audit_snapshot_cannot_be_modified_through_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, '审计快照'):
                maintain_profile(temp, '.standards-history/old/before.json', change())

    def test_preview_has_no_filesystem_mutation_then_commit_has_immutable_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'rules.json')
            original = json.dumps(profile(), ensure_ascii=False).encode()
            path.write_bytes(original)
            preview = maintain_profile(temp, 'rules.json', change())
            self.assertFalse(preview['executed'])
            self.assertEqual(list(Path(temp).iterdir()), [path])
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, 'expected_sha256'):
                maintain_profile(temp, 'rules.json', change(), execute=True)
            result = maintain_profile(temp, 'rules.json', change(), True, preview['before_sha256'])
            self.assertTrue(result['executed'])
            audit = Path(result['audit_path'])
            self.assertEqual((audit / 'before.json').read_bytes(), original)
            self.assertEqual((audit / 'after.json').read_bytes(), path.read_bytes())
            self.assertTrue((audit / 'committed.json').is_file())
            self.assertEqual(result['after_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertFalse(Path(temp, '.standards-history', 'write.lock').exists())

    def test_stale_preview_and_live_lock_fail_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'rules.json')
            path.write_text(json.dumps(profile()), encoding='utf-8')
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'SHA'):
                maintain_profile(temp, 'rules.json', change(), True, '0' * 64)
            history = Path(temp, '.standards-history')
            history.mkdir()
            (history / 'write.lock').write_text('another task')
            with self.assertRaises(FileExistsError):
                maintain_profile(temp, 'rules.json', change(), True, hashlib.sha256(before).hexdigest())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual((history / 'write.lock').read_text(), 'another task')

    def test_cross_platform_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for value in ['../rules.json', '..\\rules.json', '/tmp/rules.json', 'C:\\rules.json', 'x/./rules.json',
                          'x/../rules.json', '//server/share/a.json', 'x.json:stream', 'x. /rules.json']:
                with self.subTest(path=value), self.assertRaises(ValueError):
                    safe_path(temp, value)

    def test_symlink_escape_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as elsewhere:
            link = Path(temp, 'link')
            try:
                link.symlink_to(elsewhere, target_is_directory=True)
            except OSError:
                self.skipTest('该Windows环境未授予创建符号链接权限')
            with self.assertRaisesRegex(ValueError, '链接|重解析'):
                safe_path(temp, 'link/rules.json')

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp, 'input.json')
            p.write_text('{"revision":"1.0.0","revision":"2.0.0"}')
            with self.assertRaisesRegex(ValueError, '重复键'):
                read_json(p)


if __name__ == '__main__':
    unittest.main()
