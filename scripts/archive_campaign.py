#!/usr/bin/env python3
"""Archive every local file, verify, then optionally thin a finished campaign.
Default is a read-only preview. Archives are ordinary, UNSPLIT .tar.gz files.
External symlink targets are dependencies, except final GDS links which are
materialized in the archive. Never runs collect or rewrites experiment tables.
"""
import argparse
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import uuid

GROUPS = ('records', 'gds', 'work')
LIVE = {'.json', '.log', '.csv', '.txt', '.tcl', '.sdc', '.sh', '.yaml', '.yml', '.toml', '.ini'}
RECORDS = LIVE | {'.rpt', '.md', '.html', '.xml', '.rdb', '.lyrdb'}
TERMINAL = {'completed', 'flow_error', 'timeout', 'launcher_error'}


def read_json(path):
    return json.loads(path.read_text())


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as f:
        json.dump(value, f, indent=2)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)
    sync_dir(path.parent)


def hash_stream(f):
    h = hashlib.sha256()
    while block := f.read(1024 * 1024):
        h.update(block)
    return h.hexdigest()


def digest(p):
    with p.open('rb') as f:
        return hash_stream(f)


def signature(p):
    s = p.lstat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode)


def inventory(root):
    entries = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            p = Path(base) / name
            rel = p.relative_to(root)
            if name == 'runner.lock':
                continue
            s = p.lstat()
            kind = 'link' if p.is_symlink() else 'dir' if stat.S_ISDIR(s.st_mode) else 'file'
            if kind == 'file' and not stat.S_ISREG(s.st_mode):
                raise ValueError(f'Unsupported special file: {p}')
            parts = rel.parts
            run = any(parts[i:i+2] == ('runs', 'run') for i in range(len(parts)-1))
            final = any(parts[i:i+3] == ('runs', 'run', 'final') for i in range(len(parts)-2))
            gds = final and name.lower().endswith(('.gds', '.gdsii', '.gds.gz')) and kind != 'dir'
            materialized = gds and kind == 'link'
            if materialized and not p.is_file():
                raise ValueError(f'Broken final GDS link: {p}')
            data_stat = p.stat() if materialized else s
            group = 'gds' if gds else 'records' if not run or rel.suffix.lower() in RECORDS or kind != 'file' else 'work'
            entries.append(dict(path=rel.as_posix(), kind=kind, group=group,
                                keep=kind == 'dir' or gds or not run or rel.suffix.lower() in LIVE,
                                materialized=materialized, size=data_stat.st_size if kind == 'file' or materialized else 0,
                                mode=stat.S_IMODE(data_stat.st_mode), mtime=data_stat.st_mtime,
                                sig=list(signature(p)), link=os.readlink(p) if kind == 'link' else None))
    # Preserve internal targets of retained symlinks, including directory targets.
    changed = True
    while changed:
        changed = False
        for link in [e for e in entries if e['kind'] == 'link' and e['keep']]:
            target = (root / link['path']).resolve()
            if target.is_relative_to(root):
                for e in entries:
                    p = root / e['path']
                    if not e['keep'] and (p == target or p.is_relative_to(target)):
                        e['keep'] = True; changed = True
    return sorted(entries, key=lambda e: e['path'])


