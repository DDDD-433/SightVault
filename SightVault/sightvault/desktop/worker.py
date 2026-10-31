"""Setup-worker mode: the only code path that may download models.

Started by SetupJobRunner as a separate process. Each action reuses the pinned
downloader, checksum verifier and MLX converter that ship with the source tree.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

from .paths import resource_root
from .setup_jobs import ACTIONS, MLX_CONVERSIONS, mlx_supported


def _load_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handoff(resources):
    handoff = Path(resources) / 'handoff'
    if str(handoff) not in sys.path:
        sys.path.insert(0, str(handoff))
    return handoff


def download(resources, model_root):
    module = _load_script(_handoff(resources) / 'download_pinned_models.py', 'sightvault_pinned_downloader')
    return module.main(['--model-root', str(model_root)])


def verify(resources, model_root):
    handoff = _handoff(resources)
    import verify_transfer
    manifest = json.loads((handoff / 'MODEL_MANIFEST.json').read_text(encoding='utf-8'))
    selected = verify_transfer.required_models(manifest)
    checked, failures = verify_transfer.verify_model_files(manifest, model_root, selected)
    report = {'scope': 'models', 'model_root': str(model_root), 'checked': checked,
              'selected_models': sorted(selected), 'failures': failures, 'passed': not failures}
    conversions = verify_conversions(resources, model_root, require=False)
    report['mlx_conversions'] = conversions
    print(json.dumps(report, indent=2), flush=True)
    return int(bool(failures) or not conversions['passed'])


def _sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def conversion_failures(manifest, folder, name):
    """Compare a converted checkpoint folder with the reviewed fingerprints for that conversion."""
    prefix = ('.models', name + '-mlx-4bit')
    failures, checked = [], 0
    for item in manifest['files']:
        parts = Path(item['path']).parts
        if parts[:2] != prefix:
            continue
        path = Path(folder).joinpath(*parts[2:]).resolve()
        if not path.is_relative_to(Path(folder).resolve()):
            raise ValueError('Converted model path escapes its folder: ' + item['path'])
        checked += 1
        if not path.is_file() or path.stat().st_size != item['size']:
            failures.append({'path': item['path'], 'error': 'missing or size differs'})
        elif _sha256(path) != item['sha256']:
            failures.append({'path': item['path'], 'error': 'SHA-256 differs'})
    if not checked:
        failures.append({'path': '/'.join(prefix), 'error': 'no reviewed fingerprints recorded'})
    return failures


def verify_conversions(resources, model_root, require=True):
    manifest = json.loads((Path(resources) / 'handoff' / 'MAC_CONVERTED_MODEL_MANIFEST.json').read_text(encoding='utf-8'))
    results = []
    for name in MLX_CONVERSIONS:
        folder = Path(model_root) / (name + '-mlx-4bit')
        if not folder.exists() and not require:
            results.append({'name': name + '-mlx-4bit', 'state': 'not prepared'})
            continue
        failures = conversion_failures(manifest, folder, name)
        results.append({'name': name + '-mlx-4bit', 'state': 'verified' if not failures else 'differs',
                        'failures': failures})
    return {'passed': all(r['state'] != 'differs' for r in results), 'results': results}


def convert_mlx(resources, model_root):
    if not mlx_supported():
        print('MLX checkpoints are only prepared on Apple Silicon Macs.', flush=True)
        return 2
    converter = _load_script(Path(resources) / 'scripts' / 'prepare_mac_models.py', 'sightvault_mlx_converter')
    manifest = converter.load_manifest()
    report = converter.verify_sources(manifest, model_root)
    if not report['passed']:
        print(json.dumps(report, indent=2), flush=True)
        print('Download and verify the pinned models before converting them.', flush=True)
        return 1
    fingerprints = json.loads((Path(resources) / 'handoff' / 'MAC_CONVERTED_MODEL_MANIFEST.json').read_text(encoding='utf-8'))
    status = 0
    for name in MLX_CONVERSIONS:
        final = Path(model_root) / (name + '-mlx-4bit')
        if final.exists():
            failures = conversion_failures(fingerprints, final, name)
            print(json.dumps({'name': final.name, 'state': 'already present',
                              'matches_reviewed_fingerprint': not failures}), flush=True)
            status |= int(bool(failures))
            continue
        # Convert into a staging folder; only a fingerprint-verified result is moved into place,
        # because the app uses any checkpoint it finds at the final path.
        staging = Path(model_root) / ('.staging-' + final.name)
        if staging.exists():
            shutil.rmtree(staging)
        converter.convert_one(manifest, model_root, name, staging)
        failures = conversion_failures(fingerprints, staging, name)
        if failures:
            print(json.dumps({'name': final.name, 'state': 'differs from reviewed fingerprint',
                              'failures': failures, 'kept_for_inspection': str(staging)}), flush=True)
            status = 1
            continue
        os.replace(staging, final)
        print(json.dumps({'name': final.name, 'state': 'converted and verified'}), flush=True)
    return status


def run(argv=None):
    parser = argparse.ArgumentParser(prog='SightVault --setup-worker', description=__doc__)
    parser.add_argument('action', choices=sorted(ACTIONS))
    parser.add_argument('--model-root', required=True, type=Path)
    args = parser.parse_args(argv)
    model_root = args.model_root.expanduser()
    if not model_root.is_absolute():
        parser.error('--model-root must be absolute')
    resources = resource_root()
    print(f'SightVault setup: {ACTIONS[args.action]} -> {model_root}', flush=True)
    if args.action == 'download':
        model_root.mkdir(parents=True, exist_ok=True)
        return download(resources, model_root)
    if args.action == 'verify':
        return verify(resources, model_root)
    return convert_mlx(resources, model_root)
