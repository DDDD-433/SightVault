#!/bin/sh
set -eu

OCR_BENCH_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
OCR_BENCH_PYTHON="${OCR_BENCH_PYTHON:-$OCR_BENCH_ROOT/../../ocr-v2-venv/bin/python}"

# inference is pinned/local-only; the OS sandbox denies outbound access while
# allowing loopback, and the Python runner adds an in-process DNS/socket guard.
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
  "$OCR_BENCH_PYTHON" "$OCR_BENCH_ROOT/qa/ocr-v2/bench_ocr.py" "$@"
