# D1 v2 evaluation

## Current status

The 100-call classification/routing run and the 320-call query-conditioned rerank score set are complete. The rerank runner's final metadata assembly failed after inference and model cleanup; an offline recovery verified and analyzed its complete atomic checkpoint. See the explicit recovery caveat below.

The fixtures and judgments are exploratory. The 62-image baseline uses single-annotator AI visual fixture labels, not human ground truth; images are correlated and include duplicate pixel content. The 6-image diagnostic supplement is separately reported and consists of synthetic chart/table fixtures plus a small person-image check.

## Model and runtime

- Model: `LiquidAI/d1-2B`, revision `10e7671d44e4b37700020b1eed3f594c5f2af0b4`.
- Weights: BF16 safetensors, 3,836,417,920 bytes, SHA-256 `b5e573687315e82ea80f4f7dd8d76b5cec3b6ee97e3509fd0493cfebb203719f`.
- Runtime: Transformers 5.22.0 / PyTorch 2.11.0 / Python 3.12.9 on macOS 26.5.1 arm64, Apple M1 Pro with 16 GiB unified memory.
- Inference: PyTorch Transformers reference `system_one`, MPS, BF16, one model resident, concurrency 1. The model was loaded from the pinned local snapshot. Hugging Face offline flags and the macOS outbound-deny sandbox were used with the process guard. The classification runner recorded no blocked attempts; the rerank attempt counter was not persisted after finalization failed.
- The official d1 ONNX repository provides model graphs and documents their inputs, but no turnkey Python `system_one` ONNX runner was found. The official Python reference imports PyTorch; reproducing the typed readout and question/image packing would require a custom adapter. No ONNX/CoreML or Windows runtime inference was performed. See [ONNX_RUNTIME_INVESTIGATION.md](ONNX_RUNTIME_INVESTIGATION.md).

## Classification: frozen 62-image baseline

One image call asked ten typed binary questions and a typed content-type question. Routing calls shared that same image pass. Thresholds below were evaluated as fixed values; they were not tuned on a held-out dataset.

| Metric | Result |
| --- | ---: |
| Content type accuracy | 58/62 (93.55%) |
| Content type macro-F1 over classes with gold support | 0.897 |
| Content type multiclass Brier score, mean per image | 0.1138 |
| Top-choice probability Brier / 10-bin ECE | 0.0573 / 0.0543 |
| Binary label-pair Brier / 10-bin ECE | 0.0416 / 0.0446 |

Binary macro results average the seven labels with positive gold support. `contains_chart` and `contains_table` have no positive examples in this baseline and are excluded as undefined. A supported class with no positive predictions contributes precision 0, recall 0 and F1 0. The macro metrics do not count true negatives as success.

| Threshold | Macro precision | Macro recall | Macro F1 |
| ---: | ---: | ---: | ---: |
| 0.5 | 0.982 | 0.882 | 0.916 |
| 0.7 | 0.857 | 0.715 | 0.754 |
| 0.8 | 0.857 | 0.710 | 0.751 |
| 0.9 | 0.857 | 0.679 | 0.731 |

At 0.5, the largest weakness is `contains_ui` recall (14/38, 0.368). At thresholds 0.7–0.9, the single positive `contains_person` example is missed. Chart and table labels need more real examples before their baseline behavior can be assessed.

## Separate 6-image diagnostic supplement

Content type was correct on 6/6, with two examples each for chart, table and photo. The supplement's binary labels have two positives each for chart, table and person; all six positive instances were detected at thresholds 0.5–0.9. Other labels have no positive support there and are undefined. This tiny synthetic diagnostic set does not establish generalization.

## Routing

The runner scored 36 request-intent cases, 12 per declared route. It chose the fixture rubric's route correctly in 36/36 cases (macro-F1 1.0). Classification and route selection shared one forward pass per image. Mean complete call latency was 4.38 s, median 4.24 s, p95 5.47 s. Routing labels are fixture-designed intent targets, not independently collected user requests.

