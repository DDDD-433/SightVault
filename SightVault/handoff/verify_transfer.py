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
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('Manifest path escapes its root: ' + relative)
    return path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', action='store_true', help='Verify weights instead of immutable source snapshot')
    parser.add_argument('--model-root', type=Path, help='Path to an alternative .models directory')
    args = parser.parse_args()
    manifest = ROOT / 'handoff' / ('MODEL_MANIFEST.json' if args.models else 'PACKAGE_MANIFEST.json')
    report = json.loads(manifest.read_text(encoding='utf8'))
    failures = []
    for item in report['files']:
        if args.models:
            relative = Path(item['path']).relative_to('.models')
            path = safe_path(args.model_root or ROOT / '.models', relative)
        else:
            path = safe_path(ROOT, item['path'])
        if not path.is_file():
            failures.append({'path': item['path'], 'error': 'missing'})
        elif path.stat().st_size != item['size']:
            failures.append({'path': item['path'], 'error': 'size differs'})
        elif sha256(path) != item['sha256']:
            failures.append({'path': item['path'], 'error': 'SHA-256 differs'})
    print(json.dumps({'scope': 'models' if args.models else 'immutable extraction',
                      'checked': len(report['files']), 'failures': failures,
                      'passed': not failures}, indent=2))
    return int(bool(failures))

if __name__ == '__main__':
    sys.exit(main())
