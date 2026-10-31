"""Verify a portable package ZIP's external archive SHA and internal file manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys
from zipfile import BadZipFile, ZipFile

MANIFEST_PATH = 'SightVault/handoff/PORTABLE_PACKAGE_MANIFEST.json'
REVIEWED_PUBLIC_JSONL = 'SightVault/qa/ocr-v2/ground_truth.jsonl'
REVIEWED_PUBLIC_JSONL_SHA256 = '0001032a7d8e23b8b6922a5dcda73280e6ec1f641373f1107bd4c457806cb13c'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_archive(archive_path: Path, sha_path: Path | None = None) -> dict:
    archive_path = archive_path.resolve()
    sha_path = sha_path or archive_path.with_suffix(archive_path.suffix + '.sha256')
    if not archive_path.is_file() or not sha_path.is_file():
        raise ValueError('Archive or adjacent .sha256 file is missing')
    sidecar = sha_path.read_text(encoding='ascii').strip().split()
    if not sidecar or len(sidecar[0]) != 64:
        raise ValueError('Malformed .sha256 sidecar')
    with archive_path.open('rb') as stream:
        archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    if archive_hash != sidecar[0].lower():
        raise ValueError('Archive SHA-256 does not match its sidecar')
    if len(sidecar) > 1 and sidecar[-1] != archive_path.name:
        raise ValueError('Sidecar names a different archive')

    with ZipFile(archive_path) as bundle:
        bad_member = bundle.testzip()
        if bad_member is not None:
            raise ValueError(f'ZIP CRC check failed for {bad_member}')
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError('ZIP contains duplicate member names')
        for info in bundle.infolist():
            name = info.filename
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name:
                raise ValueError(f'Unsafe archive member path: {name}')
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if info.is_dir() or (info.create_system == 3 and file_type not in (0, stat.S_IFREG)):
                raise ValueError(f'Package archive contains a symlink, directory, or special file: {name}')
            if any(part in ('.venv', '.venv-lfm3', '.models') for part in path.parts):
                raise ValueError(f'Package unexpectedly contains a model or virtual environment: {name}')
            if path.suffix.lower() in {'.safetensors', '.gguf', '.pt', '.pth', '.onnx', '.bin', '.weights', '.log'}:
                raise ValueError(f'Package unexpectedly contains weights or raw logs: {name}')
            if path.suffix.lower() == '.jsonl':
                is_reviewed_fixture = (
                    name == REVIEWED_PUBLIC_JSONL
                    and digest(bundle.read(name)) == REVIEWED_PUBLIC_JSONL_SHA256
                )
                if not is_reviewed_fixture:
                    raise ValueError(f'Package unexpectedly contains weights or raw logs: {name}')
            if 'data' in path.parts:
                allowed_demo_db = (len(path.parts) == 3 and path.parts[:2] == ('SightVault', 'data')
                                   and path.parts[2] == 'sightvault.sqlite3')
                allowed_demo_asset = (len(path.parts) == 4 and path.parts[:2] == ('SightVault', 'data')
                                      and path.parts[2] in ('images', 'thumbs'))
                if not (allowed_demo_db or allowed_demo_asset):
                    raise ValueError(f'Package contains non-demo data: {name}')
            if not name.startswith('SightVault/'):
                raise ValueError(f'Unexpected archive root entry: {name}')
        if MANIFEST_PATH not in names:
            raise ValueError('Internal portable package manifest is missing')
        manifest = json.loads(bundle.read(MANIFEST_PATH))
        if manifest.get('artifact_type') != 'portable source-and-setup bundle' or manifest.get('native_executable_included') is not False:
            raise ValueError('Artifact metadata does not identify a portable source-and-setup bundle')
        if manifest.get('package_name') != archive_path.name:
            raise ValueError('Internal package name does not match the ZIP filename')
        records = manifest.get('files')
        if not isinstance(records, list):
            raise ValueError('Internal file records are missing')
        expected = {}
        for record in records:
            path = record.get('path')
            safe = PurePosixPath(path or '')
            if not path or safe.is_absolute() or '..' in safe.parts or '\\' in path or path in expected:
                raise ValueError(f'Invalid or duplicate internal file path: {path!r}')
            expected[path] = record
        actual = {name.removeprefix('SightVault/') for name in names
                  if name.startswith('SightVault/') and name != MANIFEST_PATH}
        if actual != set(expected):
            missing = sorted(set(expected) - actual)
            unlisted = sorted(actual - set(expected))
            raise ValueError(f'Internal file inventory mismatch; missing={missing}, unlisted={unlisted}')
        for relative, record in expected.items():
            name = 'SightVault/' + relative
            content = bundle.read(name)
            if len(content) != record.get('size') or digest(content) != record.get('sha256'):
                raise ValueError(f'Internal file hash or size mismatch: {relative}')
        demo = manifest.get('demo')
        evidence_record = expected.get('handoff/DEMO_EVIDENCE_MANIFEST.json')
        if not isinstance(demo, dict) or not evidence_record:
            raise ValueError('Public demo evidence manifest or package metadata is missing')
        if evidence_record.get('sha256') != demo.get('evidence_manifest_sha256'):
            raise ValueError('Included public demo evidence manifest does not match its recorded SHA-256')
        evidence_manifest = json.loads(bundle.read('SightVault/handoff/DEMO_EVIDENCE_MANIFEST.json'))
        if (evidence_manifest.get('format_version') != 1
                or evidence_manifest.get('artifact_type') != 'reviewed public demo stored evidence manifest'
                or not isinstance(evidence_manifest.get('entries'), list)):
            raise ValueError('Included public demo evidence manifest is malformed')
        model_record = expected.get('handoff/MODEL_MANIFEST.json')
        if not model_record or model_record.get('sha256') != manifest.get('model_manifest_sha256'):
            raise ValueError('Included model manifest does not match its recorded SHA-256')
        model_data = json.loads(bundle.read('SightVault/handoff/MODEL_MANIFEST.json'))
        sources = model_data.get('sources', {})
        model_files = model_data.get('files', [])
        model_groups = []
        for name, source in sorted(sources.items()):
            group_files = [record for record in model_files if PurePosixPath(record['path']).parts[1] == name]
            optional = bool(source.get('optional', False) or source.get('required') is False)
            model_groups.append({
                'name': name,
                'repo': source.get('repo'),
                'revision': source.get('revision'),
                'required': not optional,
                'optional': optional,
                'file_count': len(group_files),
                'download_bytes': sum(record['size'] for record in group_files),
            })
        total_model_bytes = sum(record['size'] for record in model_files)
        if manifest.get('model_weights_included') is not False:
            raise ValueError('Package manifest must explicitly state that model weights are excluded')
        if manifest.get('models') != model_groups or manifest.get('model_download_bytes') != total_model_bytes:
            raise ValueError('Package model groups or expected download sizes differ from the included pinned manifest')
    return {'archive': archive_path.name, 'sha256': archive_hash,
            'files_checked': len(expected), 'artifact_type': manifest['artifact_type'],
            'platform_target': manifest.get('platform_target'), 'passed': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256-file', type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_archive(args.archive, args.sha256_file)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, BadZipFile) as exc:
        print(json.dumps({'archive': args.archive.name, 'passed': False, 'error': str(exc)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