## Cost and memory

The process loaded the model once (5.62 s) and made 100 successful image calls (68 fixture IDs plus 32 declared repeats; two more repeat pixel contents already occur within the 68 fixtures). Thus there were 100 requests but 66 unique pixel contents. Evaluation wall time was 419.51 s; measured post-load throughput including per-request Python GC and `torch.mps.empty_cache()` every five successful calls was 0.239 requests/s. The summed request-call time under that cleanup policy was 418.17 s. No inference failed and no OOM occurred.

The sampled max process RSS was 729,694,208 bytes (~0.68 GiB). The maximum sampled MPS current allocation was 4,050,478,080 bytes; maximum sampled driver allocation after a request was 9,986,719,744 bytes, dropping to 8,894,906,368 bytes after every-five cache release. These are sampled backend counters, not a complete unified-memory peak. After dropping model/engine references and collecting garbage, in-process MPS current allocation was 0 and model/engine weakrefs were dead; the process then exited. Driver allocation after that in-process cleanup sample was 1,858,158,592 bytes.

The nearest idle snapshot was taken before this run, not immediately before model load: system free memory 79%, swap used 14.80 GB, `vm_stat` pageouts 226,946 and swapouts 3,441,439. At request 5 pageouts were 227,604; at request 100 they were 232,050. The free-memory gauge varied during the run and recovered from 32% at request 5 to 39% at request 100. These system-wide samples are not attributable solely to d1; no OOM or sustained MPS-current growth was observed.

| Completed requests | Elapsed after load | Throughput |
| ---: | ---: | ---: |
| 10 | 44.43 s | 0.225 req/s |
| 50 | 215.10 s | 0.232 req/s |
| 62 | 265.37 s | 0.234 req/s |
| 68 | 285.41 s | 0.238 req/s |
| 100 | 418.17 s | 0.239 req/s |

## Retrieval reranking

The frozen retrieval baseline uses 16 queries and the original EmbeddingGemma top 20 for each query. Candidate sets contain 320 query/candidate references across 62 unique images. Relevance judgments were not used to select candidates and were not exposed to d1. Each d1 call received one candidate image and only its matching query as one typed `noul` question. The runner made 320 independent calls; it did not batch across queries.

All 16 candidate sets already contain every judged relevant image (macro candidate Recall@20 1.0). EmbeddingGemma has a relevant top-1 on all 16 queries, a relevant top-5 on all 16, and macro MRR@5 and MRR@20 of 1.0. Its macro Recall@5 is 0.9766 because Q15 has eight relevant boards and five appear in the top five (5/8). The d1 reranking preserves those exact aggregate values:

| Metric | EmbeddingGemma top 20 | d1 reranked top 20 |
| --- | ---: | ---: |
| Candidate Recall@20 | 1.000 | 1.000 |
| Recall@5 | 0.9766 | 0.9766 |
| Hit@5 | 1.000 | 1.000 |
| Top-1 accuracy | 1.000 | 1.000 |
| MRR@5 | 1.000 | 1.000 |
| MRR@20 | 1.000 | 1.000 |

d1 changed the order of all 16 top-20 lists, but did not improve any measured relevance metric. The judgments are a small, single-annotator visual set, so these saturated results do not establish general retrieval quality.

The measured serial cost is a mean 50.25 seconds per query for its 20 candidates (median 49.63 seconds; about 2.51 seconds per candidate), including answer release and the every-five cache policy. The 15 persisted query-loop wall measurements average 50.53 seconds (median 49.90), including checkpoint and monitoring work. Q16's 20 per-call elapsed times sum to 49.29 seconds with policy overhead, but its checkpoint-inclusive loop wall is unknown because the runner failed before persisting that final timing. Across all pairs, inference-only time sums to 782.26 seconds and answer-release/cache-policy time to 21.74 seconds, or 804.00 seconds combined. EmbeddingGemma query embedding and ranking averaged 0.098 seconds per query in the frozen preparation run. d1 model-load and processor initialization timings are unavailable, so cold end-to-end latency cannot be reported; the measured loaded path alone adds about 50 seconds per query.

