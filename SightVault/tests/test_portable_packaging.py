from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from handoff.verify_transfer import required_models, safe_path, verify_portable_source_manifest
from handoff.download_pinned_models import current_platform_id, incompatible_platform_groups
from scripts.build_portable_package import (
    collect_source_files,
    demo_rows,
    model_group_records,
    public_image_manifest_entries,
    relative_path,
    validate_model_manifest,
    zip_write,
)
from scripts.prepare_disposable_demo_benchmark import prepare_disposable_demo
from scripts.launch import compatible_instance, provider_mismatch
from scripts.windows_setup_preflight import validate_optional_model_groups
from scripts.verify_package_integrity import (
    MANIFEST_PATH,
    REVIEWED_PUBLIC_JSONL,
    REVIEWED_PUBLIC_JSONL_SHA256,
    verify_archive,
)


def _make_public_demo_fixture(source_root, data_root, db_path, *, symlink_images_to=None):
    (source_root / 'handoff').mkdir(parents=True, exist_ok=True)
    (source_root / 'samples').mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    if symlink_images_to is None:
        (data_root / 'images').mkdir()
        images_root = data_root / 'images'
    else:
        symlink_images_to.mkdir(parents=True, exist_ok=True)
        (data_root / 'images').symlink_to(symlink_images_to, target_is_directory=True)
        images_root = symlink_images_to
    (data_root / 'thumbs').mkdir()

    sample_records = []
    evidence_by_sha = {}
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('CREATE TABLE assets (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT, sha256 TEXT, image_path TEXT, thumbnail_path TEXT, embedding BLOB, embedding_model TEXT)')
        for index in range(62):
            filename = f'demo-{index:02d}.png'
            raw = b'source-' + index.to_bytes(2, 'big')
            digest = hashlib.sha256(raw).hexdigest()
            sample_path = source_root / 'samples' / filename
            sample_path.write_bytes(raw)
            sample_records.append({'path': f'samples/{filename}', 'size': len(raw), 'sha256': digest})

            image_name = digest + '.png'
            thumb_name = digest + '.webp'
            image_bytes = b'expected-image-' + index.to_bytes(2, 'big')
            thumb_bytes = b'expected-thumb-' + index.to_bytes(2, 'big')
            (images_root / image_name).write_bytes(image_bytes)
            (data_root / 'thumbs' / thumb_name).write_bytes(thumb_bytes)
            evidence_by_sha[digest] = {
                'source_sha256': digest,
                'source_samples': [f'samples/{filename}'],
                'image': {
                    'path': f'data/images/{image_name}', 'size': len(image_bytes),
                    'sha256': hashlib.sha256(image_bytes).hexdigest(),
                    'decoded_rgb_sha256': hashlib.sha256(image_bytes).hexdigest(), 'width': 1, 'height': 1,
                },
                'thumbnail': {
                    'path': f'data/thumbs/{thumb_name}', 'size': len(thumb_bytes),
                    'sha256': hashlib.sha256(thumb_bytes).hexdigest(),
                    'decoded_rgb_sha256': hashlib.sha256(thumb_bytes).hexdigest(), 'width': 1, 'height': 1,
                },
            }
            db.execute('INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
                index + 1, filename, f'/old/{filename}', digest, image_name, thumb_name,
                b'\0' * (768 * 4), 'google/embeddinggemma-2:768:text-image'))

    sample_manifest_path = source_root / 'handoff' / 'SAMPLE_MANIFEST.json'
    sample_manifest_path.write_text(json.dumps(sample_records), encoding='utf-8')
    evidence = {
        'format_version': 1,
        'artifact_type': 'reviewed public demo stored evidence manifest',
        'provenance': {
            'baseline_git_revision': 'fixture-baseline',
            'sample_manifest_sha256': hashlib.sha256(sample_manifest_path.read_bytes()).hexdigest(),
        },
        'validation': {
            'source_image_count': 62,
            'unique_source_hash_count': len(evidence_by_sha),
            'stored_pngs_verified': len(evidence_by_sha),
            'stored_thumbnails_verified': len(evidence_by_sha),
        },
        'entries': [evidence_by_sha[key] for key in sorted(evidence_by_sha)],
    }
    (source_root / 'handoff' / 'DEMO_EVIDENCE_MANIFEST.json').write_text(
        json.dumps(evidence), encoding='utf-8')


