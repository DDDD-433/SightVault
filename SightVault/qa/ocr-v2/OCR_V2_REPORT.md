# SightVault OCR V2 plain OCR comparison

Status: RapidOCR and MiniCPM completed the full 100-call stream. OvisOCR 3 completed 81 calls and was stopped by the coordinator after a host-memory-pressure check. Its unique-image quality score is complete, but its 100-call throughput was not measured. No model call failed and no CUDA or MPS out-of-memory error occurred.

## Decision evidence

For the shared 56-image plain OCR set, MiniCPM has the lowest character and word error rates and the strongest exact identifier/numeric extraction. RapidOCR remains the bulk-speed control: its measured 100-call continuous rate was about 14.5 times MiniCPM's. Ovis has strong exact identifiers and numbers, but its CER/WER are worse than MiniCPM, it is slower than both other backends, and its full 100-call throughput remains unknown. These plain OCR results do not establish performance on spotting, KIE, tables, charts, or full-page structured parsing; those are separate probes.

| Backend | Unique 56 quality | Exact identifiers, micro F1 | Exact numeric values, micro F1 | Measured continuous stream | Warm image p50 / p95 | Peak host RSS | Accelerator allocation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RapidOCR 1.4.4 | CER 9.49%, WER 24.94% | 71.70% (152 TP / 58 FP / 62 FN) | 92.25% (244 / 11 / 30) | 100 calls: 2.446 images/s; 100 unique+repeat calls | 0.393 / 0.478 s | 0.893 GB | CPU path; not measured |
| MiniCPM-OCR 1.0 | CER 4.43%, WER 6.89% | 96.26% (206 / 8 / 8) | 100.00% (274 / 0 / 0) | 100 calls: 0.1685 images/s; 56 unique + 44 repeats | 5.393 / 9.327 s | 3.075 GB | MLX active peak 3.673 GB |
| OvisOCR 3 | CER 10.77%, WER 14.99% | 96.96% (207 / 6 / 7) | 98.53% (269 / 3 / 5) | 50 calls: 0.0790 images/s; 50 unique. Run stopped at 81 calls | Not reported for incomplete run | 0.588 GB | MPS driver sampled peak 8.291 GB; current allocation 1.706 GB at final snapshot |

Quality uses only the first occurrence of each of 56 images, so repeated throughput calls never add quality weight. CER/WER use edit distance and penalize insertions as well as omissions and substitutions. Empty-support categories are null; exact identifier/numeric metrics use token boundaries and count false positives and false negatives. The Ovis quality score is still over all 56 unique images even though its throughput run ended early.

MiniCPM's exact date, time, version, price, number, and combined numeric-value F1 scores were each 100% on their stated supports (42, 17, 43, 61, 111, and 274 values). Ovis reached 100% F1 for dates (42), times (17), and prices (61), 92.68% for versions (38 TP / 1 FP / 5 FN; support 43), and 99.11% for numbers (111 / 2 / 0; support 111). Rapid reached 97.56% for dates (40 / 0 / 2), 87.50% for times (14 / 1 / 3), 92.50% for versions (37 / 0 / 6), 91.80% for prices (56 / 5 / 5), and 91.08% for numbers (97 / 5 / 14). In the full text, Mini's extra/omitted word counts were 41/36, Rapid's 22/178, and Ovis's 136/60, showing that Ovis's good exact-field results do not mean its transcribed prose was clean.

## Throughput and load

Throughput boundaries are defined in [README.md](README.md). The continuous-stream rate includes raw result writes and harness gaps but excludes post-run quality scoring. Pipeline and inference-only rates are recorded separately in each raw JSON. The throughput sequence is one continuous stream: each backend processed all 56 unique fixtures once, then cycled through its first 44 fixtures again.

| Backend | Load | First request | 10-call continuous rate | 50-call continuous rate | 100-call continuous rate | Total timed stream |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| RapidOCR | 0.309 s | 0.574 s | 2.3984 images/s | 2.4814 images/s | 2.4457 images/s | 40.889 s |
| MiniCPM-OCR | 4.500 s | 8.030 s | 0.1827 images/s | 0.1875 images/s | 0.1685 images/s | 593.397 s |
| OvisOCR 3 | 6.129 s | 16.478 s | 0.07956 images/s | 0.07900 images/s | **not measured** | 81 calls completed; stopped before 100 |

