# Phase 2 model selection

Integration baseline: `766027c79d4c6f17ba7b88b1df3bc079dfbbd141`; branch `integration/unified`. Experiments ran serially on an Apple M1 Pro, 16 GiB unified memory, native ARM64 Python 3.12.9, macOS 26.5.1. No allocator safety limits were disabled. Results describe this Mac; Windows execution is unvalidated.

## Shipping choices

| Role | Selected model/runtime | Precision and decision |
| --- | --- | --- |
| Direct image/text retrieval | google/embeddinggemma-2; SentenceTransformers/PyTorch MPS | FP32, normalized 768 dimensions. Vendor prohibits FP16; no validated 8-bit direct-image path was established. Fits this machine and preserves retrieval. CPU fallback remains labeled. |
| Accurate OCR | openbmb/MiniCPM-OCR-1.0; native MLX-VLM on Mac | Locally converted mixed affine 4-bit, group 64, sensitive/vision tensors BF16. Preferred OCR; explicit failure and fallback labels remain. |
| Fast OCR/fallback | RapidOCR 1.4.4; ONNX Runtime CPU | Existing local detector/recognizer; actual word geometry differs from character geometry. |
| Default image analysis | openbmb/MiniCPM-V-4.7-1B; native MLX-VLM on Mac | Mixed affine 4-bit/BF16, group 64. Fastest measured default; model identity unchanged. |
| Optional image specialist | LiquidAI/LFM3-VL-2B-MLX-4bit | Vendor QAD mixed 4-bit, group 32, 8-bit token embeddings/BF16 tensors. Default thinking off. Explicit `SIGHTVAULT_LFM3_THINKING_TOKENS=128` adds a separately bounded 384-token final answer. Apple Silicon only. |
| Optional image analysis | LiquidAI/LFM3-Omni-2B-GGUF; pinned upstream llama.cpp | Q4_K_M model and matching Q4_K_M multimodal projector. Opt in with `SIGHTVAULT_VISION=omni`. Local child process, lazy loading, explicit setup, no automatic downloads or provider substitution. Windows remains unvalidated. |

One application, UI and SQLite schema remain. OCR and image generation share a serialized generative slot; embedding search retains its independent lock. Omni is stopped before OCR; native generative weights are released before Omni. Optional-backend errors leave core search available. Voice/video/audio are experiment evidence, not shipping UI/API features.

Exact revisions, every original/optional downloadable file SHA-256 and bytes are in `handoff/MODEL_MANIFEST.json`. Local MiniCPM conversion fingerprints are in `handoff/MAC_CONVERTED_MODEL_MANIFEST.json`. MLX-VLM is pinned to upstream `918763c6875c80e82f9f48344dfad4763346fea8`; llama.cpp to `e0f142d5ddc1461ce721644d92af33db23545105`. No experimental runtime fork is required.

## Visual experiment

The frozen 32-case arena preserves the original 16 image/question/OCR inputs and adds GUI, diagrams, terminals, visual-only color pairs, ambiguity and injection cases. All four normal backends completed 32/32 requests with a shared 384-token final cap, temperature zero and a 160-word instruction. Omni requires its documented text-output system instruction. Scores below are nonblind AI rubric reviews, not human accuracy estimates; full final answers and rubric judgments remain inspectable in `visual-v2/`.

| Backend | Warm median / p95 seconds | Peak sampled RSS, decimal GB | Factual / grounding / hallucination / instruction rubric means (0–5; higher better) |
| --- | --- | --- | --- |
| MiniCPM-V | 1.670 / 2.780 | 2.089 | 4.281 / 4.438 / 4.188 / 4.344 |
| LFM3 normal | 2.839 / 4.415 | 2.453 | 4.469 / 4.375 / 3.938 / 4.469 |
| Omni | 3.430 / 4.822 | 2.877 | 4.562 / 4.625 / 4.406 / 4.594 |
| Bonsai 4B Q2_0 | 8.716 / 11.628 | 3.487 | 4.594 / 4.562 / 3.812 / 4.250 |

Omni and Bonsai each passed a subsequent 50-call serial stability run, with repeated answer hashes matching the arena. Bonsai's sampled RSS reached 6.990 GB and later fell; that is not proof of a leak. Bonsai is much slower and does not clearly improve grounding/instruction reliability. Its 9B gate failed, so no 9B download/run was performed and neither Bonsai weight set is required by shipping setup.

LFM3 bounded thinking produced usable finals on 32/32 cases; 28 reached the forced thought boundary and four terminated naturally. Mean latency rose from 3.070 to 4.617 seconds. It corrected several diagram/biology/grounding errors, but is an explicit optional latency tradeoff. Hidden reasoning was neither read nor saved. No real answer reached the 384 final-token boundary; the boundary needs the focused synthetic stream regression as separate evidence. Two answers exceeded the 160-word instruction and their review scores were corrected.

