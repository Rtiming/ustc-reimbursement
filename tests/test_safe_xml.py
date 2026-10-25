"""真实解析正常Office XML，并拒绝跨编码实体、结构耗尽和压缩包歧义。"""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import safe_xml as xml


class SafeXMLTests(unittest.TestCase):
    def test_old_parser_runtime_is_rejected_even_for_plain_input(self):
        with patch.object(xml.pyexpat, 'version_info', (2, 6, 3)), self.assertRaises(xml.UnsafeXML):
            xml.fromstring('<root/>')

    def test_plain_xml_and_dom_preserve_chinese_namespaces(self):
        raw = '<w:document xmlns:w="urn:test"><w:t>合成材料 &amp; text</w:t></w:document>'.encode()
        self.assertEqual(xml.fromstring(raw)[0].text, '合成材料 & text')
        self.assertIn('合成材料', xml.parse_dom(raw).toxml())

    def test_entities_are_rejected_in_utf8_utf16_and_poppler_mode(self):
        for encoding in ('utf-8', 'utf-16'):
            for declaration in ('<!ENTITY x "synthetic">', '<!ENTITY x SYSTEM "file:///synthetic-missing-file">'):
                raw = ('<?xml version="1.0" encoding="'+encoding+'"?><!DOCTYPE a ['+declaration+']><a>&x;</a>').encode(encoding)
                for allow in (False, True):
                    with self.subTest(encoding=encoding, allow=allow), self.assertRaises(xml.UnsafeXML):
                        xml.fromstring(raw, allow_dtd=allow)
                with self.assertRaises(xml.UnsafeXML): xml.parse_dom(raw)
        self.assertEqual(xml.fromstring('<!DOCTYPE html><html/>', allow_dtd=True).tag, 'html')
        with self.assertRaises(xml.UnsafeXML): xml.fromstring('<!DOCTYPE html><html/>')

    def test_depth_node_and_byte_limits_fail_before_unbounded_tree(self):
        with self.assertRaises(xml.UnsafeXML): xml.fromstring('<a>'*129+'</a>'*129)
        with patch.object(xml, 'MAX_ELEMENTS', 3), self.assertRaises(xml.UnsafeXML):
            xml.fromstring('<a><b/><c/><d/></a>')
        with patch.object(xml, 'MAX_XML_BYTES', 10), self.assertRaises(xml.UnsafeXML):
            xml.parse(io.BytesIO(b'<a>too much data</a>'))

    def test_zip_member_limit_is_checked_before_reading(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive: archive.writestr('part.xml', b'x'*100)
        with zipfile.ZipFile(stream) as archive, patch.object(archive, 'open') as opened:
            with self.assertRaises(xml.UnsafeXML): xml.read_zip_member(archive, 'part.xml', limit=32)
            opened.assert_not_called()

    def test_utf16_entities_in_actual_workbook_are_rejected(self):
        from confirmation_workbook import workbook_values
        raw = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE a [<!ENTITY x "synthetic">]><a>&x;</a>'.encode('utf-16')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.xlsx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('xl/sharedStrings.xml', raw)
            with self.assertRaises(xml.UnsafeXML): workbook_values(path)