The Ovis run reached 81 completed calls (56 unique plus 25 repeats). A SIGINT arrived while the next call was underway; that call completed before the interrupt was handled, and the harness then wrote an `interrupted` result with all 81 outputs and scored the 56 unique images after releasing the model. The raw result's `failure` field correctly records `KeyboardInterrupt` from the coordinator. It is not a model failure and must not be treated as a 100-call result. Ovis's 10- and 50-call milestones are measured; there is no 100-call value or Ovis p50/p95 summary for the incomplete run.

## Resource observations

The Ovis run used native BF16 on Apple MPS, with MPS fallback disabled. Its sampled MPS driver peak stayed approximately flat at 8.291 GB; the per-call allocation snapshot stayed near 1.706 GB current and 3.84–3.85 GB driver until the final interrupted snapshot reported the driver reservation at 8.291 GB. Process RSS peak was 0.588 GB. This does not prove an allocator leak or a model failure.

Host `memory_pressure -Q` and VM counters were sampled outside the model process. Before Ovis, free memory was 79%, swap used was 14.01 GiB, pageouts were 219,298, and swapouts were 3,261,175. During the stream, free memory varied: 20% after call 1, 40% at call 10, 57% at calls 42–63, and 17% at call 80. At call 80, swap used was 15.44 GiB, pageouts 226,884, and swapouts 3,441,439. After the Ovis process exited, free memory returned to 79%; swap used was 14.80 GiB, pageouts 226,946, and swapouts remained 3,441,439. The coordinator stopped at the 80-call check after observing the temporary host-pressure dip; the Ovis process unloaded and the host recovered. This records a transient resource observation, not an OOM or sustained allocation-growth claim.

## Models, runtimes, and deployment scope

All evaluated artifacts are Apache-2.0 licensed, offline snapshots. Model/runtime provenance is preserved under `provenance/`, and run JSON includes exact model and code metadata.

| Candidate | Exact revision / artifact | Runtime on this Mac | Included model artifacts |
| --- | --- | --- | ---: |
| RapidOCR control | `rapidocr-onnxruntime==1.4.4`, RapidAI/RapidOCR source `86ae3f5079df3422c1829cd84baf19bc8a7a9453` | ONNX Runtime 1.31.1 CPU | 16,387,900 bytes |
| MiniCPM-OCR 1.0 | `openbmb/MiniCPM-OCR-1.0@c5833cddb363aa62f1cb60ddde48c832e8f1a92c`; MLX-VLM source `918763c6875c80e82f9f48344dfad4763346fea8` | MLX-VLM 0.7.11 / MLX 0.32.4, Apple Metal, affine 4-bit group size 64 with mixed BF16 layers | 2,178,031,276 bytes total; 2,157,942,509-byte `model.safetensors`, SHA-256 `d87f333cfca410bb378ab449aaf45eb96dd7c32c10297745963938619455a055` |
| OvisOCR 3 | `ATH-MaaS/OvisOCR3@4833d5d2afa6e769ee933f024504d67ace80a24c` | Transformers 5.22.0 / PyTorch 2.11.0 / Accelerate 1.15.0, Apple MPS, BF16, fallback disabled | 1,728,993,040 bytes total; 1,706,030,528-byte `model.safetensors`, SHA-256 `aa2311c6b22b07cf3afe9aac6e8bcb02a2b19dd8671b2a91d0b144071577e600` |

RapidOCR's pinned PyPI wheel SHA-256 is `971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf`. Its detector, recognizer, and angle-classifier files are 4,745,517 bytes (`d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9`), 10,857,958 bytes (`48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b`), and 585,532 bytes (`e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c`) respectively. The RapidOCR ONNX weights were used at the package's original precision.

