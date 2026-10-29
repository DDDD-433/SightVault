#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Use Start-SightVault.cmd on Windows." >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Run bash Setup-Mac.sh first." >&2
  exit 1
fi
# Auto selects validated MPS embedding and local MLX generative adapters on Apple Silicon.
export SIGHTVAULT_DEVICE="${SIGHTVAULT_DEVICE:-auto}"
export SIGHTVAULT_PORT="${SIGHTVAULT_PORT:-7861}"
exec .venv/bin/python scripts/launch.py
