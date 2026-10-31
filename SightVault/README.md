# SightVault

A local visual-memory app for screenshots, documents and photos. Search by meaning or exact text, inspect the source image and timestamp, and ask a local vision model about the evidence.

See the [repository overview](https://github.com/DDDD-433/SightVault#readme) for screenshots and features.

See [Phase 2 selection and runtime matrix](qa/FINAL_MODEL_SELECTION.md), [unified acceptance](qa/FINAL_UNIFIED_QA.md), and the [current UI redesign and QA](qa/UI_REDESIGN.md).

## Portable packages

`SightVault-macOS-arm64.zip` and `SightVault-Windows-x64.zip` are portable **source-and-setup archives**, not native executables. They exclude Python environments and model weights. Follow [PORTABLE_SETUP.md](docs/PORTABLE_SETUP.md) for exact setup steps and package integrity checks. The Windows dependency lock is a hash-pinned x64/Python 3.12 resolution; it is not evidence of a Windows runtime pass. See [WINDOWS_VALIDATION.md](docs/WINDOWS_VALIDATION.md) for the native validation procedure.

## Desktop app (development preview)

A native desktop wrapper (macOS Apple Silicon `.app`/`.dmg`, Windows x64 installer) is scripted in `packaging/desktop/`. It runs the same server and UI in a native window, keeps data in per-user folders and downloads models only from an explicit setup action. The Mac app has been built and exercised; Windows native validation and distribution signing remain outstanding. See [DESKTOP_RELEASE.md](docs/DESKTOP_RELEASE.md) for the architecture, build commands, signing status and pending checks. The source setup below is unchanged.

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

When the portable bundle includes the verified public 62-image demo, extract it and install dependencies and models as above. Run the relocation helper **before starting the app**:

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

### Optional vision backends

MiniCPM-V is the default. The unified Mac environment supports the tested optional LFM3-VL specialist; no separate environment is needed:

```bash
.venv/bin/python handoff/download_pinned_models.py --only lfm3-vl-2b-mlx-4bit
SIGHTVAULT_VISION=lfm3 bash Start-SightVault.command
# Optional: spend up to 128 hidden thought tokens before the separate 384-token final answer.
SIGHTVAULT_VISION=lfm3 SIGHTVAULT_LFM3_THINKING_TOKENS=128 bash Start-SightVault.command
```

LFM3-VL is Apple Silicon only. Thinking defaults to off and is slower. Stop the existing server before changing the selected backend or thinking mode.

Omni is an optional image-analysis provider using upstream llama.cpp and a matched Q4_K_M multimodal projector:

```bash
.venv/bin/python scripts/prepare_omni_vision.py --download
# Build/verify the pinned native runtime using the instructions below before launch.
SIGHTVAULT_VISION=omni bash Start-SightVault.command
```

Follow [Omni runtime setup](qa/visual-v2/OMNI_VISION_PROVIDER.md) for the exact source commit, build commands and local verification. Runtime launch never downloads or compiles. GPU execution is reported only with actual offload evidence; otherwise the device is explicitly unverified. Audio/video/TTS remain CLI experiments and are not application features. Windows optional-runtime instructions are unvalidated.

See [Phase 2 model selection](qa/FINAL_MODEL_SELECTION.md) and [unified QA](qa/FINAL_UNIFIED_QA.md) for results, memory, costs and limitations. Bonsai, OvisOCR3 and d1 remain experiment branches/evidence; setup does not download them.

Hybrid search combines semantic similarity and lexical matching. **Visual meaning** uses direct embeddings; **Exact text** uses OCR and filenames. Earliest-match filtering considers the full relevant index before display limits.

Models are loaded from local files. The pinned downloader verifies original revisions and checksums; the converter records provenance and output hashes in each checkpoint's `source.json`. Dependencies are pinned in `requirements-mac.lock.txt`. See [FINAL_MODEL_SELECTION.md](qa/FINAL_MODEL_SELECTION.md) for current precision choices, memory and timing measurements. [MAC_QA_REPORT.md](MAC_QA_REPORT.md) preserves the earlier migration results.

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
