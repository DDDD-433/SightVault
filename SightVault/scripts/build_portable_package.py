"""Build a deterministic source-and-setup ZIP from reviewed inputs only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PACKAGE_NAMES = {
    'macos-arm64': 'SightVault-macOS-arm64.zip',
    'windows-x64': 'SightVault-Windows-x64.zip',
}
PACKAGE_MANIFEST = 'handoff/PORTABLE_PACKAGE_MANIFEST.json'
DEMO_EVIDENCE_MANIFEST = 'handoff/DEMO_EVIDENCE_MANIFEST.json'
EMBEDDING_MODEL = 'google/embeddinggemma-2:768:text-image'
FORBIDDEN_COMPONENTS = {'.git', '.venv', '.venv-lfm3', '.models', 'data', 'output', '__pycache__', '.pytest_cache'}
FORBIDDEN_SUFFIXES = {'.pyc', '.log', '.sqlite', '.sqlite3', '.db', '.wal', '.shm', '.safetensors', '.gguf', '.pt', '.pth'}
DEMO_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative_path(raw: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise ValueError('Allowlist paths must be non-empty strings')
    normalized = raw.replace('\\', '/')
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ('', '.', '..') for part in path.parts):
        raise ValueError(f'Unsafe relative path in allowlist: {raw!r}')
    if ':' in path.parts[0]:
        raise ValueError(f'Unsafe drive-qualified path in allowlist: {raw!r}')
    return path


def reject_symlink_components(root: Path, relative: PurePosixPath) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Refusing package input through symlink component: {current.relative_to(root).as_posix()}')


def confined_file(root: Path, relative: PurePosixPath) -> Path:
    reject_symlink_components(root, relative)
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f'Package input is missing or escapes its source root: {relative.as_posix()}')
    return resolved


def read_allowlist(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('format_version') != 1:
        raise ValueError(f'Unsupported allowlist format: {path}')
    return value


def collect_source_files(source_root: Path, allowlist_path: Path, qa_allowlist_path: Path) -> dict[str, Path]:
    source_root = source_root.resolve()
    allowlist = read_allowlist(allowlist_path)
    files: dict[str, Path] = {}

    def add(relative: PurePosixPath):
        name = relative.as_posix()
        if any(part in FORBIDDEN_COMPONENTS for part in relative.parts) or relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f'Path is excluded from portable source packages: {name}')
        if name in files:
            return
        files[name] = confined_file(source_root, relative)

    for raw in allowlist.get('root_files', []):
        relative = relative_path(raw)
        if len(relative.parts) != 1:
            raise ValueError(f'root_files entries must name a root file: {raw!r}')
        add(relative)

    for tree in allowlist.get('trees', []):
        base = relative_path(tree['path'])
        reject_symlink_components(source_root, base)
        directory = source_root.joinpath(*base.parts)
        if directory.is_symlink() or not directory.resolve().is_relative_to(source_root) or not directory.is_dir():
            raise ValueError(f'Allowlisted source directory is missing or unsafe: {base.as_posix()}')
        extensions = {str(s).lower() for s in tree.get('extensions', [])}
        exact_files = set()
        for raw in tree.get('files', []):
            item = relative_path(raw)
            exact_files.add(item.as_posix() if item.parts[:len(base.parts)] == base.parts
                            else (base / item).as_posix())
        for path in sorted(directory.rglob('*')):
            if path.is_dir():
                if path.is_symlink():
                    raise ValueError(f'Refusing symlinked package directory: {path.relative_to(source_root).as_posix()}')
                continue
            rel = PurePosixPath(path.relative_to(source_root).as_posix())
            if path.is_symlink():
                raise ValueError(f'Refusing symlinked package input: {rel.as_posix()}')
            if (path.suffix.lower() in extensions and not tree.get('reviewed_manifest')) or rel.as_posix() in exact_files:
                add(rel)
        for exact in exact_files:
            add(PurePosixPath(exact))
        reviewed_manifest = tree.get('reviewed_manifest')
        if reviewed_manifest:
            manifest_relative = relative_path(reviewed_manifest)
            manifest_path = confined_file(source_root, manifest_relative)
            try:
                reviewed_records = json.loads(manifest_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f'Could not read reviewed file manifest: {manifest_relative.as_posix()}') from exc
            if not isinstance(reviewed_records, list):
                raise ValueError(f'Reviewed file manifest must contain an array: {manifest_relative.as_posix()}')
            for record in reviewed_records:
                if not isinstance(record, dict) or not isinstance(record.get('path'), str):
                    continue
                relative = relative_path(record['path'])
                if relative.parts[:len(base.parts)] != base.parts or relative.suffix.lower() not in extensions:
                    continue
                path = confined_file(source_root, relative)
                if path.stat().st_size != record.get('size') or sha256_file(path) != record.get('sha256'):
                    raise ValueError(f'Reviewed manifest file failed size/SHA-256 validation: {relative.as_posix()}')
                add(relative)

    qa_spec = json.loads(qa_allowlist_path.read_text(encoding='utf-8'))
    qa_files = qa_spec.get('files') if isinstance(qa_spec, dict) else qa_spec
    if not isinstance(qa_files, list):
        raise ValueError('QA allowlist must contain a files array')
    seen_qa = set()
    for raw in qa_files:
        relative = relative_path(raw)
        if relative.parts[0] != 'qa':
            raise ValueError(f'QA allowlist entries must be inside qa/: {raw!r}')
        if relative.as_posix() in seen_qa:
            continue
        seen_qa.add(relative.as_posix())
        add(relative)

    # The supplied manifest is authoritative, and old extraction manifests are stale after edits.
    files.pop('handoff/MODEL_MANIFEST.json', None)
    files.pop(PACKAGE_MANIFEST, None)
    return files


def validate_model_manifest(path: Path) -> tuple[dict, str]:
    data = json.loads(path.read_text(encoding='utf-8'))
    sources = data.get('sources')
    entries = data.get('files')
    if not isinstance(sources, dict) or not sources or not isinstance(entries, list) or not entries:
        raise ValueError('Model manifest must contain non-empty sources and files arrays')
    for name, source in sources.items():
        if not name or not isinstance(source.get('repo'), str) or not source.get('repo'):
            raise ValueError(f'Model source {name!r} is missing a repository id')
        if not isinstance(source.get('revision'), str) or not source.get('revision'):
            raise ValueError(f'Model source {name!r} is missing a pinned revision')
    seen = set()
    total = 0
    group_file_count = {name: 0 for name in sources}
    for item in entries:
        relative = relative_path(item.get('path'))
        if len(relative.parts) < 3 or relative.parts[0] != '.models':
            raise ValueError(f'Model file path must be under .models/<model>/: {item.get("path")!r}')
        if relative.parts[1] not in sources:
            raise ValueError(f'Model file has no matching source entry: {relative.as_posix()}')
        if relative.as_posix() in seen:
            raise ValueError(f'Duplicate model path: {relative.as_posix()}')
        seen.add(relative.as_posix())
        group_file_count[relative.parts[1]] += 1
        if not isinstance(item.get('size'), int) or item['size'] < 0:
            raise ValueError(f'Invalid model file size for {relative.as_posix()}')
        digest = item.get('sha256', '')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest.lower()):
            raise ValueError(f'Invalid SHA-256 for {relative.as_posix()}')
        total += item['size']
    if any(group_file_count[name] == 0 for name in sources):
        raise ValueError('Every model source group must have at least one pinned file')
    if data.get('total_bytes') is not None and data['total_bytes'] != total:
        raise ValueError('Model manifest total_bytes does not equal the sum of listed files')
    data.setdefault('total_bytes', total)
    return data, sha256_file(path)


def model_group_records(data: dict) -> list[dict]:
    result = []
    for name, source in sorted(data['sources'].items()):
        files = [item for item in data['files'] if PurePosixPath(item['path']).parts[1] == name]
        optional = bool(source.get('optional', False) or source.get('required') is False)
        result.append({
            'name': name,
            'repo': source['repo'],
            'revision': source['revision'],
            'required': not optional,
            'optional': optional,
            'file_count': len(files),
            'download_bytes': sum(item['size'] for item in files),
        })
    return result


def checked_demo_evidence(data_root: Path, subdir: str, name: str) -> Path:
    data_root = Path(os.path.abspath(data_root.expanduser()))
    if data_root.is_symlink():
        raise ValueError('Refusing a symlinked public demo data root')
    resolved_data_root = data_root.resolve()
    evidence_root = data_root / subdir
    if evidence_root.is_symlink():
        raise ValueError(f'Refusing symlinked demo evidence directory: data/{subdir}')
    resolved_evidence_root = evidence_root.resolve()
    if not resolved_evidence_root.is_relative_to(resolved_data_root) or not resolved_evidence_root.is_dir():
        raise ValueError(f'Demo evidence directory escapes the explicit data root: data/{subdir}')
    relative = relative_path(name)
    current = evidence_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Refusing symlinked demo evidence file: data/{subdir}/{name}')
    resolved = current.resolve()
    if not resolved.is_relative_to(resolved_data_root) or not resolved.is_file():
        raise ValueError(f'Missing or unsafe stored demo evidence: data/{subdir}/{name}')
    return resolved


def load_demo_evidence_manifest(source_root: Path, sample_images: list[dict]) -> tuple[dict, dict[str, dict], str]:
    manifest_path = source_root / DEMO_EVIDENCE_MANIFEST
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('format_version') != 1 or manifest.get('artifact_type') != 'reviewed public demo stored evidence manifest':
        raise ValueError('Unsupported public demo evidence manifest')

    sample_manifest_path = source_root / 'handoff' / 'SAMPLE_MANIFEST.json'
    sample_manifest_sha = sha256_file(sample_manifest_path)
    provenance = manifest.get('provenance')
    if not isinstance(provenance, dict) or provenance.get('sample_manifest_sha256') != sample_manifest_sha:
        raise ValueError('Public demo evidence manifest does not match the included canonical sample manifest')
    if not isinstance(provenance.get('baseline_git_revision'), str) or not provenance['baseline_git_revision']:
        raise ValueError('Public demo evidence manifest is missing its verified baseline revision')

    samples_by_sha: dict[str, list[str]] = {}
    for item in sample_images:
        sample_path = relative_path(item.get('path'))
        digest = item.get('sha256')
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f'Invalid sample SHA-256 in canonical manifest: {sample_path.as_posix()}')
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError(f'Invalid sample SHA-256 in canonical manifest: {sample_path.as_posix()}') from exc
        samples_by_sha.setdefault(digest, []).append(sample_path.as_posix())
    samples_by_sha = {digest: sorted(paths) for digest, paths in samples_by_sha.items()}

    validation = manifest.get('validation')
    if (not isinstance(validation, dict)
            or validation.get('source_image_count') != len(sample_images)
            or validation.get('unique_source_hash_count') != len(samples_by_sha)
            or validation.get('stored_pngs_verified') != len(samples_by_sha)
            or validation.get('stored_thumbnails_verified') != len(samples_by_sha)):
        raise ValueError('Public demo evidence manifest does not report a complete verification of the canonical sample set')

    entries = manifest.get('entries')
    if not isinstance(entries, list) or len(entries) != len(samples_by_sha):
        raise ValueError('Public demo evidence manifest has missing or extra source image entries')
    evidence_by_path: dict[str, dict] = {}
    seen_hashes = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Public demo evidence manifest contains an invalid entry')
        digest = entry.get('source_sha256')
        if digest not in samples_by_sha or digest in seen_hashes:
            raise ValueError('Public demo evidence manifest contains an unknown or duplicate source SHA-256')
        seen_hashes.add(digest)
        if entry.get('source_samples') != samples_by_sha[digest]:
            raise ValueError(f'Public demo evidence manifest has incorrect sample links for {digest}')
        for kind, subdir, suffix in (('image', 'images', '.png'), ('thumbnail', 'thumbs', '.webp')):
            expected_path = f'data/{subdir}/{digest}{suffix}'
            record = entry.get(kind)
            if (not isinstance(record, dict) or record.get('path') != expected_path
                    or type(record.get('size')) is not int or record['size'] <= 0
                    or not isinstance(record.get('sha256'), str) or len(record['sha256']) != 64
                    or not isinstance(record.get('decoded_rgb_sha256'), str)
                    or len(record['decoded_rgb_sha256']) != 64
                    or type(record.get('width')) is not int or record['width'] <= 0
                    or type(record.get('height')) is not int or record['height'] <= 0):
                raise ValueError(f'Public demo evidence manifest has an invalid {kind} record for {digest}')
            try:
                int(record['sha256'], 16)
                int(record['decoded_rgb_sha256'], 16)
            except ValueError as exc:
                raise ValueError(f'Public demo evidence manifest has an invalid digest for {expected_path}') from exc
            if expected_path in evidence_by_path:
                raise ValueError(f'Public demo evidence manifest duplicates path: {expected_path}')
            evidence_by_path[expected_path] = record
    if seen_hashes != set(samples_by_sha):
        raise ValueError('Public demo evidence manifest has missing or extra source hashes')
    return manifest, evidence_by_path, sha256_bytes(manifest_bytes)


def demo_rows(source_root: Path, demo_db: Path, demo_data_root: Path,
              evidence_by_path: dict[str, dict] | None = None) -> list[sqlite3.Row]:
    demo_data_root = Path(os.path.abspath(demo_data_root.expanduser()))
    if demo_data_root.is_symlink():
        raise ValueError('Refusing a symlinked public demo data root')
    manifest_path = source_root / 'handoff' / 'SAMPLE_MANIFEST.json'
    sample_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    sample_images = public_image_manifest_entries(sample_manifest)
    expected = {PurePosixPath(item['path']).name: item for item in sample_images}
    if len(expected) != 62 or len(sample_images) != 62:
        raise ValueError('Expected exactly 62 sample manifest entries')
    if evidence_by_path is None:
        _, evidence_by_path, _ = load_demo_evidence_manifest(source_root, sample_images)
    for item in sample_images:
        rel = relative_path(item['path'])
        if rel.parts[0] != 'samples':
            raise ValueError(f'Sample manifest entry is outside samples/: {item["path"]}')
        sample = confined_file(source_root, rel)
        if sample.stat().st_size != item['size'] or sha256_file(sample) != item['sha256']:
            raise ValueError(f'Missing or changed public demo image: {rel.as_posix()}')

    uri = demo_db.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Demo database failed SQLite integrity_check')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(assets)')}
        required_columns = {'filename', 'filepath', 'sha256', 'image_path', 'thumbnail_path', 'embedding', 'embedding_model'}
        if not required_columns.issubset(columns):
            raise ValueError('Demo database does not have the expected SightVault assets schema')
        rows = list(db.execute('SELECT filename,filepath,sha256,image_path,thumbnail_path,embedding,embedding_model FROM assets ORDER BY id'))
    if len(rows) != 62:
        raise ValueError(f'Expected 62 public demo rows, found {len(rows)}')
    if len({row['filename'] for row in rows}) != 62:
        raise ValueError('Demo database contains duplicate filenames')
    for row in rows:
        item = expected.get(row['filename'])
        if item is None or row['sha256'] != item['sha256'] or Path(row['filepath']).name != row['filename']:
            raise ValueError(f'Demo database contains an unknown or changed asset: {row["filename"]}')
        if row['embedding'] is None or len(row['embedding']) != 768 * 4 or row['embedding_model'] != EMBEDDING_MODEL:
            raise ValueError(f'Demo asset is missing its direct 768-dimensional embedding: {row["filename"]}')
        image_name, thumb_name = row['image_path'], row['thumbnail_path']
        if image_name != row['sha256'] + '.png' or thumb_name != row['sha256'] + '.webp':
            raise ValueError(f'Unexpected stored evidence filename for {row["filename"]}')
        for subdir, name in (('images', image_name), ('thumbs', thumb_name)):
            relative = f'data/{subdir}/{name}'
            record = evidence_by_path.get(relative)
            if record is None:
                raise ValueError(f'No reviewed public demo evidence hash is recorded for {relative}')
            path = checked_demo_evidence(demo_data_root, subdir, name)
            if path.stat().st_size != record['size'] or sha256_file(path) != record['sha256']:
                raise ValueError(f'Stored public demo evidence differs from the independent reviewed hash: {relative}')
    return rows


def sqlite_backup(source: Path, target: Path) -> None:
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as db:
        with sqlite3.connect(target) as backup:
            db.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite backup failed integrity_check')


def source_revision(source_root: Path) -> str | None:
    try:
        return subprocess.check_output(['git', '-C', str(source_root), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def public_image_manifest_entries(sample_manifest: list[dict]) -> list[dict]:
    """Ignore the two source metadata JSON files alongside the 62 image records."""
    return [item for item in sample_manifest
            if PurePosixPath(item['path']).suffix.lower() in DEMO_IMAGE_SUFFIXES]


def zip_write(archive: ZipFile, archive_name: str, data: bytes) -> None:
    info = ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def build(args) -> tuple[Path, Path]:
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f'Source root does not exist: {source_root}')
    allowlist = args.source_allowlist or source_root / 'packaging' / 'PORTABLE_SOURCE_ALLOWLIST.json'
    qa_allowlist = args.qa_allowlist
    model_manifest, model_manifest_sha = validate_model_manifest(args.model_manifest)
    demo_data_root = Path(os.path.abspath(args.demo_data_root.expanduser()))
    demo_db = args.demo_db.resolve()
    sample_manifest = json.loads((source_root / 'handoff' / 'SAMPLE_MANIFEST.json').read_text(encoding='utf-8'))
    sample_images = public_image_manifest_entries(sample_manifest)
    _, evidence_by_path, evidence_manifest_sha = load_demo_evidence_manifest(source_root, sample_images)
    rows = demo_rows(source_root, demo_db, demo_data_root, evidence_by_path)
    source_files = collect_source_files(source_root, allowlist, qa_allowlist)

    output = args.output or source_root / 'exports' / PACKAGE_NAMES[args.platform]
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sightvault-portable-') as temporary:
        backup_path = Path(temporary) / 'sightvault.sqlite3'
        sqlite_backup(demo_db, backup_path)
        package_files = dict(source_files)
        package_files['handoff/MODEL_MANIFEST.json'] = args.model_manifest.resolve()
        entries: dict[str, bytes] = {}
        for relative, path in package_files.items():
            entries[relative] = path.read_bytes()
        if sha256_bytes(entries['handoff/DEMO_EVIDENCE_MANIFEST.json']) != evidence_manifest_sha:
            raise ValueError('Public demo evidence manifest changed during package assembly')
        if sha256_bytes(entries['handoff/SAMPLE_MANIFEST.json']) != json.loads(
                entries['handoff/DEMO_EVIDENCE_MANIFEST.json'])['provenance']['sample_manifest_sha256']:
            raise ValueError('Canonical sample manifest changed during package assembly')
        entries['data/sightvault.sqlite3'] = backup_path.read_bytes()
        for row in rows:
            for subdir, name in (('images', row['image_path']), ('thumbs', row['thumbnail_path'])):
                relative = f'data/{subdir}/{name}'
                path = checked_demo_evidence(demo_data_root, subdir, name)
                anchor = evidence_by_path.get(relative)
                payload = path.read_bytes()
                if anchor is None or len(payload) != anchor['size'] or sha256_bytes(payload) != anchor['sha256']:
                    raise ValueError(f'Stored public demo evidence changed after validation: {relative}')
                entries[relative] = payload

        file_records = [
            {'path': name, 'size': len(content), 'sha256': sha256_bytes(content)}
            for name, content in sorted(entries.items())
        ]
        platform_label = {'macos-arm64': 'macOS arm64', 'windows-x64': 'Windows x64'}[args.platform]
        package_manifest = {
            'format_version': 1,
            'artifact_type': 'portable source-and-setup bundle',
            'package_name': PACKAGE_NAMES[args.platform],
            'platform_target': platform_label,
            'native_executable_included': False,
            'model_weights_included': False,
            'python_environment_included': False,
            'source_revision': source_revision(source_root),
            'model_manifest_sha256': model_manifest_sha,
            'model_download_bytes': model_manifest['total_bytes'],
            'model_weights_included': False,
            'models': model_group_records(model_manifest),
            'demo': {'asset_count': len(rows), 'embedding_model': EMBEDDING_MODEL,
                     'database_sha256': sha256_bytes(entries['data/sightvault.sqlite3']),
                     'evidence_manifest_sha256': evidence_manifest_sha},
            'files': file_records,
        }
        manifest_bytes = (json.dumps(package_manifest, indent=2, sort_keys=True) + '\n').encode('utf-8')
        with ZipFile(output, 'w') as archive:
            archive.comment = b''
            for name, content in sorted(entries.items()):
                zip_write(archive, 'SightVault/' + name, content)
            zip_write(archive, 'SightVault/' + PACKAGE_MANIFEST, manifest_bytes)
    archive_digest = sha256_file(output)
    sidecar = output.with_suffix(output.suffix + '.sha256')
    sidecar.write_text(f'{archive_digest}  {output.name}\n', encoding='ascii', newline='\n')
    return output, sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path, help='Reviewed app source root')
    parser.add_argument('--platform', required=True, choices=sorted(PACKAGE_NAMES))
    parser.add_argument('--model-manifest', required=True, type=Path, help='Pinned model revision and SHA-256 manifest to include')
    parser.add_argument('--qa-allowlist', required=True, type=Path, help='JSON list of individually reviewed public QA paths')
    parser.add_argument('--source-allowlist', type=Path, help='Defaults to <source-root>/packaging/PORTABLE_SOURCE_ALLOWLIST.json')
    parser.add_argument('--demo-db', required=True, type=Path, help='Explicit, verified 62-image public demo SQLite DB')
    parser.add_argument('--demo-data-root', required=True, type=Path, help='Explicit folder containing the selected DB, images/, and thumbs/')
    parser.add_argument('--output', type=Path, help='Output ZIP path; defaults to exports/<platform package name>')
    args = parser.parse_args(argv)
    try:
        output, sidecar = build(args)
    except (OSError, ValueError, KeyError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f'Portable package build stopped: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'artifact_type': 'portable source-and-setup bundle', 'archive': str(output),
                      'sha256_file': str(sidecar), 'package_built': True}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
