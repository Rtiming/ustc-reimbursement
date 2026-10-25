#!/usr/bin/env python3
"""按已核对的材料计划排成A4单面PDF；只读原件，不操作财务系统。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw
import material_coverage
import payment_evidence


PROJECT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_plan(path):
    path = Path(path).resolve()
    plan = json.loads(path.read_text(encoding='utf-8'))
    source_root = (path.parent / plan['source_root']).resolve()
    expected = json.loads((source_root / plan['invoice_manifest']).read_text(encoding='utf-8'))
    form = json.loads((source_root / plan['form_config']).read_text(encoding='utf-8'))
    if expected['case_id'] != plan['case_id'] or str(expected['serial']) != str(plan['serial']):
        raise ValueError('打印计划与本案清单的案件/单号不一致')
    if form['case_id'] != plan['case_id'] or str(form['serial']) != str(plan['serial']) or Decimal(form['amount']) != Decimal(plan['amount']):
        raise ValueError('打印计划与本案参数的案件/单号/总额不一致')
    if plan.get('explanation_source') and digest(source_root / plan['explanation_source']) != plan['explanation_sha256']:
        raise ValueError('定稿说明SHA已改变，重新核对')
    invoices = [r for s in plan['sections'] for r in s['items'] if r.get('role') == 'invoice']
    wanted = {(Path(r['path']).name, r['sha256']) for r in expected['original_files']}
    actual = {(Path(r['path']).name, r['sha256']) for r in invoices}
    if actual != wanted or len(invoices) != expected['expected_count'] or any(r['kind'] != 'source' for r in invoices):
        raise ValueError('打印计划必须包含已核定清单中的每张发票且仅一次')
    if len({r['id'] for s in plan['sections'] for r in s['items']}) != sum(len(s['items']) for s in plan['sections']):
        raise ValueError('材料编号重复')
    if sum((Decimal(r['amount']) for r in invoices), Decimal('0')) != Decimal(plan['amount']):
        raise ValueError('打印发票金额不等于已核定总额')
    seen = set()
    for section in plan['sections']:
        for item in section['items']:
            if item['kind'] != 'source':
                continue
            file = (source_root / item['path']).resolve()
            if not file.is_relative_to(source_root) or not file.is_file():
                raise ValueError('材料须位于本活动资料根内：' + item['id'])
            if digest(file) != item['sha256']:
                raise ValueError('原件SHA已改变，重新核对：' + item['id'])
            identity = (str(file), item.get('page', 0))
            if identity in seen:
                raise ValueError('同一材料页重复纳入：' + item['id'])
            seen.add(identity)
    material_coverage.validate(plan, source_root)
    payment_evidence.enforce_assembly(payment_evidence.audit_plan(plan, json.loads((source_root / plan['coverage_manifest']).read_text())))
    return plan, source_root


def content_rect(page):
    """只裁外围白边和源文件的生成页码，保留票面、印章、二维码及扫描照片。"""
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    im = Image.frombytes('RGB', [pix.width, pix.height], pix.samples).convert('L')
    mask = im.point(lambda p: 255 if p < 248 else 0)
    draw = ImageDraw.Draw(mask)
    for block in page.get_text('blocks'):
        if len(block) > 4 and isinstance(block[4], str):
            compact = re.sub(r'\s+', '', block[4])
            if block[1] > page.rect.height * .7 and re.fullmatch(r'第\d+页[/／]?共\d+页', compact):
                draw.rectangle(tuple(block[:4]), fill=0)
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError('空白材料页不能加入打印包')
    rect = fitz.Rect(bounds)
    rect.x0 = max(0, rect.x0 - 4)
    rect.y0 = max(0, rect.y0 - 4)
    rect.x1 = min(page.rect.width, rect.x1 + 4)
    rect.y1 = min(page.rect.height, rect.y1 + 4)
    return rect


class Packet:
    def __init__(self, plan, root, profile):
        self.plan, self.root, self.p = plan, root, profile
        self.doc = fitz.open()
        self.font = fitz.Font(profile['font'])
        self.font_name = 'PacketCN'
        self.font_pages = set()
        for key in ['one_material_per_page', 'auto_orientation', 'show_material_labels', 'show_headers', 'show_footers']:
            if not isinstance(profile.get(key, key in ['one_material_per_page', 'auto_orientation']), bool):
                raise ValueError('打印开关必须使用true/false：' + key)
        self.one_per_page = profile.get('one_material_per_page', True)
        self.auto_orientation = profile.get('auto_orientation', True)
        self.labels = profile.get('show_material_labels', False)
        self.headers = profile.get('show_headers', False)
        self.footers = profile.get('show_footers', False)
        self.base_width, self.base_height = profile['page_size_pt']
        self.left = profile['margin_pt']
        self.geometry('portrait')
        self.page = None
        self.records, self.outline = [], []
        self.section = None
        self.y = self.top

    def geometry(self, orientation):
        width, height = sorted([self.base_width, self.base_height])
        self.width, self.height = (height, width) if orientation == 'landscape' else (width, height)
        if not self.auto_orientation:
            self.width, self.height = self.base_width, self.base_height
        self.right = self.width - self.left
        self.usable_width = self.right - self.left
        self.top = self.p['header_bottom_pt'] if self.headers else self.left
        self.bottom = self.height - (self.base_height - self.p['footer_top_pt']) - 12 if self.footers else self.height - self.left

    def text(self, x, y, text, size=None):
        size = size or self.p['body_size_pt']
        if self.font.text_length(text, fontsize=size) > self.right - x + .01:
            raise ValueError('文字越界：' + text)
        if self.page.number not in self.font_pages:
            self.page.insert_font(fontname=self.font_name, fontbuffer=self.font.buffer)
            self.font_pages.add(self.page.number)
        self.page.insert_text((x, y), text, fontsize=size, fontname=self.font_name)

    def lines(self, text, width, size):
        output, line = [], ''
        for char in text:
            if char == '\n':
                output.append(line); line = ''
            elif line and self.font.text_length(line + char, fontsize=size) > width:
                output.append(line); line = char
            else:
                line += char
        if line:
            output.append(line)
        return output

    def paragraph(self, text, size=None):
        size = size or self.p['body_size_pt']
        for line in self.lines(text, self.usable_width, size):
            if self.y + size * 1.65 > self.bottom:
                raise ValueError('前置说明文字溢出')
            self.text(self.left, self.y + size, line, size)
            self.y += size * 1.65
        self.y += self.p['gap_pt']

    def new_page(self):
        self.page = self.doc.new_page(width=self.width, height=self.height)
        if self.headers:
            self.text(self.left, 30, self.plan['title'], self.p['label_size_pt'])
            self.text(self.left, 50, self.section['title'], self.p['title_size_pt'])
            self.page.draw_line((self.left, 57), (self.right, 57), width=.5)
        self.y = self.top

    def room(self, height):
        if height > self.bottom - self.top + .01:
            raise ValueError('单个材料超过页面容纳范围')
        if self.page is None or self.y + height > self.bottom:
            self.new_page()

    def add_source(self, item):
        file = self.root / item['path']
        original = fitz.open(file)
        if file.suffix.lower() != '.pdf':
            source = fitz.open('pdf', original.convert_to_pdf())
            original.close()
        else:
            source = original
        index = item.get('page', 0)
        if not 0 <= index < len(source):
            raise ValueError('页码无效：' + item['id'])
        sp = source[index]
        rotate = item.get('rotate', 0)
        if rotate not in [0, 90, 180, 270]:
            raise ValueError('材料旋转只允许0/90/180/270度')
        clip = content_rect(sp) if item.get('trim', True) else sp.rect
        label_size = self.p['label_size_pt']
        source_width, source_height = (clip.height, clip.width) if rotate in [90, 270] else (clip.width, clip.height)
        if self.auto_orientation:
            self.geometry('landscape' if source_width > source_height else 'portrait')
        # 横向材料用横向纸面的可用宽度，避免沿用竖向宽度导致发票仍过小。
        label_lines = self.lines(item['id'] + '  ' + item['label'], self.usable_width, label_size) if self.labels else []
        label_height = len(label_lines) * 13 + 5 if label_lines else 0
        capacity = self.bottom - self.top - label_height - self.p['gap_pt']
        max_width = self.p.get('landscape_source_max_width_pt', self.usable_width) if self.auto_orientation and self.width > self.height else self.p.get('source_max_width_pt', self.usable_width)
        material_width = min(self.usable_width, max_width)
        scale = min(material_width / source_width, capacity / source_height)
        target_width, target_height = source_width * scale, source_height * scale
        height = label_height + target_height + self.p['gap_pt']
        if self.one_per_page or self.page is not None and (abs(self.page.rect.width - self.width) > .01 or abs(self.page.rect.height - self.height) > .01):
            self.new_page()
        self.room(height)
        for line in label_lines:
            self.text(self.left, self.y + label_size, line, label_size)
            self.y += 13
        if label_lines:
            self.y += 5
        x = self.left + (self.usable_width - target_width) / 2
        rect = fitz.Rect(x, self.y, x + target_width, self.y + target_height)
        missing_fonts = [font[3] for font in source.get_page_fonts(index) if not source.extract_font(font[0])[3]]
        if missing_fonts:
            # 有些原始发票未嵌入字体。固定为已核看的高清页面，避免换设备打印时字体替换。
            dpi = self.p.get('raster_dpi_for_unembedded_fonts', 300)
            pix = sp.get_pixmap(matrix=fitz.Matrix(dpi / 72 * scale, dpi / 72 * scale).prerotate(-rotate), clip=clip, alpha=False)
            self.page.insert_image(rect, pixmap=pix)
            render_mode = f'raster_{dpi}dpi'
        else:
            self.page.show_pdf_page(rect, source, index, clip=clip, rotate=rotate)
            render_mode = 'vector'
        self.records.append({**item, 'output_page': self.page.number + 1, 'source_clip': list(clip),
                             'output_rect': list(rect), 'scale': scale, 'render_mode': render_mode,
                             'page_size_pt': [self.width, self.height],
                             'page_orientation': 'landscape' if self.width > self.height else 'portrait',
                             'unembedded_source_fonts': missing_fonts})
        self.y += target_height + self.p['gap_pt']
        source.close()

    def add_slot(self, item):
        self.geometry('portrait')
        height = self.p['original_slot_height_pt']
        if self.one_per_page:
            self.new_page()
        self.room(height + self.p['gap_pt'])
        rect = fitz.Rect(self.left, self.y, self.right, self.y + height)
        self.page.draw_rect(rect, width=.5, color=(.55, .55, .55))
        self.text(self.left + 9, self.y + 22, item['label'])
        self.text(self.left + 9, self.y + 42, '纸质原件随本人票据附入；此处供整理、附贴原件。', self.p['label_size_pt'])
        self.records.append({**item, 'output_page': self.page.number + 1, 'output_rect': list(rect)})
        self.y += height + self.p['gap_pt']

    def add_table(self, item):
        self.geometry('portrait')
        self.new_page()
        for paragraph in item.get('paragraphs', []):
            self.paragraph(paragraph)
        widths = item['column_widths_pt']
        if abs(sum(widths) - self.usable_width) > 1:
            raise ValueError('表格列宽须等于正文宽度')
        for row in [item['columns'], *item['rows']]:
            wrapped = [self.lines(str(cell), width - 10, self.p['body_size_pt']) for cell, width in zip(row, widths)]
            height = max(len(lines) for lines in wrapped) * 18 + 13
            if self.y + height > self.bottom:
                raise ValueError('前置表格溢出')
            x = self.left
            for lines, width in zip(wrapped, widths):
                self.page.draw_rect(fitz.Rect(x, self.y, x + width, self.y + height), width=.5)
                for j, line in enumerate(lines):
                    self.text(x + 5, self.y + 18 + j * 18, line)
                x += width
            self.y += height
        self.y += 18
        for paragraph in item.get('after', []):
            self.paragraph(paragraph)
        self.records.append({**item, 'output_page': self.page.number + 1})

    def build(self):
        for section in self.plan['sections']:
            self.section = section
            self.page = None
            start = len(self.doc) + 1
            for item in section['items']:
                if item['kind'] == 'source':
                    self.add_source(item)
                elif item['kind'] == 'original_slot':
                    self.add_slot(item)
                elif item['kind'] == 'table':
                    self.add_table(item)
                else:
                    raise ValueError('未知材料种类')
            if len(self.doc) >= start:
                self.outline.append([1, section['title'], start])
        self.doc.set_toc(self.outline)
        count = len(self.doc)
        if self.one_per_page and [r['output_page'] for r in self.records] != list(range(1, count + 1)):
            raise ValueError('逐张打印时每页须恰好对应一项材料，不得拼排或插入空白页')
        if self.footers:
            for page in self.doc:
                self.page = page
                self.geometry('landscape' if page.rect.width > page.rect.height else 'portrait')
                self.text(self.left, self.height - 19, f'单据{self.plan["serial"]}附件 · 单面打印', self.p['footer_size_pt'])
                text = f'第{page.number + 1}页／共{count}页'
                self.text(self.right - self.font.text_length(text, fontsize=self.p['footer_size_pt']), self.height - 19, text, self.p['footer_size_pt'])
        self.doc.xref_set_key(self.doc.pdf_catalog(), 'ViewerPreferences', '<< /Duplex /Simplex /PrintScaling /None >>')
        self.doc.set_metadata({'title': self.plan['title'], 'subject': 'A4单面打印附件；正式信息面须另取审批后的版本', 'creator': '科大活动报销助手'})
        self.doc.subset_fonts()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, default=PROJECT / 'data/printing_profile.json')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    if args.output.exists() and not args.replace:
        raise ValueError('输出已存在；使用新版本名称，或明确--replace')
    plan, root = load_plan(args.plan)
    originals = {(root / r['path']).resolve() for s in plan['sections'] for r in s['items'] if r['kind'] == 'source'}
    if args.output.resolve() in originals:
        raise ValueError('打印输出不得覆盖原始材料，即使指定--replace')
    packet = Packet(plan, root, json.loads(args.profile.read_text(encoding='utf-8')))
    packet.build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    packet.doc.save(args.output, garbage=4, deflate=True)
    pages = len(packet.doc)
    packet.doc.close()
    report = {'case_id': plan['case_id'], 'serial': plan['serial'], 'pdf': str(args.output.resolve()),
              'pdf_sha256': digest(args.output), 'plan_sha256': digest(args.plan), 'profile': str(args.profile.resolve()), 'profile_sha256': digest(args.profile), 'pages': pages,
              'invoice_count': sum(r.get('role') == 'invoice' for r in packet.records), 'invoice_total': plan['amount'],
              'simplex': True, 'official_information_page_included': any(r.get('role') == 'official_information' for r in packet.records),
              'one_material_per_page': packet.one_per_page, 'material_labels_enabled': packet.labels,
              'auto_orientation': packet.auto_orientation,
              'page_orientations': {orientation: sum(r.get('page_orientation') == orientation for r in packet.records) for orientation in ['portrait', 'landscape']},
              'headers_enabled': packet.headers, 'footers_enabled': packet.footers,
              'open_items': plan.get('open_items', []), 'materials': packet.records, 'outline': packet.outline,
              'source_files_unchanged': all(digest(root / r['path']) == r['sha256'] for r in packet.records if r['kind'] == 'source'),
              'coverage': material_coverage.validate(plan, root),
              'payment_evidence': payment_evidence.audit_plan(plan, json.loads((root / plan['coverage_manifest']).read_text())),
              'finance_system_write': False}
    args.output.with_suffix('.manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with args.output.with_suffix('.索引.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['打印页码', '材料编号', '类别', '材料', '原件路径', '原件页码', '计入报销金额'])
        for row in packet.records:
            writer.writerow([row['output_page'], row['id'], row.get('role', ''), row['label'], row.get('path', ''), row.get('page', 0) + 1 if row['kind'] == 'source' else '', row.get('amount', '')])
    summary = {k: v for k, v in report.items() if k not in ['materials', 'outline', 'payment_evidence']}
    if report['payment_evidence']:
        summary['payment_materials_ready'] = report['payment_evidence']['ready_for_payment_material_delivery']
        summary['payment_missing_invoice_count'] = report['payment_evidence']['missing_invoice_count']
        summary['payment_assembly_mode'] = report['payment_evidence']['assembly_mode']
        summary['payment_assembly_ready'] = report['payment_evidence']['ready_for_payment_assembly']
        summary['payment_required_missing_invoice_count'] = report['payment_evidence']['required_missing_invoice_count']
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
