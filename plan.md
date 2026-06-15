# Implementation Plan — Bulk entry, dataset export, and a multi-player analysis page

This plan is for a single implementing agent. Read `CLAUDE.md` and `docs/scraping.md` first.
**Do not touch `scraper/` internals, the scraping logic, or the record dict shape** — every feature
here is web/UI layer only (`webapp/`, `config.py`, templates, CSS) plus one small forms change.
Honor every rule in `CLAUDE.md`'s "What NOT to do" section.

## Context: current state

- `webapp/forms.py` caps entry at `MAX_PLAYERS_PER_REQUEST = 2`; `parse_player_inputs` reads fixed
  `uscf_id_0/1` + `dob_0/1` fields.
- `webapp/templates/index.html` hardcodes exactly 2 player rows (`{% for i in range(2) %}`) with a
  live name/ID search autocomplete. Search JS binds per-row at page load (won't see rows added later).
- `webapp/routes.py`:
  - `/scrape` → stores `pending_scrape` in session → `/scrape/progress` → SSE `/scrape/stream`
    scrapes each player sequentially and on completion **redirects to `/compare?ids=...`**.
    The progress page (`progress.html`) already handles an arbitrary number of players — no change needed there.
  - `/compare` caps at **2** players (`request.args.getlist("ids")[:2]`), uses a 2-color palette,
    and serializes only the selected players' chart data.
- `compare.html` renders 4 Chart.js line charts (months / games / age / score). Tooltips already
  exist via a `tooltip.callbacks.label` formatter, but charts are static (no add/remove, no save).
- `webapp/cache.py` stores the full record JSON per `(source, player_id)`; `list_players()` returns
  lightweight summaries used by the "Previously analyzed" list on the index page.
- Record shape (per `CLAUDE.md` "Scraper API contract"): `milestones` is a dict keyed by
  `str(threshold)` with `{months, games, age, score_pct}`, any of which may be `None`.

The **"previously analyzed folder"** the user refers to = the "Previously analyzed" cached-players
list on the index page (`cached` / `list_players()`), backed by SQLite. There is no filesystem folder.

---

## Feature 1 — Bulk player entry (up to 100), choose the count first

**Goal:** the user first picks how many players (1–100), then is shown exactly that many entry rows.
Each row keeps the existing seamless UX: type a name to search + pick, or type a numeric ID directly;
optional DOB (`MM/DD/YYYY`) for age stats.

### 1a. `webapp/forms.py`
- Change `MAX_PLAYERS_PER_REQUEST = 2` → `100`.
- Rewrite `parse_player_inputs` so it does **not** assume a fixed 2 rows. Iterate over all submitted
  `uscf_id_{i}` / `dob_{i}` indices present in the form (e.g. collect indices by scanning
  `form.keys()` for the `uscf_id_` prefix, or loop `range(MAX_PLAYERS_PER_REQUEST)`), skipping rows
  where both ID and DOB are blank. Keep:
  - per-row validation with `Row {i+1}: ...` error messages,
  - the dedup-by-id pass,
  - the "Enter at least one {label}" fallback when nothing valid was entered.
- No change to `validate_*` helpers or `normalize_source`.

### 1b. `webapp/templates/index.html` — dynamic rows
- Replace the fixed `{% for i in range(2) %}` block with:
  - A "How many players?" control at the top of the analyze form: a number input
    (`min=1 max=100 value=2`) plus a label. Changing it (or a small "Set" button) (re)renders that
    many player rows. Keep an "Add another player" / "Remove" affordance as a secondary convenience,
    but the count input is the primary mechanism the user requested ("specify this before being asked
    for info"). Clamp to 1–100; never destroy already-entered data when only *adding* rows.
  - A single hidden `<template id="player-row-template">` containing the existing row markup
    (hidden `uscf_id_{i}`, search input, dropdown, hint, DOB input). Use a placeholder token (e.g.
    `__IDX__`) for the index in all `id`/`name`/`data-slot`/`aria-controls` attributes, and substitute
    on clone. Re-index rows after add/remove so `uscf_id_0..N-1` / `dob_0..N-1` stay contiguous.
- **Refactor the search autocomplete JS to use event delegation** so dynamically added rows work:
  - Bind `input`/`keydown` on a container (e.g. `.player-rows`) and dispatch by
    `event.target.closest('.player-search-input')` / its `data-slot`, instead of
    `querySelectorAll(...).forEach` at load.
  - Keep all existing behavior: debounce, numeric-ID shortcut sets the hidden field directly,
    `_seq` out-of-order guard, dropdown keyboard nav, outside-click close, and the submit-time
    validation that a typed name must have resolved to a selected ID.
  - The source toggle's `applySource()` must clear/reset **all** current rows (not just rows 0/1).
- Keep the milestone editor and "Previously analyzed" sections working.

### 1c. Sanity
- Submitting N rows must produce N progress entries (the SSE flow + `progress.html` already scale).
- After scraping, redirect target changes per Feature 3 (to `/analyze`).

---

## Feature 2 — Export analyzed players as a CSV dataset

**Goal:** download the analyzed players as a flat CSV (one row per player).

### 2a. `webapp/routes.py` — new route `GET /export.csv`
- Accept optional repeated `ids` query params (namespaced `source:player_id`, same parsing as
  `/compare`/`/analyze`). If `ids` are given, export those; if none given, export **all** cached
  players (`list_players()` → `get_player(...)` for each, or add a `list_full_records()` helper in
  `cache.py`). Skip ids not in cache.
- Build the CSV with the stdlib `csv` module into an `io.StringIO`; return a `Response` with
  `mimetype="text/csv"` and `Content-Disposition: attachment; filename="chess_players.csv"`.
- **Columns** (flatten the record): `source, player_id, name, country, dob, dob_source,
  first_tournament_date, initial_rating, age_at_first_tournament, scraped_at`, then for the **union of
  all milestone thresholds** across the exported players (sorted ascending) four columns each:
  `m{threshold}_months`, `m{threshold}_games`, `m{threshold}_age`, `m{threshold}_score_pct`.
  Write `None` cells as empty strings. Leave `score_pct` as the raw fraction (e.g. `0.534`) — do not
  pre-multiply; document this in the column header is unnecessary, just keep it raw and consistent.
- Guard the empty case (no cached players / no valid ids): flash a message and redirect to index.

### 2b. UI buttons
- Add a "Download CSV" button/link in the index "Previously analyzed" header (exports all cached)
  and on the new `/analyze` page (exports the currently-selected players, by passing the selected
  `ids`). On `/analyze`, the button should reflect the live selection (build the href from checked
  boxes in JS, or submit a tiny GET form).

---

## Feature 3 — New multi-player analysis page (`/analyze`) with add/remove + savable charts + hover tooltips

**Goal:** a dedicated page where the user can chart **as many cached players as they want**, toggle
players on/off live, see exact values on hover, and save the charts as images. This supersedes the
2-player `/compare` cap.

### 3a. `webapp/routes.py` — `GET /analyze`
- Like `/compare` but **no 2-player cap**. Parse all `ids` (namespaced `source:player_id`); for any
  missing, flash and skip. If `ids` is empty, default to selecting **none** but still render the page
  with the full cached list available to add (so the page is usable as a hub). If there are zero
  cached players at all, flash and redirect to index.
- Serialize chart data for **all cached players** (not just the pre-selected ones) so add/remove is
  fully client-side with no re-fetch. Shape per player:
  `{source, player_id, name, months[], games[], age[], score[]}` aligned to a shared sorted
  `milestones` axis = the union of every cached player's thresholds. Mark which ids are initially
  selected. Reuse the existing `chart_data` construction from `/compare` but over the full set and
  with an `initial_selected` list.
- Keep `mixed_sources` banner logic, but compute it from the **currently selected** set on the client
  (server can pass per-player `source`; JS recomputes the banner as selection changes), or keep a
  static banner if any mixed sources exist in the selection. Simpler acceptable approach: compute on
  the client from selected players.

### 3b. Redirect the existing flows to `/analyze`
- In `scrape_stream`'s `done` payload, change `url_for("main.compare", ids=scraped_ids)` →
  `url_for("main.analyze", ids=scraped_ids)`.
- On the index page, the "Compare selected on charts" button → point its form action to
  `main.analyze` and **remove the 2-pick cap** JS (`compare-pick` change handler) so users can select
  many. Update the helper text ("Pick 1 or 2 players" → "Pick any players").
- Keep `/compare` as a thin permanent redirect to `/analyze` preserving `ids` (back-compat for any
  bookmarks); or repoint it. A redirect is fine.

### 3c. `webapp/templates/analyze.html` (new)
- Layout: a **player picker sidebar/panel** listing every cached player with a checkbox (grouped or
  filterable by source like the index list is nice-to-have, not required), and the 4-chart grid
  reused from `compare.html` (months / games / age / score). Initially-selected ids are checked.
- **Add/remove live:** checking/unchecking a player adds/removes its dataset from all four Chart.js
  charts and calls `chart.update()`. Assign each player a stable color from a palette that supports
  many players (generate via HSL spread, e.g. `hsl(i * 360/N, 65%, 50%)`, or a fixed ~12-color
  palette cycled). Keep color stable per player across all four charts and across toggles.
- **Hover tooltips:** enable rich Chart.js tooltips — set `interaction: {mode:'nearest', intersect:false}`
  (or `mode:'index'`) and a `tooltip.callbacks.label`/`title` that shows the player name, milestone,
  and the exact value (months/games/age as-is; score as `xx.x%`). Show `point` hover styling
  (`pointHoverRadius`). This satisfies "hover over a data point and see a small popup of the exact
  number."
- **Save charts:** add a "Save" button per chart and a "Save all charts" button.
  - Implement download via `canvas.toDataURL('image/png')` → temporary `<a download>`.
  - Chart.js canvases are transparent; register a tiny inline Chart.js plugin that fills the canvas
    with a solid background (match the page card color, e.g. white/`#fffaf0`) **before** draw, so
    saved PNGs aren't transparent. Filenames like `chart-months.png`.
- **CSV button** here exports the currently selected players (Feature 2b).
- Keep the `<script src=".../chart.js@4.4.6...">` CDN include consistent with `compare.html`.

### 3d. Nav
- Add an "Analyze" link in `base.html` nav pointing to `/analyze` so the page is reachable directly
  (it doubles as the hub for charting already-collected players).

---

## Feature 4 — Analyze ALL previously-analyzed players in one click

**Goal:** "analyze all players from the previously analyzed folder."

- On the index "Previously analyzed" section, add a **"Select all"** control and an
  **"Analyze all"** button that opens `/analyze` with every cached player's id pre-selected
  (`/analyze?ids=src:id&ids=...`). Easiest robust implementation: a server-rendered link/form whose
  `ids` are all `cached` players (built in the template), independent of the checkboxes; plus a JS
  "select all" that ticks the existing `compare-pick` checkboxes for users who then hit "Compare/
  Analyze selected."
- This works with Feature 3's full-cache serialization, so all players chart immediately.

---

## Cross-cutting requirements

- **Do not** import Flask anywhere under `scraper/`. All changes are in `webapp/`, templates, CSS,
  `config.py` (only if a constant is genuinely needed — likely none), and `forms.py`.
- **Do not** change the record dict shape or the cache schema. The CSV and charts must read the
  existing shape and tolerate `None` milestone cells everywhere.
- Reuse existing CSS classes/patterns (`.card`, `.chart-grid`, `.chart-wrap`, `.badge`, `.muted`,
  `.primary`, `.link-button`) and add new rules to `webapp/static/styles.css` rather than inline
  styles where practical. Match the existing visual style.
- Keep scraping **sequential** (no parallel USCF requests) — unchanged.
- Keep accessibility parity with the current search UI (roles/aria already present).

## Files to touch
- `webapp/forms.py` — bump cap to 100, generalize `parse_player_inputs`.
- `webapp/routes.py` — add `/export.csv`, add `/analyze`, redirect `/compare`, repoint scrape-done
  redirect and index compare form.
- `webapp/cache.py` — optional `list_full_records()` helper for export/analyze.
- `webapp/templates/index.html` — count-first dynamic rows, delegated search JS, CSV + Analyze-all +
  Select-all controls, remove 2-pick cap.
- `webapp/templates/analyze.html` — **new** multi-player chart page (add/remove, save, tooltips, CSV).
- `webapp/templates/base.html` — add "Analyze" nav link.
- `webapp/static/styles.css` — styles for new controls/page.
- (`compare.html` may be retired in favor of `analyze.html`; if `/compare` is repointed to a redirect,
  the template can be deleted or left unused — your call, but don't leave a broken route.)

## Verification (do this before declaring done)
1. `python run.py`, open http://localhost:5050.
2. Set count to e.g. 3, confirm 3 rows render and each row's search autocomplete works (name search +
   numeric ID). Submit with a couple of real IDs (USCF and FIDE) and confirm the progress page shows
   the right number of players and redirects to `/analyze`.
3. On `/analyze`: toggle players on/off and confirm lines add/remove live across all 4 charts; hover a
   point and confirm a tooltip shows the exact value; click "Save" on a chart and confirm a
   non-transparent PNG downloads; click "Save all".
4. From index, "Analyze all" opens `/analyze` with all cached players selected.
5. "Download CSV" (index = all; analyze = selected) downloads a well-formed CSV with the milestone
   columns and empty cells for unreached milestones. Open it and spot-check a row against a player page.
6. Re-run the existing 1–2 player happy path to ensure no regression.
7. Use the Playwright MCP browser tools if helpful to drive/screenshot the UI.

## After implementing
- **Append a dated entry to `progress.md`** describing what changed (this repo's convention — see the
  `feedback_progress_md.md` memory). Keep it consistent with the existing entries' style.
- Summarize the changes and the verification results back to the user.
