import base64
import json
import unittest

import test_material_review as fixture
from material_review_model import ReviewError, validate
from material_review_pdf import export_pdf
from material_review_store import append, state


class ReaderPdfTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ReviewTest()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def original(self, kind='明白纸'):
        import fitz,hashlib
        f=self.f
        with fitz.open() as doc:
            doc.new_page(width=400,height=250).insert_text((15,30),'CONTEXT ORIGINAL 123.45')
            raw=doc.tobytes()
        aid=hashlib.sha256(raw).hexdigest();path=f.root/(aid+'.pdf');path.write_bytes(raw)
        f.data['assets'][aid]={'id':aid,'sha256':aid,'path':str(path),'label':kind+'原件.pdf',
            'suffix':'.pdf','kind':kind,'pages':1,'item_ids':['1'],'origins':[],'invoice_numbers':[]}
        f.data['items']['1']['asset_ids'].append(aid)
        return aid

    def test_exif_dimensions_and_display_rotation_preserve_layout(self):
        import io,hashlib
        import fitz
        from PIL import Image
        from material_review_pdf import source_dimensions
        f=self.f
        # 5至8包含转置及90度旋转；不能仅处理常见的6/8。
        for orientation in range(1,9):
            im=Image.new('RGB',(300,100),'blue');exif=im.getexif();exif[274]=orientation
            raw=io.BytesIO();im.save(raw,'JPEG',exif=exif)
            aid=hashlib.sha256(raw.getvalue()).hexdigest();source=f.root/(aid+'.jpg')
            source.write_bytes(raw.getvalue())
            asset={'id':aid,'sha256':aid,'path':str(source),'suffix':'.jpg','pages':1,
                   'label':f'方向{orientation}.jpg','kind':'明白纸','item_ids':['1'],
                   'origins':[],'invoice_numbers':[]}
            self.assertEqual(source_dimensions(f.data,asset,f.ws,1),
                             (100,300) if orientation in (5,6,7,8) else (300,100))
        # EXIF旋转后再采用已登记的展示旋转，两者不能重复或遗漏。
        f.data['assets'][asset['id']]=dict(asset,rotation=90)
        f.data['items']['1']['paper_ids']=[asset['id']]
        f.data['issues']['Q1']['targets']=[{'asset_id':asset['id'],'page':1}]
        f.publish();path,_=export_pdf(f.ws,['1'],['Q1'])
        with fitz.open(path) as doc:
            rect=fitz.Rect(doc[0].get_image_info()[0]['bbox'])
            self.assertAlmostEqual(rect.width/rect.height,3,places=5)
        changed=dict(asset,sha256='0'*64)
        with self.assertRaises(ReviewError):source_dimensions(f.data,changed,f.ws,1)

    def test_paper_context_is_exported_without_any_crop_and_identity_is_preserved(self):
        import fitz
        f=self.f;aid=self.original();f.data['items']['1']['paper_ids']=[aid]
        f.data['items']['1']['paper_total']='123.45'
        f.data['expenses']['E1'].update(refs=[{'asset_id':aid,'page':1}],
            invoice_numbers=['12345678901234567890'],face_amount='123.45')
        f.publish();path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(m['issues'][0]['paper_status'],'included')
        self.assertEqual(m['issues'][0]['paper_codes'],['1-M1'])
        self.assertEqual(len(m['overviews']),1)
        self.assertEqual(m['closeups'],[])
        with fitz.open(path) as doc:
            text=''.join(p.get_text() for p in doc)
            self.assertIn('明白纸登记 123.45元',text)
            self.assertIn('12345678901234567890',text)
            self.assertTrue(doc[0].get_links())
            self.assertEqual(len(doc[0].get_images()),1)

    def test_unlocated_paper_is_explicit_and_reply_fields_do_not_change_status(self):
        import fitz
        f=self.f
        f.data['issues']['Q1']['feedback']={'issue_fingerprint':f.data['issues']['Q1']['fingerprint'],
            'project':'社团｜活动','request':'补明白纸','context':'已收票据',
            'response':'材料编号、拟报金额及实际收款人。'}
        f.publish();path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(m['issues'][0]['paper_status'],'not_located')
        self.assertEqual(state(f.ws)['issues']['Q1']['status'],'pending')
        with fitz.open(path) as doc:
            text=''.join(p.get_text() for p in doc)
            self.assertIn('本批未定位到对应明白纸',text)
            self.assertIn('请回复：材料编号、拟报金额及实际收款人',text)
        f.data['issues']['Q1']['feedback']['response']=True
        with self.assertRaises(ReviewError):validate(f.data)

    def test_shared_paper_omission_is_not_claimed_to_be_missing(self):
        import fitz
        f=self.f;aid=self.original();f.data['items']['1']['paper_ids']=[aid]
        f.data['items']['2']={'id':'2','title':'另一项目','asset_ids':[aid],'expense_ids':[],'issue_ids':[]}
        f.data['assets'][aid]['item_ids'].append('2');f.publish()
        path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(m['overviews'],[])
        self.assertEqual(m['issues'][0]['paper_status'],'omitted')
        with fitz.open(path) as doc:
            text=''.join(p.get_text() for p in doc)
            self.assertIn('已有登记，本次选择未包含',text)
            self.assertNotIn('本批未定位到对应明白纸',text)

    def test_pdf_closeup_renders_source_region_at_output_resolution(self):
        import io
        import fitz
        from PIL import Image
        from material_review_pdf import evidence_image
        f=self.f
        with fitz.open() as doc:
            p=doc.new_page(width=1000,height=1000)
            p.draw_rect(fitz.Rect(400,400,500,425),color=(0,0,1),fill=(0,0,1))
            raw=doc.tobytes()
        append(f.ws,f.req('attachment',file={'name':'小区域.pdf','base64':base64.b64encode(raw).decode()}))
        a=next(iter(state(f.ws)['attachments'].values()))
        im=Image.open(io.BytesIO(evidence_image(f.data,a,f.ws,1,(500,125),box=[.4,.4,.1,.025])))
        self.assertEqual(im.size,(1500,375))
        self.assertEqual(im.getpixel((750,187)),(0,0,255))
        # 纸面小区域直接高清渲染，不能复用整页2倍缩略图裁出的200×50像素。
        a=dict(a,rotation=90)
        rotated=Image.open(io.BytesIO(evidence_image(f.data,a,f.ws,1,(125,500),box=[.4,.4,.1,.025])))
        self.assertEqual(rotated.size,(375,1500))

    def test_related_paper_is_visible_without_claiming_matching_scope_or_exporting_other_item(self):
        import fitz,zipfile
        from material_review_package import export_bundle
        f=self.f;aid=self.original()
        f.data['assets'][aid]['item_ids']=['2']
        f.data['items']['1']['asset_ids'].remove(aid)
        f.data['items']['2']={'id':'2','title':'同活动劳务','asset_ids':[aid], 'paper_ids':[aid],
                            'expense_ids':[],'issue_ids':[],'private_note':'不应外发的内部记录'}
        rel=dict(asset_id=aid,page=1,source_item_id='2',relation='different_scope',
                 statement='此纸为1000元劳务，不能替代有票费用。',basis='逐项核对活动名称及纸面费用')
        f.data['items']['1']['paper_relations']=[rel]
        f.publish();path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(m['issues'][0]['paper_codes'],['2-M1'])
        self.assertEqual(m['issues'][0]['paper_relations'],[rel])
        self.assertEqual(len(m['overviews']),1)
        self.assertEqual(f.data['assets'][aid]['item_ids'],['2'])
        with fitz.open(path) as doc:
            text=''.join(p.get_text() for p in doc)
            self.assertIn('同活动不同费用',text)
            self.assertNotIn('已对应',text)
            self.assertNotIn('不应外发的内部记录',text)
        bundle,_=export_bundle(f.ws,['1'],'materials',['Q1'])
        with zipfile.ZipFile(bundle) as z:
            payload=z.read('data.js').decode().removeprefix('window.REVIEW_EXPORT=').rstrip(';')
            packet=json.loads(payload);public=packet['data']
            self.assertEqual(set(public['items']),{'1'})
            self.assertEqual(public['assets'][aid]['source_item_ids'],['2'])
            self.assertEqual(public['items']['1']['paper_relations'],[rel])
            self.assertNotIn('不应外发的内部记录',payload)

    def test_paper_relation_cannot_import_arbitrary_material_or_wrong_owner(self):
        f=self.f;aid=self.original()
        rel=dict(asset_id=aid,page=1,source_item_id='1',relation='unverified',
                 statement='纸面1500元，票面3700元，范围待核。',basis='原件金额')
        f.data['items']['1']['paper_relations']=[rel]
        self.assertTrue(validate(f.data))
        rel['source_item_id']='2'
        with self.assertRaises(ReviewError):validate(f.data)
        rel['source_item_id']='1';f.data['assets'][aid]['kind']='正式打印件'
        with self.assertRaises(ReviewError):validate(f.data)

    def test_known_facts_and_candidate_amount_are_not_adopted_as_claim(self):
        import fitz
        f=self.f;f.data['expenses']['E1'].update(review_note='已知行程；只需确认垫付及收款。',
             reply_needed=True,candidate_amount='9.91')
        f.publish();path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(m['issues'][0]['expense_inventory'][0]['candidate_amount'],'9.91')
        with fitz.open(path) as doc:
            text=''.join(p.get_text() for p in doc)
            self.assertIn('文件名候选 9.91元（待确认）',text)
        self.assertEqual(state(f.ws)['issues']['Q1']['status'],'pending')
        f.data['expenses']['E1']['candidate_amount']=9.91
        with self.assertRaises(ReviewError):validate(f.data)

    def test_unembedded_cjk_font_has_fixed_appearance_in_reader_copy(self):
        import fitz
        f=self.f
        with fitz.open() as doc:
            doc.new_page().insert_text((30,60),'中文原件金额123元',fontname='china-s')
            raw=doc.tobytes()
        append(f.ws,f.req('attachment',file={'name':'未嵌字体.pdf','base64':base64.b64encode(raw).decode()}))
        aid=next(iter(state(f.ws)['attachments']))
        append(f.ws,f.req('link',target={'asset_id':aid,'page':1,'box':[0,.04,.7,.1],'basis':'核对文字区域'}))
        path,manifest=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(manifest['materials'][0]['rendering'],'overview_fixed_appearance_216dpi')
        with fitz.open(path) as doc:
            self.assertEqual(len(doc[0].get_images()),1)
            self.assertLess(doc[0].get_image_info()[0]['bbox'][1],350)
            for p in doc:
                for font in p.get_fonts(full=True):
                    self.assertTrue(doc.extract_font(font[0])[3])

    def test_two_regions_on_one_source_page_are_visible_and_keep_reply_status(self):
        import io
        import fitz
        from PIL import Image
        from material_review_server import preview
        f = self.f
        im = Image.new('RGB', (200, 100), 'white')
        for x in range(100):
            for y in range(100):im.putpixel((x,y),(0,0,255))
        stream=io.BytesIO();im.save(stream,'PNG')
        append(f.ws,f.req('attachment',file={'name':'双色原件.png','base64':base64.b64encode(stream.getvalue()).decode()}))
        aid=next(iter(state(f.ws)['attachments']))
        for box in ([0,0,.5,1],[.5,0,.5,1]):
            append(f.ws,f.req('link',target={'asset_id':aid,'page':1,'box':box,'basis':'目视核对合成色块','label':'颜色核对区域'}))
        current=state(f.ws)
        self.assertEqual(current['issues']['Q1']['status'],'replied')
        self.assertEqual(len([r for r in current['issues']['Q1']['links'] if r.get('box')]),2)
        path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(len(m['annotations']),2)
        self.assertEqual(m['closeups'],[])
        self.assertEqual(len(m['overviews']),1)
        self.assertEqual(len(m['materials']),1)
        with fitz.open(path) as doc:
            self.assertEqual(len(doc),1)
            self.assertEqual(len(doc[0].get_images()),1)
            self.assertIn('颜色核对区域',doc[0].get_text())
        crop=Image.open(io.BytesIO(preview(f.data,current['attachments'][aid],f.ws,1,[0,0,.5,1])))
        self.assertLess(crop.getpixel((50,50))[0],10)
        self.assertGreater(crop.getpixel((50,50))[2],240)

    def test_invalid_region_never_records_or_changes_status(self):
        f=self.f
        for box,basis in [([0,0,2,1],'checked'),([0,0,float('nan'),1],'checked'),([0,0,.5,1],''),([0,0,True,1],'checked'),([0,0,.5],'checked')]:
            with self.assertRaises(ReviewError):
                append(f.ws,f.req('link',target={'asset_id':f.aid,'page':1,'box':box,'basis':basis}))
        self.assertEqual(state(f.ws)['revision'],0)

    def test_single_file_has_embedded_evidence_navigation_and_no_private_journal(self):
        import fitz
        f = self.f
        with fitz.open() as doc:
            doc.new_page(width=300, height=100).insert_text((10,30),'ORIGINAL EVIDENCE PAGE')
            raw = doc.tobytes()
        append(f.ws, f.req('attachment', text='PRIVATE_JOURNAL', file={
            'name':'证据.pdf', 'base64':base64.b64encode(raw).decode()}))
        aid = next(iter(state(f.ws)['attachments']))
        append(f.ws, f.req('link', target={'asset_id':aid, 'page':1,'box':[0,.1,1,.3],'basis':'核对文字区域'}))
        path, manifest = export_pdf(f.ws, ['1'], ['Q1'])
        f.path.unlink()
        with fitz.open(path) as doc:
            self.assertEqual(len(doc), 1)
            self.assertEqual(len(doc[0].get_images()),1)
            self.assertEqual(manifest['materials'][0]['rendering'],'overview_fixed_appearance_216dpi')
            self.assertEqual(doc.get_toc()[0][2],1)
            text = ''.join(p.get_text() for p in doc)
            self.assertNotIn('PRIVATE_JOURNAL', text)
            self.assertNotIn(str(f.root), text)
            self.assertTrue(doc.get_toc())
            self.assertTrue(any(doc.extract_font(font[0])[3] for font in doc[0].get_fonts()))
        self.assertEqual(manifest['materials'][0]['source_page'], 1)
        self.assertEqual(manifest['issue_ids'], ['Q1'])

    def test_reviewed_display_copy_is_bound_to_issue_and_preserves_answers(self):
        f=self.f
        append(f.ws,f.req())
        f.data['issues']['Q1']['feedback']={
            'issue_fingerprint':f.data['issues']['Q1']['fingerprint'],
            'project':'示例社团｜春季活动','request':'补本次活动明白纸','context':'已有发票已核对'}
        validate(f.data);f.publish()
        self.assertEqual(state(f.ws)['issues']['Q1']['status'],'replied')
        path,m=export_pdf(f.ws,['1'],['Q1'])
        import fitz
        with fitz.open(path) as doc:
            self.assertIn('示例社团｜春季活动',doc[0].get_text())
            self.assertIn('本次没有可显示的对应材料',doc[0].get_text())
            self.assertEqual(len(doc[0].get_images()),0)
        f.data['issues']['Q1']['feedback']['issue_fingerprint']='outdated'
        with self.assertRaises(ReviewError):validate(f.data)

    def test_export_uses_selected_evidence_instead_of_obsolete_annotation(self):
        import io,fitz
        from PIL import Image
        f=self.f;stream=io.BytesIO();Image.new('RGB',(200,100),'white').save(stream,'PNG')
        append(f.ws,f.req('attachment',file={'name':'原表.png','base64':base64.b64encode(stream.getvalue()).decode()}))
        aid=next(iter(state(f.ws)['attachments']))
        append(f.ws,f.req('link',target={'asset_id':aid,'page':1,'box':[0,0,1,.5],'basis':'旧定位','label':'已撤销的审批要求'}))
        # 对外区域只选择已核原件中的当前问题；既有定位及回复仍留在事件历史。
        # 固定索引来源用于测试，不把补件路径混入原资料合同。
        f.data['issues']['Q1']['feedback']={'issue_fingerprint':f.data['issues']['Q1']['fingerprint'],
            'project':'活动甲','request':'实际收款人','context':'','evidence':[]}
        f.publish();path,m=export_pdf(f.ws,['1'],['Q1'])
        self.assertEqual(m['closeups'],[])
        with fitz.open(path) as doc:self.assertNotIn('已撤销的审批要求',doc[0].get_text())
        self.assertEqual(len(state(f.ws)['issues']['Q1']['links']),2)

    def test_long_material_directory_paginates_and_reply_default_is_excluded(self):
        import fitz
        f = self.f
        with fitz.open() as doc:
            for n in range(22):
                doc.new_page().insert_text((30,30),f'PAGE {n+1}')
            raw = doc.tobytes()
        append(f.ws, f.req('attachment', file={'name':'多页.pdf', 'base64':base64.b64encode(raw).decode()}))
        aid = next(iter(state(f.ws)['attachments']))
        for n in range(22):
            append(f.ws, f.req('link', target={'asset_id':aid, 'page':n+1,'box':[0,.01,.8,.15],'basis':'合成逐页文字'}))
        with self.assertRaises(ReviewError):
            export_pdf(f.ws, ['1'])
        with self.assertRaises(ReviewError):
            export_pdf(f.ws, ['1'], ['Q1'], expected_snapshot=(f.version, 0))
        path, manifest = export_pdf(f.ws, ['1'], ['Q1'])
        with fitz.open(path) as doc:
            self.assertLess(len(doc), 22)
            self.assertEqual(sum(len(p.get_images()) for p in doc),22)
            self.assertEqual(len(manifest['materials']), 22)
            for page in doc:
                self.assertTrue(all(0 <= l['page'] < len(doc) for l in page.get_links()))


if __name__ == '__main__':
    unittest.main()
