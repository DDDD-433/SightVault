# Copy this prompt into the Mac orchestrator

> Historical task prompt from the original migration snapshot at baseline `766027c`. It is retained as context and is not the active task instruction. Its model, setup and QA status may be stale. Use `docs/PORTABLE_SETUP.md` and the release-selected QA reports for current guidance.

You are taking over SightVault after a Windows-to-Mac transfer. Work in the extracted project folder. Act as the primary orchestrator using **6.1 Sol at medium/high reasoning**, as available in this environment. Use **luna-max-standard** for delegated subagents when that exact selector is available. Also use the installed **AGY CLI**, **Cursor agent CLI**, and **free models in OpenCode CLI** for bounded parallel work when useful. Inspect actual executable help and model listings before invoking them; model names and flags are not assumed. If a requested selector/tool is unavailable, report it and continue useful local work without silently substituting a paid model. You are authorized to delegate and to implement/test the migration.

Goal: leave this actual Mac with a working local SightVault app, a reliable Mac launcher and reproducible setup, verified real model inference, truthful CPU/MPS status, and a complete demo. Carry the goal through implementation and QA; do not stop at a plan or compatibility advice.

Start by reading:

1. `READ_ME_FIRST.md`
2. `handoff/PROJECT_STATE.md`
3. `handoff/MAC_MIGRATION.md`
4. `handoff/AGENT_TASKS.md`
5. `QA_REPORT.md`, `handoff/history/SightVault_Changes.md`, `README.md`, `DEMO.md`
6. `sightvault/models.py`, `config.py`, `database.py`, `ingestion.py`, `search.py`, `server.py` and the test scripts.

Facts you must preserve:

- This is the QA-fixed snapshot. The pre-QA ZIP under handoff/history is a historical reference, not the active app.
- Python/FastAPI/vanilla JS/SQLite + FTS5. Localhost port 7860 by default; no cloud inference dependency.
- Models: EmbeddingGemma 2 directly embeds images and text at 768 dimensions; MiniCPM-OCR 1.0, RapidOCR local fallback; MiniCPM-V 4.7 1B for on-demand analysis. Keep their exact identities/revisions for the migration baseline. The model manifest pins the existing weights. Do not replace vision embeddings with caption-only search.
- The supplied Windows reports say 39 tests, 17 retrieval cases, 30 browser checks plus two escaping checks passed. These are prior machine results, not Mac evidence. The handoff verification file documents the packaging-time checks separately.
- The ZIP contains a consistent 62-image demo database, stored images, thumbnails, OCR and vectors. It excludes about 7.28 GB of model weights and Windows runtime/environment directories. Transfer `.models` or use the pinned downloader; no secrets are included.
- On the current source, automatic device selection is CUDA/CPU only. An explicit MPS setting exists through the environment but has not been validated. No Mac performance or model compatibility has been proven.
- Source `filepath`/`folder` entries need rebasing on the Mac. Use the dry-run/backed-up `handoff/prepare_demo.py`, which verifies hashes and preserves indexed timestamps. Do not re-index the whole demo merely to fix paths.
- Sixty samples and their dates are synthetic; two sample photos have recorded sources. "First occurrence" means earliest matching indexed file, not actual event history. Similarity values are not confidence percentages.

Execution:

1. Verify the extracted package before modifying it. Inventory actual Mac hardware, native interpreter architecture, memory, disk and agent tools. Preserve Windows reports under history before scripts overwrite root result JSONs. Use local git checkpoints if available and appropriate, without changing the original Windows workspace.
2. Establish a clean native Python 3.12 CPU baseline. The included setup and launcher are starting scaffolds and have not been run on Mac. Never copy the Windows .venv or install the entire Windows distribution inventory. Resolve native wheel/library failures from actual errors. Preserve original requirements and record Mac-specific versions/lockfile if changes are needed.
3. Verify transferred weights against MODEL_MANIFEST.json or download those pinned revisions. Run the demo relocation dry run and apply only to this verified demo snapshot. Confirm 62 rows/vectors and accessible source/evidence paths.
4. Implement and validate Apple Silicon MPS where the hardware supports it. Test each model independently with real pixels. Use conservative dtype selection and handle unsupported operations, OOM cleanup and CPU fallback explicitly. Preserve Windows CUDA support. Fix CUDA-only memory/report code. Keep fallback device labels truthful; an available backend is not a successful inference result.
5. Delegate bounded tasks using the ownership plan. One owner edits each file. Do not let multiple agents install into the same environment, mutate the same SQLite DB, operate the same browser session or run simultaneous model-heavy QA. External CLI agents must return small reviewable changes and concrete commands/results. The orchestrator reviews and integrates them.
6. Re-run unit/regression checks and the real retrieval, OCR, VLM, ingestion and browser flows. Use disposable test DBs/directories. The ingestion QA script recursively deletes its supplied directory; verify it is a disposable child before running. Preserve the canonical 62-image demo. Include a normal CSP-enabled browser session as well as harness checks.
7. Verify offline runtime after setup with loopback allowed; record the actual isolation method and device per model. Do not claim a firewall test if only the socket guard ran. Adjust harness timeouts for measured CPU/model latency rather than removing meaningful assertions.
8. Leave the app running, perform the full demo, and write Mac-specific QA and setup instructions. Include exact installed versions, hardware, model revisions, fallback status, cold/warm timings, memory metrics where available and remaining blockers.

Priorities: preserve correctness and evidence first; reliable launch second; useful acceleration third; cosmetic/product improvements after migration succeeds. Do not broaden this into a rewrite, hosted app or cloud model migration. Never hard-code expected search results, use manifest labels for retrieval, or claim model verification from mocked tests. Small mocked regression tests are appropriate for failure handling only.

If MPS is not viable, deliver a tested CPU working path, describe the precise failed operator/model/version and keep an optional future MPS task. If an Intel Mac is detected, prioritize CPU correctness and measurements.

Deliverables:

- Working project on this Mac and tested `Start-SightVault.command`.
- Reproducible native setup/requirements or lockfile, without credentials.
- `MAC_QA_REPORT.md` and new Mac benchmark/offline/browser outputs, separate from Windows evidence.
- Tests for meaningful migration fixes; real model inference evidence for all three models.
- Updated README/DEMO with consistent date semantics and actual device behavior.
- Refreshed source/demo ZIP excluding `.venv`, `.qa-venv`, model weights, temporary caches, private images and secrets.
- Extend the old explicit-list packager so the refreshed ZIP retains Mac launch/setup files and the relevant handoff documentation and results.
- Short final verdict: what works, which device each model actually uses, how to launch, what remains blocked.

Proceed autonomously within this scope. Ask only when a necessary decision would destroy user data or materially change the goal. Give brief progress updates with findings and remaining uncertainty.
