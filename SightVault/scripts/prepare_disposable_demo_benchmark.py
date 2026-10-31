"""Validate and copy only the packaged public demo into an isolated benchmark root."""
from __future__ import annotations
from contextlib import closing

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
EMBEDDING_MODEL = 'google/embeddinggemma-2:768:text-image'
MANUAL_INSTRUCTIONS = [
    'Prepare the verified public 62-image demo in the package data/ directory.',
    'Run .\\Validate-SightVault-Windows.ps1 after Setup-Windows.ps1 -DownloadModels.',
    'If running the retrieval check manually, stop the app first and use a disposable SIGHTVAULT_DATA directory with scripts\\benchmark.py --offline-check --output <report>.',
]


def sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError('Demo paths must be non-empty strings')
    relative = PurePosixPath(value.replace('\\', '/'))
    if relative.is_absolute() or any(part in ('', '.', '..') for part in relative.parts) or ':' in relative.parts[0]:
        raise ValueError(f'Unsafe packaged demo path: {value!r}')
    return relative


def record_map(package_manifest: dict) -> dict[str, dict]:
    records = package_manifest.get('files')
    if not isinstance(records, list):
        raise ValueError('Portable package file manifest is malformed')
    result = {}
    for item in records:
        relative = safe_relative(item.get('path'))
        name = relative.as_posix()
        if name in result or not isinstance(item.get('size'), int) or item['size'] < 0:
            raise ValueError(f'Invalid or duplicate package file record: {name}')
        digest = item.get('sha256')
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f'Invalid package SHA-256 record: {name}')
        result[name] = item
    return result


def checked_package_file(root: Path, records: dict[str, dict], relative: str) -> Path:
    safe = safe_relative(relative)
    name = safe.as_posix()
    path = root.joinpath(*safe.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f'Packaged public demo file is missing or unsafe: {name}')
    record = records.get(name)
    if record is None:
        raise ValueError(f'Packaged public demo file is not listed in the package manifest: {name}')
    if path.stat().st_size != record['size'] or sha256_file(path) != record['sha256']:
        raise ValueError(f'Packaged public demo file failed its size/SHA-256 check: {name}')
    return path


def skip_result(output_data: Path, reason: str) -> dict:
    return {
        'status': 'skipped-not-validated',
        'reason': reason,
        'manual_instructions': MANUAL_INSTRUCTIONS,
        'output_data_root': str(output_data),
    }