The run produced a complete score set but did not finalize cleanly. After model cleanup, final JSON assembly raised `NameError: name 'sys' is not defined`. The original atomic checkpoint contains all 320 successful scores, 20 for each Q01–Q16, and no error rows. The raw checkpoint is retained byte-for-byte; an offline recovery script verified its pair order and provenance, then generated a recovered result and analysis. The recovery status remains `RECOVERED_COMPLETE_SCORES_FINAL_METADATA_INCOMPLETE`. Missing model/processor load duration, total 320-pair wall time, exact run-start timestamp, in-process after-unload counters, blocked-attempt count, and Q16 checkpoint-inclusive loop wall remain unknown. The runner's missing `sys` import is fixed in source for future runs; no inference was repeated.

Peak samples during the rerank were 723,894,272 bytes process RSS, 4,049,752,832 bytes MPS current allocation, and 9,993,814,016 bytes MPS driver allocation after a request; the maximum sampled driver allocation after cache release was 9,024,929,792 bytes. These are sampled backend/process counters, not a true unified-memory peak. The runner process exited. A separate post-exit system snapshot recorded 82% free memory, 15,066.50M swap used, and 241,145 pageouts; these system-wide figures are not attributable only to d1.

This experiment does not support retaining d1 as a retrieval reranker: the baseline already saturates the judged metrics and d1 adds roughly 50 seconds per query. The classification path remains a separate possible opt-in experiment, subject to stronger, diverse human-labeled evaluation; it is not required for core search.

## Artifacts and provenance

- Raw classification/routing outputs: [classification_routing_results_100_every5.json](classification_routing_results_100_every5.json).
- Scored threshold/calibration/routing metrics: [analysis_results_100_every5.json](analysis_results_100_every5.json).
- Frozen query candidate sets: [embedding_top20.json](embedding_top20.json); candidate tie-break audit: [embedding_top20_tiebreak_audit.json](embedding_top20_tiebreak_audit.json).
- Original rerank checkpoint, preserved byte-for-byte: [d1_rerank_scores_per_query_every5.partial.json](d1_rerank_scores_per_query_every5.partial.json), SHA-256 `0e6e765b9f461de55f28d12d68f789f49cea3d5c48797599374d3e84ae8bc29f`.
- Recovered complete 320-score artifact: [d1_rerank_scores_per_query_every5.recovered.json](d1_rerank_scores_per_query_every5.recovered.json); the recovery script is [recover_complete_rerank_partial.py](recover_complete_rerank_partial.py), and metrics are [analysis_results_d1_recovered.json](analysis_results_d1_recovered.json).
- Post-exit system sample: [d1_rerank_post_exit_system_snapshot.json](d1_rerank_post_exit_system_snapshot.json).
- Classification runner source at inference: experiment commit `7aa3b66d02a56199584b16fb9e6ef1e962e2425b`; rerank runner source at inference: commit `828f192`. The recovery artifact records the raw partial hash and exact source SHA at inference.
- Raw outputs preserve the isolated experiment worktree paths as provenance. They are retained in this internal evidence snapshot; review and normalize path fields before selecting any of them for an external package.

## Recommendation

Do not add d1 as a default indexing or retrieval dependency on this evidence. Its typed classification and route API worked on these fixtures, but the annotation set is small and synthetic, route targets are an intent rubric, the reference runtime took about 4.2 seconds per image, and the retrieval baseline already saturates the measured primary metrics. The query-conditioned rerank experiment also preserved every aggregate relevance metric while adding about 50 seconds per query. Consider opt-in, lazy-loaded classification only if a future diverse human-labeled dataset shows useful incremental value.
