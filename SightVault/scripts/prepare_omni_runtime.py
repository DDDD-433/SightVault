"""Verify a locally built llama.cpp binary at SightVault's pinned source commit."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPO = 'ggml-org/llama.cpp'
COMMIT = 'e0f142d5ddc1461ce721644d92af33db23545105'
COMMIT_SHORT = 'e0f142d5d'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    suffix = '.exe' if os.name == 'nt' else ''
    default_binary = ROOT / '.runtime' / 'llama.cpp' / 'build' / 'bin' / f'llama-server{suffix}'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / '.runtime' / 'llama.cpp' / 'source',
                        help='local llama.cpp checkout')
    parser.add_argument('--binary', type=Path, default=default_binary,
                        help='locally built llama-server binary')
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    binary = args.binary.expanduser().resolve()
    if not (source / '.git').exists():
        raise SystemExit(f'Not a llama.cpp Git checkout: {source}')
    commit = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True, check=True).stdout.strip()
    remote = subprocess.run(['git', '-C', str(source), 'remote', 'get-url', 'origin'],
                            capture_output=True, text=True, check=True).stdout.strip().rstrip('/')
    if commit != COMMIT:
        raise SystemExit(f'llama.cpp source commit mismatch: expected {COMMIT}, got {commit}')
    if remote not in ('https://github.com/ggml-org/llama.cpp', 'https://github.com/ggml-org/llama.cpp.git',
                      'git@github.com:ggml-org/llama.cpp.git', 'ssh://git@github.com/ggml-org/llama.cpp.git'):
        raise SystemExit(f'llama.cpp source origin is not the pinned upstream repository: {remote}')
    if not binary.is_file():
        raise SystemExit(f'Build llama-server from the pinned checkout first; binary not found: {binary}')
    version_result = subprocess.run([str(binary), '--version'], capture_output=True, text=True,
                                    timeout=20, check=True)
    version = (version_result.stdout + version_result.stderr).strip()
    if f'commit {COMMIT_SHORT}' not in version:
        raise SystemExit(f'llama-server version does not report the pinned commit {COMMIT_SHORT}: {version}')
    manifest = {
        'repo': REPO,
        'commit': COMMIT,
        'source_origin': remote,
        'version': version,
        'binary_sha256': sha256_file(binary),
    }
    manifest_path = binary.parent / 'sightvault-llama-runtime.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'binary': str(binary), 'manifest': str(manifest_path), **manifest}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
