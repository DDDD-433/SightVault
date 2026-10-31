# OCR_V2 method and reproduction

This directory freezes the OCR/document experiment inputs and harness for SightVault Phase 2. It does not change app indexing, retrieval, the canonical database, or the 62 packaged demo assets. The code baseline is `766027c79d4c6f17ba7b88b1df3bc079dfbbd141`; the exact experiment code commit is recorded by each run after this method checkpoint is committed.

## Arms and runtimes

| Arm | Pinned source | Measured path |
| --- | --- | --- |
| `minicpm` | `openbmb/MiniCPM-OCR-1.0` at `c5833cddb363aa62f1cb60ddde48c832e8f1a92c` | Existing read-only model converted with pinned MLX-VLM `918763c6875c80e82f9f48344dfad4763346fea8`; affine 4-bit group 64 with mixed BF16 layers; MLX-VLM/MLX on Apple Metal. |
| `rapid` | `rapidocr-onnxruntime==1.4.4`; source commit `86ae3f5079df3422c1829cd84baf19bc8a7a9453` | Current in-product ONNX Runtime CPU control. This remains the sole RapidOCR arm; a package migration is outside this comparison. |
| `ovis` | `ATH-MaaS/OvisOCR3` at `4833d5d2afa6e769ee933f024504d67ace80a24c` | Official Transformers/PyTorch MPS route, official BF16 checkpoint, no quantization, remote code disabled, MPS operator fallback disabled. |

Small source, prompt, config, and license files are preserved with their source URLs/revisions and hashes under `provenance/`. The Ovis model snapshot is outside this Git worktree; its download manifest pins all downloaded file hashes, including the 1,706,030,528-byte `model.safetensors` SHA-256. MiniCPM's existing source model and conversion are read-only. The experiment environment is isolated and pinned in `requirements-ocr-v2.lock.txt`.

## Frozen corpus and labels

The 56 fixtures are isolated synthetic images authored in `generate_fixtures.py`; they are not model output and contain no user screenshots or private documents. The script contains literal text and structured ground truth before rendering. `ground_truth.jsonl` stores those authored labels, field values, and manually placed line/word boxes. `manifest.json` records category counts, image hashes/sizes, Python and Pillow versions, generator hash, and available font names/hashes. The image and label files were frozen after generation; do not regenerate them for a benchmark run.

| Category | Images |
| --- | ---: |
| Terminals | 10 |
| Receipts | 8 |
| Invoices | 6 |
| Forms | 6 |
| Tables | 6 |
| Slides | 6 |
| Charts | 4 |
| Multi-column documents | 4 |
| Rotated documents | 2 |
| Transparency | 2 |
| Blur | 1 |
| JPEG compression | 1 |

The exact stress text includes `ERESOLVE`, `ModuleNotFoundError`, `CUDA_ERROR_OUT_OF_MEMORY`, `E404` versus `E4040`, visually confusable `0/O/1/I/l`, URLs, dates, times, prices, and full version strings. Ground truth preserves those forms exactly. CER/WER use edit distance, so insertions, omissions, and substitutions all count. Identifier/numeric metrics use exact token boundaries and report TP/FP/FN plus support; an empty category is `null`, not a perfect score. Structured metrics report support counts and micro aggregates. Repeat calls never duplicate quality weight: each unique fixture contributes once.

Every backend receives the same stored image files, composited to RGB on white after EXIF orientation handling. The plain OCR prompt is shared by all three arms. Capability probes use each candidate's documented task prompt or native API and are reported separately; they are not described as semantically identical prompts. Generated HTML/Markdown/JSON is retained as inert text, never rendered, executed, or forwarded to another model. JSON is parsed and validated against expected fields/cells/values.

Word-box frames are pinned in `provenance/bbox-coordinate-contracts.json`. Ovis emits integer xyxy coordinates in `[0,1000)` relative to its supplied image. RapidOCR 1.4.4 returns polygons in pixels through `return_word_box`; the saved English fixtures show per-character entries, not word-level entries. Its retained release code maps boxes through preprocessing and reverse crop rotation to the original input raster. Ground-truth boxes use pixels in the saved final raster; fixture rotations are baked into that raster and labels are transformed accordingly. IoU is not calculated for an unknown/unpinned frame. Box scoring reports invalid bounds/geometry and text-only matching separately from text-plus-IoU matches. The report's separate Rapid character-to-word result is post-hoc and unions native character geometry only after exact line-text alignment.

