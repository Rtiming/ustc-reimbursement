"""Dependency-free, byte-preserving local archive with explicit pending/resume."""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import quote
from datetime import datetime, timezone


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def mount_for(destination, explicit=None):
    dest = Path(destination).absolute()
    if explicit:
        return Path(explicit).absolute()
    if len(dest.parts) > 2 and dest.parts[1] == 'Volumes':
        return Path(*dest.parts[:3])
    return None


def available(destination, mount):
    if mount is None:
        return True
    if not destination.resolve().is_relative_to(mount.resolve()):
        raise ValueError('Destination escapes the requested mount root')
    if not os.path.ismount(mount):
        return False
    return True


def stable_copy(source, target):
    """Detect in-place edits and pathname replacement, including during copy."""
    before = source.stat()
    if getattr(before, 'st_flags', 0) & 0x40000000 and sys.platform == 'darwin':
        raise ValueError('Source is a dataless placeholder; acquire real local bytes first')
    with source.open('rb') as src, target.open('xb') as dst:
        opened = os.fstat(src.fileno())
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
        after_fd = os.fstat(src.fileno())
    after = source.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len({signature(s) for s in (before, opened, after_fd, after)}) != 1:
        raise ValueError('Source changed during snapshot; retry after saving it')
    sha = digest(target)
    if digest(source) != sha or signature(source.stat()) != signature(after):
        raise ValueError('Source changed during hash verification')
    return sha


def write_json(path, data):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def receipt_id(record):
    return hashlib.sha256((record['filename'] + '\0' + record['sha256']).encode()).hexdigest()[:24]


def read_receipt(path):
    path = Path(path).expanduser().absolute()
    record = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(record, dict) or record.get('schema') != 1 or record.get('status') not in {'pending', 'archived', 'resumed'}:
        raise ValueError('Unsupported archive receipt')
    for field in ('filename', 'source', 'snapshot', 'sha256', 'destination_directory'):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError(f'Invalid receipt field: {field}')
    if (not re.fullmatch(r'[0-9a-f]{64}', record['sha256']) or
            not isinstance(record.get('bytes'), int) or record['bytes'] < 0):
        raise ValueError('Invalid receipt hash or size')
    snapshot = Path(record['snapshot'])
    if not snapshot.is_absolute() or not snapshot.resolve().is_relative_to(path.parent.parent.resolve()):
        raise ValueError('Snapshot escapes the receipt archive directory')
    return record


def update_index(directory, record, receipt):
    index = directory / '归档索引.md'
    marker = f'<!-- artifact:{receipt_id(record)} -->'
    current = index.read_text(encoding='utf-8') if index.exists() else '# 原样归档索引\n'
    label = record['filename'].replace('\n', ' ').replace('\r', ' ').replace('[', '\\[').replace(']', '\\]')
    entry = f'{marker}\n- [{label}]({quote(Path(record["snapshot"]).name)}) · {record["status"]} · SHA-256 `{record["sha256"]}` · [记录](.artifact-archive/{receipt.name})\n'
    if marker in current:
        pattern = re.escape(marker) + r'\r?\n- [^\n]*(?:\n|$)'
        revised, count = re.subn(pattern, lambda _: entry, current, count=1)
        if not count: raise ValueError('Managed index entry was edited; inspect it before retrying')
    else:
        revised = current + '\n' + entry
    if revised != current:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, prefix='.archive-index-', delete=False) as f:
            temporary = Path(f.name)
            f.write(revised); f.flush(); os.fsync(f.fileno())
        try: os.replace(temporary, index)
        finally: temporary.unlink(missing_ok=True)
    return index


