# d1-2B MPS memory diagnosis

## Interrupted classification/routing segment

The first label-free classification/routing segment completed 50 of 68 planned image requests: 50 baseline images, no supplement images, 50 forward passes, zero model errors, and zero OOMs. The raw checkpoint is `classification_routing_results.stopped_50.json`. The first command attempt failed before model loading because the runner resolved `samples/` one directory too high; `classification_setup_and_stop.json` records that setup failure separately.

The active d1 process was stopped by SIGINT after the coordinator requested a stop at a 14% system-wide free-memory reading. The interrupt landed during request 51, so that call is not included in the checkpoint and is not counted as a model failure. The immediate whole-machine free-memory readings recorded during that segment were 82% before model loading, 51% early after load, 27% at request 15, 19% at 25, 15% at 35, and 14% at 45. After process exit an independent reading was 74%; the coordinator later saw 79%. These are global readings, not d1-specific allocations.

That partial writer did not persist per-request RSS or `torch.mps.current_allocated_memory()` / `driver_allocated_memory()` counters. The last live `ps` sample showed approximately 1.0% MEM but not an exact byte count. The separate three-image smoke had peak process RSS 465,158,144 bytes and highest post-request MPS samples 4,049,621,248 bytes current allocated and 8,757,821,440 bytes driver allocated. Do not attribute those smoke values to the longer segment.

## 15-request memory-policy probe

The 15-call probe requested garbage collection after every result and an MPS cache clear every five successful calls. All 15 model calls returned one forward pass and produced raw predictions, with zero model errors, zero OOMs, and no blocked network attempts. The raw output is `classification_memory_policy_probe_f44_bookkeeping_failure.json`; `classification_setup_and_stop.json` records its run provenance.

The output is marked incomplete because a stale `cache_release_s` variable raised `NameError` after the cache clear at requests 5, 10, and 15. Those were instrumentation failures after successful model calls. The artifact has duplicate execution rows for those three indices and does not store their per-request post-cache samples, so use only the aggregate maxima. The code was committed as `f44c919` and its exact runner hash is in the artifact.

The 15-call run loaded the model in 5.00 seconds and spent 64.10 seconds in evaluation; sampled process RSS peaked at 744,013,824 bytes. Sampled MPS current allocation stayed between 4,049,621,248 and 4,050,070,272 bytes. Driver allocation varied, with a maximum sample of 9,977,036,800 bytes after request and a maximum of 8,894,906,368 bytes after an every-five cache clear. These are maxima from separate sample groups, not a paired before/after measurement, so they do not quantify bytes released by each clear.

Whole-machine free-memory readings were 78% before model load, 26% after five requests, 27% after ten, and 36% after fifteen; an independent check after process exit was 78%. This is not directly comparable to the earlier run's sparse sampling, and it does not establish that the cache policy solved the memory-pressure concern. Current allocation being flat while driver allocation is higher and variable is consistent with reserved or other driver memory, but the failed harness did not save paired cache rows or OS virtual-memory/swap snapshots.

## Metrics from the saved 50-image checkpoint

`classification_50_partial_analysis.json` scores only the saved first50 baseline predictions against their separate visual labels. Its primary-content coverage is 2 photos, 8 document pages, 12 slides, 10 illustrations, 8 project boards, and 10 receipts; it has no terminal, chart, or table positives and omits the last12 baseline images and all six diagnostic supplement images. The observed content-type accuracy was 46/50 (0.92), supported-class macro-F1 was 0.880, and the mean multiclass Brier score was 0.1405. Across nine binary labels the Brier score was 0.0511 and 10-bin ECE was 0.0552. At threshold0.5, macro precision/recall/F1 was 0.917/0.777/0.805 over only four supported-positive labels; `contains_error`, `contains_terminal`, `contains_chart`, and `contains_table` are explicitly undefined for this partial. At0.7 and above only two positive labels remain. Routing scored 30/30 cases, but this is a short, template-heavy single-annotator fixture subset. These are interim diagnostics, not full-set results or human-grounded generalization claims.

## Static reference-runner inspection

- `model.system_one()` delegates to `SystemOne.system_one_batch()` and returns typed Python answers and token counts. It does not intentionally retain request tensors in its result.
- `SystemOne._padded()` builds per-call prompt sequences, processor inputs, logits, and position tensors locally, then returns Python readout probabilities. The activation tensors are not stored on the engine after the call.
- The engine itself is held by a `cached_property`, which is expected model residency. `Reader._texts` caches only media expansion text keyed by token IDs, with a 4096-entry bound; it does not cache image tensors.
- The raw answer dictionaries contain Python values, not model output tensors. Static inspection finds no obvious image-tensor reference leak, but the first 50-call run has no per-request counters to establish whether its resource growth came from live allocations, allocator reservations, or other system activity.
- The old call-count instrumentation replaced `engine._slot_logits` with a closure over the original bound method. That bound method retained the engine/model after `del engine, model`, until the function/process exited. This explains why the original `memory_pressure_after_unload` sample in `runtime_smoke.json` was not truly post-unload; the sample is annotated in place. It does not explain the within-run free-memory decline. The 50-request process exited, and the coordinator independently observed whole-machine free memory recover to 79% afterward.

## Next run

The runners now remove the call-counter monkeypatch and its captured bound method before cleanup, synchronize and clear MPS cache after model references are deleted, and sample MPS counters, process RSS, and model/engine weakrefs. They also record `vm_stat` and `sysctl vm.swapusage` snapshots before load and every five requests. The classifier's every-five path still needs a no-model regression check for bookkeeping and duplicate-index behavior before inference. After that, the next granted d1 slot can run the full 68-image classification set using the same `--empty-cache-every 5` policy; no rerank or 100-call throughput run is included in that slot. No allocator safety limits will change.
