"""公开源码与发行包分别验收，不用无清单源码冒称冻结发行。"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_distribution import collect_sources
from distribution_smoke import verify_members
from prepare_public_checkout import prepare


class PublicDeliveryTests(unittest.TestCase):
    def test_checkout_alias_sync_and_verification_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, raw in collect_sources(ROOT).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            with self.assertRaises(ValueError):
                prepare(root, execute=True)
            with self.assertRaises(FileNotFoundError):
                verify_members(root)
            (root / '.git').mkdir()
            self.assertEqual(verify_members(root)['verification_mode'], 'current_git_checkout')
            (root / 'README.md').write_text('out of sync', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '公开入口'):
                verify_members(root)
            self.assertIn('README.md', prepare(root)['changed'])
            self.assertEqual((root / 'README.md').read_text(), 'out of sync')
            self.assertIn('README.md', prepare(root, execute=True)['changed'])
            self.assertEqual(prepare(root)['changed'], [])
            self.assertEqual(verify_members(root)['verification_mode'], 'current_git_checkout')

    def test_private_workspace_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            (root / 'README.md').write_text('private navigation', encoding='utf-8')
            with self.assertRaises(ValueError):
                prepare(root, execute=True)
            self.assertEqual((root / 'README.md').read_text(), 'private navigation')