Omni stretch probes completed image+speech, sampled video, one audiovisual question and three PCM TTS outputs. ASR remained mixed (proper-name and segmentation errors); TTS round-trip recognition is an automated proxy, not a listening assessment. These small experiments do not justify production voice features. See `visual-v2/MODALITY_RESULTS.md` for exact counts, input bytes and limitations.

## OCR experiment

56 unique literal-ground-truth PIL fixtures cover receipts, codes, invoices, forms, tables, charts, columns, rotation and degradation. Plain extraction was measured separately from native structured formats. Higher-layer parsers did not fabricate word boxes or treat supported OTSL/nested chart output as invalid JSON.

| Backend | Unique-image CER / WER | Continuous throughput | Memory observations |
| --- | --- | --- | --- |
| RapidOCR | 9.49% / 24.94% | 100 calls / 40.889 s = 2.446 images/s | RSS 0.893 GB |
| MiniCPM-OCR | 4.43% / 6.89% | 100 calls / 593.397 s = 0.1685 images/s | RSS 3.075 GB; MLX peak 3.673 GB |
| OvisOCR3 BF16 MPS | 10.77% / 14.99% | 50 calls / 632.920 s = 0.0790 images/s | MPS driver peak 8.291 GB; separate structured probes 9.454 GB |

Ovis was stopped at 81/100 calls after the machine's free-memory gauge fell; all 56 unique cases were present. It had no inference failure/OOM. There is no Ovis 100-call result, and this observation does not prove a leak. Ovis plain-output finish metadata was unavailable; do not assert every answer was untruncated.

MiniCPM exact typed numbers/codes performed strongly; its supported KIE/table/chart probes achieved 24/24 fields, 48/48 cells and 15/15 chart pairs. Ovis also produced excellent small structured outputs, but costs more memory/time, adds a model/runtime and showed word-granularity failures. Keep MiniCPM plus RapidOCR. Four structured-document probes do not establish broad document-parser superiority; the full MiniCPM SDK's document-layout path was not measured. See `ocr-v2/OCR_V2_REPORT.md` and immutable raw/posthoc sidecars.

## d1 experiment

The 100-call classification/routing run completed without model failures; 68 labeled IDs correspond to 66 unique pixel contents. Baseline content classification was 58/62; chart/table/person supplemental counts are tiny and correlated. Threshold precision is not monotonic, and labels with no positive cases cannot establish recall.

Actual EmbeddingGemma top-20 candidates were scored query by query: 320 unique d1 forwards over 16 frozen queries, zero model errors. Baseline and d1 both achieved Recall@5 0.9766, Hit@5 1.0, top-1 1.0 and MRR@5/@20 1.0. All lists changed order without improving these ceiling-limited metrics. Serial cost averaged 50.25 seconds per query (median 49.63), versus about 98 ms for embedding search. Do not ship d1.

All 320 scores were recovered from their complete atomic checkpoint after a post-inference `NameError` in final metadata assembly. The raw checkpoint SHA is `0e6e765b9f461de55f28d12d68f789f49cea3d5c48797599374d3e84ae8bc29f`. Query 16 loop wall, total process time and load timing remain unknown. Recovery does not invent them. Official ONNX assets were investigated statically; no ONNX named-question runner or Windows runtime was validated. See `d1-v2/D1_V2_REPORT.md`.

## Memory and setup footprint

| Artifact | Local bytes, decimal GB | Shipping setup |
| --- | --- | --- |
| EmbeddingGemma snapshot | 1.526 | Required, original revision |
| MiniCPM-OCR original / local MLX | 2.621 / 2.178 | Original download; explicit Mac conversion |
| MiniCPM-V original / local MLX | 3.129 / 2.686 | Original download; explicit Mac conversion |
| Optional LFM3-VL snapshot | 1.609 | Explicit named download, Mac only |
| Optional Omni model + projector | 1.391 | Explicit named download plus native runtime build |

These are file totals, not simultaneously resident memory. RSS, MLX allocator and MPS driver counters measure different things and must not be added together. Load tests used fresh processes with filesystem caches retained, not reboot-cold measurements. Full package sizes and fresh unified execution results belong in `FINAL_UNIFIED_QA.md`.

Runtime isolation used macOS sandbox-exec outbound-deny with loopback allowed, plus Python socket guards in applicable harnesses and Hugging Face offline flags. Native llama.cpp socket attempts were not counted; OS enforcement is the evidence, not a claim of zero native attempts. Windows source/setup artifacts and static resolution do not establish native Windows compatibility.
