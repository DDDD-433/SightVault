# Visual stability and LFM3 thinking follow-up

## Scope and provenance

This note records two serial 50-call image repeatability runs, an LFM3-VL thinking-mode parser smoke, and the subsequent full 32-case thinking comparison. These are supplementary to the four-candidate image arena and do not change its base-mode measurements or default recommendation.

All runs used an Apple M1 Pro with 16 GiB unified memory. “Fresh process/model-unloaded load” means the previous server or model process had exited and a new process loaded the model. OS filesystem and shader caches were not purged. The measurements are not cold-storage or first-ever Metal compilation timings. Each run used the local pinned model files and the macOS `sandbox-exec` profile `(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))`, which denies outbound network access while allowing localhost. Native outbound attempts were not instrumented; the OS sandbox enforced the denial.

- Baseline: `766027c79d4c6f17ba7b88b1df3bc079dfbbd141`
- Code revision used by these runs: `7a102a87b040439c5ed71902d41e3405d751d8de`
- Frozen 32-case manifest SHA-256: `a32398bd1d2c2ef981628708672b6476a202a0a7f17963c638d96d1a9ea79523`
- Shared llama.cpp source: upstream commit `e0f142d5ddc1461ce721644d92af33db23545105`, build 12005 / `0.8.0-dev`
- llama-server binary SHA-256: `2d973f9be4949e8df3f66ac3a45808ef01e5d8325c5f236a6151cd7924302a13`

The serial stability runs use the same image-analysis settings as the base arena: context 4096, one parallel slot, image minimum 1024 tokens, temperature 0, seed 1, repeat penalty 1.0, reasoning disabled, and maximum 384 output tokens. Each begins with the exact 32 manifest cases and then repeats 18 of those inputs, for 50 calls total. The per-call files preserve image SHA, question, OCR-context hash, prompt hash, output text and hash, token counts, finish reason, latency, error and sampled process-tree RSS. The HTTP response bodies are preserved separately.

## Serial 50-call stability

| Candidate | Run (UTC) | Startup | Calls | HTTP 200 / failures / OOM responses | Stops / truncations | Median latency; warm median / p95 | Repeated inputs with stable answer hashes |
| --- | --- | ---: | ---: | --- | --- | --- | ---: |
| LFM3-Omni 2B | `2026-10-30 20:36:43–20:39:31` | 0.793 s | 50 | 50 / 0 / 0 | 50 / 0 | 3.374 s; 3.378 / 4.620 s | 18/18 |
| Ternary Bonsai 2 4B | `2026-10-30 20:39:52–20:45:53` | 0.784 s | 50 | 50 / 0 / 0 | 50 / 0 | 8.027 s; 7.996 / 11.364 s | 18/18 |

Warm statistics use calls 2–50; p95 uses nearest-rank percentile. Both processes exited with status 0. For both candidates, the first 32 outputs had answer hashes matching all 32 corresponding base-arena outputs, and all 18 repeated inputs returned identical answer hashes across their two occurrences.

### Sampled process-tree RSS curve

Values below are decimal GB. Each five-call band summarizes the per-call `rss_tree_after_bytes` samples; the run-level peak is the separate continuously sampled maximum for the llama-server process tree. These are sampled process RSS values, not accelerator-allocation counters.

| Candidate | Calls 1–10 | 11–20 | 21–30 | 31–40 | 41–50 | Run-level sampled peak | First / last call RSS | System free memory before → after |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |
| LFM3-Omni | 1.851–2.103 (median 1.993) | 2.128–2.364 (2.258) | 2.390–2.809 (2.501) | 2.839–2.893 (2.893) | 2.719–2.897 (2.807) | 2.898 GB | 1.851 / 2.722 GB | 80% → 68% |
| Bonsai 4B | 2.230–3.654 (median 2.964) | 3.805–5.190 (4.510) | 5.342–6.828 (6.027) | 2.623–3.607 (3.093) | 2.740–3.282 (2.832) | 6.990 GB | 2.230 / 3.282 GB | 83% → 39% |

Omni’s call-sampled RSS rose through the first 40 calls, stayed near 2.89 GB across calls 31–45, then fell to approximately 2.72 GB. Bonsai’s call-sampled RSS rose through call 30, reached a separate run-level sample of 6.990 GB, then dropped sharply for call 31 and stayed lower during repeats. The 50-call Bonsai run therefore shows a materially higher sampled peak and a large whole-machine free-memory decline than its 32-case arena run; retain both run-specific measurements. This short run does not establish a memory leak, nor does the subsequent RSS decline explain the cause of the peak.

**Omni model/runtime:** `LiquidAI/LFM3-Omni-2B-GGUF` revision `3d29af2ad166f8299c2d820937e47dc0add20ffc`, Q4_K_M model plus Q4_K_M projector. Model SHA-256 `9f8d30e9d4fd7a9349d586e077988229b3ca0a0e4ad730897563d1eccc9893e2`; projector SHA-256 `0a746c0794782a16d4456b230c2a0b5888a2d94a6677211edbed7d7a4177a39a`.

