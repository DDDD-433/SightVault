"""Verify or explicitly download the pinned local LFM3-Omni vision files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = 'LiquidAI/LFM3-Omni-2B-GGUF'
REVISION = '3d29af2ad166f8299c2d820937e47dc0add20ffc'
RUNTIME_COMMIT = 'e0f142d5ddc1461ce721644d92af33db23545105'
FILES = {
    'LFM3-Omni-2B-Q4_K_M.gguf': {
        'bytes': 1_099_630_080,
        'sha256': '9f8d30e9d4fd7a9349d586e077988229b3ca0a0e4ad730897563d1eccc9893e2',
    },
    'mmproj-LFM3-Omni-2B-Q4_K_M.gguf': {
        'bytes': 291_586_720,
        'sha256': '0a746c0794782a16d4456b230c2a0b5888a2d94a6677211edbed7d7a4177a39a',
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify(folder: Path) -> dict:
    verified = {}
    for name, expected in FILES.items():
        path = folder / name
        if not path.is_file():
            raise FileNotFoundError(f'Missing {path}; rerun this script with --download while online.')
        actual = {'bytes': path.stat().st_size, 'sha256': sha256_file(path)}
        if actual != expected:
            raise RuntimeError(f'Pinned artifact mismatch for {path}: expected {expected}, got {actual}')
        verified[name] = actual
        print(f'Verified {name}: {actual["bytes"]} bytes, SHA-256 {actual["sha256"]}')
    source = {
        'repo': MODEL_ID,
        'revision': REVISION,
        'license': 'LFM Open License v1.0',
        'runtime': 'upstream llama.cpp',
        'runtime_commit': RUNTIME_COMMIT,
        'files': verified,
    }
    (folder / 'source.json').write_text(json.dumps(source, indent=2) + '\n', encoding='utf-8')
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true',
                        help='explicitly download the pinned model and projector while online')
    parser.add_argument('--models-dir', type=Path,
                        help='model cache root (defaults to SIGHTVAULT_MODELS or .models)')
    args = parser.parse_args()
    models_root = args.models_dir or Path(os.environ.get('SIGHTVAULT_MODELS', ROOT / '.models'))
    folder = models_root.expanduser().resolve() / 'lfm3-omni-2b-gguf'
    if args.download:
        os.environ['HF_HUB_OFFLINE'] = '0'
        os.environ['TRANSFORMERS_OFFLINE'] = '0'
        from huggingface_hub import snapshot_download
        folder.mkdir(parents=True, exist_ok=True)
        print(f'Downloading only the two pinned Omni files into {folder}...', flush=True)
        snapshot_download(
            repo_id=MODEL_ID, revision=REVISION, local_dir=folder,
            allow_patterns=list(FILES),
        )
    else:
        print('Verify-only mode. Pass --download to allow an online fetch.', flush=True)
    verify(folder)
    print(f'Pinned local LFM3-Omni model and projector are ready at {folder}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
