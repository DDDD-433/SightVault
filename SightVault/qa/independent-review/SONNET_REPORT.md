# SightVault independent QA: Claude Sonnet 5.5 (xhigh) subagent

Scope: ingestion, database layer, metadata/date handling, `server.py` API boundary. Production source was at commit `b47d931` and was not modified. Every test uses `tmp_path` databases and stub models. No real model, browser, `data/`, `.models/`, `samples/`, canonical DB, benchmark/smoke/offline/ingestion-QA script or app server was used.

All evidence is in `tests/test_claude_sonnet_qa.py`. Mocks only exercise control flow and say nothing about hardware or model quality.

## Test run

```
.venv/bin/python -m pytest -q -rxX tests/test_claude_sonnet_qa.py -p no:cacheprovider
23 passed, 27 xfailed in 2.77s
```

- Tests marked `xfail(strict=True)` fail today on current source. They turn into hard XPASS failures once the defect is fixed, which tells the fixer to delete the marker. Each marker uses `raises=` so an unrelated error cannot masquerade as the finding.
- Failing view: `.venv/bin/python -m pytest -q --runxfail -p no:cacheprovider tests/test_claude_sonnet_qa.py` gives `27 failed, 23 passed`. I read every failure message; each fails for the stated reason, not because of a fixture error.
- The 23 unmarked passing tests are controls for verified-correct behavior.
- Whole suite at the time: `.venv/bin/python -m pytest -q tests -p no:cacheprovider` gave `85 passed, 39 xfailed` (this includes Opus's concurrently added file). Baseline before my file: 53 passed.
- Stability: the file was run repeatedly (more than five times) with identical results. The two concurrency tests use a `threading.Barrier` rendezvous instead of timing, so they are deterministic.

Reproduce one finding: `.venv/bin/python -m pytest -q --runxfail -p no:cacheprovider "tests/test_claude_sonnet_qa.py::<test name>"`.

## 1. Confirmed bugs

Severity scale: Medium means wrong or lost data or a documented feature not delivered on normal inputs. Low means an edge case, a hygiene issue, or one with a workaround. No High finding was found.

### S-1 (Medium) EXIF capture time stored in the Exif sub-IFD is never used

- **Where:** `sightvault/metadata.py:11-13` (`exif.get(36867) or exif.get(306)`, `exif.get(36881)`).
- **Trigger:** any standards-compliant photo. Cameras and phones store DateTimeOriginal (36867) and OffsetTimeOriginal (36881) in the Exif sub-IFD (0x8769). `Image.getexif()` returns IFD0 only, so both lookups return `None`.
- **Observed:** `timestamp_source == 'File modified time'` and the mtime is used, for JPEG, PNG and WebP carrying a valid `2020:01:02 03:04:05` plus `+05:30`. Only non-standard files with the tags in IFD0 are honoured.
- **Expected** (`README.md:70`: "EXIF capture dates are used only with an explicit timezone"): `2020-01-01T21:34:05+00:00` with source `EXIF capture time (with timezone)`.
- **Impact:** for photos copied from a phone or camera, mtime is the copy time. The date filter and "first occurrence / earliest" ordering then use the wrong date. The label stays truthful, so this is not deceptive.
- **Fix guidance:** read `exif.get_ifd(ExifTags.IFD.Exif)` and pair the tags: DateTimeOriginal with OffsetTimeOriginal (36881), DateTime (306) with OffsetTime (36880), DateTimeDigitized (36868) with 36882. Fix S-2 in the same change.
- **Tests:** `test_exif_capture_time_in_exif_subifd_is_used[jpg|png|webp]` (xfail). Control `test_exif_in_ifd0_layout_is_parsed_control` shows parsing and UTC conversion are correct and only the tag location is wrong.

### S-2 (Low now, becomes Medium once S-1 is fixed) EXIF year 0001 or 9999 aborts indexing of the whole image

- **Where:** `metadata.py:16` (`.astimezone(timezone.utc)`) and `metadata.py:18` (`except (ValueError, TypeError, AttributeError)`).
- **Trigger:** `0001:01:01 00:00:00` with `+05:00`, or `9999:12:31 23:59:59` with `-05:00`. Both parse, then `astimezone` raises `OverflowError`, which is not a `ValueError`.
- **Observed:** `image_metadata` raises `OverflowError: date value out of range`. Ingestion counts the file as failed (`errors: date value out of range`) and the valid image is not indexed.
- **Expected:** fall back to the file mtime, like every other malformed EXIF date. The six control cases in `test_malformed_exif_dates_fall_back_without_raising_control` do this.
- **Reachability:** today only for files carrying both tags in IFD0 (non-standard writers). It becomes reachable for every photo once S-1 is fixed.
- **Tests:** `test_extreme_exif_dates_fall_back_to_mtime[...]` (2 params) and `test_extreme_exif_date_does_not_block_indexing` (xfail).

### S-3 (Medium) Re-index cannot repair a missing stored copy or thumbnail

- **Where:** `ingestion.py:59-63` (skip branch); the misleading remedy is `server.py:88`.
- **Trigger:** `data/images/<digest>.png` or `data/thumbs/<digest>.webp` is deleted, lost, or absent after a partial restore.
- **Observed:** `/api/assets/{id}/image` returns 404 "Stored image is missing. Re-index the source folder." Re-indexing reports `skipped: 1` and recreates nothing. The skip test checks only the DB row, hash, mtime, embedding and OCR, never the files. The web UI does not send `force` (`web/app.js:75`), so only the CLI `--force` or a raw API call recovers.
- **Expected:** an unchanged file whose stored copy or thumbnail is absent is reprocessed (or at least the files are regenerated).
- **Test:** `test_reindex_restores_missing_stored_copies` (xfail).

### S-4 (Low-Medium) Fixed `.partial` temp name: concurrent writers collide

- **Where:** `ingestion.py:33-36` (`_atomic_save` uses `target.name + '.partial'`).
- **Trigger:** two ingestion runs that share a `data/` directory: `scripts/index_folder.py` run while the server indexes (the README documents CLI indexing at `README.md:59`), or two server processes. The in-process 409 guard (`server.py:101`) does not cover other processes.
- **Observed** (ad-hoc stress, 2 threads, 40 PNGs, 5 of 5 trials): about 17 to 23 of 40 files per run failed with `[Errno 2] No such file or directory: '<abs>/data/images/<digest>.png.partial' -> '<abs>/data/images/<digest>.png'`. The final DB had all 40 rows. In 8 further trials with 3 threads, every stored copy was pixel-identical to its source, so I saw no corruption, only spurious failures that leak absolute paths and heal on re-run.
- **Expected:** a unique temp name per write (pid and uuid), or a lock, so concurrent runs report no spurious failures.
- **Tests:** `test_atomic_save_tolerates_two_concurrent_writers` and `test_two_concurrent_ingestion_runs_have_no_spurious_failures` (xfail). Both are barrier-deterministic and tolerate a lock-based fix.

### S-5 (Low) `.partial` file left behind when a write fails

- **Where:** `ingestion.py:33-36`, with no cleanup path.
- **Trigger:** `Image.save` or `os.replace` raises (disk full, I/O error).
- **Observed:** `<digest>.png.partial` remains in `data/images`. An ad-hoc `os.replace` failure showed the same.
- **Expected:** the temp file is removed on failure.
- **Test:** `test_failed_write_does_not_leave_partial_file` (xfail).

### S-6 (Low) Editing a file orphans its old stored copy and thumbnail

- **Where:** `ingestion.py:70-77`. Nothing releases the previous digest's files.
- **Observed:** three edits of one file leave 6 stored files for 1 row, so 4 orphans (2 per edit, unbounded growth).
- **Expected:** release files no longer referenced by any row. A copy still shared by a duplicate path must stay.
- **Tests:** `test_editing_a_file_does_not_orphan_old_stored_copies` (xfail). Guard for any future cleanup: `test_edit_keeps_copy_still_used_by_a_duplicate_control` (passes).

### S-7 (Low) A row can hold a valid vector and a contradictory `embedding_error`

- **Where:** `ingestion.py:79-82` (`vector_error = blocked_embedding`) and `ingestion.py:99`.
- **Trigger:** a run where an earlier file trips the run-level embedding breaker, and a later file is a byte-identical duplicate of an already-embedded image.
- **Observed:** the duplicate gets the cached vector, `embedding_model` set, and `embedding_error='model exploded'`. The outcome depends on file name order.
- **Expected:** `embedding_error` is `None` when a vector is stored.
- **Impact:** only `/api/assets` and search JSON; the UI never renders the field.
- **Test:** `test_reused_embedding_is_not_tagged_with_an_embedding_error` (xfail).

### S-8 (Low-Medium) A forced refresh with models down destroys previously good data

- **Where:** `ingestion.py:78-100` (`force` bypasses the cache, and the record is upserted unconditionally).
- **Trigger:** `index_folder.py --force` (README line 75: "refreshes existing inference") or API `force:true` while embedding or OCR is unavailable.
- **Observed:** the row's vector becomes `NULL` and its OCR text `''` with backend `unavailable`. The file is counted `updated`, not `failed`.
- **Expected:** a failed refresh keeps the previous vector and OCR (or counts the file as failed).
- **Test:** `test_forced_refresh_with_models_down_does_not_destroy_existing_data` (xfail).

### S-9 (Low-Medium, macOS default filesystem) Folder spelling by letter case creates duplicate rows

- **Where:** `ingestion.py:14` (`Path.resolve()` keeps the typed case) and the `filepath UNIQUE` key.
- **Trigger:** on case-insensitive APFS, index `…/Pics` and later `…/pics`. A case-only rename (`Shot.png` to `shot.png`) behaves the same.
- **Observed:** two rows for one physical file (`indexed:1, duplicates:1`, folder facets `Pics` and `pics`). After the case-only rename, an ad-hoc probe showed `missing: 0` and a third row, because `Path(old).exists()` is true on that filesystem.
- **Expected:** one row per physical file (canonicalize the case, or key by device and inode).
- **Test:** `test_case_variant_folder_spelling_does_not_duplicate_rows` (xfail; skips itself on a case-sensitive volume).

### S-10 (Low, defence in depth) `encode_vector` stores a zero vector when the float32 norm overflows

- **Where:** `database.py:122-126`. `np.linalg.norm` of float32 `[1e20, 0]` is `inf`. The check is only `< 1e-8`, so `array / inf` yields zeros.
- **Observed:** `encode_vector([1e20,0])` and `[3e19,4e19]` return 8 bytes of zeros. This is the same class as the zero-vector defect Codex fixed in `Models._encode`.
- **Reachability:** not through the shipped path. `Models._encode` already rejects an infinite norm and normalizes before calling `encode_vector`.
- **Expected:** raise `ValueError` or return a unit vector.
- **Tests:** `test_encode_vector_never_stores_a_zero_vector[2 params]` (xfail).

### S-11 (Low) `POST /api/index` returns HTTP 500 for unusable but syntactically valid folder strings

- **Where:** `ingestion.py:14-15` (`Path.expanduser()` and `Path.is_dir()` raise `RuntimeError` and `OSError`). `server.py:98` converts only `ValueError`.
- **Triggers** (all `{"folder": …}`):
  - `~no_such_user_zz/pictures` raises `RuntimeError: Could not determine home directory`.
  - `'a'*300` raises `OSError` (file name too long).
  - A path under a directory without search permission raises `PermissionError`.
- **Observed:** 500 "Internal Server Error". A NUL byte correctly gives 422.
- **Expected:** 422 "Choose an existing folder containing images."
- **Tests:** `test_index_rejects_unusable_folder_values_with_422[unknown-home|name-too-long]` and `test_index_rejects_folder_inside_unreadable_directory_with_422` (xfail; the last is skipped when running as root). Control: `test_scan_folder_value_errors_are_the_documented_contract_control`.

### S-12 (Low) `/api/analyze` with a missing stored image returns 503 and leaks the absolute path

- **Where:** `server.py:138-145` (`Image.open(config.data/'images'/…)` raises `FileNotFoundError`, caught by the generic `except Exception`).
- **Observed:** 503 `Analysis could not finish. Search and OCR remain available. [Errno 2] No such file or directory: '<abs>/data/images/<digest>.png'`. This also misleadingly suggests a model outage, while the image route gives an actionable 404 for the same condition.
- **Expected:** 404 or 409 with the actionable "stored image missing" message and no filesystem path.
- **Test:** `test_analyze_with_missing_stored_image_is_actionable_and_does_not_leak_paths` (xfail).

### S-13 (Medium) The alpha channel is dropped, so transparent images become flat black

- **Where:** `ingestion.py:68` (`ImageOps.exif_transpose(opened).convert('RGB')`).
- **Trigger:** RGBA, LA or palette-with-transparency PNG or WebP with a transparent background, such as exported diagrams, slides or logos with black ink. The underlying RGB under alpha 0 is normally black.
- **Observed:** the stored copy, thumbnail, OCR input and embedding input are all a single flat colour. Pixel extrema are `((0,0),(0,0),(0,0))`, confirmed on the image object passed to `embed_image` and on the stored file. A palette PNG with transparency gave the same black result in an ad-hoc probe.
- **Expected:** composite onto a light background before RGB conversion so the content is visible to storage, OCR and embedding.
- **Impact:** a silent loss of searchability for this class of input. Opaque screenshots are unaffected, which is why existing QA did not catch it. How much retrieval degrades needs real models (see section 4).
- **Test:** `test_transparent_images_are_flattened_onto_a_visible_background[RGBA|LA]` (xfail).

### S-14 (Low) An unreadable subfolder is silently skipped

- **Where:** `ingestion.py:20` (`os.walk(path, followlinks=False)` with no `onerror`).
- **Observed:** for a folder containing `ok.png` and an unreadable `locked/hidden.png`, the report is `total:1, indexed:1, failed:0`, `warnings: []`, `errors: []`. There is no count, no warning and no hint that images were not scanned.
- **Expected:** at least a warning naming the unreadable directories.
- **Test:** `test_unreadable_subfolder_is_reported_not_silently_skipped` (xfail; skipped when running as root).

## 2. Suggestions

Hardening or design-dependent items. Each has an xfail test unless noted.

- **Hardening: `server.py:87` joins `image_path` and `thumbnail_path` from the DB onto `data/` without confinement.** A tampered or imported database (the demo ZIP ships a SQLite backup) with `image_path='../../secret.txt'` is served by `/api/assets/{id}/image` with HTTP 200 and the file content. Resolve the path and require it to be inside `data/images` or `data/thumbs`. Test: `test_tampered_image_path_cannot_serve_files_outside_data_dir`. The ingestion path always writes digest names, so there is no remote trigger.
- **One image-specific embedding exception disables semantic indexing for the rest of the run** (`ingestion.py:83-88`, `blocked_embedding`). Three valid images with a transient failure on the first got zero vectors. The affected files are not skipped on the next run, so they heal. A genuine "model missing" case should still fail fast, so consider breaking the circuit only on `ModelUnavailable` for missing files. Test: `test_one_failing_image_does_not_disable_embeddings_for_the_rest_of_the_run`.
- **`encode_vector` silently flattens a `(2, D)` batch into one `2*D` vector** (`database.py:123`, `reshape(-1)`). Unreachable today because `embed_image` selects `[0]`. Accept only 1-D or `(1, D)`. Test: `test_encode_vector_rejects_batch_shaped_input`.
- **One malformed embedding blob (length not a multiple of 4) makes every hybrid or semantic search fail** (`search.py:89`, `np.frombuffer` raises `ValueError`, and the route turns it into a 422 carrying a numpy message). The schema has no `CHECK`. Skip and log the bad row, or add `CHECK(length(embedding) % 4 = 0)`. The model-identity check already excludes stale dimensions, so this needs actual corruption. Test: `test_one_corrupt_embedding_blob_does_not_break_every_search`.
- `similar_id=-1` and `similar_id=0` return the message "This image has no semantic embedding. Re-index after installing the model." for an id that does not exist. A "not found" message would be accurate. I observed this directly and wrote no test.
- `TrustedHostMiddleware` allows the host `testserver` (`server.py:49`) in the production app. That entry exists only so `TestClient` works. Practical risk is negligible because a host named `testserver` would have to resolve to loopback, but the allow-list could be made test-only. No test; `test_origin_and_host_guards_control` pins the other host and origin rules.
- `Config` fields read `SIGHTVAULT_DATA`, `SIGHTVAULT_DEVICE` and `SIGHTVAULT_OCR` at class-definition time (`config.py:10-13`). Setting the environment after importing `sightvault` is silently ignored. Current launchers set the variables before the import, so no live bug. Code reading only; no test.
- `I;16` (16-bit grayscale) PNGs saturate at 255 on `convert('RGB')` (ad-hoc probe: a 0 to 65535 ramp became `0, 204, 255, 255, 255, 255`). Rare for screenshots; consider rescaling to 8-bit. No test.

## 3. Already fixed or verified OK (control tests, all passing)

- Cancel mid-run then resume. After cancelling at 2 of 6, the DB has 2 rows and no `.partial` files. The resume indexes 4, skips 2 and fails 0; every row has a vector and OCR; stored files equal referenced files. Test: `test_cancel_mid_run_then_resume_is_consistent_control`.
- Crash between inference and the DB write (simulated `upsert` failure). There is no partial row, a re-run indexes cleanly, no orphan files remain, and the FTS `integrity-check` passes. Test: `test_crash_between_inference_and_db_write_leaves_no_row_and_heals_control`.
- Identical bytes at two paths. One stored copy and one embedding call (`duplicates == 1`), and each row keeps its own timestamp. Test: `test_identical_bytes_at_two_paths_share_one_stored_copy_and_one_embedding_control`.
- EXIF orientation is applied, and stored dimensions are the post-transpose size. Test: `test_exif_orientation_is_applied_to_stored_copy_and_dimensions_control`.
- Index job API. The first job runs; a second start returns 409, including while a cancel is pending (cancellation is cooperative, so the job stays "running"). After the cancel the state is `cancelled` and a new job resumes with the right counts. Test: `test_index_job_cancel_conflict_and_resume_control`. In an ad-hoc probe the oldest job was evicted after more than 20 jobs.
- Database layer.
  - Float64 input becomes a normalized little-endian float32 vector of 768 dims (3072 bytes). Test: `test_vector_storage_roundtrip_shape_dtype_control`.
  - Four writer threads upserting concurrently raised no errors. `PRAGMA integrity_check` and the FTS integrity check were clean. Test: `test_fts_and_table_stay_consistent_under_threaded_upserts_control`.
  - `filepath UNIQUE` upsert keeps the id, and `NOT NULL` is enforced. Test: `test_schema_enforces_unique_path_and_not_null_control`.
- Dates.
  - mtime 0 (1970-01-01), -1 (1969-12-31T23:59:59), a 1940 value and a 2038 value all index. Date-only filters include the right records. Ad-hoc probe: mtime from 1678 to 2262 also indexes. Test: `test_epoch_and_pre_epoch_mtimes_index_and_filter_control`.
  - Local-midnight instants on US DST days (23-hour spring and 25-hour fall) convert exactly, with an exclusive end. Test: `test_date_bounds_on_dst_transition_days_control`.
  - EXIF with no offset is not trusted and falls back to mtime. Test: `test_exif_without_offset_falls_back_to_mtime_control`.
  - Six malformed EXIF date or offset forms fall back without raising. Test: `test_malformed_exif_dates_fall_back_without_raising_control`.
  - A 3000-mutation ad-hoc EXIF fuzz through `image_metadata` raised nothing.
- API.
  - Path traversal through `/static/..`, encoded `%2e%2e` and `..%2f`, and `/api/assets/1/image/../..` serves nothing. Test: `test_traversal_and_foreign_routes_serve_nothing_control`.
  - `Origin: null`, a foreign origin and a different-port origin get 403. A same-origin call passes. Bad `Host` values (including `localhost.`, `[::1]` and userinfo tricks) get 400. PUT, PATCH, DELETE and OPTIONS get 405. Test: `test_origin_and_host_guards_control`.
  - `top_k` clamps for negative, zero and huge values. Out-of-int64 asset ids return 404. Malformed ids, bools, floats, null, over-long questions and a missing question return 422. `q` over 1000 characters returns 422. A similarity id out of range returns 422. A naive date-time filter returns 422. Test: `test_numeric_parameter_boundaries_never_crash_control`.
  - Odd query strings never produced an FTS error in an ad-hoc probe: `___`, `NEAR`, `AND OR NOT`, quotes, `col:val` and 5000-character terms.
- Earlier Codex fixes (oversized ids, year-1 date bounds, stale embedding status, zero vectors, similar-image model identity) are consistent with what I observed. I did not re-test them beyond reading `tests/test_adversarial_qa.py`.

## 4. Unverified hypotheses (need real models, a browser, or a specific macOS state)

- **Retrieval impact of S-13.** I proved the model receives a flat black image. How much OCR accuracy and embedding recall drop on real transparent PNGs needs real inference.
- **Real camera files for S-1.** My fixtures were written with Pillow in the standard Exif sub-IFD layout. I did not read a real iPhone or camera JPEG, because `samples/` is off limits.
- **macOS privacy (TCC) behavior.** For S-14 and S-11 the real cause may be an unreadable directory; I could only emulate it with `chmod 000`.
- **S-9 depends on how the folder path is entered.** I did not check whether the UI normalizes case. The UI text field was not exercised.
- A named pipe or socket with a supported extension in the scanned folder would block `path.read_bytes()` indefinitely, and cancellation is checked only between files. This is code reading only, not run.
- A file edited between the digest read and `Image.open` (`ingestion.py:58` vs `:64`) could be stored under the old digest. This is a race, code reading only.
- `scan_folder` does not exclude `config.data`. Indexing the data directory itself would re-ingest stored copies, and the PNG re-encoding changes the digest so they would not deduplicate. Not run.
- `Models.__init__` mutates the process-global `tempfile.tempdir` and `TEMP` (`models.py:28-29`). Fine in the server; in a test session it leaks across tests. I saw no failure from it.
- Nothing in this report establishes MLX, MPS or hardware behavior. The Opus scope items (ranking, Unicode queries, adapters, device labels, packaging, UI races) were not covered here.
