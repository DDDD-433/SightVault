# Current project state

Snapshot prepared October 29, 2026, from the QA-fixed Windows workspace. See `SNAPSHOT.json` for precise creation time and Python version. This document describes observed source and saved evidence, not a new Mac test run.

## Product and architecture

SightVault is a private visual-memory app. It indexes a user-selected image folder, searches image pixels and OCR, shows source evidence and timestamps, and offers on-demand vision-model analysis. It uses Python, FastAPI/Uvicorn, vanilla HTML/CSS/JS, NumPy, SQLite and FTS5. It binds `127.0.0.1` and uses no cloud inference API.

| Area | Main files |
| --- | --- |
| Entry point and browser launcher | `app.py`, `scripts/launch.py`, `Start-SightVault.cmd` |
| Config and storage locations | `sightvault/config.py` |
| Lazy model loading and inference | `sightvault/models.py` |
| SQLite schema, FTS and vector serialization | `sightvault/database.py` |
| Indexing, duplicate reuse, atomic copies | `sightvault/ingestion.py` |
| Hybrid/semantic/exact ranking and earliest-match behavior | `sightvault/search.py` |
| HTTP APIs and local-origin checks | `sightvault/server.py` |
| UI | `sightvault/web/` |
| Unit/regression tests | `tests/test_core.py`, `tests/test_regressions.py` |
| Real-model verification | `scripts/benchmark.py`, `scripts/verify_offline.py`, `scripts/smoke_models.py` |
| Ingestion/browser QA | `scripts/qa_ingestion.py`, `scripts/qa_browser.py` |

## Model identity

| Folder | Repository | Revision |
| --- | --- | --- |
| `.models/embeddinggemma-2` | `google/embeddinggemma-2` | `f9972ac22e696a105c482202646979e33e4b5a1e` |
| `.models/minicpm-ocr` | `openbmb/MiniCPM-OCR-1.0` | `c5833cddb363aa62f1cb60ddde48c832e8f1a92c` |
| `.models/minicpm-v` | `openbmb/MiniCPM-V-4.7-1B` | `bb99eab4113676f30830ffa73bac1f0b2a5f1dea` |

EmbeddingGemma directly encodes images and queries into 768-dimensional normalized vectors. Audio components are disabled. MiniCPM-OCR is the preferred OCR engine; RapidOCR/ONNX Runtime is a labeled local fallback. MiniCPM-V is a 1B image-analysis model. The embedding model stays loaded; OCR and VLM share a generative slot. Locks separate embedding requests from generation, but keep OCR/VLM generation serialized.

Hybrid ranking starts from 0.70 semantic and 0.30 lexical weighting with extra literal-code/OCR boosts. Semantic mode uses vectors; exact mode uses OCR and filenames. Similarity is not a calibrated confidence score. First occurrence selects the earliest matching indexed file across the full relevant corpus before display limiting.

## Windows evidence

Existing Grok QA report: 39/39 unit/regression tests; 17/17 retrieval cases; four negative-query examples; 30/30 browser checks plus two XSS escaping checks; real OCR/VLM and offline socket-guard verification. Peak allocation in the offline test was 3.99 GiB. UI QA reported a different peak near 6.15 GB. Keep these separate rather than treating them as one measurement.

Machine: RTX 4070 with 12 GB VRAM, around 16 GB system RAM. Windows warm semantic search was about 125-165 ms; initial embedding load about 12 seconds. These numbers do not predict Mac performance.

This handoff additionally verifies source/sample hashes, SQLite integrity and migration-helper behavior on disposable copies. A full regression rerun stalled at TestClient in this restricted host; a non-HTTP subset is attempted separately. See `HANDOFF_VERIFICATION.json` for captured results and limitations. Real-model Windows QA has not been rerun merely to package the project.

## Mac-specific work still needed

- `_device()` automatically chooses CUDA or CPU, never MPS. Explicit `SIGHTVAULT_DEVICE=mps` is passed through but unverified.
- `_dtype()` uses bfloat16 only on suitable CUDA hardware, otherwise float32. Validate MPS dtype/operator support with the installed versions before changing it.
- OOM recovery and memory metrics are CUDA-specific. Add per-backend handling without swallowing arbitrary errors or silently labeling CPU inference as MPS.
- Verify loaded-device status through repeated OCR/VLM switching and backend fallback, including after a failed load; prior Windows QA does not establish MPS behavior.
- `verify_offline.py` reports CPU whenever CUDA is absent, even if explicit MPS were used. Correct the report before claiming Mac acceleration results.
- `scripts/make_demo.py` searches Windows/Linux fonts. Included images are authoritative; do not regenerate them during first migration.
- The original `scripts/download_models.py` resolves current repository heads. Use the pinned handoff downloader for baseline reproducibility.
- Old README says UTC date filters in one paragraph but local filters in QA notes. Current frontend sends local day boundaries; inspect source and update the documentation when migrating.

## Data and environment

`data/sightvault.sqlite3` has 62 demo assets, all with embeddings. `data/images` and `data/thumbs` use relative stored filenames, so evidence copies remain accessible after a move. The asset `filepath` and `folder` columns still refer to the Windows samples folder until `prepare_demo.py --apply` is run. Rebase only the packaged demo. Preserve event timestamps instead of replacing them with extraction time.

Config supports `SIGHTVAULT_DATA`, `SIGHTVAULT_MODELS`, `SIGHTVAULT_DEVICE`, `SIGHTVAULT_OCR`. Set these before importing the app. `SIGHTVAULT_PORT` is read by `scripts/launch.py`; direct `app.py` uses `--port` instead.

The Windows `.venv` depends on a base interpreter outside the project and lacks pip. Recreate it on Mac. `WINDOWS_INSTALLED_PACKAGES.txt` records installed versions, including Windows/CUDA-specific distributions. Do not install that whole list on a Mac.

## Remaining product limitations

Renamed files retain old and new rows; missing originals are flagged. Corrupt images are retried on later runs. No UI per-image force re-OCR. File dates are evidence of indexed-file chronology, not proof of when an event occurred. Sixty sample images and their dates are synthetic; two photos have provenance in `samples/photo_sources.json`. The VLM can be imprecise. Offline verification is an in-process socket guard, not an operating-system firewall audit.

`scripts/qa_ingestion.py` deletes its supplied work directory before running. Always pass a fresh, disposable temp child; never the project, data directory or user files. `qa_browser.py` assumes exactly 62 demo images and uses CSP bypass for its own evaluation calls; browser QA alone is not a full CSP security audit.
