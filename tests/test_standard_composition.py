"""通用/专项组合使用合成规则；不假定下级有权覆盖上级。"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import AssistantService, atomic_json, read_json
from assistant_guidance import GuidanceError
from reimbursement_standards import compose_profiles, select_rules


def rule(value, relation=None):
    return {
        'rule_id': 'limit',
        'revision': '1.0.0',
        'statement': '合成材料数量规则',
        'kind': 'user_workflow',
        'status': 'confirmed',
        'scope': {'all': True},
        'effect': {'target': 'batch.max_invoices_per_form', 'action': 'required', 'value': value},
        'source_refs': ['message'],
        'confirmation': {'by': 'synthetic', 'at': '2026-10-24', 'source_refs': ['message']},
        'supersedes': [],
        'exception_to': [relation] if relation else [],
    }


def profile(pid, rules, parents=()):
    return {
        'schema_version': 1,
        'profile_id': pid,
        'title': '合成模式',
        'revision': '1.0.0',
        'extends': list(parents),
        'rules': rules,
        'sources': [{'source_id': 'message', 'kind': 'user_instruction', 'location': '合成记录'}],
    }


class CompositionTests(unittest.TestCase):
    def select(self, profiles):
        composed, order = compose_profiles(profiles, 'club')
        result = select_rules(
            composed,
            {'case_id': 'demo', 'selected_profile_ids': ['club'], 'selection_basis': '合成选择'},
            '2026-10-24',
        )
        return result, order

    def test_conflicting_child_does_not_silently_override_common(self):
        result, order = self.select(
            {
                'common': profile('common', [rule(50)]),
                'club': profile('club', [rule(40)], ['common']),
            }
        )
        self.assertEqual(order, ['common', 'club'])
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['applied_rules'])
        self.assertIn('RULE_CONFLICT', [i['code'] for i in result['issues']])

    def test_explicit_scoped_exception_keeps_qualified_origin(self):
        relation = {
            'profile_id': 'common',
            'rule_id': 'limit',
            'revision': '1.0.0',
            'reason': '合成本案有来源例外',
            'source_refs': ['message'],
        }
        child = rule(40, relation)
        child['scope'] = {'case_id': 'demo'}
        result, _ = self.select(
            {'common': profile('common', [rule(50)]), 'club': profile('club', [child], ['common'])}
        )
        self.assertEqual(result['status'], 'ready')
        self.assertEqual([r['rule_id'] for r in result['applied_rules']], ['club/limit'])
        self.assertEqual(result['applied_rules'][0]['origin_profile_id'], 'club')
        self.assertFalse(result['finance_write_authorized'])

    def test_cycles_missing_parents_and_self_parent_are_rejected(self):
        for profiles in (
            {'club': profile('club', [], ['missing'])},
            {'club': profile('club', [], ['common']), 'common': profile('common', [], ['club'])},
            {'club': profile('club', [], ['club'])},
        ):
            with self.assertRaises(ValueError):
                compose_profiles(profiles, 'club')

    def test_legacy_inherits_is_not_automatically_enabled(self):
        legacy = profile('club', [])
        legacy.pop('extends')
        legacy['inherits'] = ['missing']
        _, order = compose_profiles({'club': legacy}, 'club')
        self.assertEqual(order, ['club'])

    def test_diamond_parent_is_loaded_once_and_candidate_is_skipped(self):
        candidate = rule(99)
        candidate['status'] = 'proposed'
        profiles = {
            'common': profile('common', [rule(50)]),
            'scene': profile('scene', [], ['common']),
            'club': profile('club', [candidate], ['common', 'scene']),
        }
        result, order = self.select(profiles)
        self.assertEqual(order, ['common', 'scene', 'club'])
        self.assertEqual([r['rule_id'] for r in result['applied_rules']], ['common/limit'])
        self.assertEqual(result['skipped_rules'][0]['reason'], 'proposed')


class LibraryInitTests(unittest.TestCase):
    def test_profile_append_and_rule_revision_share_the_same_library_lock(self):
        self.service.standards_init(str(self.library), 'my_club', '示例', execute=True)
        preview = self.service.standards_add_profile(
            str(self.library), 'next_club', '新增', ['common']
        )
        lock = self.library / '.standards-history/write.lock'
        lock.parent.mkdir()
        lock.write_bytes(b'original-unresolved-lock')
        with self.assertRaises(FileExistsError):
            self.service.standards_add_profile(
                str(self.library),
                'next_club',
                '新增',
                ['common'],
                True,
                preview['before_registry_sha256'],
            )
        current = self.service.standards_read(str(self.library), 'my_club')
        change = {
            'action': 'add',
            'actor': 'synthetic',
            'at': '2026-10-24',
            'reason': '合成规则追加',
            'source_refs': ['message'],
            'sources': profile('my_club', [])['sources'],
            'rule': rule(50),
        }
        with self.assertRaises(FileExistsError):
            self.service.standards_change(
                str(self.library), 'my_club', change, True, current['sha256']
            )
        self.assertEqual(lock.read_bytes(), b'original-unresolved-lock')
        self.assertFalse((self.library / 'profiles/next_club.json').exists())

    def test_append_profile_uses_registry_revision_and_preserves_originals(self):
        self.service.standards_init(str(self.library), 'my_club', '原专项', execute=True)
        original = self.library / 'profiles/my_club.json'
        original_bytes = original.read_bytes()
        preview = self.service.standards_add_profile(
            str(self.library), 'new_project', '新专项', ['common']
        )
        self.assertFalse((self.library / 'profiles/new_project.json').exists())
        with self.assertRaisesRegex(ValueError, 'registry SHA'):
            self.service.standards_add_profile(
                str(self.library), 'new_project', '新专项', ['common'], True, '0' * 64
            )
        result = self.service.standards_add_profile(
            str(self.library),
            'new_project',
            '新专项',
            ['common'],
            True,
            preview['before_registry_sha256'],
        )
        self.assertTrue(result['executed'])
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(
            self.service.standards_read(str(self.library), 'new_project')['profile']['extends'],
            ['common'],
        )
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.service.standards_add_profile(str(self.library), 'new_project', '不可覆盖', [])
        with self.assertRaisesRegex(ValueError, '父模式未登记'):
            self.service.standards_add_profile(
                str(self.library), 'unknown_parent', '待核', ['missing']
            )

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = AssistantService(self.root / 'tool', [self.root])
        self.library = self.root / '私人规范'

    def test_preview_does_not_write_and_created_library_is_usable(self):
        args = (str(self.library), 'my_club', '我的专项')
        preview = self.service.standards_init(*args)
        self.assertFalse(preview['executed'])
        self.assertFalse(self.library.exists())
        created = self.service.standards_init(*args, execute=True)
        self.assertTrue(created['executed'])
        self.assertFalse(created['system_write'])
        selected = self.service.standards_select(
            str(self.library),
            'my_club',
            {
                'case_id': 'demo',
                'selected_profile_ids': ['my_club'],
                'selection_basis': '合成用户选择',
            },
            '2026-10-24',
        )
        self.assertEqual(selected['composition_order'], ['common', 'my_club'])
        self.assertEqual(len(selected['profile_sources']), 2)
        self.assertFalse(selected['applied_rules'])
        prepared = self.service.prepare_batch(
            str(self.library),
            'my_club',
            {'case_id': 'demo', 'selected_profile_ids': ['my_club'], 'selection_basis': '合成选择'},
            '2026-10-24',
            {'case_id': 'demo', 'documents': []},
        )
        self.assertEqual(prepared['status'], 'blocked')
        self.assertIsNone(prepared['plan'])
        before = (self.library / 'registry.json').read_bytes()
        with self.assertRaises(GuidanceError):
            self.service.standards_init(*args, execute=True)
        self.assertEqual((self.library / 'registry.json').read_bytes(), before)

    def test_escaping_or_symlinked_library_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.standards_init(
                str(self.root / '.standards-history/new'), 'my_club', '示例', execute=True
            )
        for path in (self.root.parent / 'outside', self.root / '..' / 'outside'):
            with self.assertRaises((ValueError, GuidanceError)):
                self.service.standards_init(str(path), 'my_club', '示例', execute=True)
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.service.standards_init(str(link / 'new'), 'my_club', '示例', execute=True)

    def test_api_preserves_parent_sha_and_refuses_missing_parent(self):
        self.service.standards_init(str(self.library), 'my_club', '示例', execute=True)
        path = self.library / 'profiles/common.json'
        common = read_json(path)
        common['rules'] = [rule(50)]
        common['sources'] = profile('common', [])['sources']
        atomic_json(path, common)
        context = {
            'case_id': 'demo',
            'selected_profile_ids': ['my_club'],
            'selection_basis': '显式选择',
        }
        result = self.service.standards_select(str(self.library), 'my_club', context, '2026-10-24')
        self.assertEqual(result['applied_rules'][0]['rule_id'], 'common/limit')
        self.assertTrue(all(len(p['sha256']) == 64 for p in result['profile_sources']))
        child = self.library / 'profiles/my_club.json'
        data = read_json(child)
        data['extends'] = ['unknown']
        atomic_json(child, data)
        with self.assertRaises(GuidanceError):
            self.service.standards_select(str(self.library), 'my_club', context, '2026-10-24')


if __name__ == '__main__':
    unittest.main()
