#!/usr/bin/env python3
"""Download an explicit Tencent sheet attachment allowlist through the current Edge SDK."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
import uuid

from edge_runtime import browser_lock
from tencent_attachment_bridge import TencentBridge, TencentError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'ustc-tencent-attachments-v1'
DOWNLOAD_HOSTS = {'drive.docs.qq.com'}  # Observed native SDK download host; no arbitrary URL input.
MAX_BYTES = 100 * 1024 * 1024


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def text_field(value, pattern=None, maximum=255):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and (pattern is None or re.fullmatch(pattern, value) is not None))


def safe_absolute(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise TencentError('invalid_output_path')
    path = Path(value)
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise TencentError('symlink_path_rejected')
    if path.exists() and not path.is_dir():
        raise TencentError('invalid_output_path')
    return path


def validate_manifest(data, output=None):
    required = {'schema', 'case_id', 'docId', 'hostId', 'document_url', 'files'}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {'output_dir'}:
        raise TencentError('invalid_manifest_fields')
    if data['schema'] != SCHEMA or not text_field(data['case_id'], r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}'):
        raise TencentError('invalid_manifest_identity')
    if not text_field(data['docId'], r'[A-Za-z0-9_-]{6,128}') or not text_field(data['hostId'], r'[A-Za-z0-9_-]{1,128}'):
        raise TencentError('invalid_document_identity')
    if not isinstance(data['document_url'], str):
        raise TencentError('invalid_document_url')
    url = urllib.parse.urlsplit(data['document_url'])
    query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    if (url.scheme != 'https' or url.netloc != 'docs.qq.com' or url.path != '/sheet/' + data['docId']
            or url.fragment or (query and (set(query) != {'tab'} or len(query['tab']) != 1
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', query['tab'][0])))):
        raise TencentError('invalid_document_url')
    if output is not None and 'output_dir' in data and str(output) != data['output_dir']:
        raise TencentError('output_binding_mismatch')
    target = safe_absolute(str(output) if output is not None else data.get('output_dir'))
    files = data['files']
    if not isinstance(files, list) or not 1 <= len(files) <= 2000:
        raise TencentError('invalid_attachment_allowlist')
    seen = set()
    for item in files:
        fields = {'file_id', 'filename', 'expected_size', 'references'}
        if not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - {'expected_sha256'}:
            raise TencentError('invalid_attachment_fields')
        fid, name = item['file_id'], item['filename']
        if not text_field(fid, r'[0-9a-fA-F]{32}') or fid.lower() in seen:
            raise TencentError('invalid_or_duplicate_attachment_id')
        seen.add(fid.lower())
        if (not text_field(name) or name in ('.', '..') or any(c in name for c in '/\\:')
                or len(name.encode('utf-8')) > 255 or Path(name).suffix.lower() not in {'.pdf', '.jpg', '.jpeg', '.png'}):
            raise TencentError('invalid_attachment_filename')
        if type(item['expected_size']) is not int or not 0 < item['expected_size'] <= MAX_BYTES:
            raise TencentError('invalid_attachment_size')
        if 'expected_sha256' in item and not text_field(item['expected_sha256'], r'[0-9a-f]{64}'):
            raise TencentError('invalid_attachment_sha256')
        refs = item['references']
        if not isinstance(refs, list) or not refs or len(refs) > 1000:
            raise TencentError('invalid_attachment_references')
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {'sheet', 'tab', 'cell', 'row', 'person'}:
                raise TencentError('invalid_attachment_reference')
            if (not all(text_field(ref[k]) for k in ('sheet', 'tab', 'person'))
                    or not text_field(ref['cell'], r'[A-Z]{1,4}[1-9][0-9]{0,6}')
                    or type(ref['row']) is not int or ref['row'] <= 0):
                raise TencentError('invalid_attachment_reference')
    return dict(data, output_dir=str(target))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_absolute(str(path.parent))
    if path.is_symlink():
        raise TencentError('symlink_path_rejected')
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def detected_format(content, filename):
    if content.startswith(b'%PDF-'):
        fmt = 'pdf'
    elif content.startswith(b'\x89PNG\r\n\x1a\n'):
        fmt = 'png'
    elif content.startswith(b'\xff\xd8\xff'):
        fmt = 'jpeg'
    else:
        raise TencentError('unrecognized_file_format')
    ext = Path(filename).suffix.lower()
    if (ext == '.pdf') != (fmt == 'pdf'):
        raise TencentError('file_type_mismatch')
    expected = {'.pdf': 'pdf', '.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg'}[ext]
    return fmt, fmt == expected


def validate_download_url(value, resolve=True):
    if not isinstance(value, str):
        raise TencentError('invalid_native_download_url')
    url = urllib.parse.urlsplit(value)
    if (url.scheme != 'https' or url.hostname not in DOWNLOAD_HOSTS or url.username or url.password
            or url.netloc != url.hostname or url.fragment):
        raise TencentError('invalid_native_download_url')
    if resolve:
        try:
            addresses = socket.getaddrinfo(url.hostname, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise TencentError('download_dns_failed') from None
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise TencentError('download_address_rejected')
    return value


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_bytes(url, expected_size):
    # No Cookie, Authorization, browser state or Referer is transferred to this request.
    validate_download_url(url)
    try:
        opener = urllib.request.build_opener(SafeRedirect())
        with opener.open(urllib.request.Request(url), timeout=35) as response:
            validate_download_url(response.url)
            if response.status != 200:
                raise TencentError('download_http_failed')
            content = response.read(expected_size + 1)
    except TencentError:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        raise TencentError('download_failed') from None
    if len(content) != expected_size:
        raise TencentError('download_size_mismatch')
    return content


def validate_native_metadata(metadata, item):
    if not isinstance(metadata, dict) or set(metadata) != {'name', 'ext', 'size'}:
        raise TencentError('attachment_metadata_evidence_missing')
    name, ext, size = metadata['name'], metadata['ext'], metadata['size']
    if not text_field(name) or not text_field(ext, r'[A-Za-z0-9]+'):
        raise TencentError('attachment_metadata_mismatch')
    suffix = '.' + ext.lower()
    base = name[:-len(suffix)] if name.lower().endswith(suffix) else name
    expected_base, expected_ext = item['filename'].rsplit('.', 1)
    if type(size) is int:
        size_bytes = size
    elif isinstance(size, str) and re.fullmatch(r'[0-9]+', size):
        size_bytes = int(size)
    else:
        raise TencentError('attachment_metadata_mismatch')
    if base != expected_base or ext.lower() != expected_ext.lower() or size_bytes != item['expected_size']:
        raise TencentError('attachment_metadata_mismatch')
    return dict(metadata, size_bytes=size_bytes, normalized_basename=base,
                comparison='exact_basename_case_insensitive_extension')


def validate_native(result, plan, item=None):
    doc = result.get('document') if isinstance(result, dict) else None
    if (not isinstance(doc, dict) or doc.get('loadedSheet') is not True or doc.get('hostId') != plan['hostId']
            or type(doc.get('canCopy')) not in (bool, int) or doc['canCopy'] not in (True, 1)
            or type(doc.get('canExportDoc')) not in (bool, int) or doc['canExportDoc'] not in (True, 1)
            or doc.get('readEvidence') != 'loaded_sheet_and_bound_host' or doc.get('independentCanRead') is not None):
        raise TencentError('document_permission_evidence_missing')
    if item is None:
        if result.get('ready') is not True:
            raise TencentError('native_setup_unconfirmed')
    elif (result.get('file_id') != item['file_id'] or result.get('name') != item['filename']
            or type(result.get('size')) is not int or result['size'] != item['expected_size']
            or not isinstance(result.get('permission'), dict)
            or any(result['permission'].get(k) is not True for k in ('canRead', 'canCopy', 'canExport'))):
        raise TencentError('attachment_permission_evidence_missing')
    return {'document': {k: doc[k] for k in ('loadedSheet', 'hostId', 'canCopy', 'canExportDoc', 'readEvidence', 'independentCanRead')},
            **({'attachment': {k: True for k in ('canRead', 'canCopy', 'canExport')},
                'native_metadata': validate_native_metadata(result.get('native_metadata'), item)} if item else {})}


def load_state(path, binding):
    if path.is_symlink():
        raise TencentError('symlink_path_rejected')
    if not path.exists():
        return {'binding': binding, 'native': {}, 'created_at_utc': stamp()}
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError):
        raise TencentError('invalid_local_receipt') from None
    if not isinstance(result, dict) or result.get('binding') != binding:
        raise TencentError('original_intent_mismatch')
    return result


def current_intent_path(state_dir, key):
    pointer = state_dir / (key + '.current.json')
    if pointer.is_symlink():
        raise TencentError('symlink_path_rejected')
    if not pointer.exists():
        return state_dir / (key + '.json')
    try:
        value = json.loads(pointer.read_text())
    except (OSError, ValueError):
        raise TencentError('invalid_attempt_pointer') from None
    name = value.get('current') if isinstance(value, dict) else None
    if not isinstance(name, str) or not re.fullmatch(re.escape(key) + r'\.attempt-[0-9a-f]{32}\.json', name):
        raise TencentError('invalid_attempt_pointer')
    target = state_dir / name
    if target.is_symlink() or not target.is_file():
        raise TencentError('invalid_attempt_pointer')
    return target


def retry_metadata_intent(state_dir, key, path, entry, bridge):
    # This is a new explicit read attempt, only after the original marker proves a
    # completed local metadata rejection before prepareDownloadInfo/HTTP download.
    if (entry.get('last_failure', {}).get('error_code') != 'attachment_metadata_mismatch'
            or entry.get('phase') is not None or 'download' in entry or 'permission_evidence' in entry):
        raise TencentError('known_metadata_retry_not_allowed')
    observed = bridge.confirm_known_metadata_failure(entry.get('native'))
    previous_sha = digest(path.read_bytes())
    new_path = state_dir / (key + '.attempt-' + uuid.uuid4().hex + '.json')
    fresh = {'binding': entry['binding'], 'native': {}, 'created_at_utc': stamp(),
             'previous_attempt': {'path': path.name, 'sha256': previous_sha,
                                  'marker': entry['native']['marker'], 'observed_result': observed},
             'retry_reason': 'explicit_known_metadata_failure'}
    write_json(new_path, fresh)
    write_json(state_dir / (key + '.current.json'), {'current': new_path.name})
    return new_path, fresh


def run_batch(plan, manifest_sha256, tab_id, limit, *, bridge=None, fetch=fetch_bytes, state_dir=None,
              retry_known_metadata=False):
    plan = validate_manifest(plan)
    if not text_field(manifest_sha256, r'[0-9a-f]{64}'):
        raise TencentError('invalid_manifest_sha256')
    if not text_field(tab_id, r'[0-9]{1,20}') or type(limit) is not int or not 1 <= limit <= len(plan['files']):
        raise TencentError('invalid_batch_arguments')
    # Caller cannot choose another runtime store to replay an unresolved native request.
    state_dir = state_dir or ROOT / '.local/tencent_attachments'
    output = safe_absolute(plan['output_dir'])
    safe_absolute(str(state_dir))
    output.mkdir(parents=True, exist_ok=True)
    bridge = bridge or TencentBridge(plan, tab_id)
    binding = {'case_id': plan['case_id'], 'docId': plan['docId'], 'hostId': plan['hostId'],
               'manifest_sha256': manifest_sha256, 'output_dir': str(output)}
    index_path = output / 'download_receipt.json'
    index = load_state(index_path, binding)
    index.setdefault('files', {})
    counts = {'downloaded': 0, 'existing': 0, 'failed': 0}
    setup_done = False
    def save_index():
        index.update(observed_at_utc=stamp(), online_write=False, signed_url_saved=False)
        write_json(index_path, index)
    for item in plan['files'][:limit]:
        fid = item['file_id']
        key = digest(canonical([plan['case_id'], plan['docId'], fid]))
        intent_path = state_dir / (key + '.json')
        entry = None
        retry_created = False
        try:
            intent_path = current_intent_path(state_dir, key)
            entry = load_state(intent_path, dict(binding, file_id=fid))
            relative = Path('files') / fid / item['filename']
            target = output / relative
            safe_absolute(str(target.parent))
            if target.is_symlink():
                raise TencentError('symlink_path_rejected')
            # Local successful bytes are checked before any SDK operation.
            recorded = index['files'].get(fid) or entry.get('download')
            if recorded is not None:
                if (not isinstance(recorded, dict) or recorded.get('file_id') != fid
                        or recorded.get('filename') != item['filename'] or recorded.get('bytes') != item['expected_size']
                        or recorded.get('relative_path') != str(relative) or not target.is_file()
                        or target.stat().st_size != item['expected_size']
                        or digest(target.read_bytes()) != recorded.get('sha256')):
                    raise TencentError('resume_sha_mismatch')
                if item.get('expected_sha256') and recorded['sha256'] != item['expected_sha256']:
                    raise TencentError('resume_sha_mismatch')
                actual_format, extension_matches = detected_format(target.read_bytes(), item['filename'])
                if recorded.get('detected_format') != actual_format or recorded.get('extension_matches_format') != extension_matches:
                    raise TencentError('resume_format_mismatch')
                index['files'][fid] = recorded
                save_index()
                counts['existing'] += 1
                continue
            if target.exists() or entry.get('phase') == 'download_started':
                raise TencentError('download_outcome_unknown')
            if retry_known_metadata and entry.get('native'):
                intent_path, entry = retry_metadata_intent(state_dir, key, intent_path, entry, bridge)
                retry_created = True
            if not setup_done:
                setup_key = digest(canonical([plan['case_id'], plan['docId'], tab_id, 'setup']))
                setup_path = state_dir / (setup_key + '.json')
                setup = load_state(setup_path, dict(binding, tab_id=tab_id))
                native = bridge.perform('setup', plan['files'][0], setup['native'], lambda: write_json(setup_path, setup))
                setup['permission_evidence'] = validate_native(native, plan)
                setup['phase'] = 'completed'
                write_json(setup_path, setup)
                setup_done = True
            native = bridge.perform('link', item, entry['native'], lambda: write_json(intent_path, entry))
            permission = validate_native(native, plan, item)
            url = validate_download_url(native.get('url'), resolve=False)
            entry.update(phase='download_started', permission_evidence=permission)
            write_json(intent_path, entry)
            content = fetch(url, item['expected_size'])
            del url, native  # Signed URL never enters a persistent record.
            if not isinstance(content, bytes) or len(content) != item['expected_size']:
                raise TencentError('download_size_mismatch')
            sha = digest(content)
            if item.get('expected_sha256') and item['expected_sha256'] != sha:
                raise TencentError('download_sha_mismatch')
            fmt, matches = detected_format(content, item['filename'])
            row = {'file_id': fid, 'filename': item['filename'], 'relative_path': str(relative),
                   'bytes': len(content), 'sha256': sha, 'detected_format': fmt, 'extension_matches_format': matches,
                   'references': item['references'], 'permission_evidence': permission, 'retrieved_at_utc': stamp(),
                   'online_write': False, 'signed_url_saved': False, 'download_method': 'authenticated_native_sdk'}
            # Record SHA before the exclusive file write, permitting a crash after file creation
            # to be recovered by that exact SHA. No unrecorded existing bytes are adopted.
            entry.update(phase='bytes_verified', download=row)
            write_json(intent_path, entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            entry['phase'] = 'completed'
            write_json(intent_path, entry)
            index['files'][fid] = row
            save_index()
            counts['downloaded'] += 1
            bridge.cleanup(entry['native'])
        except (TencentError, OSError, ValueError, TypeError, KeyError) as exc:
            code = exc.code if isinstance(exc, TencentError) else 'local_processing_failed'
            counts['failed'] += 1
            failure = {'file_id': fid, 'error_code': code, 'observed_at_utc': stamp(),
                       'stopped_batch': True, 'automatic_retry': False}
            index.setdefault('failures', []).append(failure)
            save_index()
            if entry is not None and (not retry_known_metadata or retry_created or not entry.get('native')):
                entry['last_failure'] = failure
                write_json(intent_path, entry)
            break
    preview_close = {'status': 'not_opened_by_this_run'}
    if setup_done:
        try:
            setup.setdefault('close_native', {})
            closed = bridge.perform('close', plan['files'][0], setup['close_native'], lambda: write_json(setup_path, setup))
            validate_native(dict(closed, ready=closed.get('closed')), plan)
            setup['preview_close'] = {'closed': True, 'observed_at_utc': stamp()}
            write_json(setup_path, setup)
            preview_close = {'status': 'native_close_completed'}
        except (TencentError, OSError, ValueError, TypeError, KeyError) as exc:
            preview_close = {'status': 'unconfirmed', 'error_code': exc.code if isinstance(exc, TencentError) else 'native_close_failed'}
    result = {'ok': counts['failed'] == 0 and preview_close['status'] != 'unconfirmed', 'counts': counts, 'planned': limit,
              'allowlist_count': len(plan['files']), 'output_dir': str(output),
              'online_write': False, 'signed_url_saved': False, 'preview_close': preview_close}
    index['last_batch'] = result
    save_index()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--tab-id', required=True)
    parser.add_argument('--output', type=Path, help='Must equal manifest.output_dir when both are present')
    parser.add_argument('--limit', type=int, help='Process the first N allowlisted files; default all')
    parser.add_argument('--check-only', action='store_true', help='Offline manifest validation only')
    parser.add_argument('--retry-known-metadata', action='store_true', help='New read attempt only after the original marker proves a completed metadata rejection')
    args = parser.parse_args(argv)
    try:
        raw = args.manifest.read_bytes()
        plan = validate_manifest(json.loads(raw), args.output)
        limit = len(plan['files']) if args.limit is None else args.limit
        if not text_field(args.tab_id, r'[0-9]{1,20}') or not 1 <= limit <= len(plan['files']):
            raise TencentError('invalid_batch_arguments')
        if args.check_only:
            result = {'ok': True, 'checked_only': True, 'planned': limit, 'manifest_sha256': digest(raw)}
        else:
            with browser_lock():
                result = run_batch(plan, digest(raw), args.tab_id, limit, retry_known_metadata=args.retry_known_metadata)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 2
    except (TencentError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error_code': exc.code if isinstance(exc, TencentError) else 'invalid_input',
                          'online_write': False, 'automatic_retry': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
