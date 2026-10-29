# Independent Opus QA report — SightVault (2026-10-29)

Reviewer: Claude Opus 5.5 (QA lead, parent session `CLAUDE_RUN.json`). One `sonnet-qa` subagent was spawned from the supplied definition. Codex owns all fixes.
Baseline: commit `b47d931`. Historical pass claims were not relied on. Every finding below was reproduced in this session.

## Evidence commands

```bash
.venv/bin/python -m pytest -q tests/test_core.py tests/test_regressions.py tests/test_mac_backends.py tests/test_adversarial_qa.py   # 53 passed
.venv/bin/python -m pytest -q -rxX tests/test_claude_opus_qa.py                 # 9 passed, 12 xfailed (strict)
.venv/bin/python -m pytest -q --runxfail tests/test_claude_opus_qa.py           # 12 failed, 9 passed: every xfail fails on its own assertion
```

Each confirmed bug has a `xfail(strict=True)` test in `tests/test_claude_opus_qa.py`. A fix makes the test XPASS, which fails the run. Remove the marker in the same change. No benchmark, smoke, offline, ingestion-QA, conversion or real-model inference was run.

## Confirmed bugs (Opus)

| # | Sev | Location | Trigger → observed / expected | Test |
|---|-----|----------|-------------------------------|------|
| O1 | **High** | `sightvault/search.py:77-85` | Terms and literals use substring `in`. `earliest cat` with an older OCR row "Application error" (semantic 0.0) and a newer true match (semantic 1.0) → returns the "Application" screenshot as the earliest match, with an **OCR MATCH** badge. In relevance mode, `cat` also ranks it first. **Expected:** whole-token matching for word scripts, so `exact_match` is false and the earliest result is the cat image. | `test_earliest_match_does_not_match_inside_longer_words` |
| O2 | **High** | `sightvault/search.py:84-85,114-116` | Same root cause, but with error codes. `When did E404 first appear?` (hybrid) and `E404` in exact mode sorted oldest → returns `[E4040 row, E404 row]`. The older **E4040** screenshot is reported as the first occurrence of E404. **Expected:** only `E404`. This is the product's headline use case. | `test_earliest_error_code_is_not_a_prefix_of_a_longer_code[×2]` |
| O3 | Medium | `sightvault/search.py:8` | `earliest` is a stopword but `oldest` is not. Both phrasings trigger temporal sort, but `oldest npm error` keeps `oldest` as a term that must appear in the OCR. That defeats `exact`, so it returns a different image (`[2]`) than `earliest npm error` (`[1]`). **Expected:** the same result. Also consider `appear`, `see`, `saw`, `time`, which have the same effect. | `test_oldest_and_earliest_wording_agree` |
| O4 | Medium | `sightvault/search.py:11,74-81` | There is no Unicode normalization. An NFC query `café` against an NFD filename or NFD OCR text (macOS file APIs commonly yield NFD) → `total_matches 0` in exact mode. FTS5 matches it (unicode61 strips diacritics), but the Python term filter discards the row. **Expected:** 1 match. Fix: NFC (or NFKC) normalize the query, OCR text and filename before casefolding. | `test_nfc_query_matches_nfd_text[filename,ocr_text]` |
| O5 | Low-Med | `sightvault/search.py:11` | `len(t)>1` drops single-character terms. CJK single-character words (`猫`, cat) return 0 in exact mode even when the OCR contains `猫の写真`. **Expected:** 1. Fix: keep length-1 terms for non-ASCII word characters. Note that O1's word-boundary fix must keep substring matching for unspaced CJK text. | `test_single_character_cjk_exact_query` |
| O6 | Medium | `sightvault/metadata.py:11-13` | `image.getexif()` returns IFD0 only. DateTimeOriginal (36867) and OffsetTimeOriginal (36881) live in the Exif sub-IFD (0x8769), where cameras and iPhones write them. A JPEG with both values in the Exif IFD → timestamp = file mtime, source "File modified time". **Expected:** `2019-06-15T10:00:00+00:00`, "EXIF capture time (with timezone)". The EXIF branch is effectively dead code for real photos. Timeline and earliest-match dates silently use copy/download mtimes. Fix: `exif.get_ifd(ExifTags.IFD.Exif)`, and also OffsetTime 36880 for tag 306. | `test_exif_capture_time_from_exif_ifd_is_used` |
| O7 | Low | `sightvault/models.py:153-154,182-184` | After MPS→CPU fallback, the label is `cpu (backend fallback)`. One transient query error pops `active['embedding']`. The next success sets `cpu` and clears the error, so status no longer discloses that the requested MPS backend failed. This conflicts with the "truthful fallback labels" requirement. **Expected:** `cpu (backend fallback)` persists (e.g. a sticky `_fallback` flag). | `test_backend_fallback_label_survives_transient_error` |
| O8 | Low | `sightvault/models.py:216-227` | Only `torch.cuda.OutOfMemoryError` unloads the generative model. On the PyTorch MPS path (auto/mps when the MLX conversions are absent), an `MPS backend out of memory` RuntimeError leaves the `_vlm` weights resident. `analyze` pops only the label and does not clear the device cache. Each later request repeats the failure under memory pressure, with a non-actionable message. **Expected:** unload plus the same actionable message as CUDA. | `test_mps_oom_unloads_generative_model` |
| O9 | **High (privacy)** | `scripts/package.py:9` | The QA-evidence glob includes every `qa/**/*.log/.json/.md`. With the current tree it would ship `qa/independent-review/claude-stream.log` (a 634 KB live agent transcript containing the account email address) and `CLAUDE_RUN.json` (auth method, subscription, session id) in the public demo ZIP. `*.log` is git-ignored, but package.py walks the filesystem. **Expected:** an allowlist of QA evidence, or an explicit exclusion of agent transcripts and run metadata. Also consider scrubbing `/Users/<name>` paths in `qa/*.json`/logs. Detected statically: the test evaluates only the selection expression and never runs the packager. | `test_package_excludes_agent_transcripts_and_account_metadata` |
| O10 | Low | `sightvault/web/app.js:50` | `new Date(y,m-1,d)` maps years 0–99 to 1900–1999. A date filter of `0050-03-01` searches 1950. Verified by running the exact helper from app.js in node. Fix: `const t=new Date(0); t.setFullYear(y,m-1,d+addDays); t.setHours(0,0,0,0)`. | `test_ui_two_digit_years_are_not_remapped` |

