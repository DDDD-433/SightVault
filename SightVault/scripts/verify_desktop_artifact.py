#!/usr/bin/env python3
"""Create and verify a contents manifest for a built SightVault desktop artifact.

The manifest records what a packaged desktop artifact actually contains and where
its provenance comes from, so an artifact can be checked once and re-checked later,
before distribution or after a transfer.

What it verifies: the exact bytes (relative path, size, SHA-256) of every regular
file, the exact target of every *internal* symlink, and the provenance recorded in
the bundled build-info.json and macOS Info.plist. What it does NOT verify: code
signing, malware safety, or that the bundle can load its models and run inference.

Usage:
  python scripts/verify_desktop_artifact.py create APP --out MANIFEST.json
  python scripts/verify_desktop_artifact.py verify APP MANIFEST.json [--report FILE]

Provenance is taken exclusively from build-info.json inside the artifact (macOS:
Contents/Resources; Windows: _internal) and, for the app version, the macOS
Contents/Info.plist; nothing is invented. Exit codes: 0 verified, 1 verification
failed, 2 refusal or invalid manifest (a condition that must stop the run before
verification results can be trusted).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import stat
import sys

MANIFEST_FORMAT = 'SightVault-desktop-artifact/1'
BUILD_INFO_NAME = 'build-info.json'
MANIFEST_MAX_BYTES = 64 * 1024 * 1024
SHA256_LENGTH = 64
CHUNK = 1 << 20
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2

# build-info.json is staged at the onedir base by SightVault.spec ('.' relative to
# the COLLECT root), which lands under Contents/Resources in a macOS .app bundle
# and under _internal in a Windows onedir folder.
BUILD_INFO_LOCATIONS = {
    'macos-arm64': ('Contents/Resources/build-info.json',),
    'windows-x64': ('_internal/build-info.json',),
}
ALL_BUILD_INFO_LOCATIONS = [loc for locations in BUILD_INFO_LOCATIONS.values() for loc in locations]
INFO_PLIST_LOCATION = 'Contents/Info.plist'
KIND_BY_PLATFORM = {'macos-arm64': 'macos-app', 'windows-x64': 'windows-onedir'}

# Content that must never be shipped inside a desktop artifact: externally
# downloaded Hugging Face checkpoints and local environments or credentials.
# Deliberately narrow: other .bin files (runtime or shader binaries) are
# legitimate and are recorded like any other file, and the bundled RapidOCR
# .onnx model files are legitimate runtime assets that are recorded normally.
EXCLUDED_DIR_NAMES = ('.venv', '.models')
EXCLUDED_FILE_PATTERNS = ('*.safetensors', 'pytorch_model*.bin',
                          '.env', '.env.*', '.netrc', '.git-credentials')


class Refused(ValueError):
    """A condition that stops the run before verification results can be trusted."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value):
    return (isinstance(value, str) and len(value) == SHA256_LENGTH
            and all(character in '0123456789abcdef' for character in value))


def contained(base, path):
    """True if an already normalized absolute path is the base or inside it."""
    # Windows paths are case-insensitive. realpath() may report a different
    # capitalization than the spelling used for the artifact argument.
    base = os.path.normcase(base)
    path = os.path.normcase(path)
    try:
        return os.path.commonpath((base, path)) == base
    except ValueError:  # Different Windows drives, or otherwise incompatible roots.
        return False


def safe_relative_path(path):
    """Return the '/'-separated components of a safe manifest path, else None."""
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        return None
    if path.startswith('/'):
        return None
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return None
    if any(':' in part for part in parts):
        # Drive-like names ('C:…'), NTFS stream names and similar colon forms.
        return None
    return parts


def as_posix_path(path):
    return '/'.join(part for part in path.replace('\\', '/').split('/') if part)


def is_excluded_file_name(name):
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_FILE_PATTERNS)


# Provenance ----------------------------------------------------------------------------------