def verify_receipt(path, *, follow=True):
    """Read back archive bytes and index; never modifies a receipt or repairs content."""
    path = Path(path).expanduser().absolute()
    record = read_receipt(path)
    snapshot = Path(record['snapshot'])
    mount = mount_for(snapshot, record.get('mount_root') if record['status'] == 'archived' else None)
    if not available(snapshot, mount): integrity = 'drive_unavailable'
    elif not snapshot.is_file(): integrity = 'missing'
    elif snapshot.stat().st_size != record['bytes'] or digest(snapshot) != record['sha256']: integrity = 'mismatch'
    else: integrity = 'passed'
    index = path.parent.parent / '归档索引.md'
    marker = f'<!-- artifact:{receipt_id(record)} -->'
    text = index.read_text(encoding='utf-8') if index.is_file() else ''
    match = re.search(re.escape(marker) + r'\r?\n([^\n]+)', text)
    index_status = 'missing' if not match else 'passed' if f'· {record["status"]} ·' in match[1] else 'stale'
    result = {'receipt': str(path), 'recorded_status': record['status'], 'filename': record['filename'],
              'snapshot': str(snapshot), 'byte_integrity': integrity, 'index_status': index_status,
              'ok': integrity == 'passed' and index_status == 'passed'}
    if record['status'] == 'resumed' and follow:
        target = Path(record.get('archived_receipt', ''))
        expected = Path(record['destination_directory']) / '.artifact-archive'
        if not target.is_absolute() or target.parent.resolve() != expected.resolve():
            raise ValueError('Resumed receipt points outside its destination ledger')
        if target.is_file():
            final = read_receipt(target)
            if final['sha256'] != record['sha256'] or final['status'] != 'archived':
                raise ValueError('Resumed destination receipt does not match')
            result['destination'] = verify_receipt(target, follow=False)
        else:
            result['destination'] = {'ok': False, 'receipt': str(target), 'byte_integrity': 'receipt_missing_or_drive_unavailable'}
        result['ok'] = result['ok'] and result['destination']['ok']
    return result


def list_receipts(directory):
    directory = Path(directory).expanduser().absolute()
    if not directory.is_dir(): raise ValueError('Archive directory not found')
    rows = []
    for path in sorted(directory.glob('.artifact-archive/*.json')):
        try:
            record = read_receipt(path)
            rows.append({'receipt': str(path), 'filename': record['filename'], 'status': record['status'],
                         'destination_directory': record['destination_directory'], 'sha256': record['sha256']})
        except (OSError, ValueError) as e:
            rows.append({'receipt': str(path), 'status': 'invalid_receipt', 'error': str(e)})
    return {'directory': str(directory), 'records': rows, 'verified_bytes': False}