## Verified correct (passing tests in `tests/test_claude_opus_qa.py`)

- **Earliest match across the whole corpus:** 150 rows, `top_k=500` clamped to 100 → `total_matches` equals all 22 matches, and the first result is the true global oldest.
- **DST:** the UI day bounds in America/New_York for 2026-03-08 are `05:00Z` to the next day's `04:00Z` (a 23-hour local day). The server treats offset instants as UTC and is correct.
- **Codex b47d931 fixes, extended:** `9999-12-31T23:59:59-01:00`, `0001-01-01T00:00:00+00:01`, `similar_id=-10**30` and a mixed date/date-time pair all return 422/404. Negative oversized asset ids on `/api/assets/{id}` and `/image` return 404/422. The zero-norm and non-finite rejections and the recovered-status logic (`test_adversarial_qa.py`) are correct for the non-fallback path; see O7 for the fallback gap.
- **MLX precision label:** `mixed affine 4-bit / BF16` matches `.models/*-mlx-4bit/config.json` (`bits 4, group_size 64, mode affine`) and `prepare_mac_models.py` (`dtype bfloat16`).
- **XSS:** all API strings rendered by app.js go through `escapeHtml` or `textContent`. Numeric fields are numbers.

## Suggestions (not defects)

- `models.py:_encode` does not validate the embedding width. A 512-d output is accepted and stored under `EMBEDDING_ID …:768` (`test_wrong_dimension_embedding_is_accepted_without_validation` documents this). Reject `value.shape[-1] != 768`.
- `search.py:_instant` truncates fractional seconds on an **exclusive** end bound (`…15:00:00.5Z` → `15:00:00`), which excludes a row stamped 15:00:00. The UI always sends `.000`, so this only affects API callers. Ceil the end bound instead.
- `search.py:121`: `r['semantic_score'] or -1` treats a score of exactly 0.0 as −1.
- Smart quotes (`“disk full”`) are not treated as phrase/literal delimiters.
- `search.py:62`: when `similar_id` points to a row from a different model, the message says "no semantic embedding". It should say "indexed with a different model".
- A corrupt embedding blob (length not a multiple of 4) raises in `decode_vector` and fails the whole search with a numpy message as a 422. Skip the bad row instead.
- UI: "More results", sort, mode and date changes re-read the **unsubmitted** query box (`app.js:35`), so editing the query and then clicking "More results" silently runs a different query. Also, `cancel-job` has no catch, and a transient poll error re-enables Index while the job is still running (`app.js:92`); the server's 409 is the only guard.

## Unverified hypotheses (need real models or a browser)