def prepare_disposable_demo(source_root: Path, output_data: Path) -> dict:
    source_root = source_root.resolve()
    output_data = output_data.resolve()
    db_path = source_root / 'data' / 'sightvault.sqlite3'
    package_manifest_path = source_root / 'handoff' / 'PORTABLE_PACKAGE_MANIFEST.json'
    sample_manifest_path = source_root / 'handoff' / 'SAMPLE_MANIFEST.json'

    manifest_exists = package_manifest_path.is_file()
    if not manifest_exists:
        return skip_result(output_data, 'The portable package manifest is absent, so no demo data can be verified or copied.')

    package_manifest = json.loads(package_manifest_path.read_text(encoding='utf-8'))
    demo = package_manifest.get('demo')
    if not demo or not demo.get('asset_count'):
        return skip_result(output_data, 'The portable package manifest declares no included public demo; any local data remains untouched.')
    if demo.get('asset_count') != 62:
        raise ValueError(f'Expected a verified 62-image public demo, package manifest declares {demo.get("asset_count")}')
    if not db_path.is_file():
        raise ValueError('Package manifest declares the public 62-image demo, but data/sightvault.sqlite3 is missing')
    if not sample_manifest_path.is_file():
        raise ValueError('Package manifest declares the public demo, but handoff/SAMPLE_MANIFEST.json is missing')

    records = record_map(package_manifest)
    db_record = records.get('data/sightvault.sqlite3')
    if db_record is None:
        raise ValueError('Portable package manifest does not list data/sightvault.sqlite3')
    # prepare_demo.py --apply and normal app use can legitimately update the
    # mutable database after extraction. Validate its current rows and integrity
    # below, but do not reject a path-rebased copy solely because the archive's
    # original database-file digest no longer matches.
    database_sha256_matches_package_manifest = (
        db_path.stat().st_size == db_record['size'] and sha256_file(db_path) == db_record['sha256'])
    sample_manifest = json.loads(sample_manifest_path.read_text(encoding='utf-8'))
    image_entries = [item for item in sample_manifest
                     if PurePosixPath(item.get('path', '')).suffix.lower() in IMAGE_SUFFIXES]
    expected = {}
    for item in image_entries:
        rel = safe_relative(item.get('path'))
        if not rel.parts or rel.parts[0] != 'samples' or rel.name in expected:
            raise ValueError(f'Invalid or duplicate public sample manifest path: {item.get("path")!r}')
        source = source_root.joinpath(*rel.parts)
        if source.is_symlink() or not source.resolve().is_relative_to(source_root) or not source.is_file():
            raise ValueError(f'Public source sample is missing or unsafe: {rel.as_posix()}')
        if source.stat().st_size != item.get('size') or sha256_file(source) != item.get('sha256'):
            raise ValueError(f'Public source sample failed its size/SHA-256 check: {rel.as_posix()}')
        expected[rel.name] = item
    if len(expected) != 62:
        raise ValueError(f'Expected 62 public source images, found {len(expected)}')

    uri = db_path.resolve().as_uri() + '?mode=ro'
    output_data.mkdir(parents=True, exist_ok=True)
    if any(output_data.iterdir()):
        raise ValueError(f'Disposable benchmark data directory is not empty: {output_data}')
    output_db = output_data / 'sightvault.sqlite3'
    with closing(sqlite3.connect(uri, uri=True)) as source_db, source_db:
        with closing(sqlite3.connect(output_db)) as backup_db, backup_db:
            source_db.backup(backup_db)
            if backup_db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite backup for the disposable demo failed integrity_check')
    with closing(sqlite3.connect(output_db)) as demo_db, demo_db:
        demo_db.row_factory = sqlite3.Row
        if demo_db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Packaged public demo database failed SQLite integrity_check')
        columns = {row['name'] for row in demo_db.execute('PRAGMA table_info(assets)')}
        required = {'id', 'filename', 'filepath', 'sha256', 'image_path', 'thumbnail_path', 'embedding', 'embedding_model'}
        if not required.issubset(columns):
            raise ValueError('Packaged public demo database does not have the expected SightVault asset schema')
        rows = list(demo_db.execute(
            'SELECT id, filename, filepath, sha256, image_path, thumbnail_path, embedding, embedding_model '
            'FROM assets ORDER BY id'))
        if len(rows) != 62 or len({row['filename'] for row in rows}) != 62:
            raise ValueError(f'Expected 62 unique packaged demo assets, found {len(rows)}')

        files_to_copy = {'data/sightvault.sqlite3': db_path}
        for row in rows:
            sample = expected.get(row['filename'])
            if sample is None or row['sha256'] != sample.get('sha256'):
                raise ValueError(f'Demo database contains an unknown or changed public asset: {row["filename"]}')
            if row['embedding'] is None or len(row['embedding']) != 768 * 4 or row['embedding_model'] != EMBEDDING_MODEL:
                raise ValueError(f'Demo asset has no direct normalized 768-dimensional image embedding: {row["filename"]}')
            if not isinstance(row['filepath'], str) or Path(row['filepath']).name != row['filename']:
                raise ValueError(f'Demo asset source path does not match its public filename: {row["filename"]}')
            if row['image_path'] != row['sha256'] + '.png' or row['thumbnail_path'] != row['sha256'] + '.webp':
                raise ValueError(f'Unexpected stored evidence names for {row["filename"]}')
            for subdir, filename in (('images', row['image_path']), ('thumbs', row['thumbnail_path'])):
                rel = safe_relative(filename)
                if len(rel.parts) != 1:
                    raise ValueError(f'Unexpected stored evidence path for {row["filename"]}')
                package_relative = f'data/{subdir}/{filename}'
                path = checked_package_file(source_root, records, package_relative)
                files_to_copy[package_relative] = path

        (output_data / 'images').mkdir()
        (output_data / 'thumbs').mkdir()
        for relative, source in files_to_copy.items():
            if relative == 'data/sightvault.sqlite3':
                continue
            target = output_data.joinpath(*PurePosixPath(relative).parts[1:])
            shutil.copyfile(source, target)
            record = records[relative]
            if target.stat().st_size != record['size'] or sha256_file(target) != record['sha256']:
                raise ValueError(f'Disposable copied evidence failed its size/SHA-256 check: {relative}')

    return {
        'status': 'prepared',
        'asset_count': len(rows),
        'stored_image_count': len({row['image_path'] for row in rows}),
        'stored_thumbnail_count': len({row['thumbnail_path'] for row in rows}),
        'database_backup': 'SQLite backup API; integrity_check=ok',
        'database_sha256_matches_package_manifest': database_sha256_matches_package_manifest,
        'database_mutability_note': 'The database may have been path-rebased by prepare_demo.py or updated by normal app use; current public asset mapping and SQLite integrity were checked.',
        'source_path_rebased': False,
        'stored_evidence_sha256_verified': True,
        'output_data_root': str(output_data),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-data', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = prepare_disposable_demo(args.source_root, args.output_data)
    except (OSError, ValueError, KeyError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
