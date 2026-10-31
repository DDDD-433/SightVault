# Mac migration completed

This refreshed package includes the Mac migration. See `MAC_QA_REPORT.md` for actual results and `README.md` for setup and launch. Download pinned original weights and create local MLX conversions using the documented commands; weights and environments remain excluded. The Mac launcher defaults to port 7861. Original handoff instructions below describe the incoming Windows snapshot and are retained as history; the Mac report and README take precedence for this release.

# SightVault: Windows to Mac handoff

This is the current, QA-fixed project, with a consistent snapshot of the 62-image demo index. The Windows app has been exercised with real local models. This package has not been run on a Mac.

## What to move

1. Extract this entire `SightVault` folder on your Mac, ideally to `~/Projects/SightVault`.
2. Transfer the Windows project's **`.models` directory** into this folder. It contains **7,275,820,860 bytes (7.28 GB / 6.78 GiB)** and is excluded from this ZIP. Finder hides dot folders: press **Command+Shift+.** to show them. Use an external drive or your preferred file-transfer method. Do not copy `.venv`, `.runtime`, caches or credentials.
3. Open this folder in your Mac agent environment and give the orchestrator `handoff/ORCHESTRATOR_PROMPT.md`. It contains the full continuation task and delegation rules.

Historical Windows source location (genericized):

```text
C:\path\to\previous\SightVault
```

The original project and its running service have not been changed by this handoff. The database here was copied using SQLite's backup API. It includes only the demo collection, its stored image copies, thumbnails, OCR and embeddings.

## Quick Mac baseline

These steps require a working **native Python 3.12** and internet for dependency installation. On Apple Silicon, avoid an Intel/Rosetta interpreter. Exact Mac model compatibility still needs verification.

```bash
cd "$HOME/Projects/SightVault"
python3.12 handoff/verify_transfer.py
bash Setup-Mac.sh
.venv/bin/python handoff/verify_transfer.py --models
.venv/bin/python handoff/prepare_demo.py
.venv/bin/python handoff/prepare_demo.py --apply
bash Start-SightVault.command
```

Open <http://127.0.0.1:7860/>. The launcher defaults to CPU until MPS is implemented and tested. Setup does not automatically download weights. If you did not transfer `.models`, use `.venv/bin/python handoff/download_pinned_models.py` while online, then verify the files.

`prepare_demo.py` defaults to a dry run. `--apply` backs up the packaged database, changes its Windows source paths to this folder's `samples` paths, and restores sample mtimes from the manifest. It preserves all indexed timeline timestamps, OCR, vectors and stored evidence. Stop the app before applying it. It refuses unknown or altered demo assets.

Dependency installation may expose Mac wheel or native-library gaps. The orchestrator must resolve these on the actual Mac and record its final versions. The Windows package list is evidence, not a Mac lockfile.

## Read in this order

| File | Purpose |
| --- | --- |
| `handoff/ORCHESTRATOR_PROMPT.md` | Copy-paste task for 6.1 Sol at medium/high |
| `handoff/MAC_MIGRATION.md` | Setup, model transfer, data portability, MPS work and verification |
| `handoff/AGENT_TASKS.md` | Luna, AGY, Cursor and OpenCode task boundaries |
| `handoff/PROJECT_STATE.md` | Architecture, current behavior, evidence and unresolved issues |
| `QA_REPORT.md` | Existing Windows QA report |
| `handoff/history/SightVault_Changes.md` | Grok QA changes and remaining improvement list |
| `handoff/history/SightVault_Original.zip` | Pre-QA source reference; do not use as the active project |

The active source is the project at this ZIP's root. `README.md` and the old result JSON files are preserved as historical Windows evidence; migration notes take precedence where the old README contradicts itself about dates.

The original Windows handoff did not establish Mac results. Current Mac validation is recorded in `MAC_QA_REPORT.md`; the older checklist and Windows report remain historical context.
