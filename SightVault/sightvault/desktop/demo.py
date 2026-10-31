"""Non-destructive import of the bundled, reviewed 62-image public demo.

The bundle may carry a verified demo database with its stored evidence copies.
Importing copies the sample files into the user's data folder (restoring their
recorded dates), copies only the referenced evidence files, and inserts the
indexed rows with their original OCR, vectors and timestamps. It only runs
against an empty library and never overwrites a differing file.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
DEMO_FOLDER_NAME = 'demo-samples'


class DemoImportError(ValueError):
    pass


def _sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _confined(base, relative):
    relative = PurePosixPath(str(relative).replace('\\', '/'))
    if relative.is_absolute() or any(part in ('', '.', '..') for part in relative.parts):
        raise DemoImportError(f'Unsafe path in demo manifest: {relative}')
    base = Path(base).resolve()
    path = base.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(base):
        raise DemoImportError(f'Demo path escapes its folder: {relative}')
    return path


def _plain_name(value):
    if not isinstance(value, str) or not value or value != Path(value).name or value in ('.', '..') or '\\' in value:
        raise DemoImportError(f'Unsafe stored evidence name in demo database: {value!r}')
    return value


def bundled_demo(resources):
    folder = Path(resources) / 'demo'
    return folder if (folder / 'sightvault.sqlite3').is_file() else None


def plan_import(resources, *, expected_count=62):
    """Validate the bundled demo against its manifests. Reads only; changes nothing."""
    resources = Path(resources)
    demo = bundled_demo(resources)
    if demo is None:
        raise DemoImportError('This build does not include the public demo library.')
    samples_manifest = json.loads((resources / 'handoff' / 'SAMPLE_MANIFEST.json').read_text(encoding='utf-8'))
    evidence_manifest = json.loads((resources / 'handoff' / 'DEMO_EVIDENCE_MANIFEST.json').read_text(encoding='utf-8'))
    samples = {}
    for item in samples_manifest:
        relative = PurePosixPath(item['path'])
        if relative.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        path = _confined(resources, relative)
        if not path.is_file() or path.stat().st_size != item['size'] or _sha256(path) != item['sha256']:
            raise DemoImportError(f'Bundled demo sample is missing or changed: {relative}')
        if relative.name in samples:
            raise DemoImportError(f'Duplicate demo sample name: {relative.name}')
        samples[relative.name] = {'path': path, 'sha256': item['sha256'], 'mtime_ns': int(item['mtime_ns'])}
    evidence_hashes = {}
    for entry in evidence_manifest.get('entries', []):
        for kind, subdir in (('image', 'images'), ('thumbnail', 'thumbs')):
            record = entry[kind]
            relative = PurePosixPath(record['path'])
            if relative.parts[:2] != ('data', subdir) or len(relative.parts) != 3:
                raise DemoImportError(f'Unexpected evidence path in manifest: {relative}')
            evidence_hashes[(subdir, relative.name)] = record['sha256']
    # immutable=1: the bundled copy is read-only; SQLite must not create journal files beside it.
    uri = (demo / 'sightvault.sqlite3').resolve().as_uri() + '?mode=ro&immutable=1'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise DemoImportError('The bundled demo database failed its integrity check.')
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute('SELECT * FROM assets ORDER BY id')]
    if len(rows) != expected_count or any(row['embedding'] is None for row in rows):
        raise DemoImportError(f'Expected {expected_count} demo images with embeddings, found {len(rows)}.')
    evidence = {}
    seen = set()
    for row in rows:
        sample = samples.get(row['filename'])
        if sample is None or row['sha256'] != sample['sha256'] or row['filename'] in seen:
            raise DemoImportError('The demo database contains an unknown, duplicate or altered image.')
        seen.add(row['filename'])
        for field, subdir in (('image_path', 'images'), ('thumbnail_path', 'thumbs')):
            name = _plain_name(row[field])
            expected = evidence_hashes.get((subdir, name))
            if expected is None:
                raise DemoImportError(f'No reviewed hash is recorded for demo evidence {subdir}/{name}.')
            source = _confined(demo / subdir, name)
            if (subdir, name) not in evidence:
                if not source.is_file() or _sha256(source) != expected:
                    raise DemoImportError(f'Bundled demo evidence is missing or changed: {subdir}/{name}')
                evidence[(subdir, name)] = {'path': source, 'sha256': expected}
    return {'rows': rows, 'samples': samples, 'evidence': evidence}


def _library_count(db_path):
    with closing(sqlite3.connect(db_path, timeout=30)) as db:
        return db.execute('SELECT count(*) FROM assets').fetchone()[0]


def _already_present(target, digest):
    """True for an identical existing file; raises for anything else in the way."""
    if not (target.exists() or target.is_symlink()):
        return False
    if target.is_symlink() or not target.is_file() or _sha256(target) != digest:
        raise DemoImportError(f'A different file already exists at {target}; the demo was not imported.')
    return True


def _copy_verified(source, target, digest, mtime_ns=None):
    """Copy unless an identical file is already present. Never replaces different content."""
    if not _already_present(target, digest):
        descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '-', suffix='.partial', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as output, open(source, 'rb') as stream:
                shutil.copyfileobj(stream, output)
            if _sha256(temporary) != digest:
                raise DemoImportError(f'Copy verification failed for {target.name}.')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    if mtime_ns is not None:
        os.utime(target, ns=(target.stat().st_atime_ns, mtime_ns))


def import_demo(resources, data_dir, db_path, *, expected_count=62):
    data_dir, db_path = Path(data_dir), Path(db_path)
    plan = plan_import(resources, expected_count=expected_count)
    if _library_count(db_path):
        raise DemoImportError('The demo is only imported into an empty library. Your existing library was not changed.')
    folder = data_dir / DEMO_FOLDER_NAME
    indexed = {row['filename'] for row in plan['rows']}
    copies = [(sample['path'], folder / name, sample['sha256'], sample['mtime_ns'])
              for name, sample in sorted(plan['samples'].items()) if name in indexed]
    copies += [(item['path'], data_dir / subdir / name, item['sha256'], None)
               for (subdir, name), item in sorted(plan['evidence'].items())]
    # Check every destination before copying anything, so a conflict leaves no partial import.
    for _, target, digest, _ in copies:
        for ancestor in target.parents:
            if ancestor == data_dir:
                break
            if ancestor.is_symlink():
                raise DemoImportError(f'Demo destination is a symlink: {ancestor}')
        if not target.resolve().is_relative_to(data_dir.resolve()):
            raise DemoImportError(f'Demo destination escapes its data folder: {target}')
        _already_present(target, digest)
    for source, target, digest, mtime_ns in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_verified(source, target, digest, mtime_ns)
    with closing(sqlite3.connect(db_path, timeout=30, isolation_level=None)) as db:
        columns = [row[1] for row in db.execute('PRAGMA table_info(assets)')]
        db.execute('BEGIN IMMEDIATE')
        try:
            if db.execute('SELECT count(*) FROM assets').fetchone()[0]:
                raise DemoImportError('The library changed while importing; the demo was not imported.')
            for row in plan['rows']:
                record = {name: row[name] for name in columns if name in row}
                record['filepath'] = str(folder / row['filename'])
                record['folder'] = str(folder)
                names = list(record)
                db.execute(f"INSERT INTO assets ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                           [record[name] for name in names])
        except BaseException:
            db.execute('ROLLBACK')
            raise
        db.execute('COMMIT')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise DemoImportError('Library integrity check failed after the demo import.')
    return {'imported': len(plan['rows']), 'folder': str(folder),
            'evidence_files': len(plan['evidence']), 'indexed_timestamps_preserved': True}
