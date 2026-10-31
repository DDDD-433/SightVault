"""Check the immutable extraction, or installed model files, using stdlib only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent

def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def safe_path(base, relative):
    base = Path(base).resolve()
    relative = Path(relative)
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Manifest path escapes its root: ' + str(relative))
    return path

def required_models(manifest, only=None, with_models=None):
    sources = manifest.get('sources', {})
    if only and with_models:
        raise ValueError('Use --only or --with-model, not both')
    if only:
        unknown = sorted(set(only) - set(sources))
        if unknown:
            raise ValueError('Unknown model name(s): ' + ', '.join(unknown))
        return set(only)
    selected = set()
    for name, source in sources.items():
        optional = bool(source.get('optional', False)) or source.get('required') is False
        if not optional:
            selected.add(name)
    additions = set(with_models or [])
    unknown = sorted(additions - set(sources))
    if unknown:
        raise ValueError('Unknown model name(s): ' + ', '.join(unknown))
    selected.update(additions)
    return selected

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', action='store_true', help='Verify weights instead of immutable source snapshot')
    parser.add_argument('--model-root', type=Path, help='Path to an alternative .models directory')
    parser.add_argument('--only', action='append', help='Verify one named model group (repeat to select several)')
    parser.add_argument('--with-model', action='append', help='Verify one additional named model group (repeat to select several)')
    args = parser.parse_args()
    if (args.only or args.with_model) and not args.models:
        parser.error('--only and --with-model require --models')
    manifest = ROOT / 'handoff' / ('MODEL_MANIFEST.json' if args.models else 'PACKAGE_MANIFEST.json')
    report = json.loads(manifest.read_text(encoding='utf8'))
    failures = []
    try:
        selected = required_models(report, args.only, args.with_model) if args.models else None
    except ValueError as exc:
        parser.error(str(exc))
    checked = 0
    for item in report['files']:
        if args.models:
            model_name = Path(item['path']).parts[1] if len(Path(item['path']).parts) > 2 else None
            if model_name not in selected:
                continue
            relative = Path(item['path']).relative_to('.models')
            path = safe_path(args.model_root or ROOT / '.models', relative)
        else:
            path = safe_path(ROOT, item['path'])
        checked += 1
        if not path.is_file():
            failures.append({'path': item['path'], 'error': 'missing'})
        elif path.stat().st_size != item['size']:
            failures.append({'path': item['path'], 'error': 'size differs'})
        elif sha256(path) != item['sha256']:
            failures.append({'path': item['path'], 'error': 'SHA-256 differs'})
    print(json.dumps({'scope': 'models' if args.models else 'immutable extraction',
                      'checked': checked, 'selected_models': sorted(selected) if args.models else None,
                      'failures': failures,
                      'passed': not failures}, indent=2))
    return int(bool(failures))

if __name__ == '__main__':
    sys.exit(main())
