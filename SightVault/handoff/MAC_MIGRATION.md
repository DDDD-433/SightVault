# Mac migration and acceptance checklist

> Historical migration checklist from baseline `766027c`, retained for context. It predates the Phase 2 provider selection and current launch/setup guides. Follow `docs/PORTABLE_SETUP.md` for current setup, and treat the report below as historical unless the current QA report confirms it.

## 1. Inventory the actual Mac

Record `sw_vers`, `uname -m`, `sysctl -n hw.memsize`, Python version/architecture, available disk and installed CLI capabilities. Hardware is not known in this handoff. An Intel Mac needs a CPU baseline; Apple Silicon may support MPS but each model still needs real inference tests. Reserve at least 20 GB free for the transferred models, environment and working copies; more if caching downloads or keeping multiple environments.

Move the folder outside a synced directory while developing if convenient. Do not copy Windows executables or environments. Transfer agent authentication separately through each provider's supported login; this package contains no credentials.

## 2. Model transfer: choose one route

### Copy the installed weights

Copy this Windows folder, including its dot-prefixed name, into the Mac project root:

```text
C:\path\to\SightVault\.models
```

Use a drive/file-transfer tool that supports files above 4 GB; some model files may exceed limits of older filesystems. `Copy-Models-To-Drive.ps1` copies only model files listed in the manifest and verifies each source and destination checksum. It never mirrors or deletes other files. Example from Windows:

```powershell
& 'C:\path\to\extracted\SightVault\handoff\Copy-Models-To-Drive.ps1' -Destination 'E:\SightVault-Transfer' -ProjectRoot 'C:\Users\digvi\OneDrive\Documents\ChatGPT\Vibathon 2'
```

Then copy `SightVault-Transfer/.models` into `~/Projects/SightVault/.models` on Mac. You may instead keep the weights at another stable location and export `SIGHTVAULT_MODELS=/absolute/path/to/.models` before starting. The handoff verifier accepts `--model-root` for that case.

### Download the same revisions on Mac

After installing dependencies, run:

```bash
.venv/bin/python handoff/download_pinned_models.py
.venv/bin/python handoff/verify_transfer.py --models
```

The downloader pins all three revisions and validates every transferred file. Downloading requires internet and any model access/license acceptance requested by the host. Use normal Hugging Face login if needed; never paste secrets into project files or logs. Model licenses remain applicable.

## 3. Verify source, setup, then relocate demo

Before any edits or database rebasing:

```bash
python3.12 handoff/verify_transfer.py
bash Setup-Mac.sh
.venv/bin/python handoff/verify_transfer.py --models
.venv/bin/python handoff/prepare_demo.py
.venv/bin/python handoff/prepare_demo.py --apply
```

The source verifier checks `PACKAGE_MANIFEST.json`. Intentional edits make that immutable snapshot check fail, so run it once on extraction. Model validation remains usable later. ZIP extraction does not preserve all file mtimes accurately; the rebasing helper restores sample mtimes from `SAMPLE_MANIFEST.json`. It checks sample hashes, database rows and stored evidence before any update, and backs up the database before applying changes.

`Setup-Mac.sh` uses the project requirements on standard PyPI, does not use the CUDA wheel index, and checks FTS5 and MPS availability. It has not been run on Mac. If wheel resolution fails, use the orchestrator to inspect the error and official package compatibility; keep the original requirements, write a Mac-specific requirement/lock file and document changes. Do not downgrade model libraries blindly.

Start with:

```bash
SIGHTVAULT_DEVICE=cpu bash Start-SightVault.command
```

For foreground debugging:

```bash
SIGHTVAULT_DEVICE=cpu .venv/bin/python -u app.py --port 7860
```

## 4. Implement MPS on the real machine

Read the installed PyTorch backend documentation first. Source references checked during handoff preparation:

- <https://docs.pytorch.org/docs/stable/notes/mps.html>
- <https://docs.pytorch.org/docs/stable/mps_environment_variables.html>

Add an explicit device policy with CUDA/MPS/CPU validation and truthful fallback/status reporting. Check both `torch.backends.mps.is_built()` and `.is_available()`. Use conservative float32 initially, then consider a supported lower precision only after numerical and retrieval checks. Real tests are required for multimodal embedding, OCR and VLM generation independently.

Operator fallback via `PYTORCH_ENABLE_MPS_FALLBACK=1` is optional and may slow execution. If used, record it and distinguish it from whole-model CPU fallback. Never disable MPS allocation safety limits to make a demo fit. Measure unified memory pressure and keep embedding plus at most one generative model active; use RapidOCR for bulk demo ingestion. Preserve CPU and Windows CUDA behavior.

Fix CUDA-only cleanup/metrics and the offline report's CUDA-or-CPU device label. Check MPS memory APIs against the installed version. Add regression tests for selection/status/fallback using controlled failure conditions, and real model smoke tests for actual hardware correctness. Mocks can verify control flow but cannot establish model compatibility.

## 5. Verify and record Mac results separately

Before scripts that overwrite root result files, preserve the supplied Windows results under `handoff/history/windows-evidence/`. Stop other model-heavy processes so measurements are meaningful.

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/benchmark.py --offline-check --output qa/MAC_QA_RESULTS.json
# Writes root OFFLINE_VERIFICATION.json; preserve Windows evidence first.
.venv/bin/python scripts/verify_offline.py
```

For ingestion, create a disposable directory whose path is safe for the script's recursive deletion:

```bash
QA_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/sightvault-qa.XXXXXX")"
.venv/bin/python scripts/qa_ingestion.py "$QA_PARENT/ingestion-only"
```

For browser QA, use a separate QA environment if dependency isolation helps:

```bash
python3.12 -m venv .qa-venv
.qa-venv/bin/python -m pip install playwright
.qa-venv/bin/python -m playwright install chromium
.qa-venv/bin/python scripts/qa_browser.py http://127.0.0.1:7860 qa/mac-browser
```

`qa_browser.py` has 180-second waits, which may need increasing based on measured CPU latency. `--xss` requires an isolated index containing the marker from ingestion QA, so run that check against a separate server using the ingestion test's data path. Never add the marker to the saved 62-image demo. Preserve CSP behavior in the actual app; the test context's CSP bypass is a test harness detail.

Finish the complete `DEMO.md` flow with real models. Verify evidence images, OCR, exact matches, semantic-only queries, red/blue backpack discrimination, timeline reset, local date boundaries, similar images, analysis, keyboard/modal behavior and a clean restart. Run offline inference after setup; keep loopback allowed. A socket guard is useful evidence but not a firewall-level isolation claim.

Write `MAC_QA_REPORT.md`, exact installed versions, actual hardware, device per model, fallback warnings, cold/warm latency, indexing time, analysis latency, peak/process memory where measurable, and known limitations. Keep Windows evidence separate. A CPU-only working outcome is acceptable if MPS is blocked and the reason is recorded; do not claim acceleration without a real run.

Before refreshing a deliverable, extend `scripts/package.py`: its current explicit root-file list omits the new Mac launch/setup files, `AGENTS.md`, `READ_ME_FIRST.md` and `handoff/`. Include the needed continuation files and Mac evidence deliberately, while excluding environments, weights, transient SQLite journals and private data. Do not assume the old packager preserves this handoff automatically.

## Recovery

The source Windows folder is the known baseline and is untouched. `handoff/history/SightVault_Original.zip` is pre-Grok QA and is for comparisons only. The fixed snapshot at the project root is the correct starting point. Each demo rebase makes a SQLite backup inside `data` before changes; stop the server and restore from the appropriate backup if needed. Never use global text replacement on a SQLite file.