def confined_regular(root_real, location, description):
    """Return the path of a trusted regular provenance file inside the artifact.

    Uses only lstat/realpath metadata: a symlinked provenance file (especially one
    resolving outside the artifact) is refused before its content is ever opened.
    """
    path = os.path.join(root_real, *location.split('/'))
    try:
        status = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(status.st_mode):
        raise Refused(f'{description} is not a regular file: {location}')
    if not contained(root_real, os.path.realpath(path)):
        raise Refused(f'{description} resolves outside the artifact root: {location}')
    return path


def read_limited_bytes(path, limit, description):
    with open(path, 'rb') as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise Refused(f'{description} is not a regular file')
        if status.st_size > limit:
            raise Refused(f'{description} is unexpectedly large')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Refused(f'{description} is unexpectedly large')
    return data


def read_limited_text(path, limit, description):
    try:
        return read_limited_bytes(path, limit, description).decode('utf-8')
    except UnicodeDecodeError:
        raise Refused(f'{description} is not valid UTF-8 text')


def build_info_candidate(root_real):
    """Locate the one build-info.json inside the artifact, never through a symlink."""
    matches = []
    for location in ALL_BUILD_INFO_LOCATIONS:
        candidate = confined_regular(root_real, location, BUILD_INFO_NAME)
        if candidate is not None:
            matches.append((location, candidate))
    if not matches:
        raise Refused(f'{BUILD_INFO_NAME} not found inside the artifact (checked: '
                      f"{', '.join(ALL_BUILD_INFO_LOCATIONS)}); not a SightVault desktop build with "
                      'recorded provenance')
    if len(matches) > 1:
        raise Refused('more than one build-info.json found; cannot select provenance safely')
    return matches[0]


def mac_app_version(root_real):
    """App version from Info.plist; refused if the plist is missing-regular or escapes."""
    path = confined_regular(root_real, INFO_PLIST_LOCATION, 'Info.plist')
    if path is None:
        return None
    try:
        plist = plistlib.loads(read_limited_bytes(path, 1 << 20, 'Info.plist'))
    except plistlib.InvalidFileException:
        raise Refused('Info.plist is not valid binary/XML property list')
    version = plist.get('CFBundleShortVersionString')
    return version if isinstance(version, str) and version else None


class Provenance:
    __slots__ = ('kind', 'platform', 'source_revision', 'app_version',
                 'build_info_location', 'build_info_sha256')

    def __init__(self, kind, platform, source_revision, app_version,
                 build_info_location, build_info_sha256):
        self.kind = kind
        self.platform = platform
        self.source_revision = source_revision
        self.app_version = app_version
        self.build_info_location = build_info_location
        self.build_info_sha256 = build_info_sha256

    def as_dict(self):
        return {'kind': self.kind, 'platform': self.platform, 'source_revision': self.source_revision,
                'app_version': self.app_version, 'build_info_sha256': self.build_info_sha256}


def discover_provenance(app):
    root_real = os.path.realpath(os.path.abspath(app))
    if not os.path.isdir(root_real):
        raise Refused(f'artifact folder does not exist: {app}')
    location, path = build_info_candidate(root_real)
    try:
        build_info = json.loads(read_limited_text(path, 1 << 20, f'{BUILD_INFO_NAME} ({location})'))
    except json.JSONDecodeError:
        raise Refused(f'build-info.json is not valid JSON: {location}')
    if not isinstance(build_info, dict):
        raise Refused(f'build-info.json is not a JSON object: {location}')
    platform = build_info.get('platform')
    if platform not in BUILD_INFO_LOCATIONS:
        raise Refused('build-info.json has no known platform '
                      f'(expected macos-arm64 or windows-x64): {location}')
    source_revision = build_info.get('source_revision')
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise Refused(f'build-info.json has no source_revision: {location}')
    app_version = build_info.get('app_version')
    if app_version is not None and (not isinstance(app_version, str) or not app_version.strip()):
        raise Refused(f'build-info.json has an invalid app_version: {location}')
    if app_version is None and platform == 'macos-arm64':
        app_version = mac_app_version(root_real)
    return Provenance(KIND_BY_PLATFORM[platform], platform, source_revision, app_version,
                      location, sha256_file(path))