- `_generate_mlx` passes a PIL image list to `mlx_vlm.generate(image=[image])`. Whether mlx-vlm 0.7.11 accepts PIL objects, rather than paths, for MiniCPM-OCR/V needs a real-model check.
- `openDetail` has no request serial. Rapid clicks on two cards can leave the dialog showing the first card if its response arrives last.
- Cosine agreement between MPS and CPU EmbeddingGemma vectors, for the same image and query, has not been verified numerically.

## Requested serialized checks (Codex)

1. Real model: run `smoke_models.py` with an EXIF JPEG that has DateTimeOriginal and OffsetTimeOriginal in the Exif IFD. Confirm `timestamp_source` after the O6 fix.
2. Real model: compare EmbeddingGemma image and query embeddings on MPS against CPU (`SIGHTVAULT_DEVICE=cpu`) for 5 demo images. Report max |Δcos| and confirm the top-5 for 3 demo queries is unchanged.
3. Real model: run one MLX OCR and one MLX VLM call through `Models.ocr`/`Models.analyze` (not just the smoke script). Then force the failure path by temporarily pointing `SIGHTVAULT_MODELS` at a copy with a truncated MLX safetensors. Verify the RapidOCR fallback warning and the status label, and that the next request recovers.
4. Retrieval: after the O1/O2 fixes, re-run `benchmark.py` and the retrieval QA on the 62-asset demo. Confirm ERESOLVE and the other code queries still return the same earliest match, and that CJK and partial-word recall did not regress.
5. Browser: quickly double-click two different result cards (dialog race); edit the query and then click "More results"; set the date filter year `0050`; run a DST-day filter with the browser in America/New_York.
6. Packaging: after the O9 fix, run `package.py` into a temporary copy and list the ZIP for `*.log`, `CLAUDE_*` and `/Users/` strings.

## Sonnet subagent findings (integrated)

The single `sonnet-qa` subagent was stopped by the system when an earlier headless parent exited. I resumed the same agent ID with SendMessage; no second agent was created. Its scope was ingestion, the DB, metadata/dates and API validation. Full detail is in `qa/independent-review/SONNET_REPORT.md`; tests are in `tests/test_claude_sonnet_qa.py`.

**Execution settings.** The runtime metadata I can see does not report the subagent's resolved model. What I can show: the `sonnet-qa` definition (`claude-agents.json`: `claude-sonnet-5-5`, effort `xhigh`) and the run record (`CLAUDE_RUN.json`: `subagent_model claude-sonnet-5-5`, `subagent_effort xhigh`, `subagent_limit 1`). I did not pass a model or effort override.