**Bonsai model/runtime:** `prism-ml/Ternary-Bonsai-2-4B-gguf` revision `8d6557168168f91ea6697a4a49ed2ef622b53adb`, Q2_0 model plus Q8_0 projector. Model SHA-256 `64a5183b44921bdb5ac4a6f5f92007821d6ebe5c66997d3c147fd214ab9433d4`; projector SHA-256 `db939d5f9b7014137810205e91d05e5f54afd72b0b3af9d33bd7d82f7906a55d`.

Full raw per-call and response records:

- Omni: [`stability/runs/omni-stability-20261030T203642194424Z-29a698/`](stability/runs/omni-stability-20261030T203642194424Z-29a698/)
- Bonsai: [`stability/runs/bonsai-stability-20261030T203951341544Z-fad33e/`](stability/runs/bonsai-stability-20261030T203951341544Z-fad33e/)

## LFM3-VL thinking-mode smoke

The bounded smoke verified the parser and the 128-token thought-block cap across a controlled red cup, blue cup, and terminal image. It was not a quality benchmark. The thinking text and token IDs were not read, saved, or scored; only token accounting and final answers were retained.

| Case | Thought-block tokens | Terminator | Final-answer tokens (cap 384) | Total observed output tokens | Finish / complete |
| --- | ---: | --- | ---: | ---: | --- |
| `v2-31-cup_red_01` | 128 | forced | 83 | 213 | natural stop / complete |
| `v2-32-cup_blue_01` | 109 | natural | 123 | 234 | natural stop / complete |
| `v1-01-terminal_cuda_01` | 128 | forced | 194 | 324 | natural stop / complete |

All 3/3 final answers were complete, no response reached the independent 384-token final-answer cap, mean latency was 4.205 s, and no cap truncation occurred. The thought-block cap was exercised; the final-answer boundary was not. On this sample, process RSS after model load was 2,312,028,160 bytes, MLX active memory after load was 1,591,035,912 bytes, and MLX peak memory at completion was 2,645,537,236 bytes. These MLX allocator and process RSS measurements are different metrics.

The smoke used `LiquidAI/LFM3-VL-2B-MLX-4bit` revision `4146d7fa689833cfa7a06c474e748350b458f41e`, MLX-VLM 0.7.11 at commit `918763c6875c80e82f9f48344dfad4763346fea8`, and the vendor mixed-precision QAD checkpoint. Run record: [`thinking/runs/lfm3-thinking-smoke-20261030T204625144041Z-cf06ec/`](thinking/runs/lfm3-thinking-smoke-20261030T204625144041Z-cf06ec/).

## Full 32-case LFM3-VL thinking comparison

The later granted full run used the same 32 images, questions, OCR context, and answer prompt as normal LFM3-VL. It enabled the model's thinking template, capped the thinking block at 128 tokens, and imposed a separate 384-token final-answer cap. It completed all 32 cases with no incomplete answers or truncation. No answer reached the final-token cap; that boundary remains unexercised.

| Measure | Result |
| --- | ---: |
| Usable final answers | 32/32 |
| Mean thought-block tokens | 123.25 / 128 cap |
| Forced terminator at cap | 28/32 |
| Natural thought terminator | 4/32 |
| Mean final-answer tokens | 148.38 / 384 cap |
| Mean latency | 4.617 s |
| Normal-mode mean latency for the same 32 cases | 3.070 s |
| Paired mean latency increase | 1.547 s (+50.4%) |
| Mean output tokens observed | 273.63 |
| Process RSS after load | 2,314,600,448 bytes |
| MLX active / peak memory at completion | 1,591,035,918 / 2,691,439,160 bytes |

Only final-answer text was rated. The separate exploratory AI review is non-blind and owner-assessed; it is not a human judgment or accuracy percentage. Its 0–5 dimension means were 4.969 factual correctness, 4.812 grounding, 4.625 hallucination control, and 4.875 instruction following. The paired normal-mode means were 4.469, 4.375, 3.938, and 4.469, respectively. Two object-study answers exceeded the 160-word user-facing limit (162 and 169 words), which lowered instruction-following scores. These reviewer scores are qualitative and should not be read as statistically significant evidence; see [`results/lfm3-thinking-ai-review-results.json`](results/lfm3-thinking-ai-review-results.json). A coarse deterministic report is saved beside the run as `final-answer-coarse-score.json`; it remains separate from rubric review.

The all-case raw answers and per-case token counts are in [`thinking/runs/lfm3-thinking-all-20261030T210520891236Z-731645/`](thinking/runs/lfm3-thinking-all-20261030T210520891236Z-731645/). Hidden reasoning text and token IDs were not retained or scored. Actual audio, spoken-image, video, audiovisual, TTS, and TTS-ASR follow-up results are in [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md); keep each modality's errors and quality separate from image answers.
