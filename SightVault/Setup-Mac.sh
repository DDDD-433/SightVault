#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "This setup is for macOS. The Windows environment is left unchanged." >&2
  exit 1
fi
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
"$PYTHON_BIN" -c 'import platform,sys; assert sys.version_info[:2] == (3,12), "Use Python 3.12"; print("Python:",sys.version.split()[0],"architecture:",platform.machine())'
if [[ ! -e .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
elif [[ ! -x .venv/bin/python ]]; then
  echo "A non-Mac or incomplete .venv exists. Rename it manually, then run setup again." >&2
  exit 1
fi
.venv/bin/python -m pip install --upgrade pip
# PyPI wheels, not the Windows NVIDIA cu128 package index.
.venv/bin/python -m pip install -r requirements.txt
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
echo "Setup complete. Transfer .models or run: .venv/bin/python handoff/download_pinned_models.py"
echo "Next: .venv/bin/python handoff/prepare_demo.py --apply"
echo "Then: bash Start-SightVault.command"
