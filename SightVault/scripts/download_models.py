"""Backward-compatible setup entry point; all downloads use the pinned manifest."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE_MODELS = ('embeddinggemma-2', 'minicpm-ocr', 'minicpm-v')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=BASE_MODELS)
    parser.add_argument('--vision', choices=('lfm3',), help='Download only the optional, pinned Mac LFM3 snapshot.')
    args = parser.parse_args()
    if args.only and args.vision:
        parser.error('Use --vision lfm3 separately from --only.')
    command = [sys.executable, str(ROOT / 'handoff' / 'download_pinned_models.py')]
    group = 'lfm3-vl-2b-mlx-4bit' if args.vision else args.only
    if group:
        command.extend(['--only', group])
    return subprocess.call(command)

if __name__ == '__main__':
    raise SystemExit(main())
