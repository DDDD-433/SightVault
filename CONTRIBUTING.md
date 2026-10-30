# Contributing

The application lives in `SightVault/`. Follow its [setup guide](SightVault/README.md) to install the native environment and models.

## Make a change

Create a branch from the latest `main`, keep the change focused, and open a pull request describing the problem, resulting behavior and verification.

```bash
git switch main
git pull --ff-only
git switch -c your-name/short-description
cd SightVault
.venv/bin/python -m pytest tests -q
```

Unit tests use disposable data and model stubs. Changes to model adapters, image processing or ranking also need the appropriate real-model checks; mocked tests do not establish runtime compatibility.

## Preserve the core behavior

- Keep inference local and model/device/fallback labels accurate.
- Preserve direct image embeddings, model identities and source evidence.
- Evaluate earliest matches across the full relevant corpus before display limits.
- Keep benchmark labels out of retrieval and ingestion.
- Keep historical Windows results separate from current Mac evidence.

Do not commit weights, environments, personal indexes, credentials or raw agent transcripts. Test destructive ingestion flows only in a fresh disposable child directory. Keep the prepared demo unchanged when using it to reproduce benchmarks.

See [AGENTS.md](SightVault/AGENTS.md) for repository instructions and the [final QA report](SightVault/qa/independent-review/FINAL_QA_REPORT.md) for known limitations and verification evidence.
