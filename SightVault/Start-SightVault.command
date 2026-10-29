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
# Establish CPU correctness before explicitly selecting the still-unverified MPS path.
export SIGHTVAULT_DEVICE="${SIGHTVAULT_DEVICE:-cpu}"
exec .venv/bin/python scripts/launch.py