# Artifact scan -------------------------------------------------------------------------------

def classify_link(root_real, link_abs, target, *, is_junction=False):
    """Return None when an internal symlink is acceptable, else the rejection reason.

    Absolute, escaping, cyclic and dangling links are rejected, as are drive-like
    (C:\\…) and UNC targets. A Windows relative symlink may use backslashes as its
    path separators; those are normalized for the containment check. Junctions are
    explicitly rejected because their target syntax is Windows-specific and they
    must never be traversed by the scanner. Only metadata (stat/readlink-style
    lookups) is used for classification; target content is never opened.
    """
    if is_junction:
        return 'Windows junctions are not allowed in desktop artifacts'
    if os.path.isabs(target) or target.startswith('//'):
        return 'absolute, drive-like or UNC symlink target'
    if re.match(r'^[A-Za-z]:', target):
        return 'absolute, drive-like or UNC symlink target'
    if '\\' in target:
        if os.name != 'nt':
            return 'absolute, drive-like or UNC symlink target'
        target = target.replace('\\', os.sep)
    lexical = os.path.normpath(os.path.join(os.path.dirname(link_abs), target))
    if not contained(root_real, lexical):
        return 'symlink target escapes the app root'
    try:
        os.stat(link_abs)  # intermediate symlinks are resolved by the OS; ELOOP => cycle
    except OSError as exc:
        # Windows reports a reparse-point cycle as ERROR_CANT_RESOLVE_FILENAME
        # (1921) on some Python/Windows combinations instead of errno.ELOOP.
        if exc.errno == errno.ELOOP or getattr(exc, 'winerror', None) == 1921:
            return 'cyclic symlink target'
        # WinError 2/3/267 correspond to a missing file, missing path, or a
        # non-directory path component respectively. Keep the POSIX classification.
        if exc.errno in (errno.ENOENT, errno.ENOTDIR) \
                or getattr(exc, 'winerror', None) in (2, 3, 267):
            return 'dangling symlink (target does not exist)'
        return 'symlink target cannot be resolved'
    try:
        resolved = os.path.realpath(link_abs)
    except OSError:
        return 'symlink target cannot be resolved'
    if not contained(root_real, resolved):
        return 'symlink target resolves outside the app root'
    return None


ISJUNCTION = getattr(os.path, 'isjunction', None)


def scan_artifact(root_real):
    """Walk files and symlinks below root; never descend into symlinked directories.

    Windows junctions, when the interpreter can identify them (os.path.isjunction,
    Python 3.12+), are recorded and rejected before traversal instead of being
    followed, so a junction cannot be used to smuggle traversal outside the root.
    """
    files, links, errors = [], [], []
    stack = [root_real]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                listing = list(entries)
        except OSError as exc:
            errors.append(f'folder cannot be read: {as_posix_path(os.path.relpath(directory, root_real))} '
                          f'({exc.strerror})')
            continue
        for entry in listing:
            relative = as_posix_path(os.path.relpath(entry.path, root_real))
            is_junction = bool(ISJUNCTION and ISJUNCTION(entry.path))
            if entry.is_symlink() or is_junction:
                try:
                    target = os.readlink(entry.path)
                except OSError as exc:
                    errors.append(f'link cannot be read: {relative} ({exc.strerror})')
                    continue
                links.append({'path': relative, 'target': target,
                              'reason': classify_link(root_real, entry.path, target,
                                                      is_junction=is_junction)})
            elif entry.is_dir():
                stack.append(entry.path)
            elif entry.is_file():
                try:
                    digest = sha256_file(entry.path)
                except OSError as exc:
                    errors.append(f'file cannot be read: {relative} ({exc.strerror})')
                    continue
                files.append({'path': relative, 'size': entry.stat(follow_symlinks=False).st_size,
                              'sha256': digest})
            else:
                errors.append(f'irregular file type, not recorded: {relative}')
    return files, links, errors


