"""Read only declared human-answer cells; emit a review candidate, never finance writes."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
import safe_xml as ET

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
RESULTS = {'确认', '仍看不清', '原件未写', '需核实', '需补材料', '已补材料'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verified(ref):
    path = Path(ref['path'])
    if not path.is_absolute() or not path.is_file() or sha(path) != ref['sha256']:
        raise ValueError('冻结来源已变化或缺失：' + str(path))
    return path


def workbook_values(path):
    """No Office execution, formula evaluation, hyperlink following, or ZIP extraction."""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > 512 * 1024**2:
            raise ValueError('工作簿超出有界读取范围')
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or any('..' in PurePosixPath(n).parts or n.startswith('/') for n in names):
            raise ValueError('工作簿成员名称异常')
        if any(n.endswith('vbaProject.bin') for n in names):
            raise ValueError('不读取带宏工作簿')
        def xml(name):
            raw = ET.read_zip_member(z, name)
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                raise ValueError('不接受XML实体声明')
            return ET.fromstring(raw)
        strings = []
        if 'xl/sharedStrings.xml' in names:
            strings = [''.join(e.itertext()) for e in xml('xl/sharedStrings.xml').findall(f'{{{S}}}si')]
        rels = {e.attrib['Id']: e.attrib for e in xml('xl/_rels/workbook.xml.rels')}
        sheets = {}
        for entry in xml('xl/workbook.xml').findall(f'{{{S}}}sheets/{{{S}}}sheet'):
            rel = rels[entry.attrib[f'{{{R}}}id']]
            if rel.get('TargetMode') == 'External':
                raise ValueError('工作表不可引用外部内容')
            target = rel['Target']
            location = target.lstrip('/') if target.startswith('/') else str(PurePosixPath('xl') / target)
            root = xml(location)
            cells = {}
            for cell in root.findall(f'.//{{{S}}}sheetData/{{{S}}}row/{{{S}}}c'):
                address = cell.attrib['r']
                if address in cells or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', address):
                    raise ValueError('重复或非法单元格')
                if cell.find(f'{{{S}}}f') is not None:
                    raise ValueError('确认表不接受公式：' + address)
                kind = cell.attrib.get('t')
                value = cell.findtext(f'{{{S}}}v', '')
                if kind == 's': value = strings[int(value)]
                elif kind == 'inlineStr': value = ''.join(cell.find(f'{{{S}}}is').itertext())
                elif kind == 'e': raise ValueError('单元格错误：' + address)
                if value: cells[address] = value
            merges = sorted(e.attrib['ref'] for e in root.findall(f'{{{S}}}mergeCells/{{{S}}}mergeCell'))
            sheets[entry.attrib['name']] = {'cells': cells, 'merges': merges}
        media = Counter(hashlib.sha256(z.read(n)).hexdigest() for n in names if n.startswith('xl/media/') and not n.endswith('/'))
    return sheets, media


def read_answers(workbook, manifest, output=None):
    workbook, manifest = Path(workbook), Path(manifest)
    frozen = json.loads(manifest.read_text(encoding='utf-8'))
    version = frozen.get('schema')
    if version not in {'ustc-confirmation-roundtrip-v1', 'ustc-confirmation-roundtrip-v2'}:
        raise ValueError('不支持的确认表映射')
    baseline = verified(frozen['baseline'])
    questions_path = verified(frozen['questions'])
    questions = json.loads(questions_path.read_text(encoding='utf-8'))
    for source in frozen['sources']:
        verified(source)
    current, images = workbook_values(workbook)
    original, original_images = workbook_values(baseline)
    if set(current) != set(original) or images != original_images:
        raise ValueError('工作表或原始证据图片已变化；停止自动读回')
    controls = frozen['controls']
    def sheet_for(control):
        return control['worksheet'] if version.endswith('v2') else frozen['worksheet']
    addresses = [(sheet_for(c), c['anchor']) for c in controls]
    if len(addresses) != len(set(addresses)):
        raise ValueError('重复答案锚点')
    if any(name not in current for name, _ in addresses):
        raise ValueError('确认工作表缺失')
    for name, before in original.items():
        after = current[name]
        if before['merges'] != after['merges']:
            raise ValueError('行列或合并区已移动；请保留原版式')
        allowed = {cell for sheet, cell in addresses if sheet == name}
        for cell in set(before['cells']) | set(after['cells']):
            if cell not in allowed and before['cells'].get(cell, '') != after['cells'].get(cell, ''):
                raise ValueError('黄色答案栏以外的内容改变：' + name + '!' + cell)
        if version.endswith('v1') and any(before['cells'].get(cell) for cell in allowed):
            raise ValueError('冻结基线的答案必须为空')
    cards = {c['id']: c for c in questions['cards']}
    if len(cards) != len(questions['cards']):
        raise ValueError('问题ID重复')
    answers = {key: {} for key in cards}
    changes = {key: {} for key in cards}
    for control in controls:
        key, field = control['card_id'], control['key']
        if key not in answers or field in answers[key]:
            raise ValueError('问题或答案映射重复')
        worksheet = sheet_for(control)
        value = current[worksheet]['cells'].get(control['anchor'], '')
        previous = original[worksheet]['cells'].get(control['anchor'], '')
        answers[key][field] = value.strip()
        if value != previous:
            changes[key][field] = {'before': previous, 'after': value}
    documents = []
    for key, card in cards.items():
        values = answers[key]
        result = values.get('result', '')
        if result and result not in RESULTS:
            raise ValueError('未知核对结果：' + key)
        definitions = card['fields'] + [f for e in card.get('evidence', []) for f in e.get('user_fields', [])]
        required = {f['key'] for f in definitions if f.get('required', True)}
        missing = sorted(k for k in required if not values.get(k))
        errors = []
        for field, value in values.items():
            if not value: continue
            if len(value) > 16000: errors.append(field + '过长')
            if field == 'phone' and not re.fullmatch(r'1[0-9]{10}', value): errors.append('phone须为11位完整号码')
            if 'student_id' in field and not re.fullmatch(r'[A-Za-z]{1,4}[0-9]{5,12}', value): errors.append(field + '格式待核')
            if field in {'claim_amount', 'payee_amount'} and not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?', value): errors.append(field + '须为非负金额且最多两位小数')
            if field == 'business_date':
                try: date.fromisoformat(value)
                except ValueError: errors.append('business_date须为年-月-日')
        any_answer = any(value for field, value in values.items() if field != 'result')
        state = 'pending'
        if result == '确认': state = 'user_confirmed_pending_review' if not missing and not errors else 'incomplete_confirmation'
        elif result == '已补材料': state = 'materials_named_pending_review' if values.get('materials') else 'incomplete_confirmation'
        elif result: state = 'held_by_user'
        elif any_answer: state = 'draft_answer'
        documents.append({'question_id': key, 'sequences': card.get('sequences', []), 'title': card['title'],
                          'state': state, 'answers': values, 'changed_since_baseline': changes[key],
                          'missing_required_fields': missing, 'validation_errors': errors})
    result = {'schema': 'ustc-confirmation-readback-v1', 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
              'workbook': {'path': str(workbook.resolve()), 'sha256': sha(workbook)},
              'mapping': {'path': str(manifest.resolve()), 'sha256': sha(manifest)},
              'counts': dict(Counter(d['state'] for d in documents)), 'documents': documents,
              'changed_fields': sum(len(c) for c in changes.values()),
              'system_write': False, 'case_modified': False, 'formal_plan_modified': False,
              'next': '按问题ID复核用户答案及补件，生成新版本；未填项保留，不把确认表当财务保存回执。'}
    if output:
        output = Path(output)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    return result
