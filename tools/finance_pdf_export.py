#!/usr/bin/env python3
"""分块冻结同案真实财务PDF预览；不导航、不提交、不称正式打印。"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from reimbursement_batch_plan import safe_filename
from reimbursement_standards import _pairs, encoded, iso_date, read_json, safe_path

ROOT = Path(__file__).resolve().parents[1]
KIND = 'system_view_preview_not_final_print'
MAX_BYTES = 64 * 1024 * 1024
MAX_CHUNK = 512 * 1024
DEFAULT_CHUNK = 128 * 1024
MAX_INTENT_JSON = 1024 * 1024
MAX_RECEIPT_JSON = 16 * 1024 * 1024
MAX_STAGE_JSON = 1024 * 1024
MAX_STAGE_SCRIPT = 256 * 1024
MAX_STAGE_ENTRIES = 2 * (MAX_BYTES // 1024 + 2) + 256
BINDING_KEYS = ('case_id', 'serial', 'tab_id', 'expected_userid', 'plan_sha256', 'destination')
IDENTITY_KEYS = ('case_id', 'serial', 'userid', 'origin', 'pathname', 'project', 'node', 'session_token',
                 'pdf_fingerprint', 'pages', 'size', 'sha256', 'pdf_serial', 'viewer_path','view_kind')

JS_COMMON = r'''
const c=CONFIG, cacheKey='__ustcReimbursementPdfExportV1';
const digest=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
function guard(){
 if(location.origin!=='https://cwzh.ustc.edu.cn'||location.pathname!=='/YBX/main.jsp'||
    window.$?.getProjID?.()!=='WF_YBX'||String($.UC.bindData('#0-userinfo.userid#')||'')!==c.expected_userid||
    String($.UC.bindData('#61-serialno#')||'')!==c.serial)throw Error('PDF_EXPORT_NATIVE_IDENTITY_MISMATCH');
 const outer=document.querySelector(c.view_kind==='details'?'#iframepage_pdf':'#iframepage_print');
 if(!outer||!outer.getClientRects().length)throw Error('PDF_EXPORT_PREVIEW_NOT_VISIBLE');
 const viewer=outer.contentDocument?.querySelector('iframe')?.contentWindow;
 if(!viewer||viewer.location.origin!==location.origin)throw Error('PDF_EXPORT_SAME_ORIGIN_VIEWER_REQUIRED');
 const pdf=viewer.PDFViewerApplication?.pdfDocument;
 if(!pdf||!Number.isSafeInteger(pdf.numPages)||pdf.numPages<1||pdf.numPages>2000)throw Error('PDF_EXPORT_DOCUMENT_NOT_READY');
 const fingerprint=pdf.fingerprints?.[0]||pdf.fingerprint;
 if(typeof fingerprint!=='string'||!fingerprint||fingerprint.length>256)throw Error('PDF_EXPORT_FINGERPRINT_REQUIRED');
 const node=String($.UC.bindData('#61-node_id#')??'');
 if(!node&&c.view_kind!=='details')throw Error('PDF_EXPORT_NATIVE_NODE_REQUIRED');
 return{pdf,fingerprint,node,viewerPath:viewer.location.origin+viewer.location.pathname};
}
async function documentSerial(pdf){
 const content=await(await pdf.getPage(1)).getTextContent();
 if(!Array.isArray(content.items))throw Error('PDF_EXPORT_FIRST_PAGE_TEXT_UNAVAILABLE');
 const text=content.items.map(x=>typeof x.str==='string'?x.str:'').join('').replace(/\s+/g,'');
 if(text.length>100000)throw Error('PDF_EXPORT_FIRST_PAGE_TEXT_OVER_LIMIT');
 const serials=[...text.matchAll(/报销单号[：:](\d+)/g)].map(x=>x[1]);
 if(!serials.length||serials.some(x=>x!==c.serial))throw Error('PDF_EXPORT_DOCUMENT_SERIAL_MISMATCH');
 return c.serial;
}
function state(){
 const d=Object.getOwnPropertyDescriptor(window,cacheKey);
 if(!d||!('value'in d))throw Error('PDF_EXPORT_ORIGINAL_SESSION_REQUIRED');
 const s=d.value,g=guard();
 if(s.token!==c.session_token||s.caseId!==c.case_id||s.serial!==c.serial||s.userid!==c.expected_userid||
    s.pdf!==g.pdf||s.fingerprint!==g.fingerprint||s.pages!==g.pdf.numPages||s.node!==g.node||s.viewerPath!==g.viewerPath)
    throw Error('PDF_EXPORT_DOCUMENT_CHANGED');
 return s;
}
function metadata(s){return{ok:true,kind:'system_view_preview_not_final_print',case_id:s.caseId,serial:s.serial,
 userid:s.userid,origin:location.origin,pathname:location.pathname,project:'WF_YBX',node:s.node,
 session_token:s.token,pdf_fingerprint:s.fingerprint,pdf_serial:s.serial,pages:s.pages,size:s.bytes.length,
 sha256:s.sha256,viewer_path:s.viewerPath,view_kind:c.view_kind||'print_preview',system_write:false,approval_verified:false,final_print_verified:false};}
'''

JS_ACTIONS = {
    'begin': r'''
if(Object.prototype.hasOwnProperty.call(window,cacheKey))throw Error('PDF_EXPORT_SESSION_ALREADY_EXISTS_RECOVER_ORIGINAL');
const initial=guard(),serial=c.delivery_mode==='rename_only'?c.serial:await documentSerial(initial.pdf),bytes=await initial.pdf.getData();
if(!bytes||!Number.isSafeInteger(bytes.length)||bytes.length<8||bytes.length>c.max_bytes)throw Error('PDF_EXPORT_SIZE_OUT_OF_BOUNDS');
if(String.fromCharCode(...bytes.subarray(0,5))!=='%PDF-')throw Error('PDF_EXPORT_INVALID_HEADER');
const sha256=await digest(bytes),current=guard();
if(current.pdf!==initial.pdf||current.fingerprint!==initial.fingerprint||current.node!==initial.node||
 current.viewerPath!==initial.viewerPath)throw Error('PDF_EXPORT_DOCUMENT_CHANGED_DURING_BEGIN');
const s={pdf:initial.pdf,bytes,sha256,pages:initial.pdf.numPages,fingerprint:initial.fingerprint,
 node:initial.node,viewerPath:initial.viewerPath,token:c.session_token,caseId:c.case_id,serial,userid:c.expected_userid};
Object.defineProperty(window,cacheKey,{value:s,configurable:true});
return{...metadata(s),action:'pdf_begin'};
''',
    'chunk': r'''
const s=state();
if(!Number.isSafeInteger(c.offset)||!Number.isSafeInteger(c.length)||c.offset<0||c.length<1||
 c.length>c.max_chunk||c.offset+c.length>s.bytes.length)throw Error('PDF_EXPORT_CHUNK_OUT_OF_BOUNDS');
const bytes=s.bytes.subarray(c.offset,c.offset+c.length),chunkSha=await digest(bytes);state();
let raw='';for(let i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode(...bytes.subarray(i,i+16384));
return{...metadata(s),action:'pdf_chunk',offset:c.offset,chunk_size:bytes.length,chunk_sha256:chunkSha,base64:btoa(raw)};
''',
    'finish': r'''
const s=state();
if(c.delivery_mode==='rename_only'){
 const result={...metadata(s),action:'pdf_finish',transport_finished:true,reverified:false,content_verified:false};delete window[cacheKey];return result;
}
await documentSerial(s.pdf);
const current=await s.pdf.getData(),currentSha=await digest(current);state();
if(current.length!==s.bytes.length||currentSha!==s.sha256)throw Error('PDF_EXPORT_DOCUMENT_BYTES_CHANGED');
const result={...metadata(s),action:'pdf_finish',reverified:true};delete window[cacheKey];return result;
'''
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _no_existing(path):
    if path.exists() or path.is_symlink() or (path.parent.exists() and
            any(x.name.casefold() == path.name.casefold() for x in path.parent.iterdir())):
        raise FileExistsError('保留已有文件，不覆盖：' + str(path))


def _request(case, plan, export_root, tab_id, expected_userid, silent, chunk_size):
    if not isinstance(case, dict) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', str(case.get('case_id', ''))):
        raise ValueError('须有明确本案case_id')
    serial = case.get('serial')
    if not isinstance(serial, str) or not re.fullmatch(r'\d+', serial):
        raise ValueError('须有已经绑定的真实财务单号serial')
    if not isinstance(tab_id, str) or not re.fullmatch(r'\d+', tab_id):
        raise ValueError('须显式给当前财务tab_id字符串')
    if not isinstance(expected_userid, str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', expected_userid):
        raise ValueError('须给本案预期登录人expected_userid')
    if case.get('expected_userid') not in (None, expected_userid):
        raise ValueError('本案登录人与请求不符')
    if type(silent) is not bool or type(chunk_size) is not int or not 1024 <= chunk_size <= MAX_CHUNK:
        raise ValueError('silent须为布尔值；chunk_size须为1024至524288字节整数')
    if not isinstance(plan, dict) or plan.get('schema') != 'ustc-finance-pdf-export-plan-v1':
        raise ValueError('须给ustc-finance-pdf-export-plan-v1计划')
    if plan.get('case_id') != case['case_id'] or plan.get('serial') != serial or plan.get('status') != 'confirmed':
        raise ValueError('导出计划须已确认并绑定同案同单号')
    if not isinstance(plan.get('source_refs'), list) or not plan['source_refs'] or any(not isinstance(x, str) or not x.strip() for x in plan['source_refs']):
        raise ValueError('导出计划须引用日期、序号和摘要的确认依据')
    day = iso_date(plan.get('business_date')).isoformat()
    sequence = plan.get('sequence')
    if not ((type(sequence) is int and sequence > 0) or
            (isinstance(sequence, str) and re.fullmatch(r'\d+', sequence) and int(sequence) > 0)):
        raise ValueError('导出序号未知，不能伪造')
    summary = plan.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('导出摘要未知')
    separator = plan.get('filename_separator', '_')
    if separator not in ('_', ' '):
        raise ValueError('导出文件名分隔仅支持下划线或单空格')
    delivery_mode = plan.get('delivery_mode', 'rename_only')
    if delivery_mode not in ('rename_only', 'content'):
        raise ValueError('导出后处理仅支持rename_only或显式content核对')
    view_kind=plan.get('view_kind','print_preview')
    if view_kind not in ('print_preview','details'):raise ValueError('仅支持已核同案打印预览或查看明细PDF入口')
    relative = day + '/' + safe_filename(str(sequence) + separator + summary) + '.pdf'
    if plan.get('relative_path') not in (None, relative):
        raise ValueError('计划relative_path与确认日期/序号/摘要不一致')
    destination = safe_path(export_root, relative)
    _no_existing(destination)
    return {'case_id': case['case_id'], 'serial': serial, 'tab_id': tab_id, 'expected_userid': expected_userid,
            'silent': silent, 'chunk_size': chunk_size, 'max_bytes': MAX_BYTES, 'max_chunk': MAX_CHUNK,
            'relative_path': relative, 'destination': str(destination), 'plan_sha256': digest(encoded(plan)),
            'delivery_mode': delivery_mode,'view_kind':view_kind}


def pdf_script(action, config):
    if action not in JS_ACTIONS:
        raise ValueError('只允许begin/chunk/finish固定读取动作')
    return '(async()=>{\n' + JS_COMMON.replace('CONFIG', json.dumps(config, ensure_ascii=False)) + JS_ACTIONS[action] + '\n})()'


def _metadata(value, request):
    if not isinstance(value, dict) or value.get('ok') is not True or value.get('kind') != KIND:
        raise ValueError('未取得成功的真实PDF预览回执')
    expected = {'case_id': request['case_id'], 'serial': request['serial'], 'userid': request['expected_userid'],
                'origin': 'https://cwzh.ustc.edu.cn', 'pathname': '/YBX/main.jsp', 'project': 'WF_YBX',
                'pdf_serial': request['serial'], 'session_token': request['session_token']}
    if any(value.get(k) != v for k, v in expected.items()):
        raise ValueError('原生PDF回执案件、登录人、单号、会话或页面身份不符')
    if (type(value.get('size')) is not int or not 8 <= value['size'] <= MAX_BYTES
            or type(value.get('pages')) is not int or not 1 <= value['pages'] <= 2000
            or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('sha256', '')))
            or not isinstance(value.get('pdf_fingerprint'), str) or not value['pdf_fingerprint']
            or not isinstance(value.get('node'), str) or (not value['node'] and request.get('view_kind')!='details')
            or not re.fullmatch(r'[a-f0-9]{32}', str(value.get('session_token', '')))
            or not isinstance(value.get('viewer_path'), str) or not value['viewer_path'].startswith('https://cwzh.ustc.edu.cn/')):
        raise ValueError('原生PDF回执长度、页数、SHA或文档指纹无效')
    if value.get('view_kind','print_preview')!=request.get('view_kind','print_preview'):
        raise ValueError('原生PDF入口类型改变')
    return value


def _same_document(value, initial, request):
    _metadata(value, request)
    if any(value.get(k,'print_preview' if k=='view_kind' else None) != initial.get(k,'print_preview' if k=='view_kind' else None) for k in IDENTITY_KEYS):
        raise ValueError('PDF读取期间文档身份、页数、长度或SHA改变')


def decode_chunk(value, initial, request, offset, length):
    _same_document(value, initial, request)
    if value.get('action') != 'pdf_chunk' or value.get('offset') != offset or value.get('chunk_size') != length:
        raise ValueError('PDF块顺序、偏移或长度不符')
    if length > MAX_CHUNK or length < 1 or not isinstance(value.get('base64'), str):
        raise ValueError('PDF块超出有界读取范围')
    if len(value['base64']) > 4 * ((length + 2) // 3):
        raise ValueError('PDF块base64超过预期长度')
    try:
        raw = base64.b64decode(value['base64'], validate=True)
    except ValueError as exc:
        raise ValueError('PDF块base64无效') from exc
    if len(raw) != length or digest(raw) != value.get('chunk_sha256'):
        raise ValueError('PDF块内容SHA或解码长度不符')
    return raw


def _verified_pdf(raw, metadata):
    if not isinstance(raw, bytes) or not raw.startswith(b'%PDF-') or len(raw) != metadata['size'] or digest(raw) != metadata['sha256']:
        raise ValueError('完整PDF的头部、长度或SHA不符')
    import pymupdf
    with pymupdf.open(stream=raw, filetype='pdf') as document:
        if document.needs_pass or document.is_repaired or len(document) != metadata['pages']:
            raise ValueError('PDF加密、损坏修复或真实页数与原生回执不符')
        serials = re.findall(r'报销单号[：:](\d+)', re.sub(r'\s+', '', document[0].get_text()))
        if not serials or any(x != metadata['serial'] for x in serials):
            raise ValueError('导出PDF正文中的报销单号缺失或与本案不同')


def _atomic_new(path, raw):
    """排他硬链接发布同目录临时文件，两平台均不覆盖竞争文件。"""
    _no_existing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.partial')
    with temporary.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
        if digest(path.read_bytes()) != digest(raw):
            raise ValueError('发布后回读SHA不符；保留现场，不重放导出')
    finally:
        temporary.unlink()


def _evidence_result(entry, tab_id):
    path = Path(entry['path'])
    if digest(path.read_bytes()) != entry['sha256']:
        raise ValueError('原生读取证据SHA变化')
    record = read_json(path)
    if (record.get('phase') != 'script_completed' or record.get('tab_id') != tab_id
            or digest(Path(record['script']).read_bytes()) != record.get('source_sha256')):
        raise ValueError('原生回执未完成或标签/脚本身份不符')
    return record.get('result', {})


def persist_verified_pdf(raw, metadata, case, export_plan, export_root, receipt_path,
                         *, tab_id, expected_userid, source_evidence=None):
    """无runtime证据链时只允许显式合成测试，输出明确标为合成。"""
    request = _request(case, export_plan, export_root, tab_id, expected_userid, True, DEFAULT_CHUNK)
    request['session_token'] = metadata.get('session_token')
    _metadata(metadata, request)
    if request['delivery_mode'] == 'content':
        _verified_pdf(raw, metadata)
    elif not isinstance(raw, bytes) or len(raw) != metadata['size']:
        raise ValueError('下载字节尚未完整，不能归档')
    synthetic = metadata.get('source_type') == 'synthetic_test'
    if source_evidence is None and not synthetic:
        raise ValueError('缺少原生读取证据；不得将任意PDF当作真实财务导出')
    if source_evidence is not None:
        if not isinstance(source_evidence, dict) or not all(source_evidence.get(k) for k in ('begin', 'finish', 'chunks')):
            raise ValueError('真实PDF须有完整begin/chunks/finish回执链')
        for kind in ('begin', 'finish'):
            observed = _evidence_result(source_evidence[kind], tab_id)
            _same_document(observed, metadata, request)
            finished = observed.get('transport_finished') is True if request['delivery_mode']=='rename_only' else observed.get('reverified') is True
            if observed.get('action') != 'pdf_' + kind or (kind == 'finish' and not finished):
                raise ValueError('缺少同文档最终重验回执')
        offset, complete_hash = 0, hashlib.sha256()
        for entry in source_evidence['chunks']:
            observed = _evidence_result(entry, tab_id)
            if entry.get('offset') != offset or entry.get('sha256_bytes') != observed.get('chunk_sha256'):
                raise ValueError('PDF块证据索引偏移或SHA不符')
            complete_hash.update(decode_chunk(observed, metadata, request, offset, entry['size']))
            offset += entry['size']
        if offset != len(raw) or complete_hash.hexdigest() != digest(raw):
            raise ValueError('读取证据链遗漏/重复PDF块或与最终字节不同')
    receipt = safe_path(Path(receipt_path).parent, Path(receipt_path).name)
    destination = Path(request['destination'])
    if receipt == destination:
        raise ValueError('回执和PDF不能使用同一路径')
    _no_existing(receipt)
    result = {'schema': 'ustc-finance-pdf-export-v1', 'ok': True, 'status': 'ready', 'case_id': case['case_id'],
              'serial': case['serial'], 'kind': 'synthetic_test_pdf_not_finance' if synthetic else KIND,
              'source_type': 'synthetic_test' if synthetic else 'native_pdf_viewer',
              'pdf': str(destination), 'pdf_sha256': digest(raw), 'size': len(raw), 'pages': metadata['pages'],
              'plan_sha256': request['plan_sha256'], 'export_plan': export_plan,
              'document_identity': {k: metadata.get(k,'print_preview') if k=='view_kind' else metadata[k] for k in IDENTITY_KEYS}, 'source_evidence': source_evidence,
              'system_write': False, 'approval_verified': False, 'final_print_verified': False,
              'delivery_mode':request['delivery_mode'], 'content_verified':request['delivery_mode']=='content',
              'needed_inputs': [], 'receipt': str(receipt)}
    _atomic_new(destination, raw)
    _atomic_new(receipt, encoded(result))
    return result


def _inspect_action(output):
    return {'read_only': True, 'output_directory': str(output),
            'cli': {'script': str(ROOT / 'tools/finance_pdf_export.py'), 'args': ['--inspect-output', str(output)]},
            'instruction': '只读原目录意图、分块回执与marker；不换任务名/目录重放，已有PDF只核对不覆盖。'}


def _not_reparse(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('PDF检查路径不能经过链接或重解析点：' + str(path))
    return info


def _inspection_root(value):
    path = Path(value)
    _not_reparse(path)
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('PDF检查须明确已有任务目录')
    return root


def _read_snapshot(root, relative_path, limit):
    """从同一受界文件句柄读取一次；解析和SHA都使用返回的冻结字节。"""
    path = safe_path(root, relative_path)
    before_path = _not_reparse(path)
    if not stat.S_ISREG(before_path.st_mode) or before_path.st_size > limit:
        raise ValueError('PDF检查只读有界普通文件：' + relative_path)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    attributes = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    cross_attributes = attributes[:-1] if os.name == 'nt' else attributes
    def identity(value, fields=attributes):
        return tuple(getattr(value, name) for name in fields)
    try:
        before = os.fstat(fd)
        safe_path(root, relative_path)
        current = _not_reparse(path)
        if (not stat.S_ISREG(before.st_mode) or before.st_size > limit
                or identity(before_path) != identity(current)
                or identity(before, cross_attributes) != identity(current, cross_attributes)):
            raise ValueError('PDF检查文件在打开时被替换')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            raw = handle.read(limit + 1)
        after = os.fstat(fd)
        safe_path(root, relative_path)
        current = _not_reparse(path)
        # Windows路径stat与fd stat的ctime语义不同，仍分别守住各自前后完整身份。
        if (len(raw) > limit or identity(before) != identity(after) or identity(before_path) != identity(current)
                or identity(after, cross_attributes) != identity(current, cross_attributes)):
            raise ValueError('PDF检查文件在读取期间变化或超限')
    finally:
        os.close(fd)
    return raw, {'path': str(path), 'relative_path': relative_path, 'size': len(raw), 'sha256': digest(raw)}


def _json_snapshot(root, relative_path, limit):
    raw, snapshot = _read_snapshot(root, relative_path, limit)
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError('非法JSON数值：' + x)))
    if not isinstance(value, dict):
        raise ValueError('PDF检查回执须为JSON对象：' + relative_path)
    return value, snapshot


def _absolute_destination(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('冻结PDF目的地须为明确绝对路径')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path.suffix.lower() != '.pdf':
        raise ValueError('冻结PDF目的地须为无上级跳转的绝对PDF路径')
    return path


def _inspect_binding(intent, expected_binding):
    request = intent.get('request')
    if not isinstance(request, dict) or any(key not in request for key in BINDING_KEYS):
        raise ValueError('原意图缺少案件、单号、标签、登录人、计划SHA或目的地绑定')
    if (not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', str(request['case_id']))
            or not isinstance(request['serial'], str) or not re.fullmatch(r'\d+', request['serial'])
            or not isinstance(request['tab_id'], str) or not re.fullmatch(r'\d+', request['tab_id'])
            or not isinstance(request['expected_userid'], str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', request['expected_userid'])
            or not re.fullmatch(r'[a-f0-9]{64}', str(request['plan_sha256']))):
        raise ValueError('原意图冻结身份格式不合法')
    destination = _absolute_destination(request['destination'])
    if expected_binding is not None:
        if not isinstance(expected_binding, dict) or any(key not in expected_binding for key in BINDING_KEYS):
            raise ValueError('expected_binding须包含原任务冻结的六个身份/目的地字段')
        for key in BINDING_KEYS:
            actual, expected = request[key], expected_binding[key]
            if key == 'destination':
                actual, expected = destination, _absolute_destination(expected)
            if actual != expected:
                raise ValueError('PDF原意图与原任务冻结绑定不符：' + key)
        if expected_binding.get('job_status') not in (None, 'completed', 'failed', 'unknown'):
            raise ValueError('只检查已结束或unknown的原任务，不接续正在运行任务')
    plan = intent.get('export_plan')
    if (not isinstance(plan, dict) or plan.get('schema') != 'ustc-finance-pdf-export-plan-v1'
            or plan.get('case_id') != request['case_id'] or plan.get('serial') != request['serial']
            or plan.get('status') != 'confirmed' or digest(encoded(plan)) != request['plan_sha256']):
        raise ValueError('原意图的导出计划内容与冻结身份/SHA不符')
    day = iso_date(plan.get('business_date')).isoformat()
    sequence, summary = plan.get('sequence'), plan.get('summary')
    if not ((type(sequence) is int and sequence > 0) or
            (isinstance(sequence, str) and re.fullmatch(r'\d+', sequence) and int(sequence) > 0)):
        raise ValueError('原导出计划缺少明确序号')
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('原导出计划缺少明确摘要')
    separator=plan.get('filename_separator','_')
    if separator not in ('_',' '):raise ValueError('原意图文件名分隔无效')
    relative = day + '/' + safe_filename(str(sequence) + separator + summary) + '.pdf'
    if request.get('relative_path') != relative or destination.parent.name != day or destination.name != Path(relative).name:
        raise ValueError('原意图PDF目的地与确认日期/序号/摘要不符')
    export_root = _inspection_root(destination.parent.parent)
    if safe_path(export_root, relative) != destination:
        raise ValueError('原意图PDF目的地发生重定向')
    return request, destination, export_root, relative


def _inspect_evidence_paths(evidence, output, snapshots, complete=False):
    if evidence is None and not complete:
        return
    if not isinstance(evidence, dict) or not isinstance(evidence.get('chunks'), list):
        raise ValueError('PDF原生证据索引格式不符')
    if complete and (not evidence.get('begin') or not evidence.get('finish') or not evidence['chunks']):
        raise ValueError('真实PDF回执缺少完整阶段证据')
    known = {Path(x['path']): x['sha256'] for x in snapshots}
    for role, entries in (('begin', [evidence.get('begin')]), ('finish', [evidence.get('finish')]), ('chunk', evidence['chunks'])):
        for entry in entries:
            if entry is None:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                raise ValueError('PDF阶段证据须指向固定任务文件')
            path = Path(entry['path'])
            if (not path.is_absolute() or path.parent != output / 'stages'
                    or not re.fullmatch(r'\d{5}_' + role + r'\.json', path.name)):
                raise ValueError('PDF阶段证据路径越界或不是固定角色文件')
            if path not in known or known[path] != entry.get('sha256'):
                raise ValueError('PDF阶段证据与同字节检查快照不符')


def inspect_export(output_directory, expected_binding=None):
    """只读冻结身份下的固定回执；unknown任务仅核对字节，绝不升级为completed。"""
    output = _inspection_root(output_directory)
    intent, intent_snapshot = _json_snapshot(output, 'intent.json', MAX_INTENT_JSON)
    request, destination, export_root, pdf_relative = _inspect_binding(intent, expected_binding)
    snapshots, records = [intent_snapshot], {}
    for name in ('export_receipt.json', 'failure.json'):
        path = safe_path(output, name)
        if path.exists():
            records[name], snapshot = _json_snapshot(output, name, MAX_RECEIPT_JSON)
            snapshots.append(snapshot)
    failure = records.get('failure.json')
    if failure and (failure.get('case_id') != request['case_id'] or failure.get('serial') != request['serial']):
        raise ValueError('失败回执不属于原任务案件/单号')
    stages = []
    stage_dir = safe_path(output, 'stages')
    if stage_dir.exists():
        entries = []
        for path in stage_dir.iterdir():
            if len(entries) >= MAX_STAGE_ENTRIES:
                raise ValueError('PDF阶段文件数量超限')
            _not_reparse(path)
            entries.append(path)
        for path in sorted(entries):
            if path.suffix != '.json':
                continue
            if not re.fullmatch(r'\d{5}_(?:begin|chunk|finish)\.json', path.name):
                raise ValueError('非固定PDF阶段回执名：' + path.name)
            record, snapshot = _json_snapshot(output, 'stages/' + path.name, MAX_STAGE_JSON)
            snapshots.append(snapshot)
            script_relative = 'stages/' + path.with_suffix('.js').name
            script = safe_path(output, script_relative)
            if record.get('tab_id') != request['tab_id'] or not isinstance(record.get('script'), str) or Path(record['script']) != script:
                raise ValueError('PDF阶段标签或脚本路径与原任务不符')
            raw_script, script_snapshot = _read_snapshot(output, script_relative, MAX_STAGE_SCRIPT)
            snapshots.append(script_snapshot)
            if digest(raw_script) != record.get('source_sha256'):
                raise ValueError('PDF阶段脚本SHA与回执不符')
            body = record.get('result', {})
            if not isinstance(body, dict):
                raise ValueError('PDF阶段结果须为对象')
            for key, expected in (('case_id', request['case_id']), ('serial', request['serial']),
                                  ('userid', request['expected_userid']), ('session_token', request.get('session_token'))):
                if key in body and body[key] != expected:
                    raise ValueError('PDF阶段结果不属于原任务：' + key)
            stages.append({'receipt': snapshot['path'], 'sha256': snapshot['sha256'], 'phase': record.get('phase'),
                           'marker': record.get('result_marker'), 'action': body.get('action'), 'ok': body.get('ok')})
    job_status = expected_binding.get('job_status') if expected_binding is not None else None
    if failure:
        _inspect_evidence_paths(failure.get('source_evidence'), output, snapshots)
    common = {'case_id': request['case_id'], 'serial': request['serial'], 'system_write': False,
              'approval_verified': False, 'final_print_verified': False, 'source_job_status': job_status,
              'binding_verified': expected_binding is not None, 'evidence_snapshots': snapshots,
              'stages': stages, 'next_action': _inspect_action(output)}
    result = records.get('export_receipt.json')
    if result is not None:
        if (_absolute_destination(result.get('pdf')) != destination
                or result.get('case_id') != request['case_id'] or result.get('serial') != request['serial']
                or result.get('plan_sha256') != request['plan_sha256']
                or digest(encoded(result.get('export_plan'))) != request['plan_sha256']):
            raise ValueError('PDF回执目的地或计划不等于原任务冻结绑定')
        identity = result.get('document_identity')
        if not isinstance(identity, dict):
            raise ValueError('PDF回执缺少原生文档身份')
        for key, expected in (('case_id', request['case_id']), ('serial', request['serial']),
                              ('userid', request['expected_userid']), ('session_token', request.get('session_token'))):
            if identity.get(key) != expected:
                raise ValueError('PDF回执原生身份与原意图不同：' + key)
        if (result.get('kind') not in (KIND, 'synthetic_test_pdf_not_finance')
                or not re.fullmatch(r'[a-f0-9]{64}', str(result.get('pdf_sha256', '')))):
            raise ValueError('PDF回执种类或字节SHA不合法')
        _inspect_evidence_paths(result.get('source_evidence'), output, snapshots, complete=result['kind'] == KIND)
        verified = False
        if destination.exists():
            raw, snapshot = _read_snapshot(export_root, pdf_relative, MAX_BYTES)
            snapshots.append({**snapshot, 'role': 'exported_pdf'})
            verified = digest(raw) == result['pdf_sha256'] and len(raw) == result.get('size')
        ready = verified and job_status not in ('unknown', 'failed')
        problems = [] if ready else [{'field': 'original_export', 'code': 'ORIGINAL_JOB_UNRESOLVED' if verified else 'PDF_READBACK_MISMATCH',
                                     'detail': '仅核验现有字节；原任务仍未完成，不自动升级状态' if verified else 'PDF缺失或当前字节与原回执不符'}]
        return {**result, **common, 'status': 'ready' if ready else 'blocked', 'ok': ready,
                'artifact_readback_verified': verified, 'needed_inputs': problems}
    return {**common, 'ok': False, 'status': 'blocked', 'kind': KIND,
            'pdf_exists': destination.exists(), 'artifact_readback_verified': False,
            'needed_inputs': [{'field': 'original_export', 'code': 'EXPORT_NOT_CLOSED',
                               'detail': '本次导出未形成完整落盘回执；核对已有块与原marker，不重复执行'}]}


def _runtime_stage(action, request, output, index):
    source = pdf_script(action, request)
    script, receipt = output / f'{index:05d}_{action}.js', output / f'{index:05d}_{action}.json'
    with script.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(source)
    command = [sys.executable, str(ROOT / 'tools/edge_runtime.py'), str(script), '--output', str(receipt),
               '--quiet', '--tab-id', request['tab_id'], '--timeout', '45', '--operation-kind',
               'read' if action == 'chunk' else 'browser-local']
    if not request['silent']:
        command.append('--foreground')
    result = subprocess.run(command, capture_output=True, timeout=60,
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    if not receipt.is_file():
        raise RuntimeError('原运行器未留下读取回执；保留原任务，不换目录重试')
    record = read_json(receipt)
    if (result.returncode or record.get('phase') != 'script_completed'
            or record.get('tab_id') != request['tab_id'] or record.get('source_sha256') != digest(source.encode('utf-8'))
            or not isinstance(record.get('result'), dict) or record['result'].get('ok') is not True):
        raise RuntimeError('原PDF读取未确认完成，核对原回执/marker：' + str(receipt))
    return record['result'], {'path': str(receipt), 'sha256': digest(receipt.read_bytes())}


def export_pdf(case, export_plan, export_root, output_directory, tab_id, expected_userid,
               silent=True, execute=False, chunk_size=DEFAULT_CHUNK):
    request = _request(case, export_plan, export_root, tab_id, expected_userid, silent, chunk_size)
    if type(execute) is not bool:
        raise ValueError('execute须为布尔值')
    preview = {'schema': 'ustc-finance-pdf-export-preview-v1', 'status': 'ready', 'ok': True,
               'kind': KIND, **request, 'execute': execute, 'executed': False, 'system_write': False,
               'approval_verified': False, 'final_print_verified': False, 'needed_inputs': [],
               'next_action': {'method': 'capabilities', 'params': {'intent': 'export'},
                               'instruction': '按本案固定PDF导出入口接续，执行时绑定同一计划和当前财务标签。'}}
    if not execute:
        return preview
    if request['delivery_mode']=='content':
        import pymupdf  # 仅显式正文核对模式需要PDF解析器。
    if Path(output_directory).is_symlink():
        raise ValueError('新的任务目录不能是符号链接')
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=False)
    request['session_token'] = uuid.uuid4().hex
    (output / 'intent.json').write_bytes(encoded({'request': request, 'export_plan': export_plan, 'system_write': False}))
    stage_dir = output / 'stages'
    stage_dir.mkdir()
    evidence = {'begin': None, 'chunks': [], 'finish': None}
    try:
        initial, evidence['begin'] = _runtime_stage('begin', request, stage_dir, 0)
        _metadata(initial, request)
        if initial.get('action') != 'pdf_begin':
            raise ValueError('首次读取不是原PDF冻结回执')
        raw = bytearray()
        for index, offset in enumerate(range(0, initial['size'], chunk_size), 1):
            length = min(chunk_size, initial['size'] - offset)
            chunk, receipt = _runtime_stage('chunk', {**request, 'offset': offset, 'length': length}, stage_dir, index)
            raw.extend(decode_chunk(chunk, initial, request, offset, length))
            evidence['chunks'].append({**receipt, 'offset': offset, 'size': length, 'sha256_bytes': chunk['chunk_sha256']})
        if digest(bytes(raw)) != initial['sha256']:
            raise ValueError('所有读取块合并后的SHA与首次完整文档不同')
        final, evidence['finish'] = _runtime_stage('finish', request, stage_dir, len(evidence['chunks']) + 1)
        _same_document(final, initial, request)
        finished=final.get('transport_finished') is True if request['delivery_mode']=='rename_only' else final.get('reverified') is True
        if final.get('action') != 'pdf_finish' or not finished:
            raise ValueError('缺少原文档传输完成回执')
        result = persist_verified_pdf(bytes(raw), initial, case, export_plan, export_root,
                                      output / 'export_receipt.json', tab_id=tab_id, expected_userid=expected_userid,
                                      source_evidence=evidence)
        return {**result, 'executed': True, 'silent': silent, 'next_action': _inspect_action(output)}
    except Exception as exc:
        result = {'schema': 'ustc-finance-pdf-export-failure-v1', 'ok': False, 'status': 'blocked',
                  'case_id': case['case_id'], 'serial': case['serial'], 'kind': KIND, 'error': str(exc),
                  'output_directory': str(output), 'source_evidence': evidence, 'system_write': False,
                  'executed': None, 'read_attempted': True, 'execution_status': 'incomplete_read_inspect_original',
                  'approval_verified': False, 'final_print_verified': False,
                  'needed_inputs': [{'field': 'original_read_receipt', 'code': 'PDF_READ_NOT_VERIFIED',
                                     'detail': '核对原任务回执及marker；不换任务名/目录重放未知读取'}],
                  'next_action': _inspect_action(output)}
        with (output / 'failure.json').open('xb') as handle:
            handle.write(encoded(result))
        return result


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-output', type=Path)
    for name in ('case', 'plan', 'export-root', 'output-directory'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--tab-id')
    parser.add_argument('--expected-userid')
    parser.add_argument('--silent', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--chunk-size', type=int, default=None)
    args = parser.parse_args()
    if args.inspect_output:
        if args.execute or any(getattr(args, k) for k in ('case', 'plan', 'export_root', 'output_directory', 'tab_id', 'expected_userid')):
            parser.error('--inspect-output只读，不能混用执行参数')
        result = inspect_export(args.inspect_output)
    else:
        if any(getattr(args, k) is None for k in ('case', 'plan', 'export_root', 'output_directory', 'tab_id', 'expected_userid')):
            parser.error('须给--case/--plan/--export-root/--output-directory/--tab-id/--expected-userid；旧--pdf/--receipt入口已停用')
        chunk_size=args.chunk_size if args.chunk_size is not None else (MAX_CHUNK if os.environ.get('REIMBURSEMENT_EDGE_BACKEND')=='edge_native_messaging' else DEFAULT_CHUNK)
        result = export_pdf(read_json(args.case), read_json(args.plan), args.export_root, args.output_directory,
                            args.tab_id, args.expected_userid, args.silent, args.execute, chunk_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'ready' else 2


if __name__ == '__main__':
    raise SystemExit(main())