For structured probes, output serialization/schema validity is reported separately from cell, numeric-value, or chart-value correctness. KIE requires exactly the requested fields and string values. JSON table/chart outputs must follow the expected schema; Ovis document arrays are checked against the official element schema and normalized bbox bounds. Table markup from supported model formats is parsed inertly, with rectangular structure checked. MiniCPM's direct table and chart tasks returned OTSL rather than JSON/HTML; the original raw-run scorer did not understand it, so a separate post-hoc scorer handles the observed rectangular `<fcel>` / `<nl>` form and preserves both the original metrics and corrected results. A parsed JSON value alone does not count as valid structured output.

## Commands

The plain comparison is one continuous 100-call stream per backend. The fixed ordering traverses all 56 unique images once, then starts again at image one for 44 declared repeats. The same run records measured 10-, 50-, and 100-call cumulative throughput milestones. Quality is computed only over the 56 unique images.

Before a model slot, run an import-only preflight for each adapter. It verifies every fixture path and hash under the same sandbox/offline wrapper, imports only the selected runtime, and checks that expected model files exist; it does not instantiate a model, load weights, or infer.

```sh
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task plain --preflight-only
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task plain --preflight-only
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task plain --preflight-only
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task plain --throughput-count 100
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task plain --throughput-count 100
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task plain --throughput-count 100
```

Run each backend only in its serialized heavy-run slot. Do not run these three commands concurrently. The default output goes under the ignored `results/raw_outputs/` directory; use `--output` to select a different path if needed. A full comparison result must record the exact committed experiment SHA, script/manifest/ground-truth/prompt/metric hashes, model revision/checksums, command, timestamp, runtime versions, offline evidence, and raw per-image predictions.

Separate capability commands are:

```sh
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task spotting
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task kie
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task kie
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task table
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task table
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task chart
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task chart
```

`--case-ids` selects a fixed, comma-separated subset for bounded capability probes; it is rejected for the frozen plain corpus. The predeclared case sets, token budgets, support classifications, full-page structured parsing route, and complete sequential command list are in [CAPABILITY_PROBE_PLAN.md](CAPABILITY_PROBE_PLAN.md). Ovis has a separate official full-page structured task; it is not the same measurement as the table probe. MiniCPM's complete-document SDK route includes PP-DocLayoutV3, which is distinct from the direct-image MLX-VLM adapter evaluated here.

Ovis word spotting is prompted with its pinned official task file; RapidOCR uses its native geometry API for the fixed spotting subset. MiniCPM's tested MLX route does not expose word boxes. RapidOCR 1.4.4 does not expose KIE, table structure, or chart understanding; those are recorded as unsupported without inference. Unsupported capabilities are not mocked. Per-case skips occur only where the fixture has no independent label for that task. Completed outcomes and raw-result paths are in [OCR_V2_REPORT.md](OCR_V2_REPORT.md); saved-output post-hoc scoring can be recreated with `python3 SightVault/qa/ocr-v2/posthoc_capability_review.py`.

## Timing, memory, and offline evidence

The wrapper sets Hugging Face/Transformers offline flags, telemetry off, `USE_HUB_KERNELS=NO`, and `PYTORCH_ENABLE_MPS_FALLBACK=0`. macOS `sandbox-exec` denies outbound network connections while allowing loopback, and an in-process socket/DNS guard records attempted non-loopback access. This is explicitly recorded as sandbox outbound denial, not as a firewall. Model setup/download is a separate explicit step; benchmark commands only use local model files and never download weights.

Results separate model load, first request, warm per-image median/p95, process RSS, and the available accelerator allocation counter. Throughput milestones report three measured views: continuous-stream wall rate excluding quality scoring (including raw-result writes and runner gaps), the sum of each call's image-read/decode/normalize/inference duration excluding scoring and output writes, and inference-only calls/second. Ground-truth scoring runs after the model is released, with its own wall/CPU time. Peak RSS is sampled every 50 ms. Ovis MPS driver allocation and MiniCPM MLX active allocation are distinct runtime counters; RapidOCR has no accelerator allocation measurement. Any failed call stops that backend run without a selective retry, and partial raw output is retained.

Measurements here run on macOS Apple Silicon. Windows has not been measured on hardware. The documented deployment paths are not validation claims: Ovis uses the official Transformers/PyTorch route (CPU or CUDA candidate on Windows), MiniCPM's tested MLX conversion is Apple-only, and RapidOCR's ONNX CPU route is the Windows candidate.
