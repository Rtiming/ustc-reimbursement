"""统一操作入口的本地检查。核心仅用标准库；不连接财务系统。"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from audit_materials import audit
from validate_config import load, load_basic, load_funding

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'data/operator_default.json'


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def bundle(path):
    path = Path(path).resolve()
    b = json.loads(path.read_text(encoding='utf-8'))
    if b.get('bundle_version') != 1 or not b.get('case_id'):
        raise ValueError('案件入口缺少bundle_version=1/case_id')
    paths = {key: (path.parent / b[key]).resolve() for key in ['case', 'form', 'invoices', 'state']}
    return b, paths


def check(path, hashes=False, validation_stage=None):
    if validation_stage not in [None, 'basic', 'funding']: raise ValueError('未知分阶段校验')
    b, paths = bundle(path)
    errors = []
    records = {}
    for key, p in paths.items():
        try:
            records[key] = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            errors.append({'field': key, 'error': str(exc)})
    for key, record in records.items():
        if record.get('case_id') != b['case_id']:
            errors.append({'field': key + '.case_id', 'error': '不同案件的资料不能混用'})
    c = records.get('form', {})
    serial = c.get('serial')
    for key in ['case', 'invoices', 'state']:
        if records.get(key, {}).get('serial') != serial:
            errors.append({'field': key + '.serial', 'error': '单号与填报配置不一致'})
    phase = 'existing_claim' if serial else 'before_creation'
    if serial:
        try:
            (load_basic if validation_stage == 'basic' else load_funding if validation_stage == 'funding' else load)(paths['form'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors.append({'field': 'form', 'error': str(exc)})
    else:
        errors.append({'field': 'form.serial', 'error': '尚无单号；先完成材料和发票准备，创建后登记单号'})
    materials = None
    if records.get('case'):
        try:
            materials = audit(records['case'], json.loads((ROOT / 'data/material_rules.json').read_text(encoding='utf-8')))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'field': 'case.material_context', 'error': str(exc)})
    rows = records.get('invoices', {}).get('original_files', [])
    if not rows:
        errors.append({'field': 'invoices.original_files', 'error': '尚未登记一票一文件的原件路径和SHA256'})
    count = c.get('expected_invoice_count')
    if count is not None and count != len(rows):
        errors.append({'field': 'form.expected_invoice_count', 'error': '原件数量不一致'})
    verified = 0
    if hashes:
        for i, row in enumerate(rows):
            try:
                if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('原件SHA256变化')
                verified += 1
            except (OSError, KeyError, ValueError) as exc:
                errors.append({'field': f'invoices.original_files[{i}]', 'error': str(exc)})
    return {'ok': not errors, 'case_id': b['case_id'], 'serial': serial, 'phase': phase,
            'local_check_only': True, 'errors': errors, 'original_count': len(rows),
            'original_sha_verified_count': verified, 'hashes_checked': hashes,
            'material_audit': materials, 'paths': {k: str(v) for k, v in paths.items()}}


def probe(command):
    try:
        p = subprocess.run(command, text=True, capture_output=True, timeout=15)
        return {'ok': p.returncode == 0, 'output': (p.stdout or p.stderr).strip()[:1500]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'ok': False, 'output': str(exc)}


def doctor(configure=False):
    bins = {name: shutil.which(name) for name in ['pdftotext', 'pdftoppm', 'tesseract', 'soffice', 'node']}
    versions = {}
    for name, args in [('pdftotext', ['-v']), ('pdftoppm', ['-v']), ('tesseract', ['--version']), ('soffice', ['--version'])]:
        if bins[name]:
            versions[name] = probe([bins[name], *args])['output'].splitlines()[:1]
    langs = probe([bins['tesseract'], '--list-langs']) if bins['tesseract'] else {'ok': False, 'output': ''}
    chinese_ocr = langs['ok'] and {'chi_sim', 'eng'}.issubset(set(langs['output'].splitlines()))
    font_dirs = [str(p) for p in [Path('/System/Library/Fonts'), Path('/Library/Fonts'), Path.home()/'Library/Fonts',
                  Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts', Path('/usr/share/fonts'), Path('/usr/local/share/fonts'),
                  Path.home()/'.local/share/fonts'] if p.is_dir()]
    core = sys.version_info >= (3, 10)
    result = {'generated_at_utc': stamp(), 'platform': platform.system(), 'python': sys.executable,
              'python_version': platform.python_version(), 'core_ready': core,
              'commands': bins, 'versions': versions, 'ocr_ready': bool(bins['pdftotext'] and bins['pdftoppm'] and chinese_ocr),
              'ocr_language': 'chi_sim+eng', 'document_generator_ready': core,
              'document_render_available': bool(bins['soffice'] and bins['pdftoppm']),
              'native_edge_bridge_available': platform.system() == 'Darwin' and Path('/Applications/Microsoft Edge.app').exists(),
              'browser_login_and_script_permission': 'not_checked_by_local_doctor',
              'cross_device_live_verified': False, 'font_directories': font_dirs,
              'system_write': False, 'configured': configure}
    if configure:
        write_json(ROOT / '.local/environment.json', result)
        # 本机路径自动生成，不写入共享参数；子进程使用宿主字体解决转换缺字。
        if font_dirs:
            from xml.sax.saxutils import escape
            conf = '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
            conf += ''.join('<dir>' + escape(x) + '</dir>' for x in font_dirs)
            conf += '<cachedir>' + escape(str(ROOT/'.local/font-cache')) + '</cachedir></fontconfig>'
            (ROOT/'.local/fonts.conf').write_text(conf, encoding='utf-8')
        result['configuration_file'] = str(ROOT / '.local/environment.json')
    return result


def child_env():
    env = os.environ.copy()
    # 所有任务日志都按 UTF-8 保存，子进程不能沿用 Windows 的区域编码。
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    p = ROOT/'.local/environment.json'
    if p.exists():
        c = json.loads(p.read_text(encoding='utf-8'))
        dirs = [str(Path(x).parent) for x in c.get('commands', {}).values() if x and Path(x).is_file()]
        env['PATH'] = os.pathsep.join(dict.fromkeys(dirs + [env.get('PATH', '')]))
    fonts = ROOT/'.local/fonts.conf'
    if fonts.exists():
        env['FONTCONFIG_FILE'] = str(fonts)
    return env


def init_case(case_id, destination, profile):
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', case_id):
        raise ValueError('case-id使用2至64位小写字母、数字、下划线或短横线，以字母开头')
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('目标目录已存在，避免覆盖旧案；请指定新目录')
    c = {'case_id': case_id, 'serial': None, 'system_mode': 'daily', 'amount': '', 'funding': [], 'expense_allocations': [],
         'payments': [], 'basic': {'teacher_id': '', 'teacher_name': '', 'fields': {'t-abst': '', 't-req_mobile': '', 't-req_tele': '', 't-cl_abst': ''}},
         'pre_review': [], 'explanations': {'general': '', 'card_notes': [], 'record_only': True},
         'contract': {'exists': False, 'source': '用户默认无合同；须核对本案实际合同材料，有合同则据实关联'},
         'review': {'ordinary_promise_authorized': False, 'workflow_started': False},
         'delivery': {'mode': 'self_print', 'delegate_finance_print': False, 'originals_required': None}}
    defaults=json.loads((ROOT/'data/workflow_defaults.json').read_text(encoding='utf-8'))
    c['contract'] = {'exists': defaults.get('contract_exists_default'),
                     'source': defaults.get('source', '待本案核对合同与办理口径')}
    c['delivery']['delegate_finance_print'] = defaults.get('delegate_finance_print_default')
    c['budget_allocation_mode']=defaults['single_funding_budget_allocation_default']
    # 空案只带字段，不复制本案姓名、工号、经费、原因或授权。
    records = {'form.json': c, 'case.json': {'case_id': case_id, 'serial': None, 'material_context': {'profiles': [profile]}, 'materials': []},
               'invoices.json': {'case_id': case_id, 'serial': None, 'expected_count': None, 'original_files': []},
               'state.json': {'case_id': case_id, 'serial': None, 'status': 'intake', 'completed': [], 'pending': ['collect_inputs'], 'updated_at_utc': stamp()},
               'operator.json': {'bundle_version': 1, 'case_id': case_id, 'case': 'case.json', 'form': 'form.json', 'invoices': 'invoices.json', 'state': 'state.json'}}
    destination.mkdir(parents=True)
    for name, record in records.items():
        write_json(destination/name, record)
    (destination/'receipts').mkdir()
    return {'ok': True, 'operator': str(destination/'operator.json'), 'system_write': False, 'next': '读取guide workflow并补齐本案输入；不沿用其他案件的身份、金额或授权'}
