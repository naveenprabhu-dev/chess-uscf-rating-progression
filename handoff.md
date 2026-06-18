# Handoff

Running notes for whoever picks this up next. For architecture/contracts read `CLAUDE.md`,
for scraping internals `docs/scraping.md`, and for the full change log `progress.md`. This file
is the short "where things stand and what just changed" summary.

## How to run
```bash
conda activate uscf-scraper            # env lives at /opt/anaconda3/envs/uscf-scraper
python run.py                          # http://localhost:5050  (:5000 is taken by macOS AirPlay)
```
If `python` isn't found, the interpreter is `/opt/anaconda3/envs/uscf-scraper/bin/python`.

## Current state (2026-05-30)
The Flask app supports: single/bulk player analysis (USCF + FIDE), milestone config per source, a
multi-player charts page, and CSV export. All four chart types render and are savable.

**Spec 007 (Phases 0 & 1) landed — the big model change:** the SQLite cache is now a **raw,
DOB/milestone-independent rating timeline** (`scrape_cache`), and the per-user milestone/age view is
**recomputed per request** by `scraper.compute_record`. The shared cache is **never shown to users** —
every list is scoped to the user's own **library** (`session["saved"]`, capped at 5 per browser) ∪
their most-recent analyzed batch (`session["recent"]`). Analyzing no longer auto-saves; **saving is a
separate ☆ button**. Phases 2–4 (Firebase login, 100-cap, anon→uid migration) are **not done** — see
`specs/007-user-auth/plan.md` and the latest `progress.md` session.

**Most recent session: 2026-06-18 (cap analyze charts at 5, below) — `/analyze` now draws at most
`ANALYZE_CHART_LIMIT` (5) player lines at once so a big batch stays legible.** Before that:
2026-06-17 (hide source label in UI) — dropped the "USCF" badge/legend
next to player names. Before that: production-readiness pass (gunicorn/Procfile, fail-closed
SECRET_KEY, cookie hardening, pip `requirements.txt`). Before that: require non-empty player rows +
remove source filter; remove manual refresh; cache TTL; paste-a-list bulk input + FIDE temporarily
disabled (all same day); spec 007 Phases 0 & 1 (`progress.md` 2026-05-30). The 2026-05-28 notes below
predate the cache/library rework — treat `progress.md` + `CLAUDE.md` as canonical where they differ.

---

## Session 2026-06-18 (follow-up) — Cap analyze charts at 5 players
A big batch (e.g. 100 players) would draw every line on the `/analyze` charts and become unreadable.
Added a configurable cap (default 5):
- `webapp/routes.py`: `ANALYZE_CHART_LIMIT = 5`. `analyze()` truncates the initial selection
  (`requested[:ANALYZE_CHART_LIMIT]`) so a large batch doesn't load all-checked, and passes
  `chart_limit` to the template. **The picker still lists every player** — the cap only limits how
  many lines are drawn.
- `webapp/templates/analyze.html`: `MAX_CHARTED` from `chart_limit`; `enforceLimit()` disables the
  unchecked checkboxes once the cap is hit and shows a note; "Select all" → "Select first N" and
  stops at the cap.
Client-side toggle logic + a server-side initial-selection truncation. No scraper/cache/contract
changes. Not yet smoke-tested in a browser (user is verifying locally).

---

## Session 2026-06-18 — Railway persistence: `railway.json`, `CACHE_TTL_DAYS` env override

Context: app is deployed on Railway at **elojourney.com**. The cache is a SQLite file at
`instance/cache.sqlite3` on the container disk, which is **ephemeral on Railway** unless a Volume is
attached — so the 7-day TTL never actually elapsed before a redeploy/restart wiped the cache. The
TTL *code* was always correct; only durability was missing.

- **Volume attached (by the user) at `/app/instance`** in the Railway dashboard, so the SQLite cache
  now survives redeploys/restarts. (Railway volumes are dashboard/CLI-only — no config-as-code field,
  so this can't live in `railway.json`; it's documented in CLAUDE.md "Railway deploy".)
- **`railway.json` added (committed)** — pins `NIXPACKS` builder, `gunicorn -c gunicorn.conf.py
  run:app` start command, and an `ON_FAILURE` restart policy (max 10 retries). Mirrors the Procfile,
  but makes the deploy explicit/config-as-code.