The shared benchmark host was macOS 26.5.1, Apple M1 Pro, 16 GiB RAM, Python 3.12.9. The exact source references are the [RapidOCR release source](https://github.com/RapidAI/RapidOCR/tree/86ae3f5079df3422c1829cd84baf19bc8a7a9453), [MiniCPM-OCR pinned model snapshot](https://huggingface.co/openbmb/MiniCPM-OCR-1.0/tree/c5833cddb363aa62f1cb60ddde48c832e8f1a92c), and [OvisOCR 3 pinned model snapshot](https://huggingface.co/ATH-MaaS/OvisOCR3/tree/4833d5d2afa6e769ee933f024504d67ace80a24c).

The benchmark was run offline under macOS `sandbox-exec` outbound denial with loopback allowed, plus the in-process DNS/socket guard. This is recorded as OS sandboxing, not as a firewall. All three runs report offline verification and zero blocked external attempts. Windows was not available for hardware testing. The results support only the measured Mac paths: RapidOCR CPU, MiniCPM MLX on Apple Metal, and Ovis Transformers/PyTorch on MPS. Windows packaging and performance remain unmeasured; do not infer that the tested MLX conversion works on Windows.

## Token limits and output completeness

The harness default was `max_new_tokens=384` for model generations. MiniCPM preserved `generation_tokens`; across its 56 scored unique outputs the maximum was 126 and none hit 384. RapidOCR is non-generative and has no generation-token counter. Ovis predictions contain only decoded text/line/word fields: the wrapper does not save generated-token counts, a finish reason, or stopping metadata, so truncation against 384 cannot be verified from this run. No token count or `finish_reason` is inferred for Ovis.

The follow-on capability harness records generated-token count, configured cap, and `at_token_budget` when available. Ovis still has no exposed finish reason in this adapter; a cap hit is reported as a possible limit hit, not a confirmed truncation.

## Reproducibility record

- Frozen experiment harness/fixtures checkpoint: `e380c4ae5601a4d63364d0a09701e8d637abf89e`.
- Application baseline: `766027c79d4c6f17ba7b88b1df3bc079dfbbd141`.
- Dataset: 56 synthetic images with independently authored literal/field ground truth; manifest SHA-256 `299c03ec217e5a6e217d1217f14e243d7c9cc5d25e0c669f94c0623990c1f4a4`; ground-truth SHA-256 `0001032a7d8e23b8b6922a5dcda73280e6ec1f641373f1107bd4c457806cb13c`.
- The plain runs used `bench_ocr.py` SHA-256 `edf1a3f26717c210ca4ce4711c81357edef217d8d14d7073a2c70d065a3bb575`, `offline_guard.py` SHA-256 `9d419f1d6d82e5f56ebbc04877655b567dce781aa94a6545fa8e97130e1bbb1d`, and `smoke_ovis.py` SHA-256 `fd03bdbd8125d92edc3e0c25162fdbaa24c5fe2d4349d16971f9786019ab5e36`. The plain-metric implementation SHA-256 is `908c56ff57cb7605f3735bdc80d06ee3571dacbb1026b5e2799af630a347d9b6`; structured metric SHA-256 is `9663e110b0a87c4d32ee1047d9664eccdf67ce1d6627fe57c348c49cd6521eff`; bbox-contract SHA-256 is `bbf8e475a54c3db88b1fda451d996f7104f104ae68175cddcb6535766751b104`.
- Shared plain prompt SHA-256: `8cf8ed8e8470385f4f1305c999620e2421328ffe65d156aa9b8fe836410dbc74`.
- The frozen prompt, fixture labels, model-specific capability prompts, adapter/runtime lock, model licenses, source revisions, official model files, and offline provenance are stored with the harness. Preflight-only import and image-hash checks passed for all three backends without loading weights.
- Raw per-call results are `results/raw_outputs/rapid-plain-20261030T145409Z.json`, `results/raw_outputs/minicpm-plain-20261030T145548Z.json`, and `results/raw_outputs/ovis-plain-20261030T190201Z.json`. Their run records include command, timestamps, code/metric hashes, runtime versions, artifact hashes, offline status, and output predictions. The Ovis file intentionally remains `interrupted` with 81 completed cases.
- Reproduce a full plain stream with `sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task plain --throughput-count 100` (or `--model minicpm` / `--model ovis`). A new Ovis 100-call result requires a separately authorized fresh run; it must not be synthesized from the interrupted result.

## Bounded capability results

The frozen task matrix used each backend's documented task prompt or native API. The selected synthetic cases are small and task-specific; results describe these measured routes and fixtures, not general model-family coverage.

| Backend / measured task | Cases | Result |
| --- | ---: | --- |
| RapidOCR 1.4.4 spotting | 6 | The pinned English `return_word_box=True` path produced per-character boxes. Original character-box output and score are preserved. A strict saved-output character-to-word derivation aligned 66/66 recognized lines without unassigned or ambiguous characters; derived word boxes scored 121 TP / 34 FP / 43 FN at IoU 0.5 (F1 75.86%), with text-only F1 90.28%. Native line boxes separately scored 52 / 14 / 14 (F1 78.79%). This is a post-hoc English-fixture result, not a claim that RapidOCR always returns character boxes. |
| MiniCPM-OCR 1.0 KIE | 4 | 4/4 exact JSON schemas; 24/24 exact field values, no extra or missing fields. |
| MiniCPM-OCR 1.0 tables | 3 | All 3 tables exact: 48/48 cells and 25/25 numeric values. The MLX route returned OTSL; the first JSON/HTML-only scorer misclassified it. A separate strict, inert parser handles the observed rectangular `<fcel>` / `<nl>` subset. The official model documentation says the SDK converts OTSL tables to HTML; span/merge tokens were not evaluated here. |
| MiniCPM-OCR 1.0 charts | 4 | The `Chart to Table:` task returned OTSL. Post-hoc parsing recovered 15/15 category/value pairs exactly and 15/15 numeric-semantic pairs; both axis headers were correct on all 4 cases after identifying category and value columns from numeric cells. The table output does not include chart title or source. |
| OvisOCR 3 word spotting | 6 | Native output contained 62 text regions for 164 labeled words. Strict JSON parsing succeeded for 5/6; the `MC01` output reached 768/768 tokens and was incomplete JSON (possible cap truncation; no finish reason is exposed). The other 5 region outputs had CER 0.73% / WER 7.63% against page text. For all six, including the failed-at-cap case, CER/WER were 21.54% / 26.22%. All 62 emitted boxes had valid normalized geometry. The requested word-box metric was 22 TP / 40 FP / 142 FN (F1 19.47%); this score penalizes valid multiword regions. No Ovis region was split into invented word geometry. |
| OvisOCR 3 KIE | 4 | 4/4 valid JSON schemas; 23 TP / 1 FP / 1 FN across 24 labeled fields, three cases fully exact. |
| OvisOCR 3 tables | 3 | All 3 exact: valid serialization/schema on 3/3, 48/48 cells and 25/25 numeric values. |
| OvisOCR 3 charts | 4 | All 4 outputs follow the official nested string-valued JSON schema. After flattening the documented series hierarchy for scoring, numeric-semantic values were correct for 15/15 pairs. Only 12/16 metadata fields were exact: all four `x_title` values were incorrectly `"None"`. Literal formatting matched 4/15 pairs because values used equivalent decimals and, for the percent chart, a `%` suffix. |
| OvisOCR 3 full-page structured parse | 4 | 4/4 valid official schemas and 30/30 valid normalized boxes. Extracted text CER 1.58% / WER 4.39%; identifiers 15/15, dates 4/4, versions 6/6, prices 3/3, and other numbers 4/4. This is a four-page fixture result only. |

The original run summaries remain unchanged. In particular, the original MiniCPM table/chart metrics and Ovis chart schema/value scores reflect the initial adapter's JSON/HTML and flat-value assumptions. [The post-hoc review artifact](results/posthoc-capability-review.json) stores the corrected task-native scores beside the original summaries and hashes each raw input; [its scorer](posthoc_capability_review.py) does not load a model or modify raw results. The MiniCPM OTSL subset parser is deliberately bounded and does not claim general span-cell support. Ovis chart values retain the model's raw hierarchy; only score extraction flattens it.

The word-spotting prompts are not interchangeable. RapidOCR emits native character/line geometry, while Ovis followed the official JSON schema but often grouped multiple words into one region. The Ovis native word precision/recall score is therefore retained as a strict requested-granularity result, while page-text quality and coordinate validity are reported separately. The RapidOCR derived word boxes union actual character quads only after exact line-text alignment; unalignable lines are counted and excluded rather than guessed.

## Capability runtime and generation

Warm p50/p95 below measure inference per image, excluding model load and post-run scoring. RSS and accelerator peaks are sampled within each short, serialized task command. All calls completed without model runtime errors or OOMs; each process exited between commands. These short task probes are not throughput benchmarks.

| Backend / task | Load | Warm p50 / p95 | Peak RSS | Accelerator sample |
| --- | ---: | ---: | ---: | ---: |
| RapidOCR spotting (6) | 0.734 s | 0.399 / 0.427 s | 0.862 GB | CPU; no accelerator |
| MiniCPM KIE (4) | 5.121 s | 5.140 / 5.806 s | 3.067 GB | MLX active peak 3.673 GB |
| MiniCPM table (3) | 4.415 s | 5.393 / 5.502 s | 3.071 GB | MLX active peak 3.673 GB |
| MiniCPM chart (4) | 4.752 s | 4.806 / 4.879 s | 3.075 GB | MLX active peak 3.673 GB |
| Ovis spotting (6) | 4.832 s | 28.416 / 42.350 s | 0.878 GB | MPS driver sampled peak 9.440 GB |
| Ovis KIE (4) | 4.867 s | 10.582 / 12.280 s | 0.769 GB | MPS driver sampled peak 8.283 GB |
| Ovis table (3) | 4.791 s | 21.326 / 21.544 s | 0.765 GB | MPS driver sampled peak 9.446 GB |
| Ovis chart (4) | 4.815 s | 13.940 / 14.826 s | 0.771 GB | MPS driver sampled peak 9.422 GB |
| Ovis full-page parse (4) | 5.068 s | 30.353 / 33.462 s | 1.144 GB | MPS driver sampled peak 9.454 GB |

The Ovis capability run token counts were recorded with each output. The one Ovis spotting result at its 768-token cap had no exposed finish reason and failed strict JSON parsing. All other recorded generation calls were below their configured caps. The MiniCPM calls exposed `finish_reason="stop"` and stayed below caps. Ovis was run with native BF16 on MPS and `PYTORCH_ENABLE_MPS_FALLBACK=0`; the reported driver allocation is a sampled peak, not a Python process RSS measurement. Per-command whole-host memory snapshots ranged from 79–84% free at start and 54–63% at end. Those snapshots do not attribute all system memory use to Ovis; the processes exited between calls, and no OOM or sustained leak was observed.

## Capability scope and unsupported paths

| Backend | Unsupported task route | Scope reason |
| --- | --- | --- |
| MiniCPM-OCR 1.0 | Word spotting | The tested MLX direct-image adapter does not expose a word-box task. |
| MiniCPM-OCR 1.0 | Full-document structured parsing | The official SDK path uses a separate PP-DocLayoutV3 layout stage; that complete pipeline was not installed or measured. |
| RapidOCR 1.4.4 | KIE, table, chart, full-page structured parse | This pinned OCR package exposes text and geometry, not these document-understanding operations. |

Unsupported results were recorded without loading weights. Ovis full 100-call plain throughput also remains unmeasured: its earlier stream is still the honest 81-call interrupted result described above. Nothing in these bounded probes measures Windows hardware or packaging. The measured paths are RapidOCR CPU, MiniCPM MLX/Metal on this Mac, and Ovis Transformers/PyTorch/MPS on this Mac.

## Capability provenance and raw results

- The exact frozen case lists, prompts, caps, unsupported classifications, and command lines remain in [CAPABILITY_PROBE_PLAN.md](CAPABILITY_PROBE_PLAN.md).
- The capability inference commands were run at experiment code commit `1d9a4e42ff96f7c20bc72ac0111d3e2fe5c95cf9`; each raw JSON records its command, timestamps, code hashes, runtime, model revision, token metadata where available, memory samples, and offline status. The model IDs, revisions, licenses, artifact byte counts, and SHA-256 values are listed earlier in this report and preserved in `provenance/`.
- Every inference used offline environment flags, the in-process guard, and macOS `sandbox-exec` outbound denial with loopback allowed. This is recorded as OS sandboxing, not a firewall. No external inference API was used.
- Raw capability result files are under `results/raw_outputs/`: `rapid-spotting-20261030T204803Z.json`, `rapid-kie-20261030T204934Z.json`, `rapid-table-20261030T204942Z.json`, `rapid-chart-20261030T204947Z.json`, `rapid-document_parse-20261030T204953Z.json`, `minicpm-spotting-20261030T205007Z.json`, `minicpm-kie-20261030T205012Z.json`, `minicpm-table-20261030T205102Z.json`, `minicpm-chart-20261030T205408Z.json`, `minicpm-document_parse-20261030T205438Z.json`, `ovis-spotting-20261030T205452Z.json`, `ovis-kie-20261030T205757Z.json`, `ovis-table-20261030T205900Z.json`, `ovis-chart-20261030T210021Z.json`, and `ovis-document_parse-20261030T210137Z.json`.
- Recreate the model-free post-hoc scoring artifact with `python3 SightVault/qa/ocr-v2/posthoc_capability_review.py`. It does not rerun OCR.
