# Claude UI pass: implementation and review report

Branch `experiment/claude-ui`. Edited only `sightvault/web/index.html`, `sightvault/web/style.css` and `sightvault/web/app.js`, keeping their filenames. One read-only `ux-reviewer` pass (Sonnet 5.5 xhigh) was run first, and its findings drove the changes.

**Not run:** no browser, server, model, test or shell command. Bash was unavailable in this session, so `node --check` was not run either. JS syntax and the ID cross-references were checked only by reading the code. The parent should run `qa_browser.py`, `qa_browser_edges.py`, the 390px and zoom checks and a CSP-enabled session.

## What changed

### Layout and density

- The cards start about 110px higher (an estimate):
  - The topbar went from 72px to 56px.
  - The hero eyebrow is removed, so the slogan no longer appears three times.
  - The h1 went from 39px to 30px.
  - The count is now an inline stat: "62 images indexed".
  - The margins on the search, suggestions, filter and results header are tighter.
- The results use `auto-fill minmax(250px,1fr)`. That gives 4 columns at 1440px and 2 at about 980px.

### Type and contrast

- The font stack is the system stack: SF on Mac, Segoe on Windows. It used to fall back to Arial on Mac. The mono stack is now `ui-monospace`.
- Only weights 400/500/600/700 are used.
- Text is at least 11px; most metadata and controls are 12.5–14px. Previously some text was 7–10px.
- Letter-spacing on the caps labels and h1 is reduced.
- Muted and field-border colours are raised. The estimated contrast is ≥5.9:1 for text and about 3:1 for field borders.

### Cards

- Each card is a flex column, so the footers align across a row.
- Thumbnails use a 16:10 `contain` frame. Nothing is cropped, and the frame has an inset divider.
- The rank badge has moved out of the image into the badge row.
  - Ranks show only for searches. The "01…12" numbers are gone when browsing.
  - The `BEST MATCH` text is kept, and the ✦/✓ glyphs are replaced by a CSS dot.
  - `EARLIEST MATCH` now gets the same `.earliest` emphasis as best match.
- In hybrid and exact modes the OCR preview centres on the first matched term and wraps matches in `<mark>`. It uses the existing `matched_terms` and full `ocr_text`. Each segment is escaped, and the only element added is `<mark>`. Visual meaning mode and similar-image results are never highlighted.
- The repeated "LOCAL EVIDENCE" label is replaced by the image dimensions.
- "Open & analyze" is now a real 32px bordered button with a per-card `aria-label`.
- The thumbnail button is `tabindex=-1`. It is still clickable with a mouse, and there is one tab stop per card.
- "Similarity" has a tooltip: "ranking signal, not a confidence score". The score values are unchanged.
- The hover lift is removed. Hover and focus now change the border colour.

### Evidence dialog

- The dialog has `aria-labelledby="detail-title"` and a sticky header. The close button is 38px.
- The page behind the dialog no longer scrolls (`body:has(dialog[open])`), and `overscroll-behavior` is contained.
- The image is shown at up to `min(58vh,520px)`, with an "Open full size" caption that tells screen readers it opens a new tab.
- The metadata is now a `<dl>` with labelled rows: File date (with its source), Dimensions, Indexed and Source file. The path sits on its own row in a monospace block with `user-select:all`. The missing-original flag is kept.
- The OCR `<pre>` uses 12.5px mono and is a focusable, labelled region.
- The question-and-answer area:
  - The answer sits in its own container.
  - The Source / model / latency line is 12px and stays inside `#analysis-result`.
  - Loading, error (`role=alert`, red) and placeholder states each have their own style.
  - The result area sets `aria-busy` while loading.
  - The model-uncertainty footnote is kept.
- The dialog has an in-dialog status line, `#detail-status`. Copy and empty-question feedback used to go to the page toast, which is hidden under the modal's top layer.
- Ctrl+Enter or Cmd+Enter in the question box runs the analysis. A visible hint says so.
- Clicking the backdrop closes the dialog only if the press also started on the backdrop, so drag-selecting text no longer closes it. Escape is unchanged.
- After "Find similar images", focus moves to `#results-title`.
- If opening the evidence fails, the error also shows as a toast. `body.is-busy` sets a progress cursor while it loads.

### Truthful labels

- `visionLabel` no longer defaults to `MiniCPM-V 4.7 1B`. If `/api/status` has no name, the UI says "the selected local vision model" and "Analyze image".
- The `EmbeddingGemma 2` and `MiniCPM-OCR 1.0` fallbacks are removed from the JS. Names now come from the server's `embedding_model` and `ocr_model` fields.
- The footer reads the embedding model and cloud API call count from `/api/status`. It used to have static text and a static "0".
- Empty-OCR messages now tell apart OCR skipped (`off`), OCR failed (`ocr_error`), OCR unavailable and no text detected.

