"""Download the Windows-tested model revisions, then verify their exact file hashes."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=['embeddinggemma-2', 'minicpm-ocr', 'minicpm-v'])
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '0'
    os.environ['TRANSFORMERS_OFFLINE'] = '0'
    from huggingface_hub import snapshot_download
    from verify_transfer import sha256, safe_path
    manifest = json.loads((ROOT / 'handoff' / 'MODEL_MANIFEST.json').read_text(encoding='utf8'))
    for name, source in manifest['sources'].items():
        if args.only and name != args.only:
            continue
        items = [m for m in manifest['files'] if m['path'].startswith('.models/' + name + '/')]
        prefix = '.models/' + name + '/'
        patterns = [m['path'][len(prefix):] for m in items if not m['path'].endswith('/source.json')]
        target = ROOT / '.models' / name
        print(f"Downloading {source['repo']} at {source['revision']}", flush=True)
        snapshot_download(repo_id=source['repo'], revision=source['revision'],
                          local_dir=target, allow_patterns=patterns)
        # Preserve the exact local provenance bytes used by the checksum manifest.
        (target / 'source.json').write_bytes(manifest['source_json_bytes'][name].encode('utf8'))
        for m in items:
            path = safe_path(ROOT, m['path'])
            if path.stat().st_size != m['size'] or sha256(path) != m['sha256']:
                raise ValueError('Downloaded file differs from Windows baseline: ' + m['path'])
        print(name + ': verified', flush=True)
    print('Requested model files are verified. App inference uses local files only.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
