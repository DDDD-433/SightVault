"""Manifest create/verify checks for built desktop artifacts. Stdlib only: no GUI,
network, model weights, app-binary execution or installs; fixtures are small and
synthetic. Runs under unittest (pytest collects the same cases when available)."""
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify_desktop_artifact.py'
REVISION = 'baseline1b2c99e'
VERSION = '1.2.3'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run_tool(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


def write_build_info(path, platform, revision=REVISION, version=None):
    info = {'platform': platform, 'source_revision': revision}
    if version is not None:
        info['app_version'] = version
    path.write_text(json.dumps(info) + '\n', encoding='utf-8')


def create_symlink(link, target, *, target_is_directory=False):
    """Create a fixture symlink, skipping only when Windows denies that privilege."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            raise unittest.SkipTest('Windows symlink creation requires Developer Mode or privilege')
        raise


def create_windows_junction(link, target):
    """Create a native Windows junction without requiring symlink privilege."""
    command = f'mklink /J "{link}" "{target}"'
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise unittest.SkipTest(f'Windows junction creation unavailable: {detail}')


def make_mac_app(base):
    app = base / 'SightVault.app'
    resources = app / 'Contents' / 'Resources'
    (resources / 'sightvault' / 'web').mkdir(parents=True)
    macos = app / 'Contents' / 'MacOS'
    macos.mkdir(parents=True)
    write_build_info(resources / 'build-info.json', 'macos-arm64')
    plist = {'CFBundleShortVersionString': VERSION}
    (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_XML))
    (macos / 'SightVault').write_bytes(b'\x01\x02\x03 not executed \x03\x02\x01')
    (resources / 'sightvault' / 'web' / 'ui.html').write_text('<html>ui</html>', encoding='utf-8')
    # RapidOCR ships small ONNX model files inside its package; these are
    # legitimate runtime assets and must be recorded like any other file.
    (resources / 'rapidocr_onnxruntime' / 'models').mkdir(parents=True)
    (resources / 'rapidocr_onnxruntime' / 'models' / 'ch_PP-OCRv4_det_infer.onnx').write_bytes(b'\x08onnx')
    (resources / 'runtime' / 'shader.bin').mkdir(parents=True, exist_ok=True)
    (resources / 'runtime' / 'shader.bin' / 'fragment.bin').write_bytes(b'\x00\x11')
    return app


def make_windows_app(base):
    app = base / 'SightVault'
    internal = app / '_internal'
    (internal / 'sightvault').mkdir(parents=True)
    write_build_info(internal / 'build-info.json', 'windows-x64')
    (app / 'SightVault.exe').write_bytes(b'MZ not executed')
    (internal / 'sightvault' / 'setup.html').write_text('<html>setup</html>', encoding='utf-8')
    return app


class DesktopArtifactManifestTest(unittest.TestCase):
    """Each test uses its own tiny synthetic fixture; nothing outside tempdir is touched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sv-artifact-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    # Working fixture helpers ----------------------------------------------------------------

    def create_manifest(self, app, platform):
        manifest = self.base / 'out' / 'manifest.json'
        manifest.parent.mkdir(exist_ok=True)
        result = run_tool('create', str(app), '--out', str(manifest))
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(document['manifest_format'], 'SightVault-desktop-artifact/1')
        self.assertEqual(document['artifact']['platform'], platform)
        self.assertEqual(document['artifact']['source_revision'], REVISION)
        return manifest, document

    def entries_by_path(self, document):
        return {entry['path']: entry for entry in document['entries']}

    # Round trips ----------------------------------------------------------------------------

    def test_mac_roundtrip_records_regular_files_and_rapidocr_onnx(self):
        app = make_mac_app(self.base)
        manifest, document = self.create_manifest(app, 'macos-arm64')
        self.assertEqual(document['artifact']['app_version'], VERSION)
        self.assertEqual(document['artifact']['kind'], 'macos-app')
        entries = self.entries_by_path(document)
        for path in ('Contents/MacOS/SightVault', 'Contents/Resources/sightvault/web/ui.html',
                     'Contents/Resources/rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx',
                     'Contents/Resources/runtime/shader.bin/fragment.bin'):
            self.assertIn(path, entries)
            self.assertEqual(entries[path]['sha256'], sha256(app / '/'.join(path.split('/'))))
        report = self.base / 'report.json'
        result = run_tool('verify', str(app), str(manifest), '--report', str(report))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['ok'], True)

    def test_windows_onedir_roundtrip(self):
        app = make_windows_app(self.base)
        manifest, document = self.create_manifest(app, 'windows-x64')
        self.assertEqual(document['artifact']['kind'], 'windows-onedir')
        self.assertIsNone(document['artifact']['app_version'])  # never invented
        self.assertEqual(set(document['assets']), {'runtime_executables', 'rapidocr_onnx_models'})
        self.assertEqual(document['assets']['runtime_executables'], [
            {'path': 'SightVault.exe', 'size': len(b'MZ not executed'),
             'sha256': sha256(app / 'SightVault.exe')}])
        self.assertEqual(document['assets']['rapidocr_onnx_models'], [])
        entries = self.entries_by_path(document)
        self.assertIn('SightVault.exe', entries)
        self.assertIn('_internal/sightvault/setup.html', entries)
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 0, result.stdout)

    # Tampering ------------------------------------------------------------------------------

    def test_tampered_file_fails_verification(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        (app / 'Contents' / 'Resources' / 'sightvault' / 'web' / 'ui.html').write_bytes(b'x' * 15)
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('hash mismatch: Contents/Resources/sightvault/web/ui.html', result.stdout)

    def test_extra_file_fails_verification(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        (app / 'Contents' / 'Resources' / 'extra.txt').write_text('x')
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('extra file', result.stdout)

    def test_missing_file_fails_verification(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        os.remove(app / 'Contents' / 'MacOS' / 'SightVault')
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('missing file (manifest records it): Contents/MacOS/SightVault', result.stdout)

    def test_provenance_changes_fail_verification(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        write_build_info(app / 'Contents' / 'Resources' / 'build-info.json', 'macos-arm64', 'other-rev')
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('source_revision changed', result.stdout)
        plist_path = app / 'Contents' / 'Info.plist'
        version = VERSION + '.9'
        (plist_path).write_bytes(plistlib.dumps({'CFBundleShortVersionString': version}, fmt=plistlib.FMT_XML))
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('app_version changed', result.stdout)

    # Manifest structure is validated safely before any hashing ------------------------------

    def test_unsafe_manifest_entries_are_refused(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        document = json.loads(manifest.read_text(encoding='utf-8'))
        cases = [
            {'path': '../escape', 'size': 1, 'sha256': 'a' * 64},
            {'path': '/etc/passwd', 'size': 1, 'sha256': 'a' * 64},
            {'path': 'a\\..\\b', 'size': 1, 'sha256': 'a' * 64},
            {'path': 'ok/entry', 'size': -1, 'sha256': 'a' * 64},
            {'path': 'bad/hash', 'size': 1, 'sha256': 'zz'},
        ]
        for test_entry in cases:
            with self.subTest(entry=test_entry):
                broken = dict(document, entries=document['entries'] + [test_entry])
                broken_path = self.base / 'broken-manifest.json'
                broken_path.write_text(json.dumps(broken), encoding='utf-8')
                result = run_tool('verify', str(app), str(broken_path))
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.startswith('refused: invalid manifest'), result.stderr)

    def test_structure_refusal_precedes_content_verification(self):
        app = make_mac_app(self.base)
        document = {
            'manifest_format': 'SightVault-desktop-artifact/1',
            'artifact': {'kind': 'macos-app', 'platform': 'macos-arm64',
                         'source_revision': REVISION, 'app_version': VERSION},
            'entries': [{'path': 'Contents/MacOS/SightVault', 'size': 5, 'sha256': 'a' * 64},
                        {'path': '../unsafe', 'size': 1, 'sha256': 'b' * 64}],
        }
        manifest = self.base / 'structure-manifest.json'
        manifest.write_text(json.dumps(document), encoding='utf-8')
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsafe path', result.stderr)
        self.assertNotIn('missing file', result.stdout)

    def test_manifest_inside_artifact_is_refused(self):
        app = make_mac_app(self.base)
        result = run_tool('create', str(app), '--out', str(app / 'manifest.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('inside the artifact', result.stderr)

    def test_report_inside_artifact_is_refused_before_any_write(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        report_in_app = app / 'Contents' / 'Resources' / 'report.json'
        result = run_tool('verify', str(app), str(manifest), '--report', str(report_in_app))
        self.assertEqual(result.returncode, 2)
        self.assertIn('inside the artifact', result.stderr)
        self.assertFalse(report_in_app.exists(), 'report must be rejected before it is written')

    def test_drive_colon_manifest_entries_are_refused(self):
        app = make_mac_app(self.base)
        manifest, document = self.create_manifest(app, 'macos-arm64')
        for unsafe in 'C:/Users/x', 'C:\\x', 'Z:nom/data', 'ok/../stream:name':
            document2 = dict(document, entries=document['entries'] + [{'path': unsafe, 'size': 1,
                                                                       'sha256': 'a' * 64}])
            broken = self.base / 'broken-alone.json'
            broken.write_text(json.dumps(document2), encoding='utf-8')
            result = run_tool('verify', str(app), str(broken))
            self.assertEqual(result.returncode, 2, unsafe)
            self.assertTrue(result.stderr.startswith('refused: invalid manifest'))

    # Explicit asset section (runtime executables, RapidOCR ONNX models) ---------------------

    def test_assets_recorded_separately_and_reverified(self):
        app = make_mac_app(self.base)
        manifest, document = self.create_manifest(app, 'macos-arm64')
        onnx_path = 'Contents/Resources/rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx'
        assets = document['assets']
        self.assertEqual([entry['path'] for entry in assets['rapidocr_onnx_models']], [onnx_path])
        self.assertEqual(assets['rapidocr_onnx_models'][0]['sha256'],
                         sha256(app / 'Contents' / 'Resources' / 'rapidocr_onnxruntime' / 'models'
                                / 'ch_PP-OCRv4_det_infer.onnx'))
        self.assertEqual([entry['path'] for entry in assets['runtime_executables']],
                         ['Contents/MacOS/SightVault'])
        # The tool records bytes only; it makes no claim about model weights.
        self.assertNotIn('weights', json.dumps(document).lower())
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 0, result.stdout)
        onnx_file = app / 'Contents' / 'Resources' / 'rapidocr_onnxruntime' / 'models' / 'ch_PP-OCRv4_det_infer.onnx'
        onnx_file.write_bytes(b'\x08onnX')  # same size, different bytes
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'hash mismatch: {onnx_path}', result.stdout)
        self.assertIn('recorded asset hash/size mismatch (rapidocr_onnx_models)', result.stdout)

    def test_manifest_with_missing_or_unknown_asset_section_is_refused(self):
        app = make_mac_app(self.base)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        document = json.loads(manifest.read_text(encoding='utf-8'))
        cases = [dict(document), dict(document)]
        del cases[0]['assets']
        cases[1]['assets']['unrecognized'] = []
        for index, broken_document in enumerate(cases):
            with self.subTest(index=index):
                stripped = self.base / f'bad-assets-{index}.json'
                stripped.write_text(json.dumps(broken_document), encoding='utf-8')
                result = run_tool('verify', str(app), str(stripped))
                self.assertEqual(result.returncode, 2)
                self.assertIn('missing or malformed "assets"', result.stderr)
                self.assertNotIn('hash mismatch', result.stdout)

    # Symlinks -------------------------------------------------------------------------------

    def test_legitimate_internal_symlink_is_accepted(self):
        app = make_mac_app(self.base)
        (app / 'Contents' / 'Frameworks' / 'libs').mkdir(parents=True)
        (app / 'Contents' / 'Frameworks' / 'libs' / 'libcore.dylib').write_bytes(b'\xfa\xce')
        target = Path(app / 'Contents' / 'Resources')
        create_symlink(target / 'linked-libs', '../Frameworks/libs', target_is_directory=True)
        manifest, document = self.create_manifest(app, 'macos-arm64')
        entries = self.entries_by_path(document)
        link_entry = entries.get('Contents/Resources/linked-libs')
        self.assertIsNotNone(link_entry)
        self.assertEqual(link_entry['target'].replace('\\', '/'), '../Frameworks/libs')
        self.assertNotIn('sha256', link_entry)
        self.assertIn('Contents/Frameworks/libs/libcore.dylib', entries)
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 0, result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows-specific symlink target syntax')
    def test_internal_backslash_symlink_is_accepted_on_windows(self):
        app = make_mac_app(self.base)
        target = app / 'Contents' / 'Frameworks' / 'libs'
        target.mkdir(parents=True)
        (target / 'libcore.dylib').write_bytes(b'\xfa\xce')
        link = app / 'Contents' / 'Resources' / 'linked-libs'
        create_symlink(link, r'..\Frameworks\libs', target_is_directory=True)
        manifest, document = self.create_manifest(app, 'macos-arm64')
        link_entry = self.entries_by_path(document)['Contents/Resources/linked-libs']
        self.assertEqual(link_entry['target'].replace('\\', '/'), '../Frameworks/libs')
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_escaping_symlink_rejected_before_target_is_read(self):
        app = make_mac_app(self.base)
        outside = self.base / 'outside'
        outside.mkdir(exist_ok=True)
        secret = outside / 'secret.txt'
        secret.write_bytes(b'secret bytes that must never influence any manifest entry')
        link = app / 'Contents' / 'Resources' / 'escape'
        create_symlink(link, '../../../../outside/secret.txt')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('Contents/Resources/escape', result.stderr)
        self.assertIn('escapes the app root', result.stderr)
        # The tool must not have read the outside file: no manifest was produced, and the
        # refusal does not leak the outside location.
        self.assertFalse((self.base / 'm.json').exists())
        self.assertNotIn('secret bytes', result.stderr)
        self.assertEqual(secret.read_bytes().startswith(b'secret'), True)

    def test_absolute_symlink_rejected(self):
        app = make_mac_app(self.base)
        create_symlink(app / 'Contents' / 'Resources' / 'bad', '/etc/hosts')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('absolute, drive-like or UNC symlink target', result.stderr)

    def test_drive_like_symlink_target_rejected(self):
        app = make_mac_app(self.base)
        create_symlink(app / 'Contents' / 'Resources' / 'bad', 'C:\\Windows\\System32\\x.exe')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('Contents/Resources/bad', result.stderr)
        self.assertIn('absolute, drive-like or UNC symlink target', result.stderr)
        self.assertFalse((self.base / 'm.json').exists())

    def test_dangling_symlink_rejected(self):
        app = make_mac_app(self.base)
        target = app / 'Contents' / 'Resources' / 'later'
        target.write_bytes(b'data')
        create_symlink(app / 'Contents' / 'Resources' / 'link', 'later')
        target.unlink()
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('dangling symlink', result.stderr)

    def test_cyclic_symlink_rejected(self):
        app = make_mac_app(self.base)
        resources = app / 'Contents' / 'Resources'
        create_symlink(resources / 'a', 'b')
        create_symlink(resources / 'b', 'a')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('cyclic symlink target', result.stderr)

    def test_symlinked_directories_are_not_followed(self):
        app = make_mac_app(self.base)
        payload = app / 'Contents' / 'MacOS' / 'packdata'
        payload.mkdir(parents=True)
        (payload / 'payload.txt').write_text('in linked dir', encoding='utf-8')
        create_symlink(app / 'Contents' / 'Resources' / 'pack', '../MacOS/packdata',
                       target_is_directory=True)
        manifest, document = self.create_manifest(app, 'macos-arm64')
        entries = self.entries_by_path(document)
        self.assertIn('Contents/Resources/pack', entries)
        # The symlink itself is not traversed (no aliases like Resources/pack/payload.txt);
        # the physical file inside the app is covered once, under its canonical path.
        self.assertIn('Contents/MacOS/packdata/payload.txt', entries)
        self.assertNotIn('Contents/Resources/pack/payload.txt', entries)
        payload.joinpath('payload.txt').write_text('changed after manifest', encoding='utf-8')
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('hash mismatch: Contents/MacOS/packdata/payload.txt', result.stdout)

    @unittest.skipUnless(os.name == 'nt' and hasattr(os.path, 'isjunction'),
                         'Native junction detection requires Windows and Python 3.12+')
    def test_windows_junction_is_rejected_without_traversing_external_target(self):
        app = make_mac_app(self.base)
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'private.txt').write_text('must not be scanned', encoding='utf-8')
        junction = app / 'Contents' / 'Resources' / 'external-junction'
        create_windows_junction(junction, outside)
        self.addCleanup(junction.rmdir)
        self.assertTrue(os.path.isjunction(junction))

        result = run_tool('create', str(app), '--out', str(self.base / 'manifest.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('Contents/Resources/external-junction', result.stderr)
        self.assertIn('Windows junctions are not allowed', result.stderr)
        self.assertNotIn('must not be scanned', result.stderr)
        self.assertFalse((self.base / 'manifest.json').exists())

    def test_changed_or_extra_symlink_fails_verification(self):
        app = make_mac_app(self.base)
        (app / 'Contents' / 'Frameworks' / 'libs').mkdir(parents=True)
        create_symlink(app / 'Contents' / 'Resources' / 'linked', '../Frameworks/libs',
                       target_is_directory=True)
        manifest, _ = self.create_manifest(app, 'macos-arm64')
        links = [entry for entry in json.loads(manifest.read_text(encoding='utf-8'))['entries']
                 if 'target' in entry]
        self.assertEqual(len(links), 1)
        (app / 'Contents' / 'Resources' / 'linked').unlink()
        create_symlink(app / 'Contents' / 'Resources' / 'linked', '../Frameworks',
                       target_is_directory=True)
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('symlink target changed: Contents/Resources/linked', result.stdout)
        create_symlink(app / 'Contents' / 'Resources' / 'linked2', '../Frameworks/libs',
                       target_is_directory=True)
        result = run_tool('verify', str(app), str(manifest))
        self.assertEqual(result.returncode, 1)
        self.assertIn('extra symlink', result.stdout)

    # Content that must not be packaged ------------------------------------------------------

    def test_excluded_checkpoints_environments_and_are_detected_broadly(self):
        app = make_mac_app(self.base)
        hf = app / 'Contents' / 'Resources' / '_hf'
        hf.mkdir()
        (hf / 'model.safetensors').write_bytes(b'weights')
        (hf / 'pytorch_model-00001-of-00002.bin').write_bytes(b'more weights')
        models = app / 'Contents' / 'Resources' / '.models' / 'hub' / 'org--repo'
        models.mkdir(parents=True)
        (models / 'weights.bin').write_bytes(b'checkpoint')
        venv = app / 'Contents' / 'Resources' / '.venv' / 'lib'
        venv.mkdir(parents=True)
        (venv / 'os.py').write_text('py', encoding='utf-8')
        (app / 'Contents' / 'Resources' / '.env').write_text('KEY=value', encoding='utf-8')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        for needle in ('model.safetensors', 'pytorch_model-00001-of-00002.bin',
                       '.models', '.venv', '.env'):
            self.assertIn(needle, result.stderr)
        # Other .bin files are legitimate and must not be blamed for the exclusion.
        self.assertNotIn('fragment.bin', result.stderr)
        self.assertNotIn('shader.bin', result.stderr)
        self.assertFalse((self.base / 'm.json').exists())

    def test_rapidocr_onnx_is_not_false_rejected(self):
        app = make_mac_app(self.base)
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        document = json.loads((self.base / 'm.json').read_text(encoding='utf-8'))
        onnx_paths = [e['path'] for e in document['entries'] if e['path'].endswith('.onnx')]
        self.assertEqual(len(onnx_paths), 1, onnx_paths)

    # Provenance discovery -------------------------------------------------------------------

    def test_missing_build_info_is_refused(self):
        app = self.base / 'SightVault.app'
        (app / 'Contents').mkdir(parents=True)
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('build-info.json not found', result.stderr)

    def test_symlinked_build_info_is_not_trusted(self):
        app = make_mac_app(self.base)
        real_info = self.base / 'real-build-info.json'
        real_info.write_text('{"platform": "macos-arm64", "source_revision": "evil"}', encoding='utf-8')
        info_path = app / 'Contents' / 'Resources' / 'build-info.json'
        info_path.unlink()
        create_symlink(info_path, '../../../../real-build-info.json')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_symlinked_info_plist_is_not_trusted(self):
        app = make_mac_app(self.base)
        outside = self.base / 'spoof'
        outside.mkdir(exist_ok=True)
        fake_plist = outside / 'fake-info.plist'
        fake_plist.write_bytes(plistlib.dumps({'CFBundleShortVersionString': '9.9.9'},
                                              fmt=plistlib.FMT_XML))
        plist_path = app / 'Contents' / 'Info.plist'
        plist_path.unlink()
        create_symlink(plist_path, '../../../../spoof/fake-info.plist')
        result = run_tool('create', str(app), '--out', str(self.base / 'm.json'))
        self.assertEqual(result.returncode, 2)
        # Refused before the external plist is ever opened (lstat sees the symlink first).
        self.assertTrue('Info.plist is not a regular file' in result.stderr
                        or 'Info.plist resolves outside the artifact root' in result.stderr, result.stderr)
        # The external plist content must never have been read into provenance or output.
        self.assertNotIn('9.9.9', result.stderr)
        self.assertNotIn('9.9.9', result.stdout)
        self.assertFalse((self.base / 'm.json').exists())


if __name__ == '__main__':
    unittest.main()
