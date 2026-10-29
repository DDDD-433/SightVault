# SightVault continuation

Read `READ_ME_FIRST.md`, `handoff/ORCHESTRATOR_PROMPT.md`, `handoff/PROJECT_STATE.md` and `handoff/MAC_MIGRATION.md` before changing the project. The root source is the QA-fixed version; the original ZIP under history is for comparisons.

User-requested coordination: 6.1 Sol medium/high orchestrator, luna-max-standard subagents, AGY CLI, Cursor agent CLI and free OpenCode models where actually available. Follow `handoff/AGENT_TASKS.md`; inspect installed command help/model selectors. Do not silently substitute paid models. Delegation is authorized; one owner per file, environment, DB and model-heavy test run.

Preserve local-only inference, direct image embeddings, evidence links, truthful backend/fallback labels and full-corpus earliest-match behavior. Never use benchmark labels to retrieve results. Windows reports are historical evidence; record Mac results separately. The packaged demo has 62 assets; weights and Windows environments are excluded.

Do not regenerate demo images or re-index merely to change paths. Use the validated/backed-up handoff helper. The ingestion QA script recursively deletes its supplied directory, so use only a fresh disposable child. Recreate a native Mac .venv. Keep the Windows baseline and user data intact.

Run tests appropriate to changes and perform real image embedding, OCR and VLM inference before claiming compatibility. Setup scripts and MPS are unverified on Mac until tested there. See the orchestrator prompt for concrete deliverables and acceptance criteria.
