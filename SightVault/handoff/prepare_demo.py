"""Validate and relocate only this packaged demo snapshot; default is a dry run."""
from contextlib import closing
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def safe_path(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('Path escapes the packaged root: ' + str(relative))
    return path

def plan(root, db):
    manifest = json.loads((root / 'handoff' / 'SAMPLE_MANIFEST.json').read_text(encoding='utf8'))
    expected = {PurePosixPath(m['path']).name: m for m in manifest}
    for m in manifest:
        path = safe_path(root, m['path'])
        if not path.is_file() or path.stat().st_size != m['size'] or digest(path) != m['sha256']:
            raise ValueError('Missing or changed demo file: ' + m['path'])
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Database integrity check failed')
    db.row_factory = sqlite3.Row
    rows = list(db.execute('SELECT * FROM assets ORDER BY id'))
    if len(rows) != 62 or any(r['embedding'] is None for r in rows):
        raise ValueError('Expected the unmodified 62-image demo snapshot with all embeddings')
    targets = set()
    updates = []
    for row in rows:
        m = expected.get(row['filename'])
        if m is None or row['sha256'] != m['sha256']:
            raise ValueError('Database contains an unknown or altered demo asset')
        path = safe_path(root, m['path'])
        if path in targets:
            raise ValueError('Multiple rows map to the same demo file')
        targets.add(path)
        for folder, field in [('images', 'image_path'), ('thumbs', 'thumbnail_path')]:
            evidence = safe_path(root / 'data' / folder, row[field])
            if not evidence.is_file():
                raise ValueError('Missing stored evidence: ' + str(evidence))
        updates.append((str(path), str(path.parent), row['id']))
    return manifest, rows, updates

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Back up DB, rebase source paths, restore sample mtimes')
    args = parser.parse_args()
    path = ROOT / 'data' / 'sightvault.sqlite3'
    if not path.is_file():
        raise ValueError('Packaged demo database is missing')
    # Dry run uses a read-only database connection; no file timestamps are changed.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db, db:
        manifest, rows, updates = plan(ROOT, db)
    changed = sum(r['filepath'] != u[0] or r['folder'] != u[1] for r, u in zip(rows, updates))
    report = {'dry_run': not args.apply, 'assets': len(rows), 'paths_to_rebase': changed,
              'new_folder': str(ROOT / 'samples'), 'indexed_timestamps_preserved': True}
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = path.with_name(f'sightvault.before-mac-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3')
        with closing(sqlite3.connect(path)) as db, db:
            # Backup before transaction; stop the app before running this operation.
            with closing(sqlite3.connect(backup)) as target, target:
                db.backup(target)
            db.execute('BEGIN IMMEDIATE')
            # Revalidate after acquiring the write lock before changing any records.
            manifest, rows, updates = plan(ROOT, db)
            db.executemany('UPDATE assets SET filepath=?, folder=? WHERE id=?', updates)
            for m in manifest:
                sample = safe_path(ROOT, m['path'])
                os.utime(sample, ns=(sample.stat().st_atime_ns, m['mtime_ns']))
            db.commit()
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        report['backup'] = str(backup)
        report['restored_sample_mtimes'] = len(manifest)
    print(json.dumps(report, indent=2))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error) as exc:
        print('Demo relocation stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
