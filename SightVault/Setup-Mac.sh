#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This setup is for macOS. The Windows environment is left unchanged." >&2
  exit 1
fi
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    uv python install 3.12
    PYTHON_BIN="$(uv python find 3.12)"
  else
    echo "Install native Python 3.12 or uv, then run setup again." >&2
    exit 1
  fi
fi
if [[ "$(uname -m)" == arm64 ]]; then
  "$PYTHON_BIN" -c 'import platform; assert platform.machine() == "arm64", "Use native ARM Python, not Rosetta"'
fi
"$PYTHON_BIN" -c 'import platform,sys; assert sys.version_info[:2] == (3,12), "Use Python 3.12"; print("Python:",sys.version.split()[0],"architecture:",platform.machine())'
if [[ ! -e .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
elif [[ ! -x .venv/bin/python ]]; then
  echo "A non-Mac or incomplete .venv exists. Rename it manually, then run setup again." >&2
  exit 1
fi
.venv/bin/python -m pip install --upgrade pip
# PyPI wheels, not the Windows NVIDIA cu128 package index.
.venv/bin/python -m pip install -r requirements-mac.lock.txt
.venv/bin/python - <<'PY'
import platform, sqlite3, torch
from importlib.metadata import version
for name in ('torch','torchvision','transformers','sentence-transformers','onnxruntime','rapidocr-onnxruntime'):
    print(f'{name}=={version(name)}')
db=sqlite3.connect(':memory:')
db.execute('CREATE VIRTUAL TABLE fts_check USING fts5(text)')
print('SQLite FTS5: OK')
print('Architecture:',platform.machine())
print('MPS built:',torch.backends.mps.is_built(),'MPS available:',torch.backends.mps.is_available())
print('MPS availability is not proof that the three models run on MPS.')
PY
echo "Setup complete. Download pinned weights with: .venv/bin/python handoff/download_pinned_models.py"
echo "For Apple Silicon quantization: .venv/bin/python scripts/prepare_mac_models.py"
if [[ -f data/sightvault.sqlite3 ]]; then
  echo "For a prepared demo: stop the app, dry-run handoff/prepare_demo.py, then use --apply to relocate it."
else
  echo "This checkout starts with an empty index. Launch the app and choose an image folder."
  echo "For the dated 62-image demo, use the source/demo ZIP from the GitHub Mac preview release."
fi
echo "Then: bash Start-SightVault.command"