def find_excluded(files, links):
    """Names in the artifact that must never be packaged (external checkpoints etc.)."""
    found = []
    paths = [entry['path'] for entry in files] + [link['path'] for link in links]
    for path in sort_by_path(paths):
        parts = safe_relative_path(path) or [path]
        hits = [part for part in parts if part in EXCLUDED_DIR_NAMES]
        if not hits and is_excluded_file_name(parts[-1]):
            hits = [parts[-1]]
        for hit in hits:
            found.append((path, f'{hit} must not be packaged'))
    return found


def asset_sections(platform, files):
    """Assets the brief requires to be reported explicitly, each with path and hash.

    Categories only; the tool never makes claims about model weights (present or
    absent) — weights, if a user has run setup, live in the per-user model folder.
    """
    rapidocr, runtime = [], []
    for entry in files:
        path = entry['path']
        lowered = path.lower()
        if 'rapidocr' in lowered and lowered.endswith('.onnx'):
            rapidocr.append(dict(entry))
            continue
        if platform == 'macos-arm64':
            if path.startswith('Contents/MacOS/') and path.count('/') == 2:
                runtime.append(dict(entry))
        elif path.count('/') == 0 and lowered.endswith('.exe'):
            runtime.append(dict(entry))
    return {'rapidocr_onnx_models': rapidocr, 'runtime_executables': runtime}


def sort_by_path(entries):
    if entries and isinstance(entries[0], dict):
        return sorted(entries, key=lambda entry: entry['path'])
    return sorted(entries)


# Commands ------------------------------------------------------------------------------------

def refuse_manifest_inside_artifact(output, root_real):
    output_real = os.path.realpath(os.path.join(os.getcwd(), output))
    if contained(root_real, output_real):
        raise Refused('refusing to write the manifest inside the artifact '
                      '(writing there invalidates any code signature): ' + output)


def command_create(app, output):
    provenance = discover_provenance(app)
    root_real = os.path.realpath(os.path.abspath(app))
    refuse_manifest_inside_artifact(output, root_real)
    files, links, errors = scan_artifact(root_real)
    refusals = list(errors) + [f'{link["path"]}: {link["reason"]}' for link in links if link['reason']] \
        + [f'{path}: {reason}' for path, reason in find_excluded(files, links)]
    if refusals:
        raise Refused('\n'.join(refusals))
    assets = asset_sections(provenance.platform, files)
    document = {
        'manifest_format': MANIFEST_FORMAT,
        'created_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'artifact': provenance.as_dict(),
        'assets': assets,
        'entries': sort_by_path([dict(entry) for entry in files]
                                + [{'path': link['path'], 'target': link['target']} for link in links]),
    }
    with open(output, 'w', encoding='utf-8') as stream:
        json.dump(document, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({'ok': True, 'manifest': output,
                      'artifact': provenance.as_dict(),
                      'files': len(files), 'symlinks': len(links),
                      'rapidocr_onnx_models': len(assets['rapidocr_onnx_models']),
                      'runtime_executables': len(assets['runtime_executables'])}, indent=2))
    return 0


