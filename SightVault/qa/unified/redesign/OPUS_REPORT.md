# SightVault frontend redesign — Opus 5.5 report

Status: implemented from source reading only. **Not browser-validated, not contrast-measured, no syntax checker run** (no shell in this session). Codex must run the Node syntax check, the QA scripts, real browser screenshots at 1440 / 981 / 390 / 320, and recalculate the `?v=` content hashes in `index.html` (left unchanged).

## Refinement pass 2 (after Codex first-render review)

This pass supersedes the earlier sections wherever they conflict. Same four web files; still not browser-run by me.

1. **Example-query strip removed.** The `#suggestions` element, its CSS and the `[data-query]` handler are gone. There are no canned examples anywhere. QA must type its queries; the `[data-query="…GPU…"]` click in `qa_browser.py` has to become `fill('#query', …)` plus Enter.
2. **Add images is a disclosure at every width.**
   - `#import-toggle` ("+ Add images"; just "Add" at 360px or below) has `aria-expanded` and `aria-controls="import-panel"`. The `#import-panel` `hidden` attribute and `aria-expanded` are always set together by `setImportOpen()`.
   - The panel starts closed. Escape inside the panel closes it and returns focus to the toggle.
   - When `/api/status` reports `total === 0` and no job is running, the panel opens once as onboarding. After that it only follows the user.
   - When an index job starts successfully, the panel closes; focus goes back to the toggle only if it was inside the panel. A failed start (for example a bad folder) leaves it open, with the notice showing.
   - `#job-panel` (progress and Stop) sits outside the panel, so it stays visible while a job runs whether the panel is open or not.
   - The empty library state now offers **Add a folder** (`#empty-add`, opens the panel and focuses the folder field) and **Load the demo collection** (`#empty-demo`). Both are hidden for a search that returns nothing.
3. **Settings is independent of import.** `<details id="settings">`, labelled "Settings", contains the OCR backend select and the API model status.
   - Desktop: it sits at the foot of the rail.
   - 760px or below: it is a "Settings" item in the header row, and its body drops down over the page (absolute, scrollable, max 70dvh). Escape closes it and focuses the summary.
   - At 420px or below the wordmark is visually hidden (it stays as the accessible `h1`) to keep the Library / Timeline / Add / Settings row on one line.
4. **Results header.**
   - `#first-button` is `hidden` rather than disabled when there is no query, so browsing shows only the sort menu. It appears and works as before for searches.
   - Millisecond timing is removed from `#results-meta`. It keeps the `N of M` prefix: browsing shows "`12 of 62 images`". Searches show "`5 of 23 results · visual similarity + text`", "`· visual similarity`", "`· exact text in image or filename`", or "`· similar images`" for Find similar.
   - The analysis evidence line in the inspector still reports model latency; that belongs to the answer's provenance.

**QA impact of this pass:**
- Desktop: click `#import-toggle` before `#demo-button`, `#folder` or `#index-button`. After a successful demo re-index the panel closes, so click `#import-toggle` again before filling `#folder` for the bad-folder check (it stays open after that failure).
- Open `#settings` (click its `summary`) before touching `#ocr-backend`.
- `#first-button` is not visible until a query has run.
- The demo library is populated, so the panel will not auto-open in normal QA. Expect auto-open only against an empty index.

## Files edited

- `sightvault/web/index.html` — restructured markup.
- `sightvault/web/style.css` — full rewrite.
- `sightvault/web/app.js` — targeted edits (status, view labels, card markup, day dividers, results titles, inspector fields, mobile import toggle). Retrieval, ranking, date maths, request serials, the rapid-Enter keydown handler, escaping and `textContent` paths are unchanged.
- `sightvault/web/icon.svg` — recoloured only (tile `#264b80`, marks `#f6f4ef`); geometry identical.

## Design position

A personal image archive set like a contact sheet on paper.

- **Surfaces:** warm paper workspace (`#f6f4ef`), slightly darker source-list rail, near-white sheets for fields and the inspector. Every image sits on a neutral mat with a faint print-edge shadow, so dark terminal captures and white slides both read as objects.
- **Ink:** near-black `#1c1b18` with two secondary inks. **One accent**, ink blue `#264b80`, used only for: the active view, the selected match type, the primary Search/Ask buttons, focus rings, and anything meaning "matches your query" (best/earliest frame ring and stamp, TEXT MATCH tag, OCR highlights, temporal note).
- **Type:** system UI face for words; the system monospace for facts — dates, pixel sizes, ranks, similarity, paths, OCR, results meta and job progress. That split is the catalogue texture. No display serif, no tracked uppercase eyebrows apart from the existing BEST/EARLIEST/TEXT MATCH stamps, which have to stay uppercase for QA.
- **Removed:** the "It's here. Somewhere." hero and subtitle, the breadcrumb, the "Runs on your computer" pill, the LOCAL workspace tag, the "Your files stay here" block, the version footer, the telemetry footer (count / latency / embedding / cloud calls), the "Local" tag and sparkle in the inspector, emoji-like glyph icons, the radial gradient, glow, blur backdrop and lime palette.

## Layout