def archive(source=None, destination=None, *, mount_root=None, pending_root=None,
            resume=None, max_pending_bytes=64 * 1024 * 1024):
    prior = None
    if resume:
        resume = Path(resume).expanduser().absolute()
        prior = read_receipt(resume)
        if prior['status'] not in {'pending', 'resumed'}:
            raise ValueError('Resume requires a pending/resumed receipt')
        source = Path(prior['snapshot'])
        destination = prior['destination_directory']
        mount_root = prior.get('mount_root')
        if digest(source) != prior['sha256']:
            raise ValueError('Pending snapshot hash mismatch')
    source = Path(source).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    mount = mount_for(destination, mount_root)
    ready = available(destination, mount)
    if not ready and prior:
        raise ValueError('Requested drive is still unavailable; pending receipt retained')
    if not ready and source.stat().st_size > max_pending_bytes:
        raise ValueError('Pending copy exceeds limit; reconnect drive or explicitly increase --max-pending-mib')
    target_dir = destination if ready else Path(pending_root or Path.home() / '.local/state/artifact-authoring/pending').expanduser().absolute()
    if not ready and mount and target_dir.is_relative_to(mount):
        raise ValueError('Pending directory must be outside the missing drive')
    if not available(target_dir, mount_for(target_dir)):
        raise ValueError('Pending directory is on an unavailable volume')
    target_dir.mkdir(parents=True, exist_ok=True)
    directories = {target_dir}
    if prior: directories.add(resume.parent.parent)
    locks = []
    try:
        for directory in sorted(directories):
            lock = directory / '.artifact-archive.lock'
            try: lock.mkdir()
            except FileExistsError: raise ValueError(f'Archive lock exists: {lock}; inspect active writer before removing a stale lock')
            locks.append(lock)
        with tempfile.TemporaryDirectory(prefix='.archive-', dir=target_dir) as tmp:
            snapshot = Path(tmp) / 'snapshot'
            sha = stable_copy(source, snapshot)
            if prior and sha != prior['sha256']:
                raise ValueError('Pending snapshot changed')
            if ready and not available(destination, mount):
                raise ValueError('Drive disappeared during snapshot')
            name = prior['filename'] if prior else source.name
            if Path(name).name != name or name in {'.', '..'}:
                raise ValueError('Receipt filename is not a basename')
            final = target_dir / name
            if name in {'归档索引.md', '.artifact-archive', '.artifact-archive.lock'}:
                final = target_dir / f'{Path(name).stem}__{sha[:12]}{Path(name).suffix}'
            if final.is_symlink() or (final.exists() and (not final.is_file() or digest(final) != sha)):
                final = target_dir / f'{Path(name).stem}__{sha[:12]}{Path(name).suffix}'
            if final.is_symlink() or (final.exists() and (not final.is_file() or digest(final) != sha)):
                raise ValueError('Hash-suffixed destination conflicts; no file overwritten')
            if not final.exists():
                # Same filesystem: atomically publish without replacing another writer's file.
                try:
                    os.link(snapshot, final)
                except OSError as e:
                    if e.errno not in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV}:
                        raise
                    # exFAT and other filesystems without hardlinks: exclusive creation,
                    # never replacement. A crash may leave a partial file to inspect.
                    with snapshot.open('rb') as src, final.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                        dst.flush(); os.fsync(dst.fileno())
            if digest(final) != sha:
                raise ValueError('Destination readback failed')
        ledger = target_dir / '.artifact-archive'
        ledger.mkdir(exist_ok=True)
        identity = receipt_id({'filename': name, 'sha256': sha})
        receipt = ledger / f'{identity}.json'
        record = {
            'schema': 1, 'status': 'archived' if ready else 'pending',
            'archived_at': datetime.now(timezone.utc).isoformat(),
            'source': prior['source'] if prior else str(source), 'filename': name,
            'sources': prior.get('sources', [prior['source']]) if prior else [str(source)],
            'destination_directory': str(destination), 'mount_root': str(mount) if mount else None,
            'snapshot': str(final), 'sha256': sha, 'bytes': final.stat().st_size,
            'verification': {'byte_integrity': 'passed', 'text_extraction': 'not_run',
                             'render': 'not_run', 'visual': 'not_run'},
        }
        if receipt.exists():
            old = json.loads(receipt.read_text(encoding='utf-8'))
            # A pending copy may be intended for another project; never silently retarget it.
            if old['destination_directory'] != str(destination):
                raise ValueError('Existing receipt targets another directory; use a separate pending root')
            record['archived_at'] = old['archived_at']
            record['source'] = old['source']
            record['sources'] = list(dict.fromkeys(old.get('sources', [old['source']]) + record['sources']))
        write_json(receipt, record)
        index = update_index(target_dir, record, receipt)
        if prior:
            prior.update(status='resumed', archived_receipt=str(receipt))
            write_json(Path(resume), prior)
            update_index(resume.parent.parent, prior, resume)
        return {**record, 'receipt': str(receipt), 'index': str(index)}
    finally:
        for lock in reversed(locks): lock.rmdir()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', nargs='?')
    p.add_argument('--destination')
    p.add_argument('--mount-root')
    p.add_argument('--pending-root')
    p.add_argument('--resume', help='Pending JSON receipt; verify and retain local snapshot')
    p.add_argument('--max-pending-mib', type=int, default=64)
    p.add_argument('--list', dest='list_directory', help='Read-only receipt inventory for one archive/pending directory')
    p.add_argument('--verify', help='Read-only byte and index verification of one JSON receipt')
    a = p.parse_args(argv)
    if a.list_directory or a.verify:
        if (a.list_directory and a.verify) or a.source or a.destination or a.resume or a.mount_root or a.pending_root:
            p.error('Use --list DIR or --verify RECEIPT without archive arguments')
        try:
            result = list_receipts(a.list_directory) if a.list_directory else verify_receipt(a.verify)
        except (OSError, ValueError, KeyError) as e:
            print(str(e), file=sys.stderr); return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok', True) else 1
    if (a.resume and (a.source or a.destination or a.mount_root)) or (not a.resume and not (a.source and a.destination)):
        p.error('Use SOURCE --destination DIR, or --resume RECEIPT')
    try:
        result = archive(a.source, a.destination, mount_root=a.mount_root,
                         pending_root=a.pending_root, resume=a.resume,
                         max_pending_bytes=a.max_pending_mib * 1024 * 1024)
    except (OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