class PortablePackagingTests(unittest.TestCase):
    def test_portable_zip_preserves_executable_modes_only_for_mac_entrypoints(self):
        executable_paths = (
            'SightVault/Setup-Mac.sh',
            'SightVault/Start-SightVault.command',
        )
        regular_path = 'SightVault/README.md'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / 'candidate.zip'
            with ZipFile(archive_path, 'w') as archive:
                for name in (*executable_paths, regular_path):
                    zip_write(archive, name, b'fixture\n')

            expected_modes = {name: 0o755 for name in executable_paths}
            expected_modes[regular_path] = 0o644
            with ZipFile(archive_path) as archive:
                for name, expected_mode in expected_modes.items():
                    info = archive.getinfo(name)
                    self.assertEqual(info.create_system, 3)
                    self.assertTrue(stat.S_ISREG(info.external_attr >> 16))
                    self.assertEqual((info.external_attr >> 16) & 0o777, expected_mode)

            unzip = shutil.which('unzip')
            if os.name == 'posix' and unzip:
                extracted = root / 'extracted'
                result = subprocess.run(
                    [unzip, '-q', str(archive_path), '-d', str(extracted)],
                    capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                for name, expected_mode in expected_modes.items():
                    self.assertEqual(stat.S_IMODE((extracted / name).stat().st_mode), expected_mode)

    def test_extracted_portable_package_verifier_checks_hashes_and_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'Extracted Candidate With Spaces'
            (root / 'scripts').mkdir(parents=True)
            (root / 'handoff').mkdir()
            payload = b'portable source file\n'
            (root / 'scripts' / 'sample.py').write_bytes(payload)
            manifest = {
                'artifact_type': 'portable source-and-setup bundle',
                'package_name': 'SightVault-macOS-arm64.zip',
                'source_revision': 'reviewed-source-revision',
                'files': [{
                    'path': 'scripts/sample.py',
                    'size': len(payload),
                    'sha256': hashlib.sha256(payload).hexdigest(),
                }],
            }
            (root / 'handoff' / 'PORTABLE_PACKAGE_MANIFEST.json').write_text(
                json.dumps(manifest), encoding='utf-8')

            report = verify_portable_source_manifest(root)
            self.assertTrue(report['passed'])
            self.assertEqual(report['checked'], 1)
            self.assertEqual(report['source_revision'], 'reviewed-source-revision')

            (root / 'scripts' / 'sample.py').write_bytes(b'tampered source file\n')
            tampered = verify_portable_source_manifest(root)
            self.assertFalse(tampered['passed'])
            self.assertIn('SHA-256 differs', str(tampered['failures']))

    def test_windows_model_platform_policy_rejects_mlx_and_accepts_omni_without_downloads(self):
        sources = {
            'lfm3-vl-2b-mlx-4bit': {
                'optional': True,
                'platforms': ['macos-arm64'],
            },
            'lfm3-omni-2b-gguf': {
                'optional': True,
            },
        }
        windows = current_platform_id('Windows', 'AMD64')
        self.assertEqual(windows, 'windows-x64')
        self.assertTrue(incompatible_platform_groups(sources, {'lfm3-vl-2b-mlx-4bit'}, windows))
        failures = validate_optional_model_groups(sources, ['lfm3-vl-2b-mlx-4bit'], windows)
        self.assertTrue(any('supports macos-arm64' in failure for failure in failures))
        self.assertEqual([], incompatible_platform_groups(sources, {'lfm3-omni-2b-gguf'}, windows))
        self.assertEqual([], validate_optional_model_groups(sources, ['lfm3-omni-2b-gguf'], windows))

    def test_transfer_safe_path_formats_path_errors_and_keeps_confinement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, r'\.\./outside'):
                safe_path(root, Path('../outside'))
            outside = root.parent / (root.name + '-outside')
            outside.mkdir()
            link = root / 'linked'
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('Symlinks unavailable for this account')
            with self.assertRaisesRegex(ValueError, 'escapes its root'):
                safe_path(root, Path('linked/file'))

    def test_package_allowlists_add_only_named_source_and_qa_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            (root / 'qa').mkdir()
            (root / 'scripts' / 'ok.py').write_text('pass\n')
            (root / 'scripts' / 'ignored.txt').write_text('not allowed')
            (root / 'qa' / 'reviewed.json').write_text('{}')
            (root / 'qa' / 'private.log').write_text('never default')
            source_allowlist = root / 'source.json'
            source_allowlist.write_text(json.dumps({'format_version': 1, 'root_files': [], 'trees': [
                {'path': 'scripts', 'extensions': ['.py']}
            ]}))
            qa_allowlist = root / 'qa.json'
            qa_allowlist.write_text(json.dumps({'format_version': 1, 'files': ['qa/reviewed.json']}))
            found = collect_source_files(root, source_allowlist, qa_allowlist)
            self.assertEqual(set(found), {'scripts/ok.py', 'qa/reviewed.json'})

    def test_sample_package_includes_only_canonical_sample_manifest_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'samples').mkdir()
            (root / 'handoff').mkdir()
            public = b'reviewed public fixture'
            private = b'unlisted drop file'
            (root / 'samples' / 'public.png').write_bytes(public)
            (root / 'samples' / 'private-drop.png').write_bytes(private)
            (root / 'handoff' / 'SAMPLE_MANIFEST.json').write_text(json.dumps([{
                'path': 'samples/public.png', 'size': len(public), 'sha256': hashlib.sha256(public).hexdigest(),
            }]))
            source_allowlist = root / 'source.json'
            source_allowlist.write_text(json.dumps({'format_version': 1, 'root_files': [], 'trees': [
                {'path': 'samples', 'extensions': ['.png'], 'reviewed_manifest': 'handoff/SAMPLE_MANIFEST.json'},
            ]}))
            qa_allowlist = root / 'qa.json'
            qa_allowlist.write_text(json.dumps({'format_version': 1, 'files': []}))
            found = collect_source_files(root, source_allowlist, qa_allowlist)
            self.assertEqual(set(found), {'samples/public.png'})

    def test_allowlisted_file_rejects_an_in_root_symlinked_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'private').mkdir()
            (root / 'private' / 'reviewed.json').write_text('{}')
            try:
                (root / 'qa').symlink_to(root / 'private', target_is_directory=True)
            except OSError:
                self.skipTest('Symlinks unavailable for this account')
            source_allowlist = root / 'source.json'
            source_allowlist.write_text(json.dumps({'format_version': 1, 'root_files': [], 'trees': []}))
            qa_allowlist = root / 'qa.json'
            qa_allowlist.write_text(json.dumps({'format_version': 1, 'files': ['qa/reviewed.json']}))
            with self.assertRaisesRegex(ValueError, 'symlink component'):
                collect_source_files(root, source_allowlist, qa_allowlist)

    def test_allowlist_rejects_traversal_and_model_manifests_pin_hashes(self):
        for unsafe in ('../outside', '/absolute/path', 'C:\\outside'):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                relative_path(unsafe)
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / 'models.json'
            manifest = {
                'sources': {
                    'core': {'repo': 'org/core', 'revision': 'abc123'},
                    'optional-vision': {'repo': 'org/vision', 'revision': 'def456', 'optional': True},
                },
                'files': [
                    {'path': '.models/core/model.safetensors', 'size': 5, 'sha256': 'a' * 64},
                    {'path': '.models/optional-vision/model.safetensors', 'size': 7, 'sha256': 'b' * 64},
                ],
                'total_bytes': 12,
            }
            manifest_path.write_text(json.dumps(manifest))
            checked, _ = validate_model_manifest(manifest_path)
            self.assertEqual(required_models(checked), {'core'})
            self.assertEqual(required_models(checked, with_models=['optional-vision']), {'core', 'optional-vision'})
            self.assertEqual(required_models(checked, only=['optional-vision']), {'optional-vision'})
            groups = model_group_records(checked)
            self.assertEqual(groups, [
                {'name': 'core', 'repo': 'org/core', 'revision': 'abc123', 'required': True,
                 'optional': False, 'file_count': 1, 'download_bytes': 5},
                {'name': 'optional-vision', 'repo': 'org/vision', 'revision': 'def456', 'required': False,
                 'optional': True, 'file_count': 1, 'download_bytes': 7},
            ])

    def test_demo_manifest_filters_adjacent_metadata_from_the_62_image_count(self):
        records = [{'path': f'samples/image-{index:02d}.png'} for index in range(62)]
        records += [{'path': 'samples/manifest.json'}, {'path': 'samples/photo_sources.json'}]
        images = public_image_manifest_entries(records)
        self.assertEqual(len(images), 62)
        self.assertTrue(all(Path(item['path']).suffix == '.png' for item in images))

    def test_demo_rows_reject_symlinked_images_directory_outside_explicit_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            data_root = Path(temporary) / 'data'
            external_images = Path(temporary) / 'external-images'
            try:
                _make_public_demo_fixture(root, data_root, Path(temporary) / 'demo.sqlite3',
                                          symlink_images_to=external_images)
            except OSError:
                self.skipTest('Symlinks unavailable for this account')
            with self.assertRaisesRegex(ValueError, 'symlinked demo evidence directory'):
                demo_rows(root, Path(temporary) / 'demo.sqlite3', data_root)

    def test_demo_rows_reject_tampered_stored_image_bytes_at_valid_hash_derived_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            data_root = Path(temporary) / 'data'
            db_path = Path(temporary) / 'demo.sqlite3'
            _make_public_demo_fixture(root, data_root, db_path)
            sample = json.loads((root / 'handoff' / 'SAMPLE_MANIFEST.json').read_text())[0]
            image_path = data_root / 'images' / f'{sample["sha256"]}.png'
            image_path.write_bytes(b'PRIVATE_NON_PNG_CONTENT')
            with self.assertRaisesRegex(ValueError, 'independent reviewed hash'):
                demo_rows(root, db_path, data_root)

    def test_demo_rows_reject_missing_extra_or_wrong_evidence_manifest_paths(self):
        for mutation, error in (
            ('missing', 'missing or extra source image entries'),
            ('extra', 'missing or extra source image entries'),
            ('wrong-path', 'invalid image record'),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'source'
                data_root = Path(temporary) / 'data'
                db_path = Path(temporary) / 'demo.sqlite3'
                _make_public_demo_fixture(root, data_root, db_path)
                manifest_path = root / 'handoff' / 'DEMO_EVIDENCE_MANIFEST.json'
                evidence = json.loads(manifest_path.read_text())
                if mutation == 'missing':
                    evidence['entries'].pop()
                elif mutation == 'extra':
                    extra = dict(evidence['entries'][0])
                    extra['source_sha256'] = 'f' * 64
                    evidence['entries'].append(extra)
                else:
                    evidence['entries'][0]['image']['path'] = 'data/images/not-reviewed.png'
                manifest_path.write_text(json.dumps(evidence), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, error):
                    demo_rows(root, db_path, data_root)

    def test_disposable_demo_copy_uses_sqlite_backup_and_only_verified_stored_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'package'
            for relative in ('handoff', 'samples', 'data/images', 'data/thumbs'):
                (root / relative).mkdir(parents=True, exist_ok=True)
            samples = []
            records = []
            db_path = root / 'data' / 'sightvault.sqlite3'
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute('CREATE TABLE assets (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT, sha256 TEXT, image_path TEXT, thumbnail_path TEXT, embedding BLOB, embedding_model TEXT)')
                for index in range(62):
                    filename = f'demo-{index:02d}.png'
                    raw = b'public-sample-' + index.to_bytes(2, 'big')
                    sample_hash = hashlib.sha256(raw).hexdigest()
                    sample_path = root / 'samples' / filename
                    sample_path.write_bytes(raw)
                    samples.append({'path': f'samples/{filename}', 'size': len(raw), 'sha256': sample_hash})

                    image_name = sample_hash + '.png'
                    thumb_name = sample_hash + '.webp'
                    image = b'stored-png-' + index.to_bytes(2, 'big')
                    thumb = b'stored-webp-' + index.to_bytes(2, 'big')
                    (root / 'data' / 'images' / image_name).write_bytes(image)
                    (root / 'data' / 'thumbs' / thumb_name).write_bytes(thumb)
                    db.execute('INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
                        index + 1, filename, f'/old/location/{filename}', sample_hash,
                        image_name, thumb_name, b'\0' * (768 * 4), 'google/embeddinggemma-2:768:text-image'))
            for path in sorted((root / 'data').rglob('*')):
                if path.is_file():
                    relative = path.relative_to(root).as_posix()
                    payload = path.read_bytes()
                    records.append({'path': relative, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
            (root / 'handoff' / 'SAMPLE_MANIFEST.json').write_text(json.dumps(samples))
            (root / 'handoff' / 'PORTABLE_PACKAGE_MANIFEST.json').write_text(json.dumps({'demo': {'asset_count': 62}, 'files': records}))
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute("UPDATE assets SET filepath = 'C:/extracted/SightVault/samples/' || filename")

            output = Path(temporary) / 'disposable-data'
            report = prepare_disposable_demo(root, output)
            self.assertEqual(report['status'], 'prepared')
            self.assertEqual(report['asset_count'], 62)
            self.assertEqual(report['stored_image_count'], 62)
            self.assertEqual(report['stored_thumbnail_count'], 62)
            self.assertTrue(report['database_backup'].startswith('SQLite backup API'))
            self.assertFalse(report['database_sha256_matches_package_manifest'])
            self.assertFalse(report['source_path_rebased'])
            self.assertEqual(len(list((output / 'images').iterdir())), 62)
            self.assertEqual(len(list((output / 'thumbs').iterdir())), 62)
            with closing(sqlite3.connect(output / 'sightvault.sqlite3')) as db, db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT count(*) FROM assets').fetchone()[0], 62)
                self.assertTrue(db.execute('SELECT filepath FROM assets LIMIT 1').fetchone()[0].startswith('C:/extracted/'))
            self.assertFalse((output / 'samples').exists())

    def test_disposable_demo_copy_reports_absent_demo_as_not_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'package'
            root.mkdir()
            report = prepare_disposable_demo(root, Path(temporary) / 'copy')
            self.assertEqual(report['status'], 'skipped-not-validated')
            self.assertTrue(report['manual_instructions'])

    def test_running_launcher_rejects_a_different_or_unreported_vision_backend(self):
        root = str(Path(__file__).resolve().parents[1])
        status = {'instance_root': root, 'vision_backend': 'minicpm'}
        old = os.environ.get('SIGHTVAULT_VISION')
        try:
            os.environ['SIGHTVAULT_VISION'] = 'lfm3'
            self.assertFalse(compatible_instance(status))
            self.assertTrue(provider_mismatch(status))
            self.assertTrue(provider_mismatch({'instance_root': root}))
            self.assertFalse(provider_mismatch({'instance_root': root + '-other', 'vision_backend': 'minicpm'}))
        finally:
            if old is None:
                os.environ.pop('SIGHTVAULT_VISION', None)
            else:
                os.environ['SIGHTVAULT_VISION'] = old

    def test_package_integrity_verifies_zip_sha_and_every_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / 'SightVault-Windows-x64.zip'
            payload = b'portable source fixture\n'
            model_manifest = json.dumps({
                'sources': {'core': {'repo': 'org/core', 'revision': 'abc123'}},
                'files': [{'path': '.models/core/model.safetensors', 'size': 5, 'sha256': 'b' * 64}],
                'total_bytes': 5,
            }, sort_keys=True).encode() + b'\n'
            evidence_manifest = json.dumps({
                'format_version': 1,
                'artifact_type': 'reviewed public demo stored evidence manifest',
                'entries': [],
            }, sort_keys=True).encode() + b'\n'
            manifest = {
                'artifact_type': 'portable source-and-setup bundle',
                'package_name': archive_path.name,
                'native_executable_included': False,
                'model_weights_included': False,
                'model_download_bytes': 5,
                'models': [{
                    'name': 'core', 'repo': 'org/core', 'revision': 'abc123',
                    'required': True, 'optional': False, 'file_count': 1, 'download_bytes': 5,
                }],
                'platform_target': 'Windows x64',
                'model_manifest_sha256': hashlib.sha256(model_manifest).hexdigest(),
                'demo': {
                    'asset_count': 62,
                    'embedding_model': 'google/embeddinggemma-2:768:text-image',
                    'evidence_manifest_sha256': hashlib.sha256(evidence_manifest).hexdigest(),
                },
                'files': [
                    {'path': 'README.md', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()},
                    {'path': 'handoff/MODEL_MANIFEST.json', 'size': len(model_manifest), 'sha256': hashlib.sha256(model_manifest).hexdigest()},
                    {'path': 'handoff/DEMO_EVIDENCE_MANIFEST.json', 'size': len(evidence_manifest), 'sha256': hashlib.sha256(evidence_manifest).hexdigest()},
                ],
            }
            with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED) as archive:
                archive.writestr('SightVault/README.md', payload)
                archive.writestr('SightVault/handoff/MODEL_MANIFEST.json', model_manifest)
                archive.writestr('SightVault/handoff/DEMO_EVIDENCE_MANIFEST.json', evidence_manifest)
                archive.writestr(MANIFEST_PATH, json.dumps(manifest).encode())
            sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            archive_path.with_suffix('.zip.sha256').write_text(f'{sha}  {archive_path.name}\n')
            report = verify_archive(archive_path)
            self.assertTrue(report['passed'])
            self.assertEqual(report['files_checked'], 3)
            archive_path.with_suffix('.zip.sha256').write_text(('0' * 64) + '  ' + archive_path.name + '\n')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                verify_archive(archive_path)

    def test_package_integrity_allows_only_the_exact_reviewed_ground_truth_jsonl(self):
        fixture_path = Path(__file__).resolve().parents[1] / 'qa' / 'ocr-v2' / 'ground_truth.jsonl'
        fixture_bytes = fixture_path.read_bytes()
        self.assertEqual(hashlib.sha256(fixture_bytes).hexdigest(), REVIEWED_PUBLIC_JSONL_SHA256)

        def write_package(archive_path, jsonl_path, jsonl_bytes):
            payloads = {'README.md': b'portable source fixture\n'}
            model_manifest = json.dumps({
                'sources': {'core': {'repo': 'org/core', 'revision': 'abc123'}},
                'files': [{'path': '.models/core/model.safetensors', 'size': 5, 'sha256': 'b' * 64}],
                'total_bytes': 5,
            }, sort_keys=True).encode() + b'\n'
            evidence_manifest = json.dumps({
                'format_version': 1,
                'artifact_type': 'reviewed public demo stored evidence manifest',
                'entries': [],
            }, sort_keys=True).encode() + b'\n'
            payloads['handoff/MODEL_MANIFEST.json'] = model_manifest
            payloads['handoff/DEMO_EVIDENCE_MANIFEST.json'] = evidence_manifest
            payloads[jsonl_path.removeprefix('SightVault/')] = jsonl_bytes
            records = [{
                'path': name,
                'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
            } for name, data in payloads.items()]
            manifest = {
                'artifact_type': 'portable source-and-setup bundle',
                'package_name': archive_path.name,
                'native_executable_included': False,
                'model_weights_included': False,
                'model_download_bytes': 5,
                'models': [{
                    'name': 'core', 'repo': 'org/core', 'revision': 'abc123',
                    'required': True, 'optional': False, 'file_count': 1, 'download_bytes': 5,
                }],
                'platform_target': 'macOS arm64',
                'model_manifest_sha256': hashlib.sha256(model_manifest).hexdigest(),
                'demo': {
                    'asset_count': 62,
                    'embedding_model': 'google/embeddinggemma-2:768:text-image',
                    'evidence_manifest_sha256': hashlib.sha256(evidence_manifest).hexdigest(),
                },
                'files': records,
            }
            with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED) as archive:
                for name, data in payloads.items():
                    archive.writestr('SightVault/' + name, data)
                archive.writestr(MANIFEST_PATH, json.dumps(manifest).encode())
            archive_path.with_suffix('.zip.sha256').write_text(
                hashlib.sha256(archive_path.read_bytes()).hexdigest() + '  ' + archive_path.name + '\n')

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / 'reviewed.zip'
            write_package(allowed, REVIEWED_PUBLIC_JSONL, fixture_bytes)
            report = verify_archive(allowed)
            self.assertTrue(report['passed'])

            for label, path, payload in (
                ('arbitrary', 'SightVault/qa/ocr-v2/other.jsonl', fixture_bytes),
                ('tampered', REVIEWED_PUBLIC_JSONL, fixture_bytes + b'\n'),
            ):
                with self.subTest(label=label):
                    rejected = root / f'{label}.zip'
                    write_package(rejected, path, payload)
                    with self.assertRaisesRegex(ValueError, 'weights or raw logs'):
                        verify_archive(rejected)

    def test_package_integrity_rejects_public_demo_evidence_manifest_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / 'SightVault-Windows-x64.zip'
            payload = b'fixture\n'
            model_manifest = json.dumps({
                'sources': {'core': {'repo': 'org/core', 'revision': 'abc123'}},
                'files': [{'path': '.models/core/model.safetensors', 'size': 5, 'sha256': 'b' * 64}],
                'total_bytes': 5,
            }, sort_keys=True).encode() + b'\n'
            evidence_manifest = json.dumps({
                'format_version': 1,
                'artifact_type': 'reviewed public demo stored evidence manifest',
                'entries': [],
            }, sort_keys=True).encode() + b'\n'
            manifest = {
                'artifact_type': 'portable source-and-setup bundle',
                'package_name': archive_path.name,
                'native_executable_included': False,
                'model_weights_included': False,
                'model_download_bytes': 5,
                'models': [{'name': 'core', 'repo': 'org/core', 'revision': 'abc123',
                            'required': True, 'optional': False, 'file_count': 1, 'download_bytes': 5}],
                'platform_target': 'Windows x64',
                'model_manifest_sha256': hashlib.sha256(model_manifest).hexdigest(),
                'demo': {'asset_count': 62, 'embedding_model': 'google/embeddinggemma-2:768:text-image',
                         'evidence_manifest_sha256': '0' * 64},
                'files': [
                    {'path': 'README.md', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()},
                    {'path': 'handoff/MODEL_MANIFEST.json', 'size': len(model_manifest), 'sha256': hashlib.sha256(model_manifest).hexdigest()},
                    {'path': 'handoff/DEMO_EVIDENCE_MANIFEST.json', 'size': len(evidence_manifest), 'sha256': hashlib.sha256(evidence_manifest).hexdigest()},
                ],
            }
            with ZipFile(archive_path, 'w', compression=ZIP_DEFLATED) as archive:
                archive.writestr('SightVault/README.md', payload)
                archive.writestr('SightVault/handoff/MODEL_MANIFEST.json', model_manifest)
                archive.writestr('SightVault/handoff/DEMO_EVIDENCE_MANIFEST.json', evidence_manifest)
                archive.writestr(MANIFEST_PATH, json.dumps(manifest).encode())
            sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            archive_path.with_suffix('.zip.sha256').write_text(f'{sha}  {archive_path.name}\n')
            with self.assertRaisesRegex(ValueError, 'does not match its recorded SHA-256'):
                verify_archive(archive_path)

    def test_package_integrity_rejects_symlink_and_disguised_directory_members(self):
        for label, file_type, payload in (
            ('symlink', stat.S_IFLNK | 0o777, b'../../outside-target'),
            ('directory-disguised', stat.S_IFDIR | 0o755, b'not a directory'),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                archive_path = Path(temporary) / f'{label}.zip'
                info = ZipInfo(f'SightVault/qa/{label}')
                info.create_system = 3
                info.external_attr = file_type << 16
                with ZipFile(archive_path, 'w') as archive:
                    archive.writestr(info, payload)
                archive_path.with_suffix('.zip.sha256').write_text(
                    hashlib.sha256(archive_path.read_bytes()).hexdigest() + '  ' + archive_path.name + '\n')
                with self.assertRaisesRegex(ValueError, 'symlink, directory, or special file'):
                    verify_archive(archive_path)


if __name__ == '__main__':
    unittest.main()
