"""Stage verified, non-code bundle inputs for the desktop build.

Writes into an explicit, empty staging folder:
  samples/        the 62 manifest-verified sample images (+ metadata JSON) with their
                  recorded synthetic dates restored, so "Load the demo collection" keeps them
  demo/           optional: the reviewed 62-image demo DB (SQLite backup) and only the
                  stored evidence files it references, each checked against the reviewed hashes
  build-info.json source revision, lock-file hashes, interpreter and platform

Model weights, environments and user data are never staged. The source tree is not modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.build_portable_package import (  # noqa: E402  (reuse the reviewed demo validation)
    checked_demo_evidence, confined_file, demo_rows, load_demo_evidence_manifest,
    public_image_manifest_entries, relative_path, source_revision, sqlite_backup,
)
from sightvault.desktop import APP_VERSION

LOCK_FILES = ('requirements-mac.lock.txt', 'requirements-windows.lock.txt',
              'packaging/desktop/requirements-desktop-macos.lock.txt',
              'packaging/desktop/requirements-desktop-windows.lock.txt')


def sha256_file(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stage_samples(stage):
    manifest = json.loads((ROOT / 'handoff' / 'SAMPLE_MANIFEST.json').read_text(encoding='utf-8'))
    count = 0
    for item in manifest:
        relative = relative_path(item['path'])
        if relative.parts[0] != 'samples':
            raise ValueError(f'Sample manifest entry outside samples/: {item["path"]}')
        source = confined_file(ROOT, relative)
        if source.stat().st_size != item['size'] or sha256_file(source) != item['sha256']:
            raise ValueError(f'Sample differs from the reviewed manifest: {relative.as_posix()}')
        target = stage.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        os.utime(target, ns=(item['mtime_ns'], item['mtime_ns']))
        count += 1
    images = len(public_image_manifest_entries(manifest))
    if images != 62:
        raise ValueError(f'Expected 62 sample images, staged {images}')
    return count


def stage_demo(stage, demo_db, demo_data_root):
    sample_manifest = json.loads((ROOT / 'handoff' / 'SAMPLE_MANIFEST.json').read_text(encoding='utf-8'))
    _, evidence_by_path, evidence_sha = load_demo_evidence_manifest(ROOT, public_image_manifest_entries(sample_manifest))
    rows = demo_rows(ROOT, demo_db, demo_data_root, evidence_by_path)
    demo = stage / 'demo'
    for subdir in ('images', 'thumbs'):
        (demo / subdir).mkdir(parents=True, exist_ok=True)
    # Rollback-journal mode: a read-only bundled copy never needs -wal/-shm files beside it.
    from contextlib import closing
    import sqlite3
    with closing(sqlite3.connect(Path(demo_db).resolve().as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(demo / 'sightvault.sqlite3')) as target:
            source.backup(target)
    with closing(sqlite3.connect(demo / 'sightvault.sqlite3')) as db:
        if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
            raise ValueError('Could not switch the staged demo database to rollback-journal mode')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Staged demo database failed integrity_check')
    copied = set()
    for row in rows:
        for subdir, name in (('images', row['image_path']), ('thumbs', row['thumbnail_path'])):
            if (subdir, name) in copied:
                continue
            source = checked_demo_evidence(Path(os.path.abspath(demo_data_root)), subdir, name)
            record = evidence_by_path[f'data/{subdir}/{name}']
            shutil.copyfile(source, demo / subdir / name)
            if sha256_file(demo / subdir / name) != record['sha256']:
                raise ValueError(f'Staged demo evidence changed during copy: {subdir}/{name}')
            copied.add((subdir, name))
    return {'rows': len(rows), 'evidence_files': len(copied), 'evidence_manifest_sha256': evidence_sha}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--stage', required=True, type=Path, help='New or empty staging folder')
    parser.add_argument('--platform', required=True, choices=('macos-arm64', 'windows-x64'))
    parser.add_argument('--demo-db', type=Path, help='Verified 62-image public demo SQLite DB (optional)')
    parser.add_argument('--demo-data-root', type=Path, help='Folder holding that DB\'s images/ and thumbs/')
    args = parser.parse_args(argv)
    if bool(args.demo_db) != bool(args.demo_data_root):
        parser.error('--demo-db and --demo-data-root must be given together')
    stage = Path(os.path.abspath(args.stage.expanduser()))
    if stage.exists() and (stage.is_symlink() or not stage.is_dir() or any(stage.iterdir())):
        parser.error(f'Staging folder must be new or empty: {stage}')
    if stage == ROOT or ROOT.is_relative_to(stage):
        parser.error('Staging folder cannot contain the source tree')
    stage.mkdir(parents=True, exist_ok=True)
    report = {'artifact_type': 'SightVault desktop build inputs', 'platform': args.platform,
              'app_version': APP_VERSION,
              'created_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'source_revision': source_revision(ROOT), 'python': sys.version.split()[0],
              'build_host': {'system': platform.system(), 'machine': platform.machine(), 'release': platform.release()},
              'samples_staged': stage_samples(stage),
              'lock_files': {name: sha256_file(ROOT / name) for name in LOCK_FILES if (ROOT / name).is_file()},
              'model_weights_included': False}
    report['demo'] = stage_demo(stage, args.demo_db.resolve(), args.demo_data_root) if args.demo_db else None
    (stage / 'build-info.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print('Staging stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