def load_manifest(path):
    """Validate manifest structure completely before any artifact byte is hashed."""
    text = read_limited_text(path, MANIFEST_MAX_BYTES, 'manifest')
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f'invalid manifest: not valid JSON ({exc})')
    if not isinstance(document, dict):
        raise Refused('invalid manifest: top level is not an object')
    if document.get('manifest_format') != MANIFEST_FORMAT:
        raise Refused(f'invalid manifest: unsupported manifest_format: {document.get("manifest_format")!r}')
    provenance = document.get('artifact')
    if not isinstance(provenance, dict):
        raise Refused('invalid manifest: missing artifact provenance object')
    for field in ('kind', 'platform', 'source_revision'):
        value = provenance.get(field)
        if not isinstance(value, str) or not value:
            raise Refused(f'invalid manifest: artifact.{field} must be a non-empty string')
    app_version = provenance.get('app_version')
    if app_version is not None and not isinstance(app_version, str):
        raise Refused('invalid manifest: artifact.app_version must be a string or null')
    build_info_sha256 = provenance.get('build_info_sha256')
    if build_info_sha256 is not None and not is_sha256(build_info_sha256):
        raise Refused('invalid manifest: artifact.build_info_sha256 must be a SHA-256 hex digest or null')
    entries = document.get('entries')
    if not isinstance(entries, list):
        raise Refused('invalid manifest: entries must be a list')
    seen, regular, symlinks = set(), [], []
    for entry in entries:
        if not isinstance(entry, dict):
            raise Refused('invalid manifest: entry is not an object')
        parts = safe_relative_path(entry.get('path'))
        if parts is None:
            raise Refused(f'invalid manifest: unsafe path: {entry.get("path")!r}')
        path = '/'.join(parts)
        if path in seen:
            raise Refused(f'invalid manifest: duplicate path: {path}')
        seen.add(path)
        keys = set(entry)
        if keys == {'path', 'size', 'sha256'}:
            size = entry['size']
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise Refused(f'invalid manifest: size must be a non-negative integer: {path}')
            if not is_sha256(entry['sha256']):
                raise Refused(f'invalid manifest: sha256 must be a 64-character hex digest: {path}')
            regular.append({'path': path, 'size': size, 'sha256': entry['sha256']})
        elif keys == {'path', 'target'}:
            if not isinstance(entry['target'], str) or not entry['target']:
                raise Refused(f'invalid manifest: target must be a non-empty string: {path}')
            symlinks.append({'path': path, 'target': entry['target']})
        else:
            raise Refused('invalid manifest: entry must be a file (path, size, sha256) or '
                          f'a symlink (path, target): {path}')
    assets = manifest_asset_section(document)
    return provenance, regular, symlinks, assets


def manifest_asset_section(document):
    """Validate the explicit asset section (runtime executables, RapidOCR ONNX models)."""
    assets = document.get('assets')
    if not isinstance(assets, dict) or set(assets) != {'runtime_executables', 'rapidocr_onnx_models'}:
        raise Refused('invalid manifest: missing or malformed "assets" section'
                      ' (must list runtime_executables and rapidocr_onnx_models)')
    validated = {}
    for category, entries in assets.items():
        if not isinstance(entries, list):
            raise Refused(f'invalid manifest: assets.{category} must be a list')
        seen = set()
        clean = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
                raise Refused(f'invalid manifest: assets.{category} entry must have path, size, sha256')
            parts = safe_relative_path(entry.get('path'))
            if parts is None:
                raise Refused(f'invalid manifest: unsafe asset path: {entry.get("path")!r}')
            if not isinstance(entry['size'], int) or isinstance(entry['size'], bool) or entry['size'] < 0 \
                    or not is_sha256(entry['sha256']):
                raise Refused(f'invalid manifest: malformed asset record: {"/".join(parts)}')
            key = '/'.join(parts)
            if key in seen:
                raise Refused(f'invalid manifest: duplicate asset path: {key}')
            seen.add(key)
            clean.append({'path': key, 'size': entry['size'], 'sha256': entry['sha256']})
        validated[category] = clean
    return validated


def compare_assets(assets_from_manifest, recomputed, reasons):
    for category, recorded in assets_from_manifest.items():
        actual = {entry['path']: entry for entry in recomputed.get(category, [])}
        manifest_map = {entry['path']: entry for entry in recorded}
        for path in sorted(set(actual) | set(manifest_map)):
            if path not in actual:
                reasons.append(f'recorded asset no longer detected ({category}): {path}')
            elif path not in manifest_map:
                reasons.append(f'recorded asset not reported by manifest ({category}): {path}')
            elif actual[path]['sha256'] != manifest_map[path]['sha256'] \
                    or actual[path]['size'] != manifest_map[path]['size']:
                reasons.append(f'recorded asset hash/size mismatch ({category}): {path}')