- **`CACHE_TTL_DAYS` is now env-overridable** (`config.py` `_env_int` helper) — set it as a Railway
  service variable to tune the freshness window without a code change; `0` disables expiry. Blank/
  unparseable falls back to the default 7. Read once at import, so a change needs a redeploy/restart.
- Docs updated: CLAUDE.md "Run in production" gained a **Railway deploy** subsection (volume mount
  path + env vars) and the cache-TTL bullet now flags the persistent-disk requirement.
- No app-logic change — `routes.py` still imports `CACHE_TTL_DAYS` from `config` and the
  `is_timeline_stale` path is untouched.

This commit also sweeps in prior uncommitted working-tree changes (FIDE source wiring in
`scraper/*` + `webapp/*`, template/CSS tweaks) at the user's request — see `progress.md` for those.

## Session 2026-06-17 — hide the source ("USCF") label next to player names

User asked to drop the source tag shown beside player names. Since the app is USCF-only in the UI for
now, the badge was redundant noise. Removed in three spots:

- **Analyze chart legend** — `analyze.html` dataset label `p.name + ' (' + source + ')'` → just `p.name`.
- **Analyze player picker** — the `<span class="badge badge-{source}">` next to each `.picker-name`.
- **Main page library list** — the same badge next to each saved player's name in `index.html`.

`compare.html` has the same badge + legend pattern but is a **dead template** (the `/compare` route
redirects to `/analyze`), so it was intentionally left as-is. The `.badge`/`badge-uscf` CSS is kept
(harmless, still referenced by the dead template). **Verified:** all templates compile; `GET /`
renders 200 with no `badge-uscf` in the output.

Files: `webapp/templates/analyze.html`, `webapp/templates/index.html`.

---

## Session 2026-06-17 — production-readiness pass (deploy blockers #1/#2/#3 + cookies)

First batch of the "what blocks publishing?" list. **Not deployed yet** — this just makes it
deployable + safe. Mechanical only; no scraper/record/cache-logic changes.

- **#1 Real WSGI server, debug off.** `run.py` no longer hardcodes `debug=True` — it defaults OFF
  (Werkzeug debugger = RCE if exposed), opt in with `FLASK_DEBUG=1`; `HOST`/`PORT` env-overridable.
  New **`Procfile`** (`web: gunicorn -c gunicorn.conf.py run:app`) + **`gunicorn.conf.py`** with
  **`gthread`** workers (the SSE scrape stream would block a sync worker), generous `timeout=120`
  (long scrapes), stdout logging, `$PORT`-aware bind.
- **#2 Fail-closed SECRET_KEY.** `webapp/__init__.py` now raises at boot if `APP_ENV=production` and
  `FLASK_SECRET_KEY` is unset (was silently falling back to `"dev-only-not-secure"`, a forgeable
  known key). Dev still uses the default when not in production.
- **#3 Portable deps.** New **`requirements.txt`** (flask/requests/bs4/lxml/dateutil/curl_cffi +
  gunicorn) — `environment.yml` is a macOS-ARM conda export that won't recreate on Linux.
