# SightVault: what changed and what to do next

QA ran on Oct 29, 2026, on the demo PC (RTX 4070 12 GB, 15.8 GB RAM). Everything ran on the real local models: EmbeddingGemma, MiniCPM-OCR 1.0, MiniCPM-V 4.7 1B, plus the RapidOCR fallback. Nothing was mocked, and no demo results are hard-coded.

`SightVault_Original.zip` is the code as it was before QA. `SightVault_Fixed.zip` is the code now. The originals of every changed file are also kept on the PC in `qa_backups\20261029_174416\`.

## Fixes and improvements

Each fix is checked by a test in `tests/test_regressions.py`, unless it says otherwise.

1. **"First occurrence" got stuck.**
   - **Problem:** after you clicked First occurrence, every later search stayed in oldest-first order.
   - **Impact:** demo step 5 ("red backpack") showed the wrong image first.
   - **Fix:** a new search now goes back to relevance order. That includes the search after "When did this first happen?".
   - **Test:** checked in the browser by `scripts/qa_browser.py`.
2. **The word "first" was too sensitive.**
   - **Problem:** any query containing "first", e.g. "first normal form", switched to oldest-first order.
   - **Impact:** ordinary searches were silently re-ordered.
   - **Fix:** only real "when did this first…" or "earliest/oldest" questions trigger it now.
   - **Test:** 6 example queries.
3. **Date filters used UTC.**
   - **Problem:** the filter cut days at UTC midnight, while the screen shows local time.
   - **Impact:** a screenshot taken in the evening could fall outside its own day.
   - **Fix:** dates now use your local timezone.
   - **Test:** 2 tests.
4. **A crash could corrupt saved images.**
   - **Problem:** images and thumbnails were written in place.
   - **Impact:** an interrupted run could leave a cut-off file that was then reused forever.
   - **Fix:** files are written under a temporary name and swapped in only when complete.
   - **Test:** a test plus a killed-process recovery run.
5. **Renamed or deleted files were ignored without a word.**
   - **Fix:** indexing now warns about them, and the detail view says "original moved or deleted; saved copy shown".
   - **Test:** yes.
6. **Search waited for image analysis.**
   - **Problem:** search and the vision model shared one lock.
   - **Impact:** a search could hang for 15 s or more while an analysis ran.
   - **Fix:** separate locks.
   - **Test:** yes. It fails on the old code.
7. **Running out of GPU memory left the model stuck.**
   - **Fix:** the model is unloaded, the memory is freed, and you get a clear message. Search keeps working.
   - **Test:** yes. It fails on the old code.
8. **Images indexed with OCR off were reprocessed on every run.**
   - **Fix:** they are skipped correctly now.
   - **Test:** yes.
9. **Misleading "OCR MATCH" badge.**
   - **Problem:** the badge showed in Visual-meaning and similar-image results, where OCR text has no effect on ranking.
   - **Fix:** the badge only shows where OCR text affects the ranking.
   - **Test:** yes.
10. **The launcher was fragile.**
    - **Problem:** it waited 10 s and gave no hint when startup failed.
    - **Fix:** it now waits up to 90 s, warns when the port is in use, shows the end of the server log if startup crashes, and recognises an instance that's already running. You can set the port with `SIGHTVAULT_PORT`.
11. **Hardening and housekeeping.**
    - Added the `Cross-Origin-Resource-Policy: same-origin` header.
    - The model-missing messages point to `download_models.py`.
    - The saved benchmark results were out of date (7 cases saved, 8 in the script). They have been regenerated.
12. **More testing.**
    - **Benchmark:** grew from 8 to 17 cases (paraphrase, colour, object, layout and real-photo queries), plus 4 off-topic queries. It also checks that re-computed image embeddings match the stored ones.
    - **New QA scripts:** a real-model ingestion test (`qa_ingestion.py`) and a Playwright browser test (`qa_browser.py`). The browser test includes an HTML/script injection check.

## Results

| Check | Result |
|---|---|
| Automated tests | 39/39 pass (24 before) |
| Search benchmark | 17/17. Every off-topic query scores below every real match (0.561 vs 0.651). |
| Offline check | Passes |
| Ingestion test | Passes: duplicates, corrupt files, unsupported files, edits, renames, empty and missing folders, cancel and resume, crash recovery |
| Browser test | 30/30, plus the injection check 2/2 |
| Local security | Only reachable from this PC. Path tricks, wrong host headers, cross-site requests, oversized queries and huge result counts are all rejected or capped. |

## Measurements

- **Startup and search:**
  - The app starts in 4.4 s.
  - The first search takes 11.6–13.1 s while the model loads.
  - After that, a search takes about 0.15 s, and an exact-text search about 0.01 s.
- **Indexing:** about 4–5 s per image with RapidOCR, about 15 s per image with MiniCPM-OCR.
- **Image analysis:** about 15 s, with peak VRAM 6.2 GB and server memory 2.2 GB (peak 5.0 GB). Each model loads once and is then reused.

## What should be improved next (priority order)

1. **Warm up the models automatically at launch.** This hides the 12 s first search. Until then, do one search before demoing.
2. **Make the environment reproducible.** The .venv relies on an outside base Python and has no pip. A pinned `uv` lock file or an installer script would fix that.
3. **Clean up renamed or deleted files.** Right now a renamed file keeps both its old and new entries. Offer a "remove missing" action, or track files by content hash so renames merge into one entry.
4. **Stop retrying known-bad files.** Remember corrupt files and only retry them when they change.
5. **Add a "re-OCR this image" button,** so one image can be upgraded from RapidOCR to MiniCPM-OCR without re-indexing everything.
6. **Show indexing progress per file, with an estimated finish time.** MiniCPM-OCR is slow (about 15 s per image), so recommend RapidOCR for large folders.
7. **Improve the analysis answers.** The 1B vision model gives short and sometimes vague answers. Offer an optional larger model, or return the OCR text alongside each answer.
8. **Run an offline test at the firewall level.** The current proof blocks the network inside the program; a Windows firewall test needs admin.
9. **Replace the demo dataset dates.** The generated samples have made-up dates. Use real screenshots, with EXIF or file dates, for a credible timeline.
10. **Small items:**
    - Date-filter presets (today / this week).
    - Keyboard navigation in results.
    - A dark-mode check in the browser test.