# SightVault Mac migration — October 29, 2026

Working app: <http://127.0.0.1:7861/>. Launch with `bash Start-SightVault.command` or double-click that file. This copy uses 7861 because an independent SightVault copy was already listening on 7860; it was left untouched. `SIGHTVAULT_PORT` overrides the port. Launcher identity checking prevents a different checkout from being mistaken for this app. The service binds loopback, and its child is detached from the launcher terminal on macOS.

Machine: Apple M1 Pro, 8 CPU cores (6 performance / 2 efficiency), 16 GiB unified memory; macOS 26.5.1 (25F80); native ARM64 Python 3.12.9. Initial free disk was 211 GiB. No Metal memory safety limits were disabled. Measurements below are per-process/runtime observations, not an isolated whole-machine memory benchmark; another app copy was present.

| Model | Source revision | Actual Mac runtime / precision |
| --- | --- | --- |
| google/embeddinggemma-2 | f9972ac22e696a105c482202646979e33e4b5a1e | PyTorch MPS, FP32; real CPU baseline also passed |
| openbmb/MiniCPM-OCR-1.0 | c5833cddb363aa62f1cb60ddde48c832e8f1a92c | MLX Metal, locally converted mixed affine 4-bit, group size 64 / BF16 |
| openbmb/MiniCPM-V-4.7-1B | bb99eab4113676f30830ffa73bac1f0b2a5f1dea | MLX Metal, locally converted mixed affine 4-bit, group size 64 / BF16 |

All original weights were downloaded on this Mac and all 32 files passed the handoff SHA-256 verifier. No Windows weights or environment were transferred. MLX-VLM 0.7.11 includes complete native processors, vision towers, mergers and generation for `minicpm_ocr` and `minicpmv4_7`; real image inference verified both. A text-only mlx-serve conversion was rejected. Published MLX conversions used other revisions, so this migration converted the exact verified baseline weights locally. The converter enforces baseline verification first. Output hashes and sources are in `qa/MAC_MODEL_MANIFEST.json` and each local checkpoint's `source.json`.

The quantization is deliberately mixed: OCR averages 13.274 bits/weight and its checkpoint is 2,157,942,509 bytes; VLM averages 13.719 bits/weight and is 2,665,920,710 bytes. Vision, embeddings, mergers, normalization and sensitive recurrent components retain the runtime's higher-precision policy. “4-bit” applies to selected layers, not every tensor. The strict loader, full-pixel OCR and repeated image generation passed on this M1 Pro. These smoke tests do not establish comprehensive OCR accuracy parity.