def ready(root):
    manifest = read_json(root / 'manifest.json')
    with (root / 'runs.csv').open(newline='') as f:
        rows = list(csv.DictReader(f))
    ids = [j['id'] for j in manifest['jobs']]
    by_id = {r['run_id']: r for r in rows}
    if not ids or len(set(ids)) != len(ids) or len(by_id) != len(rows) or set(ids) != set(by_id):
        raise ValueError('CSV must cover the planned campaign exactly.')
    for job in manifest['jobs']:
        ident = job['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
            raise ValueError('Unsafe job ID.')
        d = root / 'jobs' / ident
        a = read_json(d / 'attempt.json')
        if a['status'] not in TERMINAL or by_id[ident]['status'] != a['status']:
            raise ValueError(f'Incomplete/stale outcome: {ident}. Archive-only is available.')
        if (d / 'attempt.json').stat().st_mtime_ns > (root / 'runs.csv').stat().st_mtime_ns:
            raise ValueError('CSV predates an attempt. Inspect before pruning.')
        if job.get('config_sha256') and digest(d / 'config.json') != job['config_sha256']:
            raise ValueError(f'Prepared config changed: {ident}')
        if by_id[ident].get('gds_available') == 'True' and not any(p.is_file() for p in (d/'runs/run/final').rglob('*.gds')):
            raise ValueError(f'CSV claims a GDS already missing: {ident}. Use archive-only; do not recollect blindly.')


class HashReader:
    def __init__(self, f):
        self.f, self.sha = f, hashlib.sha256()
    def read(self, size):
        data = self.f.read(size); self.sha.update(data)
        return data


def verify(dest):
    m = read_json(dest / 'ARCHIVE.json')
    if set(m['archives']) != set(GROUPS):
        raise ValueError('Missing archive groups.')
    for group, record in m['archives'].items():
        p = dest / (group + '.tar.gz')
        if p.stat().st_size != record['size'] or digest(p) != record['sha256']:
            raise ValueError(f'Archive checksum mismatch: {p}')
        expected = {m['root_name']+'/'+e['path']: e for e in m['entries'] if e['group'] == group}
        with tarfile.open(p, 'r|gz') as tar:
            for member in tar:
                e = expected.pop(member.name, None)
                if e is None:
                    raise ValueError('Unexpected/duplicate archive entry.')
                if e['kind'] == 'dir':
                    ok = member.isdir()
                elif e['kind'] == 'link' and not e['materialized']:
                    ok = member.issym() and member.linkname == e['link']
                else:
                    ok = member.isfile() and member.size == e['size'] and hash_stream(tar.extractfile(member)) == e['sha256']
                if not ok:
                    raise ValueError(f'Archive content mismatch: {member.name}')
        if expected:
            raise ValueError('Archive omitted files.')
    return m


def unchanged(root, entries):
    now = inventory(root)
    if {e['path']: e['sig'] for e in now} != {e['path']: e['sig'] for e in entries}:
        raise ValueError('Source tree changed. No pruning performed.')
    for e in entries:
        p = root/e['path']
        if e.get('sha256') and digest(p) != e['sha256']:
            raise ValueError(f'Source content changed: {p}. No pruning performed.')
        if e['kind'] == 'link' and os.readlink(p) != e['link']:
            raise ValueError('Symlink changed. No pruning performed.')


def execute(root, out=None, action='preview'):
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir() or not root.name:
        raise ValueError('Choose a campaign or old archive directory.')
    with ExitStack() as stack:
        lock_paths = set(root.rglob('runner.lock'))
        if (root/'manifest.json').is_file():
            lock_paths.add(root/'runner.lock')
        for p in sorted(lock_paths):
            if p.is_symlink():
                raise ValueError('Symlinked runner lock refused.')
            if not p.exists() and action == 'preview':
                continue
            f = stack.enter_context(p.open('r' if action == 'preview' else 'a+'))
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(f'Runner active: {p.parent}')
        if action == 'apply':
            ready(root)
        entries = inventory(root)
        print('Source:', root, flush=True)
        for group in GROUPS:
            size = sum(e['size'] for e in entries if e['group'] == group)
            print(f'  {group}.tar.gz: {size/1024**3:.2f} GiB uncompressed logical input', flush=True)
        candidates = [e for e in entries if not e['keep']]
        print(f"  Live cleanup candidates: {sum(e['size'] for e in candidates)/1024**3:.2f} GiB logical bytes", flush=True)
        print('  Keep final GDS, metrics, configs, logs and retry records in place.', flush=True)
        if action == 'preview':
            print('Preview only. Archives are unsplit; compressed sizes and actual disk savings are unknown.')
            return None
        if action == 'apply' and not candidates:
            print('Nothing to prune. Use --archive-only for another snapshot.')
            return None
        if out is None:
            raise ValueError('Specify --out outside the checkout.')
        out = out.expanduser().resolve()
        if out == root or out.is_relative_to(root):
            raise ValueError('Archive destination cannot be inside the source.')
        for parent in (root, *root.parents):
            if (parent/'.git').exists():
                if out == parent or out.is_relative_to(parent):
                    raise ValueError('Keep archives outside the Git checkout.')
                break
        out.mkdir(parents=True, exist_ok=True); sync_dir(out.parent)
        print(f'Free at destination: {shutil.disk_usage(out).free/1024**3:.1f} GiB', flush=True)
        ident = root.name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        pending = out/(ident+'.incomplete'); pending.mkdir()
        m = dict(schema=1, source=str(root), root_name=root.name, entries=entries, archives={},
                 note='All local entries except runner.lock. External symlink dependencies are not bundled.')
        for group in GROUPS:
            path = pending/(group+'.tar.gz')
            print('Writing', path.name, flush=True)
            with path.open('xb') as raw:
                with tarfile.open(fileobj=raw, mode='w:gz', compresslevel=6) as tar:
                    for e in entries:
                        if e['group'] != group:
                            continue
                        if shutil.disk_usage(out).free < 1024**3:
                            raise OSError('Less than 1 GiB free: archive stopped; source has not been pruned.')
                        p = root/e['path']
                        if list(signature(p)) != e['sig']:
                            raise ValueError('Source changed while archiving; nothing pruned.')
                        info = tarfile.TarInfo(root.name+'/'+e['path'])
                        info.mode, info.mtime = e['mode'], e['mtime']
                        if e['kind'] == 'dir':
                            info.type = tarfile.DIRTYPE; tar.addfile(info)
                        elif e['kind'] == 'link' and not e['materialized']:
                            info.type, info.linkname = tarfile.SYMTYPE, e['link']; tar.addfile(info)
                        else:
                            info.size = e['size']
                            with p.open('rb') as f:
                                reader = HashReader(f); tar.addfile(info, reader)
                                if f.read(1):
                                    raise ValueError('File grew while archiving; nothing pruned.')
                                e['sha256'] = reader.sha.hexdigest()
                raw.flush(); os.fsync(raw.fileno())
            m['archives'][group] = dict(size=path.stat().st_size, sha256=digest(path))
        write_json(pending/'ARCHIVE.json', m)
        print('Verifying all archive contents...', flush=True)
        verify(pending); unchanged(root, entries)
        dest = out/ident; pending.rename(dest); sync_dir(out)
        print('VERIFIED archive:', dest, flush=True)
        if action == 'apply':
            removed = []
            for e in candidates:
                p = root/e['path']
                if list(signature(p)) != e['sig']:
                    raise ValueError(f'Source changed during pruning. Restore earlier removals from {dest} if needed.')
                p.unlink(); removed.append(e['path'])
            receipt = dict(archive=str(dest), archive_manifest_sha256=digest(dest/'ARCHIVE.json'),
                           removed_files=removed, note='No collect was run; original tables retained.')
            write_json(dest/'PRUNED.json', receipt); write_json(root/'SLIMMED.json', receipt)
            print(f'Removed {len(removed)} archived files; final GDS and study records remain.', flush=True)
        else:
            print('Archive-only: no source files removed.', flush=True)
        return dest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', nargs='?', type=Path)
    p.add_argument('--out', type=Path)
    a = p.add_mutually_exclusive_group()
    a.add_argument('--apply', action='store_true')
    a.add_argument('--archive-only', action='store_true')
    a.add_argument('--dry-run', action='store_true')
    p.add_argument('--verify', type=Path)
    args = p.parse_args()
    if args.verify:
        if args.source or args.apply or args.archive_only:
            p.error('--verify is a standalone action.')
        verify(args.verify.expanduser().resolve()); print('Verified:', args.verify)
    elif args.source:
        execute(args.source, args.out, 'apply' if args.apply else 'archive-only' if args.archive_only else 'preview')
    else:
        p.error('Specify a source directory.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError, EOFError) as exc:
        raise SystemExit(f'ERROR: {exc}. Keep the source and any .incomplete archive for inspection.') from exc
