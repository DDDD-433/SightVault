# Unified acceptance record

Branch: `integration/unified`, based on `766027c79d4c6f17ba7b88b1df3bc079dfbbd141`. Host: Apple M1 Pro, 16 GiB unified memory, macOS 26.5.1, native Python 3.12.9 arm64. Model decisions and experimental limits are in [FINAL_MODEL_SELECTION.md](FINAL_MODEL_SELECTION.md).

This is a local Python application with portable source-and-setup bundles. Model weights and Python environments are not included in the ZIPs. Native Windows execution has not been tested on this Mac.

## Runtime and data

The unified `.venv` was created natively, installed from the pinned Mac lock, and `Setup-Mac.sh` completed. `pip check` passed. MLX-VLM reports version 0.7.11 with the required upstream VCS commit `918763c6875c80e82f9f48344dfad4763346fea8`; this is not interchangeable with an arbitrary PyPI release bearing that version. Upstream llama.cpp `e0f142d5ddc1461ce721644d92af33db23545105` was rebuilt inside the unified checkout; the local executable hash and compiler version are in [installed-runtime.json](unified/installed-runtime.json).

The selected local model cache is independent of experimental worktrees. No model-directory symlinks remain. All 55 original/optional model-manifest files and 22 Mac conversion files passed exact size/SHA-256 checks. The manifest records revisions, required/optional groups, expected bytes and the exact local provenance bytes. RapidOCR's wheel and three bundled ONNX model hashes are recorded in the OCR report.

The 62-image demo was copied with SQLite backup, verified and relocated with the supported dry-run/apply helper. It retains 62 normalized 768-dimensional vectors and original indexed timestamps. The source sample manifest contains two duplicate-content pairs, yielding 60 unique stored PNG/thumbnail pairs. No regeneration or re-indexing was used to move paths. The canonical source checkout and its data remain separate.

## Completed model and application checks

| Check | Result and evidence |
| --- | --- |
| Retrieval | 17/17, four negative similarities below all positive semantic top results; fresh direct-pixel/stored-vector cosines 0.9999, 1.0000, 1.0000. [retrieval.json](unified/retrieval.json) |
| Default offline inference | Real direct text/image embedding on PyTorch MPS, MiniCPM-OCR and MiniCPM-V on MLX Metal. Explicit preferred OCR succeeded, no RapidOCR fallback. [offline-default.json](unified/offline-default.json) |
| Optional Omni acceptance | OCR → Omni reuse/recovery → OCR; real semantic search preserved before/after intentionally missing native binary; all 12 checks passed. [models-omni.json](unified/models-omni.json) |
| Optional LFM3 bounded thinking | Real OCR/model switching and 128/384 token accounting; all 10 checks passed. [models-lfm3-thinking.json](unified/models-lfm3-thinking.json) |
| LFM3 API/mode identity | Real API final answer, model revision, MLX device and bounded-token metadata; launcher accepts matching mode and rejects a different thinking budget. [lfm3-thinking-api.json](unified/lfm3-thinking-api.json) |
| Default complete browser demo | 37/37 checks before the subsequent UI polish, including search modes, earliest match, dates, evidence, local generation, similar images, keyboard, reindex skipping and narrow layout. [browser-default.json](unified/browser-default.json) |
| Omni complete browser demo | 37/37 with actual Omni backend/model identity and local analysis. [browser-omni.json](unified/browser-omni.json) |
| CSP-enabled browser | 10/10, including pagination query preservation, async evidence race, early years and DST 23-hour day; no page errors. Additional real analysis was performed through the normal in-app/CLI browser session. [normal-csp.json](unified/normal-csp.json) |
| Default concurrency | 40 search/status responses checked; 18 semantic searches completed before image analysis ended. [concurrency-default.json](unified/concurrency-default.json) |
| Omni concurrency | 40 search/status responses checked; actual semantic overlap and analysis/evidence identities passed. [concurrency-omni.json](unified/concurrency-omni.json) |
| Real recovery and ingestion | Disposable invalid OCR weights triggered labeled RapidOCR fallback; restoring only the disposable weight reference recovered MiniCPM; VLM analysis and fresh EXIF/timezone image embedding passed. Original weights untouched. [model-recovery.json](unified/model-recovery.json) |
| OS network isolation | Loopback status HTTP 200; external TCP connect denied with EPERM by macOS sandbox-exec. [os-offline-proof.json](unified/os-offline-proof.json) |
| Fresh extracted candidate | Archive/extracted manifests passed; installation into a path containing spaces created a new native environment; 178 tests passed, one expected failure; retrieval 17/17 and real offline embedding/OCR/VLM passed. [package-installation.json](unified/package-installation.json) |

The Omni acceptance JSON predates the final device-label wording fix. Its `CPU (GPU offload not verified)` observation is superseded by the current accurate label **`unverified (no GPU offload evidence)`**. The observed native process emitted no layer-offload line despite a Metal-enabled build. No CPU or GPU execution claim is made for that run. The later real API status records the corrected label and pinned projector/runtime: [omni-api-status.json](unified/omni-api-status.json).