EmbeddingGemma remains FP32. Its [official model card](https://huggingface.co/google/embeddinggemma-2) explicitly prohibits FP16 due to activation-range failures and recommends BF16 or FP32. No validated 8-bit configuration covering direct image embeddings was established for this runtime. FP32 fits and preserves quality: all 17 retrieval cases passed, all four negative-query similarities remained below every positive semantic top result, and fresh direct-pixel embeddings matched stored vectors at cosine 0.9999, 1.0000 and 1.0000. There is no caption-based indexing, reduced vector dimension, model substitution or use of benchmark labels in retrieval.

Installed key versions: torch 2.11.0, torchvision 0.26.0, transformers 5.22.0, sentence-transformers 6.2.0, mlx 0.32.4, mlx-vlm 0.7.11, onnxruntime 1.31.1, rapidocr-onnxruntime 1.4.4, fastapi 0.162.0, uvicorn 0.54.0. `requirements-mac.lock.txt` pins the complete environment. `Setup-Mac.sh` was run successfully and checks native architecture, SQLite FTS5 and MPS availability. Original Windows requirements remain intact.

| Check | Mac result / evidence |
| --- | --- |
| Immutable incoming package | 245 files verified before any edits; original manifest retained under handoff/history |
| Original weights | 32 files verified; `qa/MAC_MODEL_VERIFICATION.json` |
| Unit and regression tests | 44 passed; `qa/mac-unit-tests.log` |
| Real retrieval | 17/17; `qa/MAC_QA_RESULTS.json` |
| Image/text embedding | CPU and MPS finite normalized 768d vectors; `qa/mac-cpu-embedding.json`, `qa/mac-mps-embedding.json` |
| Preferred OCR | Real screenshot transcription and `ModuleNotFoundError` ingestion succeeded; `qa/mac-ocr-smoke.json`, `qa/mac_ingestion_qa.json` |
| OCR/VLM switching | OCR → vision → OCR → vision passed; only one generative slot active; `qa/MAC_SWITCHING.json` |
| Offline inference | Search, OCR and VLM passed with outbound socket.connect guard and loopback allowed; `qa/MAC_OFFLINE_VERIFICATION.json` |
| Ingestion | Duplicate reuse, skip, edits, rename warning, corrupt inputs, cancel/resume and forced-process interruption recovery passed; `qa/MAC_INGESTION_VALIDATION.json` |
| Browser harness | 30/30; `qa/MAC_BROWSER_QA.json`; screenshots under output/playwright/mac-browser |
| Normal CSP browser | Semantic search, source dialog and real analysis passed; zero console errors; `qa/MAC_CSP_BROWSER_QA.json`, output/playwright/csp-analysis.png |
| Escaping | 2/2 against disposable index, never canonical demo; `qa/MAC_XSS_QA.json` |
| Demo integrity | 62 records/vectors; all original/evidence/thumbnail paths accessible; every nonpath DB field identical to original backup; `qa/MAC_DEMO_INTEGRITY.json` |

The initial ingestion harness failed because `/tmp` is a symlink to `/private/tmp` on macOS: indexing canonicalized paths but the test compared an unnormalized path. The harness now resolves its work directory first. The fresh rerun indexed eight valid files, rejected two intentionally corrupt files and two unsupported files, skipped eight on rerun, detected a rename, cancelled after three files, resumed the remaining nine, and recovered seven files after killing a child after its third completed file. No partial image files remained. All 32 disposable-index records have 768d vectors. Its final preferred OCR completed in 7.4 seconds. The canonical demo was never used as the destructive QA directory; its browser reindex skipped all 62 records.

| Measurement | Observed result |
| --- | --- |
| CPU cold direct image + query, including lazy imports/load | 43.39 s; process RSS 2.63 GiB |
| MPS cold direct image + query | 23.72 s; subsequent query 31.5 ms |
| MPS allocated / driver at embedding sample | 1.635 / 3.237 GiB |
| Retrieval benchmark warm semantic median | 32.35 ms; observed range 31.7–580.4 ms |
| First OCR standalone sample | 12.79 s; MLX peak 3.232 GiB; RSS 2.575 GiB |
| Offline run OCR / VLM | 7.18 / 5.21 s |
| Combined offline run | MPS current 1.635 GiB; MLX peak 3.242 GiB; end RSS 1.51 GiB |
| Browser analysis | 5.39 s API latency, about 6.0 s observed by browser |
| First disposable ingestion batch | 15.5 s for ten inputs: eight valid, two corrupt |

These are different runs and scopes. RSS, MPS driver/allocator memory and MLX peak are not additive measures of whole-machine unified-memory consumption. Windows timings are historical and are not reused as Mac results. Warm model requests avoid imports and model loading; cold results vary with filesystem cache and other processes.

CSP-enabled browser verification is recorded separately from the harness, which explicitly uses CSP bypass for its evaluation helpers. Offline evidence is an in-process socket guard, not an OS firewall or Wi-Fi disconnection test. The application loads local model directories only; no inference service or cloud model API was added.

OCR remains imperfect: the CUDA sample's `loss.backward()` was transcribed as `lossbackward()`. The 1B vision model provides short hypotheses, not established causes. Sixty demo images/dates are synthetic; two photos retain their source records. First occurrence means the earliest matching indexed file, and similarity is not confidence. UI date filters use browser-local day boundaries; date-only API filters retain UTC semantics. CPU generative loaders and Windows CUDA behavior are retained, but full CPU OCR/VLM performance and fresh Windows hardware execution were not requalified in this Mac run.

Coordination discovery: the exact requested `luna-max-standard` selector was unavailable. AGY ran a bounded read-only setup review using its listed GPT-OSS 120B (Medium); inaccurate findings were rejected by source review. Cursor's agent model list required authentication, and OpenCode failed during startup on its global SQLite schema. Neither account configuration nor global agent DB was changed. See `qa/AGENT_COORDINATION.md`. One orchestrator owned edits, environment, database and model execution.

Reproduce with the commands in README. The refreshed source/demo ZIP excludes environments, model weights, private images, caches and secrets; it contains a SQLite backup of only the verified public demo and a new archive manifest. A fresh extraction passed archive-manifest verification and both relocation dry-run and backed-up apply; `qa/MAC_PACKAGE_VERIFICATION.json`. The original Windows manifest and reports are retained as history. Download models and run local conversion after extracting the refreshed ZIP.

Additional independent QA is complete: Claude Opus 5.5 high with exactly one Sonnet 5.5 xhigh subagent, verified from actual runtime metadata. Codex integrated and validated fixes; the suite now has **123 passed and 1 expected storage-retention failure**. See `qa/independent-review/FINAL_QA_REPORT.md` for dispositions, real-model recovery, CPU/MPS agreement, transparent-image inference, browser races and final export validation. Original reports retain baseline reproduction evidence.