### Accessibility

Each item below lists what was added or changed.

- **Skip link:** a "Skip to search" link.
- **Search form:** `role=search` and `aria-keyshortcuts="/"`.
- **Mode buttons:** `aria-pressed` on the mode segments, set in the HTML and kept in sync by JS. The selected state also has a weight and underline cue, not only colour.
- **Navigation:** `aria-current` on the navigation, kept in sync by `setView`.
- **Live regions:**
  - Removed `aria-live` from the whole results grid; `#results-meta` is now the status region.
  - `#results` sets `aria-busy` while loading.
  - Only `#job-title` is live in the job panel; it used to announce every file.
- **Decorative glyphs:** marked `aria-hidden`, including the ones JS re-injects.
- **Arrows:** the ↗ arrows are removed, because they implied external links.
- **Clear button:** an `aria-label` on Clear.
- **Focus rings:** fixed on the folder input and the search input, which used to have `outline:none`, and on the thumbnail button, whose ring was clipped.
- **Target size:** controls are at least 32px, or 44px on coarse pointers.
- **Mobile inputs:** 16px on coarse mobile pointers, to avoid iOS zoom.
- **Reduced motion:** loading now has a static opacity cue as well.
- **Forced colours:** outlines added.
- **Empty state:** `#empty` starts `hidden`, so the empty state no longer flashes on load.
- **`/` shortcut:** now ignores `<select>`, contenteditable elements and Ctrl/Cmd/Alt combinations.

### Mobile (390px)

- Results use one column.
- Timeline cards use `118px minmax(0,1fr)`, which fixes title-driven track overflow.
- Timeline navigation is now reachable: a compact two-button nav next to the brand icon. The brand text stays available to screen readers.
- The folder labels are hidden visually instead of removed from the accessibility tree.
- The dialog is `100dvh`-aware.

## Contracts deliberately kept

- All existing IDs, classes and data attributes are kept. These include `.card`, `.card-title`, `.card-time`, `.card.best .rank-badge`, `.match-badge`, `.ocr-preview`, `.open-evidence`, `.image-wrap[data-open]`, `[data-mode]`, `[data-query]`, `#results.loading`, `#job-title` and `#job-detail`.
- The badge text still ends in `BEST MATCH`.
- `.match-badge` appears only when `exact_match` is set.
- `#results-meta` is still plain text in the form "N of M …".
- `#asset-count` is plain digits.
- The `Source: <filename> · <model> · <latency>` line is still inside `#analysis-result`.
- The model name is still in `#analyze-button` and `#analysis-description`.
- Tab from `#query` still reaches `#search-button` directly. The `<kbd>` is `aria-hidden` and cannot be focused.
- There are no inline styles or handlers, so CSP is unchanged.
- No new IDs collide. Additions:
  - `embedding-metric`, `cloud-metric`, `detail-status`, `question-hint`, `folder-hint`, `add-label`
  - per-card `card-title-<id>`
- Search, ranking, dates and DST, the local-midnight conversion, first occurrence, similar images, sort, pagination, the demo action and the job/error flows are unchanged in logic.

## Limits and risks for the parent to verify

1. **The `qa_browser.py` line 107 hazard is untested.** That line does `pg.click('body')` and then presses `/`. With the denser layout a card button may now sit at the centre of body, which would open the dialog, and then `/` would be ignored. This existed before the change but depends on layout. Check the `slash_focuses_search` result.
2. **The OCR backend `<select>` labels are unchanged.** They name engines ("MiniCPM-OCR · auto fallback", "RapidOCR") that map to backend option values. I did not change them, because that would need confirming the backend's current OCR lineup.
3. **Highlighting is approximate.** It uses case-insensitive substring matching of the server's normalised `matched_terms` against raw OCR text, with terms of 3+ characters. If a term was normalised differently from the raw text, nothing is highlighted and the preview falls back to the start of the text. Highlighting is disabled if lowercasing changes the string length (some non-ASCII text).
4. **Mobile still hides some sidebar content.** Below 760px the OCR backend select, the privacy block and Model status stay hidden (Model status was hidden before too). The top pill still says "Runs on your computer".
5. **CSS support is partly unconfirmed.** `:has()`, `dvh`, `aspect-ratio`, `line-clamp` and `forced-colors` are supported in current Chromium and Safari, with fallbacks where it matters. They have not been tested in the parent's browsers.
6. **No screenshots were taken.** All visual claims, including card position at 1280×800 and contrast ratios, are estimates.
