import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from assistant_api import AssistantService, APIError
import check_public_docs as docs
from build_distribution import collect_sources


class PublicDocsTests(unittest.TestCase):
    def test_real_bilingual_topics_and_links_are_complete(self):
        result = docs.check(ROOT)
        self.assertTrue(result['ok'], result['issues'])
        service = AssistantService(ROOT)
        for topic in docs.DOCS:
            for language in ('zh', 'en'):
                value = service.guide(topic, language)
                self.assertEqual(value['language'], language)
                self.assertGreater(len(value['text']), 40)
        with self.assertRaises(APIError): service.guide('start', '../private')

    def test_missing_translation_broken_link_and_write_example_fail(self):
        members = collect_sources(ROOT)
        broken = dict(members); broken.pop(docs.DOCS_EN['configuration'])
        self.assertFalse(docs.check_members(broken)['ok'])
        broken = dict(members); broken['docs/broken.md'] = b'[missing](not-present.md)'
        self.assertFalse(docs.check_members(broken)['ok'])
        broken = dict(members); broken['examples/requests/unsafe.json'] = json.dumps({'method': 'workflow', 'params': {'execute': True}}).encode()
        self.assertFalse(docs.check_members(broken)['ok'])
