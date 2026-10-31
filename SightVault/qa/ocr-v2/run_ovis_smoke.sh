#!/bin/sh
set -eu

PHASE2_OCR_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
PHASE2_OCR_PYTHON="${PHASE2_OCR_PYTHON:-$PHASE2_OCR_ROOT/../../ocr-v2-venv/bin/python}"

"$PHASE2_OCR_PYTHON" "$PHASE2_OCR_ROOT/qa/ocr-v2/download_ovis_model.py"

# The inference phase is strictly local: Hub/kernel download paths are disabled,
# MPS fallback is disabled, and smoke_ovis.py records blocked non-loopback access.
/usr/bin/sandbox-exec \
  -p '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))' \
  env \
  HF_HUB_OFFLINE=1 \
  TRANSFORMERS_OFFLINE=1 \
  HF_HUB_DISABLE_TELEMETRY=1 \
  USE_HUB_KERNELS=NO \
  TOKENIZERS_PARALLELISM=false \
  PYTORCH_ENABLE_MPS_FALLBACK=0 \
  PHASE2_OCR_SANDBOX_PROFILE='macOS sandbox-exec outbound deny with loopback allowed' \
  "$PHASE2_OCR_PYTHON" "$PHASE2_OCR_ROOT/qa/ocr-v2/smoke_ovis.py" \
    --device mps \
    --image "$PHASE2_OCR_ROOT/qa/ocr-v2/images/T05.png" \
    --output "$PHASE2_OCR_ROOT/qa/ocr-v2/results/raw_outputs/ovisocr3-smoke.json"
