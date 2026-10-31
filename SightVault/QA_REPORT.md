# SightVault QA Report — 2026-10-29

> Historical Windows QA report. Its model runs and measurements describe the Windows baseline only; they are not current Mac or Windows validation of this portable source-and-setup bundle. See `MAC_QA_REPORT.md` for the Mac migration record.

**Verdict: demo-ready.** Real local inference throughout (EmbeddingGemma, MiniCPM-OCR 1.0, MiniCPM-V 4.7 1B, RapidOCR fallback); no mocks in the app path. Originals of every changed file: `qa_backups\20261029_174416\` (incl. DB backup).

## Results
| Area | Result |
|---|---|
| Unit/regression tests | **39/39 pass** (`.venv\Scripts\python.exe -m pytest tests -q`), baseline was 24 |
| Retrieval benchmark (`scripts\benchmark.py`, offline socket guard) | **17/17** cases; 4 negatives all score below every positive (max neg 0.561 < min pos 0.651); fresh-vs-stored image-embedding cosine 1.0 |
| Offline verification (`scripts\verify_offline.py`) | pass; MiniCPM-OCR 21.0 s, analysis 11.6 s, peak GPU alloc 3.99 GiB |
| Ingestion QA (`scripts\qa_ingestion.py`, isolated DB) | pass — see `qa\ingestion_qa.json` |
| Browser QA (Playwright, `scripts\qa_browser.py`) | **30/30** + XSS check 2/2 (escaped in card and dialog); screenshots in `qa\screenshots` |
| Security probes | binds 127.0.0.1 only; path traversal 404; bad Host 400; foreign/`null` Origin POST 403; oversized query 422; top_k clamped; CSP, nosniff, no-referrer, CORP same-origin |

## Measurements (RTX 4070 12 GB, 15.8 GB RAM)
- Launcher cold start 4.4 s; first semantic query 11.6–13.1 s (model load) — **warm up before demo**; warm semantic 125–165 ms; exact 8–10 ms.
- Ingestion: ~4–5 s/image with RapidOCR (10 files 39–48 s); MiniCPM-OCR ~15 s/image.
- UI evidence analysis 14.5–15.1 s cold; peak VRAM 6.15 GB; server RSS 2.2 GB (peak 5.0 GB); each model loaded once and reused.

## Issues found and fixed
1. **First-occurrence sort stuck** — later searches stayed oldest-first and broke DEMO step 5 (red backpack). Fresh searches now reset to relevance (also after the contextual "When did this first happen?" query).
2. **Over-broad temporal trigger** — any query with "first" (e.g. "first normal form") switched to oldest-first. Tight intent regex.
3. **Date filters in UTC while UI shows local time** — now local-timezone day bounds.
4. **Non-atomic image/thumbnail writes** — crash could leave truncated copies reused forever; now write `.partial` + `os.replace`.
5. **Renamed/deleted sources silent** — run reports `missing` + warning; detail view flags "original moved or deleted".
6. **Search blocked behind generation** — split embed/generation/RapidOCR locks.
7. **GPU OOM left model wedged** — unloads model, frees cache, actionable error.
8. **OCR-off items reprocessed every run** — skipped correctly now.
9. **"OCR MATCH" badge in Visual-meaning/similar mode** although OCR didn't affect ranking — hidden there.
10. **Fragile launcher** (10 s wait, no diagnostics) — port check, 90 s wait, early-exit + log tail, "already running".
11. Missing CORP header; stale benchmark results (7 vs 8 cases) — fixed/regenerated, benchmark extended to 17 + negatives.

## Remaining limitations
- `.venv` depends on an external base Python runtime and has no pip; do not move/rebuild before the demo.
- Demo sample timestamps are generated (fictional); real files use EXIF/file mtime.
- Corrupt images are retried (and reported) on every run; renamed files keep both old and new entries; no UI "force re-OCR".
- Offline proof uses an in-process socket guard; a Windows firewall block was not applied (needs admin).
- 1B VLM answers are short and can be imprecise; UI labels guesses separately and cites the source.
