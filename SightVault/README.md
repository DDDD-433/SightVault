# SightVault

A local visual-memory app for screenshots, documents and photos. Search by meaning or exact text, inspect the source image and timestamp, and ask a local vision model about the evidence.

See the [repository overview](https://github.com/DDDD-433/SightVault#readme) for screenshots and features.

## Mac setup

Use native Python 3.12 on Apple Silicon. `Setup-Mac.sh` can locate or install it through `uv` if `uv` is already available.

From this directory:

```bash
bash Setup-Mac.sh
.venv/bin/python handoff/download_pinned_models.py
.venv/bin/python scripts/prepare_mac_models.py
bash Start-SightVault.command
```

Open **http://127.0.0.1:7861/**. Select an image folder and click **Index folder**. A fresh Git checkout creates an empty database on first launch. Downloaded model weights, virtual environments and local indexes are not committed.

The launcher defaults to `SIGHTVAULT_DEVICE=auto`. On the validated Apple Silicon setup, EmbeddingGemma uses FP32/MPS and the MiniCPM models use local MLX Metal conversions. OCR and vision generation share one slot. Set `SIGHTVAULT_DEVICE=cpu` to use the original PyTorch weights; CPU embedding was verified, while full CPU OCR/VLM performance was not requalified in the Mac QA run.

To change the launcher port:

```bash
SIGHTVAULT_PORT=7862 bash Start-SightVault.command
```

## Prepared demo

Download `SightVault_Source_Demo.zip` from the [Mac preview release](https://github.com/DDDD-433/SightVault/releases/tag/v0.1.0-mac-preview) and extract it. In that extracted `SightVault` directory, install dependencies and models as above, then run the relocation helper **before starting the app**:

```bash
.venv/bin/python handoff/prepare_demo.py
.venv/bin/python handoff/prepare_demo.py --apply
bash Start-SightVault.command
```

The first command is a dry run. The second validates the known demo files, backs up its database, rebases source paths and restores sample dates. It preserves indexed timestamps, OCR and vectors.

The demo contains 60 synthetic images with fictional file dates and two public photos. Photo sources are recorded in [`samples/photo_sources.json`](samples/photo_sources.json). Git does not preserve these sample mtimes, so use the prepared archive when reproducing the dated demo and benchmark. Do not regenerate the samples to set up an existing demo.

Follow [DEMO.md](DEMO.md) for the walkthrough.

## Models and storage

| Component | Role |
| --- | --- |
| Google EmbeddingGemma 2 | Direct image/query embeddings, normalized 768d vectors; audio disabled |
| OpenBMB MiniCPM-OCR 1.0 | Preferred OCR; RapidOCR is a labeled local fallback |
| OpenBMB MiniCPM-V 4.7 1B | On-demand image analysis |
| SQLite + FTS5 | Metadata, vectors and text retrieval |

Hybrid search combines semantic similarity and lexical matching. **Visual meaning** uses direct embeddings; **Exact text** uses OCR and filenames. Earliest-match filtering considers the full relevant index before display limits.

Models are loaded from local files. The pinned downloader verifies original revisions and checksums; the converter records provenance and output hashes in each checkpoint's `source.json`. Dependencies are pinned in `requirements-mac.lock.txt`. See [MAC_QA_REPORT.md](MAC_QA_REPORT.md) for precision choices, memory and timing measurements.

Stored image copies, thumbnails and the SQLite index live under `data/`. Only your selected folder is scanned. The service binds loopback and has no cloud-inference integration.

## Verification

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/benchmark.py --offline-check --output qa/local-retrieval.json
.venv/bin/python scripts/verify_offline.py
.venv/bin/python scripts/qa_browser.py http://127.0.0.1:7861 output/playwright/local
```

The benchmark and full browser harness expect the prepared 62-image demo. The offline verifier uses an in-process socket guard; it is not an OS firewall test. Use a fresh disposable child directory for `scripts/qa_ingestion.py`: it deletes its supplied work directory.

Latest recorded result: **123 tests passed, one expected retention failure; 17/17 retrieval cases; 30/30 browser checks; 5/5 normal-CSP edge checks; 40/40 concurrent requests**. See the [final QA report](qa/independent-review/FINAL_QA_REPORT.md). Windows evidence remains under `handoff/history/windows-evidence/` and is separate from Mac results.

## Limits

- PNG, JPG, WebP, BMP and TIFF; 30 MB and 40 megapixels per image; 2,000 images per indexing run. Export PDFs or slides to images first.
- File mtime is the default date. EXIF capture dates are used only with an explicit timezone. UI date filters use the browser's local timezone; plain date-only API filters use UTC days.
- First occurrence means the earliest matching indexed file. It does not establish when an event happened. Similarity values are not confidence percentages.
- OCR and model analysis can be inaccurate. Keep the source image visible when interpreting an answer.
- Renamed files can retain old and new records. Older saved copies of edited files are retained; automatic garbage collection is not implemented.
- Reindexing repairs missing saved copies. `--force` on the indexing CLI refreshes inference. Failed refreshes preserve valid existing inference for unchanged content.

Official model references: [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2), [MiniCPM-OCR](https://huggingface.co/openbmb/MiniCPM-OCR-1.0), [MiniCPM-V](https://huggingface.co/openbmb/MiniCPM-V-4.7-1B). Their respective model licenses apply.