Analysis responses now snapshot provenance and generation metadata while holding the generative lock, so a later queued analysis/OCR request cannot change the previous answer's attribution. A deterministic regression reproduces the post-worker state-change boundary. Separate embedding locking preserves semantic progress during generation. Shutdown closes owned native children; no unrelated listening process is terminated by the launcher.

## Package boundary checks

The builder uses explicit reviewed source/QA allowlists, canonical sample hashes, an independent rendered-image/thumbnail evidence anchor, and SQLite backup. The anchor was independently checked against the original 766027c package hashes and exact Pillow rendering: all 60 stored PNGs and WebP thumbnails matched bytes, decoded RGB and dimensions. It rejects arbitrary replacement evidence bytes, unlisted sample images, ancestor symlinks, out-of-root evidence, ZIP symlink/special-file members and tampered extracted files. These checks are exercised with disposable adversarial fixtures.

An extracted candidate exposed an obsolete immutable-snapshot verification command. The shipped verifier now recognizes `PORTABLE_PACKAGE_MANIFEST.json`; the old extraction manifest remains the fallback for historical packages. The repaired command was retested before candidate installation.

Windows setup statically handles native stderr under PowerShell 5.1 by checking process exit codes, rejects macOS-only optional models before downloading, quotes paths containing spaces and uses disposable validation data. Its report records lock/package/benchmark hashes. Windows dependency resolution and these code reviews do not establish native Windows execution. Run [WINDOWS_VALIDATION.md](../docs/WINDOWS_VALIDATION.md) on the target PC.

## UI pass and final artifacts

The requested Claude Code pass uses Opus 5.5 high with exactly one Sonnet 5.5 xhigh read-only reviewer. Coding-provider use is separate from the product's entirely local inference. Raw CLI transcripts/account metadata remain outside the repository and are excluded from source packages. The patched app passed **185 unit/regression tests** (one known expected failure), **37 complete browser checks on each of MiniCPM and Omni**, **10 normal-CSP edge checks**, and **29 supplemental UI checks**. See [UI_UX_REVIEW.md](UI_UX_REVIEW.md) for the three captured steps, before/after screenshots, real keyboard-triggered inference, escaping, missing-runtime presentation, reduced motion and reflow limits.

The final archives are `SightVault-macOS-arm64.zip` and `SightVault-Windows-x64.zip`. Their exact bytes/SHA-256, independent re-extraction results and platform-validation limits are recorded in the accompanying `exports/RELEASE_MANIFEST.json` and `.sha256` sidecars. No weights, virtual environments, private indexes or raw coding-agent transcripts are bundled. The Mac `.command` and setup script retain executable bits; all other members remain regular files. Native experiment HTTP responses originally saved as opaque `.bin` files have exact-byte JSON/WAV companions in the bundles; [PORTABLE_RESPONSE_COPIES.json](visual-v2/PORTABLE_RESPONSE_COPIES.json) maps original paths to typed copies and their unchanged SHA-256 values. The immutable originals remain in source history. The sole JSONL exception is the exact-path, exact-SHA reviewed OCR ground-truth fixture; arbitrary raw logs remain rejected.

| Footprint | Measured size |
| --- | --- |
| Fresh native Python environment | 1,827,614,780 regular-file bytes; `du` disk usage 1,945,956,352 bytes |
| Required original model download set | 7,275,820,860 bytes |
| Local Mac MiniCPM conversions | 4,864,039,454 bytes |
| Optional LFM3-VL snapshot | 1,609,119,455 bytes |
| Optional Omni model/projector/provenance | 1,391,217,363 bytes |
| Candidate source/setup ZIP before final QA/UI content | 9,866,280 bytes; not the final release ZIP |
| Final platform ZIPs | Exact compressed bytes and hashes in the adjacent release manifest |

[FINAL_ACCEPTANCE.json](unified/FINAL_ACCEPTANCE.json) binds source fingerprints to the checked results. The fresh candidate installation is labeled separately from the final archives; model weights were reused from the already verified local cache, never copied from Windows. Native llama.cpp was rebuilt with the unused embedded web UI and HTTPS support disabled, avoiding an external OpenSSL runtime dependency; the final Omni UI run used this build.

## Measurement limits

- The experiment reports retain raw final answers and explicitly labeled nonblind AI reviews. They are not human accuracy or generalization estimates.
- Ovis's 100-call run was stopped at 81 by host-memory pressure; no model failure/OOM occurred and no 100-call throughput is invented.
- d1's complete 320-score checkpoint was recovered after post-inference metadata assembly failed; the missing final loop wall/load/total metadata remains unknown.
- RSS, MLX allocator and MPS driver counters are different quantities. They are not added together. Fresh-process timings retained filesystem caches and are not reboot-cold measurements.
- Sandbox-exec enforced outbound denial with loopback allowed during model checks. Applicable Python guards are additional instrumentation. Native llama.cpp external attempts were not counted, and this was not a firewall test.
- One pre-existing expected-failure regression documents orphaned stored copies after editing an indexed source file (S-6). It affects accumulated disk usage; Phase 2 does not silently claim that issue fixed.