**Opus re-verification.** `pytest -q -rxX tests/test_claude_sonnet_qa.py` → 23 passed, 27 xfailed. With `--runxfail`: 27 failed, 23 passed. I inspected every failure line: each fails on its stated assertion or declared exception (S-2's `OverflowError`), and none is a fixture or setup error.

| # | Sev | Location | Finding | Test(s) |
|---|-----|----------|---------|---------|
| S-1 | Medium | `metadata.py:11-13` | **Same as O6**, found independently. EXIF capture time is read from IFD0 only (verified for JPG, PNG and WebP). | `test_exif_capture_time_in_exif_subifd_is_used[jpg,png,webp]` |
| S-2 | Low, rising to Medium once O6/S-1 is fixed | `metadata.py:16-18` | An EXIF year of 0001 or 9999 with an offset raises an uncaught `OverflowError`, and the file fails to index. The fix for O6 must also catch `OverflowError` and fall back to mtime. | `test_extreme_exif_dates_fall_back_to_mtime`, `test_extreme_exif_date_does_not_block_indexing` |
| S-3 | Medium | `ingestion.py:59-63` | The unchanged-file skip does not check that the stored copy and thumbnail exist. "Re-index" (the advice in the `server.py:88` 404 message) never repairs them. | `test_reindex_restores_missing_stored_copies` |
| S-4 | Low-Med | `ingestion.py:33-36` | Concurrent writers share one `<digest>.partial` temp name. Two in-process ingestion runs got spurious `FileNotFoundError`s, about 17 to 23 of 40 files. The server allows only one job at a time, so this affects the CLI and other concurrent paths. No corruption was observed. | `test_atomic_save_tolerates_two_concurrent_writers`, `test_two_concurrent_ingestion_runs_have_no_spurious_failures` |
| S-5 | Low | `ingestion.py:33-36` | A failed save leaves a `.partial` file behind. | `test_failed_write_does_not_leave_partial_file` |
| S-6 | Low | `ingestion.py:70-77` | Editing a file orphans the old content-addressed copy and thumbnail. Codex reports this as its one deliberate storage-retention limitation. | `test_editing_a_file_does_not_orphan_old_stored_copies` |
| S-7 | Low | `ingestion.py:79-87` | A vector reused from the hash cache is still tagged with `embedding_error` after an earlier embedding failure in the same run. | `test_reused_embedding_is_not_tagged_with_an_embedding_error` |
| S-8 | Low-Med | `ingestion.py:78-100` | `force=True` while the models are down overwrites a good vector and OCR text with empty values and counts the file as "updated". | `test_forced_refresh_with_models_down_does_not_destroy_existing_data` |
| S-9 | Low-Med | `ingestion.py:14`, `database.py:15` | On case-insensitive APFS, indexing the same folder with different letter case creates duplicate rows for the same files. | `test_case_variant_folder_spelling_does_not_duplicate_rows` |
| S-10 | Low | `database.py:122-126` | `encode_vector`'s float32 norm overflows to inf, so a huge-magnitude vector is stored as all zeros. `_encode` normalizes first, so the shipped path is not affected. | `test_encode_vector_never_stores_a_zero_vector[×2]` |
| S-11 | Low | `ingestion.py:14-15`, `server.py:98` | `/api/index` returns 500 instead of 422 for `~unknownuser/x`, an over-long name component, or a folder under an unreadable directory. | `test_index_rejects_unusable_folder_values_with_422[×2]`, `test_index_rejects_folder_inside_unreadable_directory_with_422` |
| S-12 | Low | `server.py:138-145` | `/api/analyze` with a missing stored image returns 503 and leaks the absolute data path. Expected: 404 or 410 with "Re-index". | `test_analyze_with_missing_stored_image_is_actionable_and_does_not_leak_paths` |
| S-13 | Medium | `ingestion.py:68` | `convert('RGB')` discards alpha. A transparent PNG or WebP with dark ink becomes a flat black image for storage, OCR and embedding. | `test_transparent_images_are_flattened_onto_a_visible_background[RGBA,LA]` |
| S-14 | Low | `ingestion.py:20` | `os.walk` silently skips an unreadable subfolder (for example a macOS TCC-protected one) with no warning. | `test_unreadable_subfolder_is_reported_not_silently_skipped` |

**Sonnet suggestions (xfail-documented):**
- A tampered `image_path` row can serve files outside `data/`. This needs DB write access, so it is defense in depth.
- One failing image disables embeddings for the rest of the run (`blocked_embedding`).
- `encode_vector` flattens a `(2, D)` batch into one vector.
- One corrupt blob breaks every search. This matches my suggestion on the same point.

**Untested:** `testserver` is in the production host allow-list, and `Config` reads env vars at import time.

**Sonnet verified OK:**
- Cancel, resume, and the 409 for a second job.
- A crash between inference and the DB write leaves no partial row.
- Duplicate files share one copy and one embedding.
- EXIF orientation is applied.
- Epoch, pre-epoch and DST date bounds.
- Path traversal, Origin and Host guards.
- Concurrent DB writes, with FTS and table integrity clean.

**Needs real files:** iPhone/camera EXIF (the fixtures were written by Pillow), TCC-protected folders, and retrieval impact on transparent screenshots.

## Combined result

```bash
.venv/bin/python -m pytest -q tests/test_core.py tests/test_regressions.py tests/test_mac_backends.py \
  tests/test_adversarial_qa.py tests/test_claude_opus_qa.py tests/test_claude_sonnet_qa.py -p no:cacheprovider
# 85 passed, 39 xfailed in 6.75s   (53 baseline + 9 Opus + 23 Sonnet passes; 12 Opus + 27 Sonnet strict xfails)
```

This run is against unchanged source `b47d931`. Codex reports that a separate staged fix set passes 123 tests. I did not inspect the staging directory and have not verified that claim.

**Priority for Codex:**
1. O9: packaging privacy leak.
2. O1/O2: wrong earliest match from substring matching.
3. O6/S-1 together with S-2: EXIF sub-IFD read, with an overflow guard.
4. S-13: alpha flattening.
5. S-3: re-index repair.
6. O3, O4, S-8, S-9.
7. The rest.

The new real-model and browser checks are in the requested-checks list above. Add these:
7. Real iPhone HEIC→JPEG and camera JPEG EXIF timestamps.
8. A transparent-PNG OCR and embedding comparison before and after S-13.
9. Index the demo folder with different path letter case through the UI.
