# Additional QA pass

Codex remains the overall orchestrator. The requested Opus 5.5 high lead and one Sonnet 5.5 xhigh subagent have NOT run. Claude Code 2.1.312 was installed and authenticated using API-key billing; the probe returned HTTP 400, “Credit balance is too low.” The user's $100 subscription is not being declared unfunded: the CLI needs subscription authentication via `claude auth login --claudeai`. The exact model definitions, ownership boundaries and task are prepared in this directory. No other Claude model was substituted and no top-up or account change was made.

While authentication is pending, Codex reproduced and fixed five confirmed defect families with nine regression cases. All nine failed before their corresponding fixes. No canonical demo records, images, timestamps, vectors or model weights were changed.

| Finding | Trigger and observed behavior | Fix |
| --- | --- | --- |
| P2: date overflow | `end=9999-12-31`, or a year-one timestamp with positive offset, returned HTTP 500 | Convert date arithmetic/UTC overflow into the existing validation error / HTTP 422 |
| P2: stale embedding status | One transient encode failure followed by a successful retry left the cached model labeled unloaded and its old error visible | Restore actual device and clear stale error after valid inference; retain explicit CPU fallback warnings |
| P2: zero vectors | A zero query vector was accepted and silently ranked every image with meaningless zero similarity | Reject zero/invalid norms as ModelUnavailable |
| P2: incompatible similarity source | Similar-image search accepted a source vector from a different model identity when dimensions happened to match | Require the configured embedding identity on the source, as already required on candidate vectors |
| P2: SQLite ID overflow | Oversized numeric IDs crashed asset detail, image, similar-image and analysis routes with HTTP 500 | Treat out-of-range signed-64-bit IDs as nonexistent before binding SQLite parameters |

Tests: `.venv/bin/python -m pytest tests -q` → 53 passed. Reproduction failures are in `adversarial-before.log` and `id-boundaries-before.log`; final results are in `tests-after.log`. Mocked adapters exercise numerical/failure control flow only; they are not evidence of hardware compatibility.

Real-model retrieval: `.venv/bin/python scripts/benchmark.py --offline-check --output qa/independent-review/retrieval.json` → 17/17 with negatives below positives and direct-pixel vector agreement of 0.9999/1.0000/1.0000. The in-process socket guard was used, not an OS firewall.

Browser: `.venv/bin/python scripts/qa_browser.py http://127.0.0.1:7861 output/playwright/independent-review` → 30/30, including real VLM analysis, first occurrence/reset, date filters, responsive layout, evidence and reindex skipping all 62 files. Harness CSP bypass remains explicit; the prior normal-CSP evidence is separate. This pass did not change frontend code.

Concurrency is measured by `scripts/qa_concurrency.py`: 40 bounded live search/status requests during real VLM analysis, with search expected to complete while generation is still running. See `concurrency.json` for the actual result and timings.

The app is left running on localhost:7861 with the fixes loaded. The original migration measurements and limitations remain in MAC_QA_REPORT.md. This is a Codex QA result; the requested Claude independent review remains pending subscription login.