- **Cookie flags.** `SESSION_COOKIE_HTTPONLY=True` (explicit), `SAMESITE="Lax"`, and
  `SECURE=_is_production()` (HTTPS-only in prod; off in dev so localhost http keeps the cookie). Added
  **`ProxyFix`** in production (trusts one upstream proxy's `X-Forwarded-*` → correct scheme/IP).
- New env knobs, all read in `create_app` / `gunicorn.conf.py`: `APP_ENV`, `FLASK_SECRET_KEY`,
  `FLASK_DEBUG`, `HOST`, `PORT`, `WEB_CONCURRENCY`, `THREADS`, `TIMEOUT`, `LOG_LEVEL`. CLAUDE.md gained
  a "Run in production" section.

**Verified** (`uscf-scraper` env): `py_compile` clean; behavior matrix — dev → dev secret + non-Secure
cookie + no ProxyFix; `APP_ENV=production` w/o secret → `RuntimeError` at boot; with secret → Secure
cookie + ProxyFix + real secret. gunicorn isn't in the local env (deploy-only), so `--check-config`
was skipped; config is import-clean.

**Still open from the publish list** (not done here): host choice (#4), **scraping survives from a
datacenter IP / Cloudflare** (#5 — the big unknown; must test from the real host), **rate-limiting
`/scrape`** (#6 — the IP-ban risk; recommended next), DOB-of-minors privacy + uschess.org ToS (#8),
and polish (tests, custom error pages, stale "FIDE source" cache-reset flash text).

Files: `run.py`, `webapp/__init__.py`, `requirements.txt`, `Procfile`, `gunicorn.conf.py`, `CLAUDE.md`.

---

## Session 2026-06-17 — require non-empty player rows (force fill-or-remove) + drop source filter

Two small UI follow-ups (both `webapp/templates/` / `static/`, no Python except the prior `forms.py`):

1. **Empty rows now block Analyze.** Previously the analyze-form validator silently skipped blank
   rows (`if (!raw) return`), so the default 2-row form could be analyzed with just one ID filled.
   Now **every row must have a USCF ID** — the submit validator flags any empty row red and blocks,
   showing a note on that row: *"Empty row — enter a USCF ID, or click Remove if not adding another
   player."* (or *"Enter a USCF ID to analyze."* when no row is filled at all, i.e. `filledCount === 0`).
   Refactored the handler around a `noteError(slot)` min-tracker and a pre-computed `filledCount`;
   dropped the old `anyFilled` fallback (now redundant). Typing an ID clears the note (existing
   `input` handler); removing the row clears it too. Bulk-fill is unaffected (it only ever creates
   filled rows). Verified (Playwright): both-empty → blocked w/ "Enter a USCF ID to analyze." on each;
   one-filled-one-empty → blocked w/ the remove note on the empty row; remove the empty row → submits.

2. **Removed the All/USCF/FIDE source filter** from the index "My library" header and the `/analyze`
   picker (USCF-only for now) — see the dedicated section further down for details.

---

## Session 2026-06-17 — remove the manual "Refresh from USCF" button

User asked to remove the manual refresh button entirely. With the 7-day TTL (previous session) now
auto-refreshing stale scrapes on analyze, the manual override was redundant. Removed:

- **Button** — the refresh `<form>` in `webapp/templates/player.html` (the "Refresh from USCF/FIDE"
  link-button). The "View charts" link + save/remove controls stay.
- **Route** — `refresh_player` (`POST /player/<source>/<player_id>/refresh`) deleted from
  `webapp/routes.py`. No other code/templates/JS referenced it (grep-verified); no dangling imports
  (its helpers — `_fetch_timeline`, `save_timeline`, `get_timeline`, exception classes — are all
  still used by `scrape_stream`).
- **Comments** updated in `config.py` + `routes.py` (the TTL is now described as the *only* re-scrape
  path for an already-cached player), and CLAUDE.md (overview, repo-layout route list, Cache rules).

**Consequence:** there is no longer any way to force an immediate re-scrape of a cached player from
the UI. A cached player is re-fetched **only** when it ages past `CACHE_TTL_DAYS` and someone
re-analyzes it. If an on-demand refresh is ever wanted again, it's a small revert (re-add the route +
button) — historical `specs/*/plan.md` still describe the old behavior and were intentionally left
as-is. **Verified:** `create_app()` imports clean, `main.refresh_player` no longer in the URL map,
`main.player`/`main.apply_milestones` still present, zero residual references in `webapp/`/`config.py`.

Files: `webapp/templates/player.html`, `webapp/routes.py`, `config.py`, `CLAUDE.md`.

---

## Session 2026-06-17 — cache freshness / TTL (auto re-scrape after 7 days)

User concern: the shared `scrape_cache` de-dupes scrapes across users (good — politeness toward
USCF/FIDE), but a cache hit served forever means a player who played a new tournament shows **stale
ratings**. Fix: cached timelines now expire.

- **`config.CACHE_TTL_DAYS = 7`** — the freshness window. `0`/`None` disables expiry (cache forever).
- **`cache.is_timeline_stale(timeline, max_age_days)`** — pure helper: parses the timeline's
  `scraped_at` (ISO-8601 UTC) and returns `True` if older than the window. Missing/unparseable
  timestamp → treated as stale (re-scrape rather than serve unknown-age data). Unit-tested across
  fresh/edge/stale/no-ts/bad-ts/ttl-off/naive-ts cases.
- **`routes.scrape_stream` worker** is the only auto-reuse path, and now reuses the cache **only on a
  *fresh* hit**. A stale hit falls through to `_fetch_timeline` + `save_timeline` (a real re-scrape),
  emitting a status line: *"Cached data is over 7 days old — re-scraping for the latest ratings."*
- **Unchanged:** read/compute paths (`_record_for`, `player`, `/analyze`, `/export.csv`,
  `save_to_library`) still use plain `get_timeline` and **never** trigger network. Timeline shape,
  record dict, and schema are all untouched — this is purely a reuse-decision change. (At the time,
  `POST .../refresh` still force-re-scraped on demand; **that button/route were removed later the same
  day** — see the session above. The TTL is now the only re-scrape path.)
- **Host-independent:** the check just reads `scraped_at` off the row, so it behaves identically on a
  VPS, Railway, or any persistent-disk host (the deployment question that prompted this).

Files: `config.py`, `webapp/cache.py`, `webapp/routes.py`.

---

## Session 2026-06-17 — paste-a-list bulk input + FIDE temporarily disabled (UI only)

User request: a button to paste a whole list of USCF IDs + birthdays at once (so you don't fill each
row by hand), and gray out FIDE as "coming soon" so it can't be selected. Web/UI layer only — no
`scraper/` internals, record dict shape, or cache schema touched.

### 1. "Paste a list" bulk input — TWO lists, paired by position (`index.html` + `styles.css`)
- New **"Paste a list"** button beside "Set" opens `#bulk-panel` with **two textareas**: all USCF
  IDs in `#bulk-ids`, all birthdays in `#bulk-dobs`. Each is tokenized on commas/spaces/newlines and
  **paired by position** (1st ID ↔ 1st birthday, …). `M/D/YYYY` is accepted and **normalized to
  `MM/DD/YYYY`**.
  - **Hard errors block the fill:** non-8-digit/non-numeric ID, invalid/impossible birthday
    (`02/30`, `13/40`), or zero IDs. Listed (first 4) in `#bulk-status` red.
  - **Count mismatch is a WARNING, not a block:** birthdays are optional, so an empty birthday box
    is fine; but if *both* lists are non-empty and lengths differ, it fills what it can and shows an
    amber warning **naming the unpaired entries**, e.g. `3 IDs but 2 birthdays — these IDs will have
    no birthday: #3 (13572468).` (extra IDs → no birthday; extra birthdays → dropped). On a warning
    the panel stays open so the user sees it; on a clean fill it auto-closes.
  - No new server endpoint — it populates the existing rows, so `/scrape` → `parse_player_inputs`
    validation/dedup is unchanged.
- Helpers in the index `<script>`: `normalizeDob`, `tokenizeList`, `parseBulkTwo`, `fillRows`,
  `openBulk`.
- CSS: `.bulk-panel` / `.bulk-cols` / `.bulk-col` / `.bulk-actions` / `.bulk-status` /
  `.hint-warning` (amber `#9a6a00`).

### 1b. Rows are paginated 5 at a time (`index.html` + `styles.css`)
- Only `PAGE_SIZE = 5` player rows are shown at once via a `#row-pager` bar (← Previous 5 / label /
  Next 5 →). **Hidden rows keep `display:none` — they stay in the DOM and still submit** (verified:
  a 12-player fill submits all 12 `uscf_id_*`), so all players are analyzed. The pager only appears
  when there's more than one page; prev/next disable at the ends; label reads `Players X–Y of N`.
- `renderPage()` toggles `.row-hidden` per row; `setRowCount`/remove/`fillRows` call it. "Add another
  player" jumps to the new row's page; "Set"/fill reset to page 1. The submit validator jumps to the
  page of the first errored row so its hint is visible even if it was on a hidden page.

### 1c. Future-birthday guard (bulk + per-row + server)
A birthday after today is "not viable" and is rejected on every path (today via `new Date()` /
`datetime.now()`):
- **Bulk:** `parseBulkTwo` adds a hard error `Birthday #N (MM/DD/YYYY) is in the future — not a
  viable birthday.` (alongside the malformed-date check) → blocks the fill, red status.
- **Per-row:** each `.dob-input` validates on `focusout` via `validateDobField` — a future (or
  malformed) date adds `.input-error` (red border, `#fdecea` tint) and a `.dob-hint` message;
  editing clears it (`input` handler), and a valid date is normalized to `MM/DD/YYYY` in place.
- **Submit:** the analyze-form validator runs `validateDobField` over every `dob_*`, blocks if any
  fails, and jumps to the first offending row's page so the red flag is visible.
- **Server (`webapp/forms.py`):** `validate_dob` now also rejects `parsed.date() > today` with the
  same message — defense for crafted POSTs. (Today's date is accepted; only strictly-future is not.)
- Helpers added to `index.html`: `isFutureDob`, `dobHintEl`, `clearDobError`, `markDobError`,
  `validateDobField`. CSS: `input[type="text"].input-error` (+ `:focus`).

### 1d. Enter never submits — analysis is button-only
A keydown handler on `#analyze-form` calls `preventDefault()` for `Enter` when the target is an
`INPUT` (ID/search, DOB, count). So typing a USCF ID and pressing Enter no longer starts the
analysis — only the Analyze button does. Textareas are excluded (Enter still makes newlines in the
bulk boxes), and the search dropdown's Enter-to-select is unaffected (its `rowsContainer` handler
fires first on bubble and runs `chooseOption` regardless of `preventDefault`).

### 2. FIDE disabled in the analyze UI ("coming soon")
- `index.html`: the FIDE radio is now `disabled` with a "coming soon" pill (`.coming-soon-tag`);
  USCF is always `checked`. Source note reads "USCF only for now — FIDE support is on the way."
- `routes.py`: `index()` pins the rendered form `source` to `"uscf"` (a stale `session["source"]
  == "fide"` no longer flips the form to FIDE labels/milestones); `scrape()` defensively coerces a
  crafted `source=fide` POST back to `uscf` with an info flash. **FIDE scraper code, `/api/search`,
  and `/search` are intentionally left intact** — only *selecting* FIDE as a scrape source is
  disabled. Re-enable later by reverting these two clamps + the radio.

### 2b. Removed the All/USCF/FIDE source filter from library + analyze (USCF-only now)
The `.filter-btns` (All/USCF/FIDE) source filter is gone from both the index "My library" header and
the `/analyze` picker sidebar, since everything is USCF for now. Removed the markup, the `empty-filter`
/ `picker-empty` "no matches" lines, and the filter JS (index `renderList` now just sorts — no
`activeFilter`; analyze dropped its source-filter block). Dropped the dead `.filter-btn(s)` and
`.empty-filter` CSS. Per-source `badge-uscf/-fide` badges remain (they only label existing rows). To
restore the filter when FIDE returns, re-add the button group + the click handlers.

### Verification (Playwright MCP, Chromium, against the running :5050 server)
`parseBulkTwo` unit-checked over 6 input pairs (clean 3+3 with `M/D/YYYY` normalization; 3 IDs/2
DOBs and 2 IDs/3 DOBs mismatch warnings naming the right unpaired entry; IDs-only no-warning; bad
ID + bad date both blocked; empty IDs blocked) — all correct. Pagination: a 12-player fill shows 5
per page with labels `1–5 / 6–10 / 11–12 of 12`, correct prev/next disabled states, back/forward
works, and **all 12 `uscf_id_*` are in the submitted FormData** (hidden pages still submit). Mismatch
warning renders amber (`rgb(154,106,0)`), panel stays open; clean fill auto-closes. FIDE radio
`disabled`, USCF `checked`. (Note: the Playwright screenshot tool was hitting a transient 5 s
font-load timeout this session — visuals were confirmed via computed styles instead.)

## Session 2026-05-28 — bulk entry, CSV export, `/analyze` page, button restyle, download fix

Delivered in this session (web/UI layer only — `scraper/` internals, the record dict shape, and
the cache schema were NOT touched). Full detail is in `progress.md` sections 12–14.

### 1. Bulk player entry — up to 100, count chosen first
- `webapp/forms.py`: `MAX_PLAYERS_PER_REQUEST` 2 → 100; `parse_player_inputs` now scans the form for
  `uscf_id_*` / `dob_*` indices instead of assuming exactly 2 rows (kept per-row errors, dedup, and
  the "enter at least one" fallback).
- `webapp/templates/index.html`: a **"How many players?"** input (1–100, default 2) + "Set" button
  renders that many rows from a hidden `<template>`; plus "Add another player" / per-row "Remove".
  The live name/ID search autocomplete was **refactored to event delegation** so dynamically added
  rows search correctly. Verified: searching in a freshly-added row returns live USCF results.

### 2. CSV dataset export
- `webapp/cache.py`: new `list_full_records()`.
- `webapp/routes.py`: new `GET /export.csv` — `?ids=` (namespaced `source:player_id`) exports those,
  else all cached. One row per player; base columns then 4 columns per milestone
  (`m{t}_months/_games/_age/_score_pct`) over the **union** of thresholds. `None`→empty cell,
  `score_pct` kept as a raw fraction. `text/csv` attachment `chess_players.csv`.

### 3. Multi-player `/analyze` page (supersedes the 2-player `/compare` cap)
- `webapp/routes.py`: `GET /analyze` serializes **all** cached players (shared union milestone axis)
  so add/remove is fully client-side; `?ids=` marks the initial selection. `/compare` now redirects
  to `/analyze`; the scrape-done SSE redirect and the index "Compare" button both target `/analyze`
  (2-pick cap removed).
- `webapp/templates/analyze.html` (new): player-picker sidebar (checkbox per player, source filter,
  Select all/none) + the 4-chart grid. Toggling a player adds/removes its line from every chart live.
  Stable per-player HSL colors with matching legend swatches. Rich hover tooltips show the exact
  value (e.g. `Rating 700 · Hikaru Nakamura (USCF): 1`). Per-chart "Save" + "Save all charts".
- `webapp/templates/base.html`: added an "Analyze" nav link.
- Index "Previously analyzed" header gained **"Analyze all"** (all cached ids pre-selected) and a
  JS **"Select all"**.

### 4. Button restyle (user request)
- `webapp/static/styles.css`: `.link-button` is now a real **secondary button** (surface fill,
  border, rounded, no underline, hover state) instead of underlined accent text. Underlined/clickable
  text is reserved for genuine navigation only. Applies everywhere the class is used
  (Set / Add / Remove / Reset / Save / Save all / Select all / Download CSV / Analyze all / Re-scrape).
  The milestone-editor "edit" affordance also lost its underline (now a small bordered chip).

### 5. Cross-browser download fix (Safari) — extensions were missing
- **Symptom:** saved charts/CSVs landed in Downloads with no `.png`/`.csv` and wouldn't open on Safari.
- **Cause:** Safari ignores the `<a download="…">` filename when the href is a `data:` URL (charts
  used `toBase64Image()`) and is unreliable naming a direct server link. Chromium honored both, which
  is why initial verification missed it.
- **Fix:** all downloads now go through a Blob + `URL.createObjectURL` object URL (Safari honors
  `download` on `blob:` URLs). `webapp/templates/analyze.html` uses `chart.canvas.toBlob(...)` for PNGs
  and `fetch`→blob for the CSV; `webapp/templates/index.html` got the same blob interceptor for its
  CSV link. The `/export.csv` route is unchanged (server `Content-Disposition` kept for right-click-save).

### Verification done this session (Playwright MCP, Chromium)
Count control renders N rows; autocomplete works on dynamically-added rows; `/analyze` charts all
cached players; toggling adds/removes lines live; hover tooltips show exact values; Save downloads a
valid non-transparent `chart-<name>.png`; CSV links download a valid `chess_players.csv` (selected vs
all reflected correctly; comma-in-name quoting intact). **Caveat:** the Safari-specific download fix
was validated by the established blob technique + Chromium regression check, not on Safari directly —
worth a real Safari smoke test if one is available.

---

## Known follow-ups / loose ends
- `webapp/templates/compare.html` is now **unused** (`/compare` redirects to `/analyze`). Harmless;
  delete if you want to tidy up.
- Scraping is intentionally **sequential** (no parallel USCF requests). Bulk-scraping ~100 players
  will be slow; the SSE progress page handles arbitrary counts but there's no batching/throttle UI.
- "Save all charts" fires 4 downloads ~300ms apart; browsers may prompt to allow multiple downloads.
- The repo root has several stray `*.png` screenshots from earlier work (e.g. `compare-charts.png`,
  `fide-*.png`); not added by this session.

## Don'ts (from CLAUDE.md — still apply)
No Google Sheets; no Flask imports in `scraper/`; no parallel USCF requests; don't change the
`scrape_player` dict shape without updating `cache.py` + templates; keep the `games_played != 0`
guards; don't reintroduce the `2025-05-16` cutoff.