- **Rail (left, 232px; 212px at or below 1180px):** `h1` wordmark; views **Library** (holds `#asset-count`) and **Timeline**; **Add images** panel (folder field, *Add folder*, *Load the demo collection*, file-type hint); a collapsed **Indexing & models** disclosure containing the OCR backend select and the live model status from `/api/status`; the job progress panel.
- **Toolbar (sticky):** view title (`#view-label`), then a full-width search field with Search attached. A second row holds the match type (`Visual + text` / `Visual` / `Exact text`, same `data-mode` values) and the From/To dates with Clear. On wide screens that row is indented to line up with the search field.
- **Content:** notice; example searches as quiet underlined links (shown only while no search is active); results header (title, mono meta line, First occurrence, sort); temporal note; results.
- **Contact sheet:** `auto-fill, minmax(216px,1fr)` → 4 columns at 1440, 3 at 981. Each frame is a 4:3 mat, then filename, mono timestamp, tags, OCR snippet (search results only), mono pixel size and an **Inspect** action. In plain browsing the OCR snippet is hidden by CSS (`.results.browsing`) so the grid reads as images; it returns for searches, similar-image results and the timeline.
- **Chronology:** when sort is Oldest or Newest, a day divider (`p.day-mark`, local calendar day, full long date) is inserted before the first image of each day, in both the grid and the timeline. This only affects display grouping of what the API returns.
- **Timeline:** one entry per row (200px print plus a caption with a 3-line OCR snippet), grouped under the day dividers.
- **Evidence inspector:** 1320×900 maximum. Sticky header holds the filename, live status, *Find similar* and *Close* (autofocused). The left pane, on paper, has the original image on a mat at up to 62dvh (never upscaled), *Open original*, a facts grid (File date + timestamp source, Pixels, Indexed, full-width Source file path, moved/deleted flag), then **Extracted text** with backend label and *Copy text*. The right pane, on a sheet, is **Ask about this image**: the API-derived model sentence, question, hint, primary *Analyze with {API label}*, answer, and the uncertainty note. The two panes scroll independently on desktop. At 900px or below it becomes one scrolling sheet: image, facts, text, then question.
- **Narrow (760px or below):** the rail becomes a header (icon, Library, Timeline, **Add images** toggle). The toggle expands the import panel inline; job progress stays visible either way. Toolbar is not sticky. At 560px or below results become an index: a 104px print beside its caption. The match type control fills the width and the dates go into a two-column grid. At 480px or below the wordmark text is visually hidden (the h1 keeps its accessible name). At 420px or below the count is hidden and Clear wraps. At 360px or below the toggle reads "Add".

## Contracts kept

All IDs used by `app.js` and the QA scripts still exist, except those listed below. Kept: `.card`, `.card-title`, `.card-time`, `.image-wrap[data-open]`, `.open-evidence`, `.rank-badge` (text still `BEST MATCH` / `EARLIEST MATCH` / `01…`), `.match-badge` (`TEXT MATCH`), `.semantic-badge`, `.ocr-preview` + `mark`, `.analysis-evidence`, `#analysis-result [role=alert]`, `#results.loading` + `aria-busy`, and the `#results-meta` "`N of M …`" plain-text format. Also kept: `#results-title` `Visually related`, `#view-label` `Timeline`, `#asset-count` (initial `0`, then the API total), `#job-title` `Indexing complete`, `#job-detail` format, `#detail-status` "copied" and "Enter a question", the API-derived `#analyze-button` / `#analysis-description` labels, a single `h1`, and `#query` immediately followed in tab order by `#search-button`.

## Changes QA must know about

1. **Removed IDs:** `nav-count`, `indexed-metric`, `latency-metric`, `embedding-metric`, `cloud-metric`; the `.intro`, `.topbar`, `.metrics`, `.side-bottom` blocks; the brand link. The single `h1` is now the rail wordmark (not a link), so `locator('h1').click()` still works.
2. **New control: `#import-toggle`** (`aria-expanded`, `aria-controls="import-panel"`). It is hidden at desktop widths, where the panel is always open, so `#folder`, `#index-button` and `#demo-button` stay directly clickable at 1280/1440. **At 760px or below, QA must click `#import-toggle` before using the folder field, Add folder or the demo link** (`#empty-demo` stays available in the empty state).
3. **`#ocr-backend` and model status** are inside a closed `<details class="settings">`. Open its `summary` before interacting.
4. **`#copy-ocr` is disabled when the asset has no OCR text.** Previously it copied an empty string. The ui_polish fixture appears to have OCR (`ZX-4471`), but verify.
5. **`.suggestions` (`#suggestions`) is hidden while a query or similar-image search is active.** The `[data-query]` buttons are visible on load and after clearing the query.
6. **Copy changes:** `.open-evidence` reads "Inspect" (aria-label "Inspect {file}: source evidence and questions"). Results titles are now "All images", "All images, oldest first", "All images, newest first", "Search results" and "Earliest matches first" ("Visually related" unchanged). The running job reads "Indexing…". Match-type labels are "Visual + text / Visual / Exact text". The Dimensions meta row is now "Pixels". `#ocr-label` reads "via {backend}".
7. `#results` may now contain `p.day-mark` siblings between `.card`s when sort is oldest or newest. Card counts by `.card` are unaffected; any `:nth-child` assumptions would be.
8. The inspector calls `showModal()` only when the dialog is not already open, and resets pane scroll positions on each open.

## Limits and honest caveats

- Nothing here is browser-verified. Specific risks to check: 981px toolbar wrapping; rail header fit at 390/320 (I added `flex-wrap` as a guard against horizontal overflow); date inputs at 320; Safari `select` appearance with the CSS chevron; desktop inspector pane scrolling (`grid-template-rows:minmax(0,1fr)`); `line-clamp` in the timeline.
- Contrast was **estimated, not measured.** The tertiary ink `#69655c` on paper and the field-boundary grey `#8f8a7f` were chosen to aim at roughly 4.5:1 and 3:1. Please run a contrast check on the screenshots.
- Light theme only; no dark-mode variant.
- No new dependencies, fonts, fetched assets, SVG illustrations or raster art. The empty-state "stack of prints" is three CSS boxes.
- The OCR backend option labels are the original strings (backend choices, not runtime claims). Every model, device and runtime label shown elsewhere comes from the API.