def compare_artifact(regular, symlinks, files, links, errors):
    reasons = list(errors)
    reasons += [f'symlink rejected in artifact: {link["path"]} — {link["reason"]}'
                for link in links if link['reason']]
    excluded = {path for path, _ in find_excluded(files, links)}
    for path, reason in find_excluded(files, links):
        reasons.append(f'excluded content in artifact: {path} — {reason}')
    manifest_files = {entry['path']: entry for entry in regular}
    manifest_links = {entry['path']: entry['target'] for entry in symlinks}
    for entry in files:
        recorded = manifest_files.pop(entry['path'], None)
        if recorded is None:
            if entry['path'] not in excluded:
                reasons.append(f'extra file (manifest does not record it): {entry["path"]}')
        elif entry['sha256'] != recorded['sha256']:
            reasons.append(f'hash mismatch: {entry["path"]}')
        elif entry['size'] != recorded['size']:
            # Defensive: equal bytes always have equal size, so this only fires for an
            # internally inconsistent manifest entry.
            reasons.append(f'size mismatch: {entry["path"]}')
    for link in links:
        if link['reason']:
            continue
        recorded = manifest_links.get(link['path'])
        if recorded is None:
            reasons.append(f'extra symlink (manifest does not record it): {link["path"]}')
        elif link['target'] != recorded:
            reasons.append(f'symlink target changed: {link["path"]}')
    for path in sorted(manifest_files):
        reasons.append(f'missing file (manifest records it): {path}')
    for path in sorted(set(manifest_links) - {link['path'] for link in links}):
        reasons.append(f'missing symlink (manifest records it): {path}')
    return reasons


def command_verify(app, manifest_path, report_path):
    provenance_from_manifest, regular, symlinks, assets_from_manifest = load_manifest(manifest_path)
    provenance = discover_provenance(app)
    root_real = os.path.realpath(os.path.abspath(app))
    refuse_manifest_inside_artifact(manifest_path, root_real)
    if report_path:
        refuse_manifest_inside_artifact(report_path, root_real)
    mismatch = []
    for field in ('platform', 'source_revision', 'app_version', 'build_info_sha256'):
        recorded = provenance_from_manifest.get(field)
        actual = provenance.as_dict().get(field)
        if recorded != actual:
            mismatch.append(f'{field} changed: manifest {recorded!r}, artifact {actual!r}')
    files, links, errors = scan_artifact(root_real)
    recomputed_assets = asset_sections(provenance.platform, files)
    reasons = mismatch + compare_artifact(regular, symlinks, files, links, errors)
    compare_assets(assets_from_manifest, recomputed_assets, reasons)
    report = {
        'ok': not reasons,
        'artifact': provenance.as_dict(),
        'files': len(files), 'symlinks': len(links),
        'rapidocr_onnx_models': len(recomputed_assets['rapidocr_onnx_models']),
        'reasons': reasons,
    }
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return EXIT_OK if not reasons else EXIT_FAILED


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create', help='record the contents of a built artifact')
    create.add_argument('app', help='the .app bundle or Windows onedir folder')
    create.add_argument('--out', required=True, help='manifest file to write (outside the artifact)')
    verify = sub.add_parser('verify', help='check an artifact against a manifest')
    verify.add_argument('app', help='the .app bundle or Windows onedir folder')
    verify.add_argument('manifest', help='manifest produced by "create"')
    verify.add_argument('--report', help='optional path for the JSON verification report')
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            return command_create(args.app, args.out)
        return command_verify(args.app, args.manifest, args.report)
    except Refused as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return EXIT_REFUSED


if __name__ == '__main__':
    sys.exit(main())
