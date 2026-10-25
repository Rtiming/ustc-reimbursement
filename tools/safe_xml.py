"""材料XML的统一边界：拒绝实体/外部资源，限制成员大小、节点数及深度。"""

from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as _ET
import pyexpat
from weakref import WeakKeyDictionary

MAX_XML_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
MAX_ELEMENTS = 500000
MAX_DEPTH = 128
_CHECKED_ARCHIVES = WeakKeyDictionary()
ParseError = _ET.ParseError
Element = _ET.Element
SubElement = _ET.SubElement
ElementTree = _ET.ElementTree
tostring = _ET.tostring
register_namespace = _ET.register_namespace


class UnsafeXML(ValueError):
    code = 'unsafe_xml'


class _Builder(_ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.count = 0

    def start(self, tag, attrs):
        self.count += 1
        self.depth += 1
        if self.count > MAX_ELEMENTS or self.depth > MAX_DEPTH:
            raise UnsafeXML('XML structure limit exceeded; split the source / XML结构超限，请拆分来源')
        return super().start(tag, attrs)

    def end(self, tag):
        result = super().end(tag)
        self.depth -= 1
        return result


def fromstring(data, *, allow_dtd=False):
    if tuple(pyexpat.version_info) < (2, 8, 5):
        raise UnsafeXML('Outdated XML runtime; use the current installer / XML运行时过旧，请使用当前安装器')
    if not isinstance(data, (str, bytes, bytearray)) or type(allow_dtd) is not bool:
        raise UnsafeXML('XML input must be text or bytes')
    size = len(data.encode('utf-8')) if isinstance(data, str) else len(data)
    if size > MAX_XML_BYTES:
        raise UnsafeXML('XML exceeds 64 MiB / XML超过64MiB')
    try:
        from defusedxml.ElementTree import DefusedXMLParser
        from defusedxml.common import DefusedXmlException
    except ImportError as error:
        raise UnsafeXML('Missing defusedxml; run the material installer / 请补齐材料依赖') from error
    parser = DefusedXMLParser(target=_Builder(), forbid_dtd=not allow_dtd,
                             forbid_entities=True, forbid_external=True)
    try:
        parser.feed(data)
        return parser.close()
    except DefusedXmlException as error:
        raise UnsafeXML('XML declaration rejected / 拒绝XML声明: ' + type(error).__name__) from None


XML = fromstring


def read_zip_member(archive, member, *, limit=MAX_XML_BYTES):
    if archive.mode != 'r':
        raise UnsafeXML('XML archive reader requires a read-only archive')
    if archive not in _CHECKED_ARCHIVES:
        rows = archive.infolist()
        if len(rows) > MAX_ARCHIVE_MEMBERS or sum(x.file_size for x in rows) > MAX_ARCHIVE_BYTES:
            raise UnsafeXML('Office/OFD archive exceeds bounded input size')
        names = [x.filename for x in rows]
        if len(set(names)) != len(names) or any(x.startswith('/') or '\\' in x or '..' in PurePosixPath(x).parts for x in names):
            raise UnsafeXML('Duplicate or non-canonical archive members')
        _CHECKED_ARCHIVES[archive] = True
    if archive.getinfo(member).file_size > limit:
        raise UnsafeXML('Archive member exceeds input limit')
    with archive.open(member) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UnsafeXML('Archive member exceeds declared input limit')
    return raw


def from_zip(archive, member):
    return fromstring(read_zip_member(archive, member))


def parse(source):
    if hasattr(source, 'read'):
        raw = source.read(MAX_XML_BYTES + 1)
    else:
        with Path(source).open('rb') as stream:
            raw = stream.read(MAX_XML_BYTES + 1)
    return ElementTree(fromstring(raw))


def parse_dom(data):
    # 先复用结构限额；DOM保留官方模板所需的前缀和节点写回方式。
    fromstring(data)
    from defusedxml.minidom import parseString
    from defusedxml.common import DefusedXmlException
    try:
        return parseString(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as error:
        raise UnsafeXML('XML declaration rejected: ' + type(error).__name__) from None
