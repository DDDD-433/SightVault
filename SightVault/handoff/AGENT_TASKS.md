# Agent coordination plan

> Historical coordination plan from the original migration snapshot at baseline `766027c`. It records requested roles and boundaries from that phase and is retained for context. Current setup and model choices are in `docs/PORTABLE_SETUP.md` and the release-selected QA reports.

User preference: 6.1 Sol medium/high orchestrator, luna-max-standard subagents, AGY CLI, Cursor agent CLI and free OpenCode models. These are requested roles, not claims that these tools/models are installed on the Mac. The orchestrator checks actual availability, tool help and model selectors first. Do not invent a command syntax or substitute a paid backend silently.

## Ownership

| Worker | Task | Allowed edits | Acceptance evidence |
| --- | --- | --- | --- |
| 6.1 Sol orchestrator | Own integration, environments, database migration, model runs and final verdict | All integration changes; only writer to active DB/environment | Working demo, reviewed patches, coherent report |
| Luna worker A | Review and implement device selection, dtype, fallback, cleanup and model status | `sightvault/models.py`, narrowly scoped new backend tests | CPU/CUDA behavior preserved; real MPS evidence provided by orchestrator |
| Luna worker B or AGY | Mac setup/launcher portability | `Setup-Mac.sh`, `Start-SightVault.command`, Mac dependency file | Clean native install and restart recorded on actual hardware |
| Cursor agent | Independent QA/source review, regression gaps, timing/report correctness | New narrowly scoped test file or a reviewed patch; no model adapter edits | Reproducible findings with file/line, trigger and expected behavior |
| Free OpenCode model | Documentation consistency, static review or small isolated UI bug | Assigned documentation or one explicitly owned UI file | Compact diff; no unverified pass/performance claims |

Start with two or three workers, not every tool at once. On a 16 GB Mac, budget memory for inference. Let agents review source in parallel while one owner runs the heavy models. Tool count is not a goal; use each only where it adds an independent useful check.

If using git, commit a clean baseline before worker branches/worktrees. Otherwise assign separate output patches or directories and have the orchestrator apply them sequentially. No two workers edit the same file. A shared database is not an inter-agent coordination channel.

## Worker prompt: device adapter

```text
Review SightVault's local model adapter for native macOS CPU and Apple Silicon MPS. Own only sightvault/models.py and a new backend-specific test file. Read handoff/PROJECT_STATE.md and MAC_MIGRATION.md. Propose/implement minimal device selection, dtype, explicit CPU fallback, correct backend memory cleanup and status updates. Preserve CUDA and local_files_only behavior. Do not change model identity or retrieval semantics. Do not install dependencies or run simultaneous large-model inference; request a serialized run from the orchestrator. Return changed files, reasoning, tests actually run and remaining real-hardware checks.
```

## Worker prompt: native setup

```text
Own only Setup-Mac.sh, Start-SightVault.command and an optional clearly named Mac requirements/lock file. Establish native Python 3.12 setup using actual installed tools and package errors. Inspect Mac architecture and avoid Rosetta/CUDA-only wheels. Coordinate all environment mutations with the orchestrator; do not delete an existing .venv. Preserve missing-model diagnostics and localhost launch. Return a small patch and exact real setup/restart results. Do not claim MPS inference based on availability checks.
```

## Worker prompt: independent QA

```text
Independently inspect the QA-fixed SightVault code and migration changes. Do not trust prior pass claims. Focus on timeline full-corpus behavior, date boundaries, status/fallback truthfulness, source-path rebasing, model-switch state, local-origin checks and HTML escaping. Read tests and identify meaningful gaps. You may add one new assigned regression-test file or return a patch; do not edit another worker's model/setup files. Use an isolated DB and fresh disposable test directory. Return severity, file/line, reproduction, expected/actual result and evidence. Coordinate real-model/browser runs with the orchestrator.
```

## Worker prompt: docs/static review

```text
Review README, DEMO and Mac handoff notes against current source. Identify contradictions in date filters, device selection, setup, port/env settings, OCR fallback labels and offline/security claims. Suggest concise corrections backed by source or actual recorded results. Own only the assigned documentation file. Do not invent Mac timings, tests or CLI model flags. Return a compact diff and unresolved questions.
```

## CLI discovery

In the actual Mac shell, use `command -v` for the executables that are installed (`agy`, `cursor-agent`, `agent`, `opencode` may be candidates, not guaranteed names). Read the selected executable's `--help`. Find the documented model-list mechanism before selecting luna-max-standard or a free model. Login through the provider's normal flow if required. Never include login state, API keys, tokens or global agent configuration in the exported project.

For every external worker, pass an absolute project path, exact allowed files, output requirements, a clear stop condition and the rule that the orchestrator owns dependency changes, shared data and model-heavy execution. Record the actual executable/model chosen in the Mac report. If a tool is missing, continue the project with available workers and report the gap.
