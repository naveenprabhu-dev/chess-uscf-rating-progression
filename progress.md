# Progress log

Running record of the changes made while turning this repo from a Google-Sheets CLI scraper into a Flask website with charts. Items are grouped by feature, roughly in the order they were done.

## 1. Environment & editor fix

**Problem:** `import gspread` showed "could not be resolved" in the editor even though the `uscf-scraper` conda env had it installed. Pylance was pointed at the wrong Python.

**Files**
- `.vscode/settings.json` (new) — pinned `python.defaultInterpreterPath` to `/opt/anaconda3/envs/uscf-scraper/bin/python` and added the workspace folder to `python.analysis.extraPaths`.

No conda env recreated — the existing one already had gspread.

## 2. Statusline (out of repo)

Configured `~/.claude/statusline-command.sh` and `~/.claude/settings.json` to show model name + 20-char progress bar + used/remaining context. Initial version used `jq`, which wasn't on PATH in the statusline shell; the user rewrote it to use `/usr/bin/python3` for the JSON parse. Now resolves friendly names from `model.id` (`claude-opus-4-7[1m]` → `Claude Opus 4.7 (1M)`).

## 3. Scraper port — sheets-free core (per CLAUDE.md)

**Goal:** lift the working logic out of `scrape_sheets.py` into a framework-agnostic `scraper/` package usable from Flask or a CLI. Drop the Google Sheets coupling; drop the hardcoded 2025-05-16 tournament cutoff; **keep** the `games_played != 0` divide-by-zero guards.

**Files**
- `archive/scrape_sheets.py` ← moved from repo root via `git mv` (history preserved).
- `config.py` — rewritten. Was a grab-bag of Sheets config; now only `DEFAULT_RATING_MILESTONES = [400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200]`.
- `scraper/__init__.py` (new) — re-exports `scrape_player`, `make_session`, `search_players`, `get_fide_birth_year`, `DEFAULT_RATING_MILESTONES`.
- `scraper/core.py` (new) — ported scraping/parsing logic. Highlights:
  - Module-level constants: `MSA_BASE`, `TOURNAMENTS_PER_PAGE = 50`, `INTER_PAGE_DELAY_SECONDS = 0.35`, realistic Chrome `User-Agent`.
  - `make_session()` builds a `requests.Session` with browser-realistic headers (UA, Accept, Accept-Language).
  - `time.sleep(INTER_PAGE_DELAY_SECONDS)` before each *pagination* page fetch (not every per-tournament fetch — those are unavoidable per-event).
  - Three custom exceptions: `PlayerNotFound`, `NoClassicalTournaments`, `ScrapeError`.
  - **Cutoff removed.** `_milestone_progress` iterates *all* tournaments.
  - **Zero-game guard preserved** — `if games_played != 0:` around both the `adjusted_win_rate` computation and the `all_classical_tournaments.append(...)`.
  - Pagination tightened: page-fetch only fires when `remaining > 0` (avoids a wasted final page-0 request).
  - `scrape_player()` returns the dict shape specified in `CLAUDE.md` (later extended — see §6).
- `webapp/` (new package, see §4).
- `run.py` (new) — `from webapp import create_app; app = create_app()`. Calls `app.run(host="127.0.0.1", port=5050, debug=True)` because **macOS AirPlay Receiver hijacks :5000** with a 403.
- `environment.yml` — renamed `uscf-scraper` → `ccc-webscraper`, dropped all gspread/oauth pip deps, added `flask==3.1.0`.
- `.gitignore` — added `__pycache__/`, `*.pyc`, `instance/`, `.DS_Store`.

## 4. Flask app scaffolding

**Files**
- `webapp/__init__.py` — `create_app()` factory. Sets a `SECRET_KEY` (env override `FLASK_SECRET_KEY`, else dev fallback), creates the instance dir, calls `cache.init_db(app)` on startup, registers the `main` blueprint, and registers `close_db` as `teardown_appcontext`.
- `webapp/cache.py` — SQLite via stdlib `sqlite3`. One table:
  ```sql
  CREATE TABLE players (
    uscf_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    data TEXT NOT NULL  -- full scrape_player dict serialized as JSON
  )
  ```
  Helpers: `get_db`, `close_db`, `init_db`, `get_player`, `save_player` (upsert via `ON CONFLICT`), `list_players` (ordered by `scraped_at DESC`), `invalidate`. DB lives at `instance/cache.sqlite3`.
- `webapp/forms.py` — `validate_uscf_id` (8-digit regex), `validate_dob` (later made optional — see §6), `parse_player_inputs` (handles up to 2 player rows, dedupes by ID), `parse_milestones` (comma/whitespace split, 100–3500 sanity range).
- `webapp/routes.py` — `main` blueprint:
  - `GET /` — form + cached players list. Reads optional `?id=...&slot=...` query for prefill (see §7).
  - `POST /scrape` — runs `scrape_player` for each row, saves to cache, redirects to `/compare` (see §5).
  - `GET /player/<uscf_id>` — detail page with milestone table. Computes `stale = (record.milestones_config != active session milestones)` and shows a banner if so.
  - `POST /player/<uscf_id>/refresh` — re-scrapes the player using stored DOB and active milestones.
  - `POST /player/<uscf_id>/delete` — invalidates one cached row.
  - `GET+POST /settings` — edit active milestones; stored in `flask.session["milestones"]`. Falls back to `DEFAULT_RATING_MILESTONES`.
  - `GET /compare` and `GET /search` — see §5 and §7.
- Templates (`webapp/templates/`):
  - `base.html` — header nav (Home / Search / Milestones), flash messages, `{% block content %}`.
  - `index.html` — analyze-form (2 rows), prefill support, cached-players checkbox compare form (§5b), search link.
  - `player.html` — detail page; "View charts" link → `/compare?ids=<id>`.
  - `_milestone_table.html` — table partial; Age column hidden when player has no DOB (§6).
  - `settings.html` — milestone editor.
  - `compare.html` — 4 charts (§5a).
  - `search.html` — name search form + results (§7).
- `webapp/static/styles.css` — dark theme; chart grid, search results table, compare checkbox row styles, blur overlay (§8).

## 5. Charts and comparison

### 5a. `/compare` route with 4 Chart.js charts

**Files**
- `webapp/templates/compare.html` (new) — renders 4 `<canvas>` cells in a 2×2 CSS grid: Months / Games / Age / Cumulative score %. Loads Chart.js 4.4.6 from `cdn.jsdelivr.net`. One inline `<script>` builds the four line charts from a `chart_data` dict serialized via `|tojson`. `spanGaps: true` keeps the line continuous through `null` values (milestones not yet reached). Score y-axis labels & tooltips multiply by 100 and suffix `%`.
- `webapp/routes.py` — `/compare?ids=…&ids=…` (up to 2). Looks each up in cache; flashes a message for any missing IDs; builds a union of milestone thresholds across players; produces parallel arrays per player for `months / games / age / score`.
- `webapp/static/styles.css` — `.chart-grid` (2 cols, 1 col under 720px), `.chart-cell h3`, `.chart-wrap { height: 280px; position: relative }`, `.swatch-0` / `.swatch-1` color chips matching chart line colors.
- `webapp/routes.py /scrape` — after successful scrape(s), redirects to `/compare?ids=<scraped...>` instead of back to the index (so charts are the natural landing page).

X axis is **categorical** (one tick per milestone, evenly spaced). If milestones become non-uniform we'd need to switch to a linear axis.

### 5b. Compare any two cached players

**Files**
- `webapp/templates/index.html` — wrapped the cached-players list in a `<form method="GET" action="/compare">`. Each row has `<input type="checkbox" name="ids" value="<uscf_id>" class="compare-pick">`. Submit button posts to `/compare`. Small inline `<script>` caps selections at 2 client-side (the route also caps server-side via `[:2]`).
- "remove" forms moved into a separate inline strip so the checkbox row stays clean.

## 6. Optional DOB with FIDE birth-year fallback

**Spec:** DOB becomes optional. If blank, follow the player's FIDE link from their USCF general page and use `01/01/<B-Year>`. If no FIDE info, omit age stats entirely.

**Files**
- `scraper/core.py`:
  - `calculate_age` now returns `None` when DOB is falsy.
  - New `get_fide_birth_year(session, uscf_id)`:
    1. Fetch `MbrDtlMain.php?<uscf_id>`, find `<td>FIDE ID</td>`, take the sibling `<td>`'s bold text.
    2. Fetch `https://ratings.fide.com/profile/<fide_id>`, locate "B-Year" label, read the sibling element.
    3. Any network/parse failure → return `None` (so the caller can fall back cleanly).
  - `scrape_player(session, uscf_id, dob=None, milestones=None)`:
    - If `dob` provided → `dob_source = "user"`.
    - Else try FIDE → on hit, `dob = "01/01/<year>"`, `dob_source = "fide"`, `fide_birth_year = <year>`.
    - Else `dob_source = "none"`, `dob` stays `None`. `calculate_age(None, …)` returns `None`, and all `milestones[*]["age"]` end up `None`.
  - Returned dict gained two fields: `dob_source` (`"user" | "fide" | "none"`) and `fide_birth_year` (int or `None`).
- `webapp/forms.py`:
  - `validate_dob` now has `required=False` default; blank input returns `(None, None)`.
  - `parse_player_inputs` no longer rejects a row missing only the DOB; it now requires only a valid USCF ID.
  - Error message switched from "Enter at least one USCF ID and DOB." to "Enter at least one USCF ID."
- Templates:
  - `index.html` — DOB label now `<span class="muted small">(optional)</span>`, with helper text on the page explaining the FIDE fallback.
  - `player.html` — DOB row hidden when `player.dob` is `None`; when `dob_source == "fide"` the row shows "(FIDE birth year)". "Age at first tournament" row only shows when present.
  - `_milestone_table.html` — sets `show_age = player.dob is not none`; Age column is omitted from the table header and rows when false; footer note added when hidden.

## 7. USCF player-name search

**Files**
- `scraper/core.py` — new `search_players(session, query, limit=20)`:
  - GETs `https://www.uschess.org/datapage/player-search.php?name=<query>`.
  - Parses every `<a>` whose href matches `MbrDtlMain\.php\?(\d{8})`. For each, walks up to the `<tr>` and pulls `td` cells: rating (index 1), state (7), expires (8). Returns a list of `{uscf_id, name, rating, state, expires}`.
- `webapp/routes.py` — new `GET /search`:
  - Reads `?q=`. <2 chars → flash an error. Otherwise calls `search_players`. Renders `search.html`.
  - `GET /` now reads `?id=<uscf_id>&slot=<0|1>` and passes a `prefill` dict to the template so the index form's input is pre-filled.
- `webapp/templates/search.html` (new) — search form + results table with name / USCF ID / rating / state / expires plus per-row "Use as Player 1" / "Use as Player 2" links → `/?id=…&slot=…`.
- `webapp/templates/base.html` — added "Search" link to the top nav.

**Note:** USCF showed a banner on the search response saying MSA is being migrated to MUIR at `https://ratings.uschess.org`. The current MSA endpoints still work but will need a port when MSA is retired.

## 8. Age chart "no data" states

Two related cases on `/compare`:

### 8a. No player has age data → blur + overlay
- `webapp/templates/compare.html` — computes `age_available = players|selectattr('dob')|list|length > 0`. When false, the age `chart-cell` gets a `no-data` class and an absolutely-positioned `Birthday could not be found` pill inside the chart wrap.
- `webapp/static/styles.css` — `.chart-cell.no-data .chart-wrap canvas { filter: blur(4px); opacity: 0.55; pointer-events: none }` and `.no-data-msg { position: absolute; centered; pill background }`.

### 8b. Mixed — some have age, some don't → small note under the chart
- `webapp/templates/compare.html` — also computes `missing_age = players|rejectattr('dob')|list`. When `age_available and missing_age`, renders `<p class="chart-note muted small">{{ names }} has no age data.</p>` below the age chart.
- `webapp/static/styles.css` — `.chart-note { margin-top: 0.5rem; font-style: italic }`.

Both paths verified with a synthetic no-DOB cache row (inserted, hit `/compare`, asserted markup, deleted).

## How to run

```bash
conda env create -f environment.yml          # one-time
conda activate ccc-webscraper
python run.py                                # serves on http://localhost:5050
```

`:5000` is hijacked by macOS AirPlay Receiver, hence `:5050`.

## Notable choices

- **Chart.js v4 from CDN** rather than a JS build step — keeps the project Python-only.
- **Categorical X axis** on charts so all milestones are evenly visible regardless of spacing. Linear axis is a 1-line change if user-defined milestones become non-uniform.
- **No background jobs** — scrapes block the request. The user is aware that real scrapes take 10s–2min per player; result is then cached so subsequent loads are instant.
- **Cache schema** stores the whole `scrape_player` dict as a JSON blob. Any future field additions (like `dob_source`/`fide_birth_year` in §6) are non-breaking because the data column doesn't have a fixed schema.
- **Server-side cap of 2 players** per compare request and per scrape submit; client-side cap enforced too, but the server is the source of truth.
- **No state-changing GETs** except for `/compare` and `/search` (which only read). Deletes and refreshes are POST.

## 9. Per-player progress bar (SSE) + inline name search

Two related index-page upgrades. The scrape used to block on the POST until both players were done; now POST returns immediately and progress streams over an event channel. The name-search side trip is folded into the form so the user doesn't navigate away to look up an ID.

### 9a. SSE-driven scrape progress

**Spec:** Submit form → progress page with one progress bar per player; bars fill as each player's tournaments are scraped; players run sequentially (one MSA request in flight at a time, per `CLAUDE.md`'s "don't parallelize" rule); auto-redirect to `/compare` on success.

**Files**
- `scraper/core.py`:
  - `scrape_player(..., progress_cb=None)` and `_milestone_progress(..., progress_cb=None)`.
  - Fires `progress_cb(0, total)` once total tournament count is known (after `get_tournaments_played`), then `progress_cb(total - remaining, total)` after each tournament iteration in `_milestone_progress`. Total comes from `get_tournaments_played`, so the bar represents every tournament row the scraper walks (classical + non-classical), which matches what the loop actually does.
  - CLI `__main__` block is unaffected (callback is keyword-only with `None` default).
- `webapp/routes.py`:
  - `POST /scrape` no longer scrapes inline. It validates entries, stashes `[[uscf_id, dob], ...]` in `session["pending_scrape"]`, and redirects to `/scrape/progress` (POST-redirect-GET).
  - `GET /scrape/progress` — renders `progress.html`. If session is empty (page revisited after stream completed), flashes a hint and redirects to `/`.
  - `GET /scrape/stream` — the SSE endpoint. Pops `pending_scrape` from session and streams events back. Implementation:
    - Spawns a daemon thread (worker) inside `app.app_context()` so `save_player()` can use `g.db`. Main scrape loop drains a `queue.Queue` and yields events.
    - Events emitted: `player_start`, `progress` (with `current`/`total`), `player_done`, `player_error`. Final `done` event carries `{redirect, scraped_ids}`.
    - Response headers include `X-Accel-Buffering: no` for forward-compat with reverse proxies; locally it doesn't matter.
    - `url_for` runs inside `stream_with_context(generate())`, which preserves the request context through the streaming generator.
  - Kept `_scrape_and_cache` because `/player/<uscf_id>/refresh` still uses it (synchronous, single player, no SSE).
- `webapp/templates/progress.html` (new) — one `<li>` per pending player, each containing a header (label · USCF ID · status), a `.progress-bar > .progress-fill` and a caption. Inline `<script>` opens `EventSource(scrape_stream)`, updates DOM per event, and on `done`:
  - If everyone succeeded → `window.location = data.redirect`.
  - If any errored but some succeeded → show "Continue to charts" button.
  - If everyone failed → show "Back to home".
  - Always `evt.close()` first to prevent EventSource's auto-reconnect from re-hitting the now-empty session.
- `webapp/static/styles.css` — `.scrape-progress` list, `.progress-bar`/`.progress-fill` (12px bar, `width` transition, accent fill, error variant), `.progress-actions` row.

**Why a thread + queue and not yielding directly:** the scrape is synchronous Python, and the progress callback fires from deep inside `_milestone_progress`. Generators can't `yield` across a function-call boundary, so the callback drops events onto a queue that the SSE generator drains. The worker also owns `save_player`, which needs Flask's app context.

**Session lifecycle:** `pending_scrape` is *popped* in `/scrape/stream`, so a refresh of the progress page mid-stream will hit "no pending scrape" rather than starting a duplicate scrape. EventSource reconnects after server-close are blocked by `evt.close()` in the `done` handler.

### 9b. Inline name search per player slot

**Spec:** Each `Player N` fieldset on `/` has a `<details>` "Search by name" expander right under DOB. Typing a query and hitting Search (or Enter) calls a JSON endpoint; results render inline; clicking "Use" populates the same row's USCF ID input and collapses the details panel. The standalone `/search` page still exists for users who want a full-page experience.

**Files**
- `webapp/routes.py` — `GET /api/search?q=…`. Returns `{"results": [...]}` (200) or `{"error": "...", "results": []}` with 400 (too short) / 502 (USCF unreachable). Delegates to `scraper.search_players`.
- `webapp/templates/index.html`:
  - Added `id="uscf_id_{{ i }}"` to each ID input so the JS can target it.
  - Added `<details class="inline-search">` with a search input + button per row.
  - New `<script>` block: `runInlineSearch(slot)` does `fetch('/api/search?q=…')`, renders `<ul class="inline-search-list">`, each item has a "Use" button whose click writes the chosen `uscf_id` into `#uscf_id_<slot>` and closes the panel. Submit-on-Enter wired via `keydown`.
- `webapp/static/styles.css` — `.inline-search`/`summary` styling (custom triangle marker since we hide the default), `.inline-search-controls` flex row, `.inline-search-list` scrollable result list.

## 10. FIDE-as-second-source spec (planning only, no code yet)

**Goal:** add FIDE alongside USCF with a per-row source toggle on the index form, so a player can be analyzed against either the USCF or the FIDE rating-progression pages.

**Why now:** the repo already has a one-shot FIDE bridge (`scraper.core.get_fide_birth_year`, see §6), and end-to-end probing confirmed FIDE exposes enough data for 3 of the 4 milestone columns. The fourth (`score_pct`) is genuinely unavailable on FIDE — handled with the same blur+overlay treatment from §8a.

**Files (planning artifacts only)**

- `specs/005-fide-source/research.md` (new) — verified endpoint inventory:
  - `GET https://ratings.fide.com/a_chart_data.phtml?event=<FIDE_ID>&period=` returns the whole rating history as JSON in one call. Verified for FIDE IDs 1503014 (Carlsen, 210 periods back to 2003-Apr at rating 2356) and 2016192 (Nakamura). Per-entry: `date_2`, `rating`, `period_games`, `rapid_rtng/games`, `blitz_rtng/games`, `name`, `country`. Rate-limited — empty response within ~30 s of a previous call. Needs `X-Requested-With: XMLHttpRequest` + a profile/chart `Referer` for reliability.
  - `GET https://ratings.fide.com/incl_search_l.php?search=<q>` returns an HTML fragment with `<table id="table_results">` of FIDE ID / Name / Title / Fed / Std / Rpd / Blz / B-Year. Also rate-limited.
  - `GET https://ratings.fide.com/profile/<id>` — already in use for B-Year extraction; no change.
  - Per-tournament W/D/L is NOT exposed anywhere player-facing on FIDE, so `score_pct` will always be `None` for FIDE records.
- `specs/005-fide-source/plan.md` (new) — implementation plan. Highlights:
  - New `scraper/fide.py` (`scrape_fide_player`, `search_fide_players`, `get_fide_history`, plus `FidePlayerNotFound` / `FideNoRatedHistory` / `FideScrapeError`).
  - Returned dict gains `source`, `player_id`, `fide_id`, `country`, `rating_type`. Shape stays uniform across sources.
  - Cache schema bumps to PK `(source, player_id)`. Old `uscf_id`-keyed rows are dropped on first run (cache lives in gitignored `instance/`, no data loss).
  - Routes shift to `/player/<source>/<player_id>` and friends. `/compare?ids=uscf:12345678&ids=fide:2016192` keeps the order-stable URL contract.
  - Per-source default milestone ladder: USCF stays `400..2200`; FIDE is `1400..2700` to match the current FIDE floor. Stored as separate session keys.
  - Mixed-source compare is allowed with a banner; the Score chart applies the existing `no-data` overlay when *all* selected players are FIDE.
  - Five implementation phases (scraper / cache / routes / templates / docs polish) each with explicit verification criteria.

**What did NOT change in this entry**

- No code was modified. `scraper/`, `webapp/`, `config.py` are untouched. This is planning-only — implementation will land in a follow-up branch tracking `specs/005-fide-source/plan.md`.

## 2026-05-21 — Spec 006: USCF JSON API as fast-path source

Implemented `specs/006-uscf-api-source/plan.md`. `scrape_player` now tries the unauthenticated `ratings-api.uschess.org` JSON API first and falls through to the existing HTML scraper on any failure.

**New file `scraper/uscf_api.py`** — single public entry `scrape_player_api(uscf_id, dob=None, milestones=None, progress_cb=None)` returning the canonical dict shape. Internal helpers:
- `_get(path, **params)`: plain `requests.get` with 15-second timeout, single retry on ConnectionError/Timeout/5xx/499. Returns parsed JSON on 200, None on 404, raises `ApiUnavailable` otherwise.
- `_paginate(path, ...)`: yields items across `?Offset=N&Size=200` pages until `hasNextPage=False`. Increments offset by `len(items)` because the API caps page size at 100 even when Size=200 is requested.
- `_member_detail(uscf_id)`: builds `name = f"{firstName} {lastName}".title()` to normalize the API's all-caps output.
- `_list_sections(uscf_id)`: filters to `ratingSystem == "R"` (regular OTB) and sorts ascending by `startDate`.
- `_list_games(uscf_id)`: groups by `(event.id, section.number)` with W/D/L/games counts. `Unknown` outcomes count toward `games` but not W/D/L (matches HTML scraper's `\b[WLD]\s+\d+` regex semantics). Empty index raises `ApiUnavailable("games index empty")`.
- `_compute_milestones`: walks sections chronologically, accumulates per-section game counts, and fills the milestone arrays with `games_played != 0` guard. Reuses `months_difference` and `calculate_age` from `scraper.core`.
- `scrape_player_api`: orchestrator. DOB precedence (user → FIDE → none) matches HTML path. Wraps everything in a try/except that re-raises `ApiUnavailable` and converts any other exception to `ApiUnavailable("unexpected: ...")` so nothing escapes that isn't a fallback signal.

**`scraper/core.py`** — single 4-line try/except block prepended to `scrape_player` after its docstring. The rest of the HTML implementation is byte-for-byte unchanged. `_milestone_progress`, `games_played_in_tournament`, `get_first_classical_tournament_details`, `get_tournaments_played`, `get_name`, `make_session`, `_get_msa`, `_is_cloudflare_challenge` all untouched.

**`scraper/__init__.py`** — re-exports `scrape_player_api` and `ApiUnavailable`.

**Deviations from the spec:**
- `API_TIMEOUT_SECONDS = 15` instead of the spec's 5. Caruana's `/games` endpoint sporadically returns HTTP 499 (upstream edge timeout) when our per-request timeout is below ~10s. Pages individually clear in ~0.05–0.6s under normal load, but variance is high. 15s gives enough headroom for the slowest observed page and still falls back quickly when the API is genuinely down.
- `_get` retries on 499 in addition to 5xx and connect/timeout failures. The spec listed only 5xx; 499 is a Cloudflare/nginx upstream-timeout marker that's transient in practice.

**Verification results:**
- Magnus Carlsen (USCF 15218438): API path, **5.4s**, 8 sections, all 10 milestones populated.
- Fabiano Caruana (USCF 12743305, 560 sections, 2288 games): API path, **7.7s** (vs ~6 minutes via HTML). All 10 milestones populated.
- Post-October-2025 coverage confirmed: Caruana's API record includes 2025 U.S. Championship (2025-10-12, postRating 2876), 2026 Saint Louis Masters (2026-02-25, 2878), and American Cup 2026 (2026-03-03, 2872) — none of which appear on the legacy HTML pages.
- HTML fallback confirmed: monkey-patching `_member_detail` to raise `ApiUnavailable` re-routes Magnus through the HTML path, producing a record indistinguishable in shape from the API record (initial_rating identical at 3001).
- SSE progress: API path fires `progress_cb(0, N)` then `progress_cb(i, N)` for i=1..N, identical contract to the HTML path.
- Flask app still serves 200 on `/` and `/search` after the module addition.
- Milestone diff (API vs HTML) on Caruana: months/games match exactly at milestones 600, 1000 within 1–2 games at 1200/1400/1600/1800/2000/2200. Small divergences are expected: the API processes data per section (multiple sections of one event produce multiple postRating readings), where the HTML scraper dedupes by tournament URL (one postRating per event). The plan's verification step explicitly allows "small floating-point differences on `score_pct`" — the per-section vs per-tournament granularity is the source of those floats.

**No dependency change.** `requests` was already pulled in transitively. `environment.yml` untouched.

## Known follow-ups

- MSA endpoints (`/msa/...` and `/datapage/player-search.php`) will be retired in favor of MUIR (`ratings.uschess.org`). Need to re-target the scraper and search when that happens.
- FIDE as a second source — see `specs/005-fide-source/plan.md` (§10 above).
- If a future feature needs bulk import, the spec says CSV upload — *not* re-adding Google Sheets.
- The earlier transient `'NoneType' object has no attribute 'find_next_sibling'` error was investigated; no real logic difference vs. the legacy `scrape_sheets.py` — almost certainly a transient USCF response. A one-line retry-on-`None` is held in reserve if it recurs. **Update (2026-05-21):** root cause identified and fixed — see entry below.

## 2026-05-21 — Cloudflare gate on /msa/ at scrape-time volume

**Root cause of the `find_next_sibling` AttributeError.** Reproduced on Fabiano Caruana (USCF 12743305, 1000 tournaments): after ~4 history-page fetches + ~150 tournament-detail fetches on one session, USCF's `/msa/` started returning HTTP 403 with Cloudflare's "Just a moment…" IUAM interstitial. The challenge page has no `<!-- Detail: N -->` comments, so `soup.find(...)` returned `None` and the next `.find_next_sibling("tr")` crashed. Not transient — fully reproducible. Old `scrape_sheets.py` would hit the same wall today; it "worked before" because Caruana had fewer tournaments and/or Cloudflare hadn't yet started gating MSA at this request volume. The `cutoff = 2025-05-16` early-break in the old script does NOT help — iteration runs oldest→newest tournament, so the cutoff only fires near the end (remaining ≈ 5–20), well after the Cloudflare block.

**Fix.** `scraper/core.py` swaps `requests` for `curl_cffi` (Chrome TLS/HTTP-2 impersonation, per the `docs/scraping.md` escalation ladder step #2). `make_session()` returns `curl_cffi.requests.Session(impersonate="chrome")`. Added `_get_msa(session, url, **kwargs)` helper: detects 403/503 + "just a moment"/"cloudflare"/"cf-chl" body markers, retries up to 2× with backoff, and raises `CloudflareChallenge` with a clear message if the gate still won't open. All MSA fetches in `search_players`, `get_tournaments_played`, `get_name`, `get_first_classical_tournament_details`, `games_played_in_tournament`, and the milestone-progress loop now route through `_get_msa`. `get_fide_birth_year` was left bare — it's wrapped in a broad `try/except` and the FIDE call is on a different host.

**Verified:** full Caruana scrape now completes (1000/1000 tournaments, ~6 minutes, all milestones populated 400→2200). HTTP 200 on every fetch; zero Cloudflare interstitials observed.

**Dependency.** `environment.yml` pip section gains `curl_cffi>=0.9,<0.10`. The pin is because `curl_cffi>=0.10` ships a wheel that fails to load on this macOS (`_CFRelease` symbol-not-found); 0.9.0 works. Re-evaluate the pin if/when the upstream issue is resolved.

## 2026-05-28 — FIDE cumulative score % (correcting "FIDE has no W/D/L")

Spec 005 and its research doc both claimed FIDE exposes no per-event W/D/L, so FIDE records carried `score_pct: None` and the UI blurred the Score chart for all-FIDE comparisons. **That was wrong.** FIDE's `/calculations` tab is JS-rendered, but it fetches data from an AJAX endpoint that *does* expose results.

**Discovery.** `GET https://ratings.fide.com/a_indv_calculation.php?id_number=<id>&rating_period=<YYYY-MM-01>&t=0` returns one `<table>` per tournament in that period. Each table's summary row carries `w` (score = wins + 0.5·draws) and `n` (games); detail rows give per-opponent results. `t=0` = standard/classical.

**`scraper/fide.py`**
- New `get_fide_calculations(session, fide_id, period) -> (score, games)` + `_parse_calculations(html)`. Sums only summary rows (first cell is an integer rating) to avoid double-counting the per-opponent detail rows. One retry on empty body; raises `FideScrapeError` on HTTP error / persistent empty.
- `scrape_fide_player` now computes real `score_pct`: walks periods chronologically, and for each period with `period_games > 0` fetches the calc page, accumulating `cum_score`/`cum_score_games`. At each milestone, `score_pct = cum_score / cum_score_games`. **Early-exits the period loop once all milestones are filled** — Carlsen tops out at 2700 after ~19 calc calls, not his full ~150-period career. If a calc page can't be fetched, score is reported up to that period and `None` after (graceful, never crashes).
- `progress_cb` now fires once per rated period processed (was a placeholder (0,1)/(1,1)), so the SSE bar is meaningful for FIDE.

**UI — removed the "FIDE has no score" special-casing**
- `_milestone_table.html`: `show_score` is always true; dropped the "Cumulative score is unavailable for FIDE" footer note.
- `compare.html` + `routes.py`: removed the `all_fide` Score-chart blur/overlay and the `all_fide` template var. The mixed-source caveat banner stays.
- `player.html`: FIDE caveat note now says score is reconstructed from per-period calculation pages (was "unavailable").
- `progress.html`: FIDE message updated ("fetches each rated period… bar advances per period").

**Rate-limit claim corrected.** The research doc's "second hit within ~30 s returns empty bytes" was never measured. Burst test 2026-05-28: 54 back-to-back requests across `a_chart_data.phtml` and `a_indv_calculation.php` (incl. 40 distinct-period calls, zero delay) → 0 empty, 0 non-200, ~0.6 s/req. No throttle observable at this scraper's volume. Kept the single retry-on-empty as cheap insurance; dropped the assumption of a 30 s window.

**Docs.** Updated `specs/005-fide-source/research.md` (corrected rate-limit + new endpoint #4 + coverage table now 4/4), `specs/005-fide-source/plan.md` (correction banner), `CLAUDE.md` (dict-shape + FIDE section), and `docs/scraping.md` (FIDE subsection).

**Verified.** `scrape_fide_player("1503014")` → Carlsen, 11.9 s, all 11 milestones with real scores (2400 @ 63.4%, 2700 @ 60.8%). No dependency change.

## 2026-05-28 — Spec 005: FIDE as a full second source (site-wide toggle)

Implemented `specs/005-fide-source/plan.md` end-to-end. FIDE (ratings.fide.com) is now a first-class source alongside USCF: a user can analyze a FIDE player the same way they analyze a USCF player (months / games / age to each milestone; `score_pct` is always `None` for FIDE).

### Deviation from the plan — SITE-WIDE source toggle (not per-row)

The plan specified a *per-row* source toggle (each of the 2 rows independently USCF/FIDE). At the user's direction this was changed to a **site-wide** toggle:

- ONE active source per session: `session["source"]` ("uscf" default, or "fide"), set by a single radio control on the index form (and the search page). Both player rows use the session source — no mixing within one scrape.
- Validation, scraper dispatch, and the active milestone list all key off `session["source"]`, not per-row. The plan's `source_N` per-row fields were dropped.
- The SCRAPE/INPUT flow is single-source; the COMPARE/CACHE layer stays genuinely source-aware exactly as the plan specified, because two arbitrary cached players (one USCF, one FIDE) can still be compared from the index list. `/compare` ids are namespaced `source:player_id`, the mixed-source caveat banner is kept for that edge case, and the Score-chart blur fires when ALL compared players are FIDE.

### Phase 1 — scraper module

- **`scraper/fide.py`** (new). `get_fide_history` (single `a_chart_data.phtml` JSON call, retries once on the empty-body rate-limit fingerprint), `search_fide_players` (parses `<table id="table_results">`), `scrape_fide_player` (canonical dict, `score_pct=None`, `first_tournament_date=YYYY-MM-01` from first period's `date_2`, cumulative `period_games`), `get_fide_birth_year_from_profile` (DOB fallback straight to the FIDE profile since we already have the FIDE ID). Exceptions `FidePlayerNotFound` / `FideNoRatedHistory` / `FideScrapeError`. Plain `requests` + `X-Requested-With`/`Referer` headers — no curl_cffi/Cloudflare. Reuses `scraper.core.calculate_age` and `months_difference`.
- **Dict shape extended (additive).** All three producers — `scraper.core.scrape_player`, `scraper.uscf_api.scrape_player_api`, `scraper.fide.scrape_fide_player` — now emit `source`, `player_id`, `fide_id`, `country`, `rating_type`. USCF producers set `source="uscf"`, `player_id=str(uscf_id)`, `fide_id=None`, `country=None`, `rating_type="classical"`. The spec-006 API-first dispatch at the top of `scrape_player` is untouched.
- **`config.py`** — `DEFAULT_USCF_MILESTONES` (400–2200) + `DEFAULT_FIDE_MILESTONES` (1400–2700); `DEFAULT_RATING_MILESTONES` is now a back-compat alias for the USCF ladder.
- **`scraper/__init__.py`** re-exports the FIDE functions, exceptions, and both ladder constants.

### Phase 2 — cache schema migration

- **`webapp/cache.py`** — PK is now `(source, player_id)`. `init_db` detects the legacy `uscf_id`-keyed schema via `PRAGMA table_info` and drops+recreates, setting `app.config["CACHE_WAS_RESET"]`. `get_player(source, player_id)`, `save_player(record)` (keys off `record["source"]`/`["player_id"]`), `invalidate(source, player_id)`, `list_players()` (rows now carry `source`/`player_id`).
- **`webapp/__init__.py`** — a `before_request` hook flashes the one-time "Cache was reset to support the new FIDE source." notice and clears the flag.

### Phase 3 — routes & forms

- **`webapp/forms.py`** — `validate_fide_id` (`^\d{5,10}$`), `validate_player_id(raw, source)`, `normalize_source`, and `parse_player_inputs(form, source)` validating IDs per the site-wide source.
- **`webapp/routes.py`** — `_active_source()`, `_active_milestones(source)`, `_scrape_one(source, ...)` dispatching to the right scraper. `/scrape` reads the `source` radio, stores it on the session + in `pending_scrape`, and the SSE worker dispatches per source and emits namespaced `source:player_id` ids. Routes are `/player/<source>/<player_id>[/refresh|/delete]` (bad source → 404). `/api/search` and `/search` take `source=`. `/compare` parses `source:id` ids, computes `mixed_sources` / `all_fide`. `/settings` POSTs a `which` (uscf|fide) and saves to the matching session key.

### Phase 4 — templates

- `index.html`: site-wide source radio toggle at the top of the form; JS live-updates ID labels/placeholders and routes inline search through `/api/search?source=`. Cached list rows use `source:player_id` checkbox values and show a source badge.
- `player.html`: source badge, source-aware ID/label rows, FIDE caveat note, `View charts` link namespaced.
- `_milestone_table.html`: new `show_score` flag (hides the Score column when `source == "fide"`), with a footer note.
- `compare.html`: mixed-source banner (`mixed_sources`), Score-chart no-data blur when `all_fide`, source badges + `(USCF)`/`(FIDE)` legend labels.
- `search.html`: source toggle + FIDE column set (Name / FIDE ID / Title / Fed / Std / Rpd / Blz / B-Year); "Use as Player N" links propagate `source`.
- `settings.html`: two independent milestone editors (USCF / FIDE).
- `progress.html`: source-aware heading/copy.
- `webapp/static/styles.css`: `.badge` / `.badge-uscf` / `.badge-fide`, `.source-toggle`, `.source-option`, `.note-fide`.

### Verification

- **FIDE unit (`scrape_fide_player(requests.Session(), "1503014")`):** name "Carlsen, Magnus", initial_rating 2356, first_tournament_date "2003-04-01", country NOR, dob_source "fide" (B-Year 1990), `milestones["2400"]["months"] == 6`, all 11 FIDE milestones reached, every `score_pct` None. `search_fide_players(..., "Carlsen, M")` → 2 rows incl. fide_id 1503014. `get_fide_history` retry path asserted (empty body → exactly 2 GETs → rate-limit error; empty-then-OK → 2 GETs → success).
- **USCF regression:** `scrape_player(make_session(), "15218438")` still returns 10/10 milestones, initial 3001, now with `source="uscf"` + the additive fields. Spec-006 API fast-path intact (~5s).
- **Cache:** legacy-schema detection drops/recreates + sets the reset flag; non-legacy doesn't; `save → get → invalidate` round-trips for both sources.
- **Browser (Playwright @ :5050):** switched site to FIDE, scraped 1503014 → auto-redirect to `/compare?ids=fide:1503014` with Months/Games/Age charts populated and the Score chart blurred ("FIDE does not expose per-event scores"). FIDE player page shows the FIDE badge, caveat note, and NO Score column. Switched back to USCF, scraped 15218438 → all four charts render including a populated Score chart (not blurred). Screenshots: `fide-player-page.png`, `fide-compare-score-blur.png`, `uscf-compare-with-score.png` (repo root, not committed).

### Caveats / risks for the next maintainer

- **Sticky session source.** Because the source is site-wide and sticky, after a FIDE scrape the index form pre-selects FIDE on reload (correct, but easy to mistake for a bug — an 8-digit USCF ID is also a valid FIDE-length ID and will be scraped as FIDE if the toggle isn't flipped).
- **FIDE rate limiting.** Probe calls must be spaced ~30 s apart; the in-app retry handles a single collision but back-to-back scrapes of two FIDE players rely on the per-player flow being sequential (it is).
- **No `environment.yml` change.** `requests` and `beautifulsoup4`/`lxml` were already present. FIDE adds no new dependency.
- The stray `sse-progress.png` / `compare-charts.png` in the repo root are from a prior run and were left untouched.

## 2026-05-28 — Color theme rework (coolors.co palette)

- Replaced the dark blue-gray theme with the warmth palette: charcoal `#312f2f`, tan `#edcb96`, slate blue `#6494aa`, sage green `#659b5e`.
- `--bg` → `#faf4e8` (light tan), `--text` → `#312f2f` (charcoal), `--accent` → `#6494aa` (slate blue), `--accent-hover` → `#4e7d94`, `--info` → `#659b5e` (green), `--error` → `#c0392b`.
- Updated all hardcoded hex values: `.banner` (tan warning tones), `.no-data-msg` overlay (charcoal), `.swatch-0/.swatch-1` (slate blue / green), button/link text (`#ffffff`), `.badge-uscf`/`.badge-fide` (light tinted badges).
- Updated chart colors in `compare.html`: series colors → `#6494aa` / `#659b5e`; axis/tick/grid → muted charcoal and tan border; legend → charcoal.

## 9. Index page UX overhaul

**Changes**

### Unified live-search player input
- Removed the separate collapsible "Search by name" `<details>` section from each player row.
- The player ID field is now a single text input that auto-searches as you type (debounced 280 ms). If input is purely numeric it's treated as a direct ID; otherwise it hits `/api/search` and shows a floating dropdown.
- Picking a result from the dropdown fills a hidden `<input>` with the canonical ID and shows the player's name in the visible field.
- Source toggle clears both player inputs (IDs are not portable across USCF/FIDE).

### Inline milestone editor
- Milestone editing no longer requires navigating to `/settings`. A collapsible `<details>` panel at the bottom of the analyze card shows the active milestones and two inline forms (one per source).
- Switching the USCF/FIDE radio immediately shows the correct panel and updates the milestone chip list in the summary.
- `/settings` POST now accepts a `next` form field; when `next=/` it redirects back to the index after saving (or on validation error).

### Previously analyzed: sort + filter
- Added source filter buttons (All / USCF / FIDE) and a sort `<select>` (newest, oldest, name A–Z/Z–A, rating high/low, source) to the "Previously analyzed" section.
- Sorting and filtering are client-side DOM reordering — no extra server round-trip.
- Remove buttons moved inside each `<li>` (linked via HTML5 `form=` attribute to hidden `<form>` elements outside the compare form).
- `list_players()` now extracts `initial_rating` via `json_extract` for use in rating sort.

### Default milestones
- `config.py` defaults updated: USCF 400–3000 by 100s (27 values), FIDE 1400–2900 by 100s (16 values). Sessions that already have stored milestones are unaffected.

**Files changed**
- `config.py` — new defaults
- `webapp/cache.py` — `list_players` adds `initial_rating` from JSON blob
- `webapp/routes.py` — `index()` passes both milestone lists; `settings()` supports `next` redirect
- `webapp/templates/index.html` — full rewrite
- `webapp/static/styles.css` — new sections for search dropdown, sort/filter controls, inline milestone editor

## 10. Index UX overhaul — Opus pass (bug fixes + polish)

Refined the section-9 changes after a review. Notable fixes and additions:

### Search dropdown
- Added **keyboard navigation**: ↑/↓ move the highlighted result, Enter selects it, Esc closes. Active row is highlighted (`.search-dropdown-item.active`).
- Added a **request-sequence guard** (`_seq[slot]`) so a slow earlier response can't overwrite a newer search's results.
- Selection now uses `mousedown` (with `preventDefault`) instead of `click` so picking a result isn't lost to the input's blur/outside-click handler.
- Per-field hint line shows "Selected #ID" on pick and an error if needed.

### Submit validation
- The analyze form now blocks submit (with an inline error) when a player field contains a typed name that was never resolved to an ID, or when no player is filled at all. Numeric input is accepted as a direct ID. Prevents silently submitting an empty ID.

### Inline milestone editing — now AJAX
- Saving milestones no longer reloads the page (which previously wiped any typed-in player search). `/settings` POST returns JSON when called with `X-Requested-With: fetch` / `Accept: application/json`; the page updates the textarea and summary chips in place and shows "Saved ✓".
- Added a **Reset to default** button per source (fills the textarea from the source's default ladder).
- Non-AJAX POST (the standalone `/settings` page) still flashes + redirects as before; `next=/` support retained for graceful degradation.

### Sort & filter
- "Sort by rating" now uses **peak rating reached** (highest milestone the player actually hit), not initial rating — a far more meaningful strength signal. `list_players()` computes it by scanning the milestones blob.
- Added a **Federation** sort (FIDE `country`; USCF rows sort last). `list_players()` now also surfaces `federation`.
- Added an **empty-state message** when a filter matches nothing.
- Cached-row meta line now reads `#ID · FED · reached NNNN · YYYY-MM-DD`.

### Cleanup
- Removed the now-redundant **Search** nav link (inline search fully replaces the standalone page; the `/search` route stays live as a fallback/deep-link target).

**Files changed**
- `webapp/cache.py` — `list_players` parses each blob for `peak_rating` + `federation`
- `webapp/routes.py` — `settings()` returns JSON for fetch requests; `index()` passes default ladders
- `webapp/templates/index.html` — keyboard nav, seq guard, submit validation, AJAX save, reset buttons, federation sort, empty state
- `webapp/templates/base.html` — dropped Search nav link
- `webapp/static/styles.css` — active dropdown row, field hints, save-status, empty-filter, milestone summary layout

## 11. Fix: `[hidden]` overridden by author `display` rules

**Bug found via browser test.** With FIDE selected, the milestone editor showed *both* the USCF (400–3000) and FIDE (1400–2900) chip lists at once. Root cause: the HTML `hidden` attribute resolves to `display: none` from the UA stylesheet, but author rules with an explicit `display` win — `.milestone-chips { display: inline-flex }` and `.player-list li { display: flex }` both silently defeated `hidden`. This also broke the source-filter on "Previously analyzed" (filtered-out rows stayed visible).

**Fix:** one global rule in `styles.css` — `[hidden] { display: none !important; }`.

Verified in-browser: FIDE shows only the FIDE ladder; toggling to USCF swaps the label + chips; the USCF/FIDE source filter correctly hides non-matching rows.

## 12. Bulk entry, CSV export, and the multi-player `/analyze` page (2026-05-28)

Three web/UI features (no `scraper/` changes; record shape and cache schema untouched).

### Bulk player entry (up to 100), count-first
- `webapp/forms.py` — `MAX_PLAYERS_PER_REQUEST` 2 → 100. `parse_player_inputs` no longer assumes 2 rows: it scans `form.keys()` for `uscf_id_`/`dob_` indices and iterates those (capped at 100), keeping per-row `Row N:` errors, the dedup-by-id pass, and the "Enter at least one …" fallback.
- `webapp/templates/index.html` — replaced the fixed `range(2)` rows with a **"How many players?"** number input (1–100, default 2) + "Set" button, an "Add another player" / per-row "Remove" affordance, and a hidden `<template id="player-row-template">` using `__IDX__`/`__NUM__` placeholders. JS clones/re-indexes rows so `uscf_id_0..N-1` / `dob_0..N-1` stay contiguous; growing never destroys entered data. The search autocomplete was **refactored to event delegation** on `.player-rows` (input/keydown dispatch via `closest('.player-search-input')`) so dynamically added rows work — debounce, numeric-ID shortcut, `_seq` out-of-order guard, dropdown keyboard nav, outside-click close, and submit validation all preserved. `applySource()` now clears **all** rows, not just 0/1. A small `initRows()` IIFE seeds the initial rows and honors the `?id=&slot=` prefill.

### CSV dataset export
- `webapp/cache.py` — new `list_full_records()` returning every cached record (full JSON), newest first.
- `webapp/routes.py` — new `GET /export.csv`: with `?ids=` (namespaced `source:player_id`) exports those, else all cached. One row per player; columns are the flat base fields then four columns per milestone threshold (`m{t}_months/_games/_age/_score_pct`) over the **union** of all exported players' thresholds (sorted). `None` → empty cell; `score_pct` kept as the raw fraction. Empty case flashes + redirects. Stdlib `csv` into `io.StringIO`, returned as a `text/csv` attachment `chess_players.csv`.
- "Download CSV" links: index header (all cached) and the `/analyze` page (live selection, href rebuilt from checked boxes in JS).

### Multi-player `/analyze` page (supersedes the 2-player `/compare` cap)
- `webapp/routes.py` — new `GET /analyze`: serializes **all** cached players' chart data (shared union milestone axis) so add/remove is fully client-side, marks `initial_selected` from `?ids=`, flashes+redirects when the cache is empty. `/compare` is now a thin redirect to `/analyze` preserving `ids`. The scrape-done SSE redirect now targets `/analyze`. The index "Compare" form points at `/analyze` with the 2-pick cap removed (helper text "Pick any players") plus a "Select all" button.
- `webapp/templates/analyze.html` (new) — player-picker sidebar (checkbox per player, source filter, Select all/none) + the 4-chart grid (months/games/age/score). Toggling a player adds/removes its dataset from all four charts via `chart.update()`. Stable per-player color via HSL spread; swatches match. Rich hover tooltips (`interaction:{mode:'nearest',intersect:false}`, `pointHoverRadius`, score shown as `xx.x%`). Per-chart "Save" + "Save all charts" download non-transparent PNGs via a `destination-over` background-fill Chart.js plugin and `chart.toBase64Image()`. Live mixed-source banner recomputed from the selection.
- `webapp/templates/base.html` — added an "Analyze" nav link.
- `webapp/static/styles.css` — styles for the count control, dynamic-row Remove button, and the `/analyze` layout (sticky picker, picker list/swatches, chart-cell headers, toolbar).

### Analyze-all from the index
- Index "Previously analyzed" header gained an **"Analyze all"** server-rendered link (all cached ids pre-selected via `cached_ids` passed from `index()`) alongside the CSV link, plus a JS **"Select all"** that ticks every `compare-pick` box.

**Verified** (server auto-reloaded; MCP browser profile was locked, so checks were via curl + the running app): all three routes return 200; `/export.csv` emits well-formed CSV with proper quoting, raw `score_pct` fractions, and empty cells for unreached milestones (selected = 2 rows, all = 3 rows); `/analyze` serializes the union axis and marks the requested ids `checked`; `/compare` 302-redirects to `/analyze` preserving `ids`; the index renders the count control, template placeholders, prefill init, and the all-ids "Analyze all" link.

## 13. Button restyle + full in-browser verification (2026-05-28)

- `webapp/static/styles.css` — per user request, removed the "link that looks like a button" / "button that looks like a URL" styling. `.link-button` is now a real **secondary button** (surface fill, `1px` border, `6px` radius, no underline, hover state) instead of underlined accent text; it applies to every former link-button across `index.html`, `analyze.html`, and `player.html` (Set / Add / Remove / Reset / Save / Save all / Select all / Download CSV / Analyze all / Re-scrape). The milestone-editor `.me-edit` "edit" affordance also lost its underline and gained a small bordered chip look. Underlined/clickable text is now reserved for genuine navigation links only.
- **Verified in-browser via Playwright MCP** (conda env `uscf-scraper`, server on :5050): count control renders N rows; the search autocomplete works on a **dynamically added** row (live USCF results); `/analyze` shows all 7 cached players across the 4 charts; toggling a player's checkbox adds/removes its line live across every chart with stable colors; hovering a data point shows a tooltip with the exact value (`Rating 700 · Hikaru Nakamura (USCF): 1`); per-chart "Save" downloads a **non-transparent** white-background PNG (top-left pixel RGBA `255,255,255,255`); the `/analyze` "Download CSV (selected)" href tracks the live selection; `/export.csv` returns a `text/csv` attachment with correct quoting (selected = 5 rows, all = 7 rows).

## 14. Cross-browser downloads — fix missing .png/.csv extensions on Safari (2026-05-28)

User reported saved charts/CSVs landed in Downloads with no extension and wouldn't open (Safari). Root cause: Safari ignores the `<a download="…">` filename when the href is a `data:` URL (charts used `chart.toBase64Image()`) and is unreliable naming a direct server link. Chromium honored both, which is why earlier verification missed it.

- `webapp/templates/analyze.html` — added a shared `downloadBlob(blob, filename)` helper that downloads via an `URL.createObjectURL(blob)` object URL (Safari honors `download` for blob: URLs). `saveChart` now uses `chart.canvas.toBlob(…, 'image/png')` (the bg-fill plugin has already painted the canvas, so the PNG stays non-transparent) instead of a data URL. The "Download CSV (selected)" link now intercepts its click, `fetch`es the CSV, and `downloadBlob`s it as `chess_players.csv` (with a "Preparing…/Download failed — retry" status); the server `Content-Disposition` is kept for right-click-save.
- `webapp/templates/index.html` — the "Download CSV" link (`#index-csv-link`) gets the same fetch→blob→`chess_players.csv` interceptor.
- The `/export.csv` route is unchanged (still emits a `text/csv` attachment).

Verified in-browser (Playwright/Chromium, no regression): the Save buttons download `chart-{name}.png` (valid 600×560 RGBA PNG, white background) and the CSV links download `chess_players.csv` (valid CSV, comma-in-name quoting intact). The blob approach is the standard fix for Safari's data-URL `download` limitation.

## Session 2026-05-30: visible USCF API-vs-HTML fallback

Made the existing (but silent) USCF API-first dispatch observable. Previously
`scrape_player` tried `scrape_player_api` first and, on `ApiUnavailable`, did a bare
`except: pass` into the slow HTML scraper — no log, no UI signal, and the failure reason
was discarded. From the outside it looked like there was no API path at all.

- Added a second callback channel `status_cb(message)` alongside the numeric
  `progress_cb(current, total)`. New `scraper/core._emit(status_cb, msg)` helper sends each
  line to both the module logger (server console) and the callback (web UI).
- `scrape_player` now emits: `Trying US Chess API…` → `US Chess API succeeded` on the API
  path, or `US Chess API unavailable (<reason>) — falling back to HTML scraping` →
  `Scraping US Chess tournament pages (this is the slow path)…` on fallback. The
  `<reason>` is the full `ApiUnavailable` string (timeout / 5xx / 499 / empty games /
  not-in-api / unexpected exception).
- `scraper/uscf_api.py`: `scrape_player_api` gains `status_cb` (emits
  `US Chess API: fetching sections + games…`) and logs the reason on its final fallback
  raise so CLI/notebook callers see it even without a callback.
- `scraper/fide.py`: `scrape_fide_player` gains a uniform `status_cb=None` (emits
  `Querying FIDE…`); no behavior change.
- `webapp/routes.py`: `_scrape_one` forwards `status_cb`; the `/scrape/stream` worker
  emits a new SSE event `{"type": "status", "player_idx", "message"}`.
- `webapp/templates/progress.html`: handles `m.type === 'status'` by writing the message
  into the existing `.scrape-status` span.
- `run.py`: `logging.basicConfig(level=logging.INFO)` so the scraper's `log.info` lines
  actually print to the terminal.

Verified by calling the scraper directly under the `uscf-scraper` env: (1) API success path
logs `Trying…`/`succeeded` and returns in seconds; (2) forcing a bad `USCF_API_BASE` host
shows the full connection-error reason and falls back to HTML (returns the uppercase
`MAGNUS CARLSEN` HTML record); (3) FIDE path still works with the new `status_cb` arg.

## Session 2026-05-30: spec 007 user-auth — Phases 0 & 1 (raw cache + per-user library)

Implemented spec `007-user-auth` **Phases 0 and 1 only** (per user decision; Firebase
Phases 2–4 deferred). Two product decisions resolved up front: (1) **separate save button**
— analyzing does NOT auto-save; only explicitly-saved players count toward the cap;
(2) scope this pass to Phase 0 + Phase 1.

### Phase 0 — fetch/compute split + raw timeline cache + invisible/per-user lists
Separated the two concepts the app had fused (expensive raw scrape vs. per-user computed view):

- **`scraper/core.py`** — new `fetch_history(session, uscf_id, ...)` (API-first, HTML fallback)
  returns a raw, DOB/milestone-INDEPENDENT timeline; new pure `compute_record(timeline, dob,
  milestones)` derives the canonical public dict (CLAUDE.md shape, unchanged). The old
  `_milestone_progress` was split into `_gather_classical_events` (network only → events list) +
  the milestone math now living in `compute_record`. The `games_played != 0` guard stays where the
  event is emitted; the score divide-by-zero guard moved into `compute_record` (`score_pct =
  score_numerator / score_games`, None when `score_games` falsy). FIDE birth year is now always
  fetched and cached on the timeline (player-specific, so cacheable). `scrape_player` is now a thin
  `compute_record(fetch_history(...))` wrapper. Added missing `DEFAULT_FIDE_MILESTONES` import.
- **`scraper/uscf_api.py`** — `_compute_milestones` → `_build_events` (raw events); new
  `fetch_history_api` returns a timeline; `scrape_player_api` is now a thin wrapper. Trimmed
  now-unused imports.
- **`scraper/fide.py`** — `scrape_fide_player` split into `fetch_fide_history` (fetches **all**
  rated periods' calc pages — no milestone-based early exit, so the cached timeline is reusable for
  any ladder) + a thin wrapper. Score still degrades to None per-cell from the first un-fetchable
  period (`score_games=None` onward). Trimmed unused imports.
- **`scraper/__init__.py`** — exports `fetch_history`, `fetch_history_api`, `fetch_fide_history`,
  `compute_record`.
- **`webapp/cache.py`** — `players` table repurposed into raw `scrape_cache` (`timeline` JSON, no
  dob/milestones). Added `users` + `saved_analyses` tables (schema only this phase). `init_db`
  detects the legacy `players` table and drops+recreates (one-time "reset" notice — reuses the FIDE
  precedent). New helpers `get_timeline` / `save_timeline` / `delete_timeline`. Old
  `get_player`/`save_player`/`list_players`/`list_full_records`/`invalidate` removed.
- **`webapp/routes.py`** — every user-facing list is now scoped to the user (library ∪ recent),
  never a global "previously analyzed". `_record_for` recomputes per request from the cached
  timeline + the entry's view params. The SSE worker now saves a timeline (`save_timeline`) and
  reports `timeline["name"]`.

### Phase 1 — ownership split + anonymous 5-save library (session cookie, Option A)
- **`config.py`** — `ANON_SAVE_LIMIT = 5`, `USER_SAVE_LIMIT = 100`.
- **Session model** — `session["saved"]` (capped library) + `session["recent"]` (last analyzed
  batch), each entry `{source, player_id, name?, dob, milestones}` so two users see their own
  DOB/ladder view from one shared timeline. Anonymous saves live entirely in the signed cookie
  (no DB rows) per Part 3a Option A.
- **New routes** — `POST /library/save` (enforces dedupe + 5-cap + "must be analyzed/cached
  first"), `POST /library/remove`, `POST /player/<s>/<id>/apply-milestones` (re-derive with current
  ladder, **no re-scrape**). `delete_player` route removed (removing from library never deletes the
  shared timeline). `/analyze` and `/export.csv` scoped to library ∪ recent.
- **Templates** — `index.html`: "Previously analyzed" → "My library (N/5)", remove forms now hit
  `/library/remove`. `player.html`: ☆ Save / ★ Saved-remove button; stale banner now offers "Apply
  current milestones (no re-scrape)". `analyze.html`: per-player ☆/★ control in the picker + library
  count. `styles.css`: picker-item flex layout for the save control.

### Verified (conda env `uscf-scraper`, live network + Flask test client)
- `compute_record` shape matches CLAUDE.md; user-DOB vs FIDE-birth-year fallback both correct.
- Live USCF API fetch (Nakamura 12641216): reached 2500 at age 13 / 82 mo / 1106 games, sane scores.
- Live USCF **HTML fallback** (forced API offline): identical timeline shape + correct milestone.
- Live FIDE fetch (Magnus 1503014): 210 events, reached 2700 at age 17, scores reconstructed, dob
  from FIDE B-Year 1990.
- Full HTTP flow w/ cookie jar: scrape → SSE done → `/analyze` shows player → save → "My library
  (1/5)" → player page shows "Saved — remove".
- Cap logic (test client): 5 saves cap at 5; 6th rejected; duplicate no-op; remove then save fits;
  saving an uncached id rejected.
- `apply-milestones`: 302, stale banner clears, ladder recomputed to exactly the new thresholds with
  no network. `/analyze` ☆/★ toggle flips library count; an unsaved-but-recent player stays viewable.

### Not done (deferred per scope decision)
Firebase Phases 2–4: `webapp/auth.py`, `/auth/session`, `static/auth.js`, `login.html`, the 100-cap
for logged-in users, `users`/`saved_analyses` **writes**, and anon→uid migration. Tables exist but
are unused (anon path is cookie-only). CLAUDE.md updated to describe the new cache/library model.

## 2026-06-15 — README rewrite + repo published
- Rewrote `README.md` for the Flask web app (USCF/FIDE input, milestone insights,
  caching, compare) — dropped the old Google Sheets instructions.
- Added `.vscode/` and `.playwright-mcp/` to `.gitignore` (editor/tool caches).
- Committed the full web-app conversion and pushed to `origin/main` (rebased onto
  6 remote README/screenshot commits; kept the new README + config.py).

## 2026-06-17 — Paste-a-list bulk input + FIDE temporarily disabled (UI only)
User asked for a button to paste a whole list of USCF IDs + birthdays at once (instead of
filling each row by hand), and to gray out FIDE as "coming soon". Web/UI layer only — no
`scraper/` internals, record dict shape, or cache schema touched.

**`webapp/templates/index.html`**
- New **"Paste a list"** button (next to "Set") toggles a `#bulk-panel` textarea. New JS helpers:
  - `parseBulk(text)` — tokenizes on commas/spaces/newlines. A purely-numeric token is a USCF ID;
    a token containing `/` is a birthday that attaches to the preceding ID (birthdays optional).
    Returns `{players, errors}`.
  - `normalizeDob(raw)` — accepts `M/D/YYYY` or `MM/DD/YYYY`, rejects impossible dates via a
    round-trip `new Date(...)` check (`02/30`, `13/40` → error), and **normalizes to `MM/DD/YYYY`**.
  - `fillRows(players)` — calls existing `setRowCount` then fills `player_search_N` / `uscf_id_N` /
    `dob_N`. `openBulk(bool)` toggles the panel + focus.
  - "Fill rows" blocks on any error (bad date, non-8-digit ID, orphan date, junk token, >100
    players) and reports them in `#bulk-status`; on success fills rows and auto-closes the panel.
- No new server route: it just populates the existing rows, so the `/scrape` →
  `parse_player_inputs` validation/dedup path is unchanged.
- FIDE radio is now `disabled` with a "coming soon" pill; USCF is always `checked`; source note
  updated to "USCF only for now — FIDE support is on the way."

**`webapp/routes.py`**
- `index()` pins the rendered form `source` to `"uscf"` (a stale `session["source"] == "fide"` can
  no longer flip the form into FIDE labels/milestones); dropped the old `?source=` flip.
- `scrape()` defensively coerces a crafted `source=fide` POST back to `uscf` with an info flash.
- **Left intact** (re-enable FIDE later by reverting just these): the FIDE scraper, `/api/search`,
  `/search`, and the library FIDE filter/badges. Only *selecting* FIDE as a scrape source is gone.

**`webapp/static/styles.css`**
- `.source-option-disabled` (muted, `not-allowed` cursor) + `.coming-soon-tag` pill;
  `.bulk-panel` / `.bulk-actions` / `.bulk-status` (surface-tint panel, monospace textarea).

**Verification (Playwright MCP, Chromium):** `parseBulk` checked over 7 inputs (mixed separators,
`M/D/YYYY` normalization, `02/30`/`13/40` rejection, short ID, junk, orphan date) — all correct.
End-to-end: paste → fill → 3 rows (2 normalized DOBs, 1 blank), panel auto-closes, FIDE `disabled`,
USCF `checked`. Screenshot `bulk-add-feature.png`. Only console error is the pre-existing favicon 404.

### 2026-06-17 (follow-up) — two-list bulk input + 5-per-page rows
User feedback on the above: split the single textarea into **two separate lists** (paste all IDs in
one box, all birthdays in another), keep the autofill button, **show only 5 players at a time** with
a back/forward pager (so 100 players don't flood the page), and on a count mismatch **say so and name
the unpaired ID/birthday**. Still UI-only (`webapp/templates/index.html` + `static/styles.css`).

- **Two lists, paired by position.** Replaced the interleaved `parseBulk` with `parseBulkTwo(idText,
  dobText)` + `tokenizeList`. IDs box `#bulk-ids`, birthdays box `#bulk-dobs`; both tokenize on
  commas/spaces/newlines and pair index-for-index. Birthdays normalize to `MM/DD/YYYY`.
  - Hard errors (non-8-digit/non-numeric ID, invalid birthday, zero IDs) → red status, block fill.
  - **Count mismatch → amber warning, not a block.** Empty birthday box is fine (optional). When both
    lists are non-empty and lengths differ, it fills what it can and names the unpaired entries:
    extra IDs (`#N (id)`) get no birthday; extra birthdays (`#N (date)`) are dropped. Panel stays
    open on a warning; auto-closes on a clean fill.
- **Pagination (`PAGE_SIZE = 5`).** New `#row-pager` (← Previous 5 / `Players X–Y of N` / Next 5 →).
  `renderPage()` toggles `.row-hidden` (`display:none`) on out-of-page rows; hidden rows **stay in
  the DOM so they still submit** (verified: 12-player fill → all 12 `uscf_id_*` in FormData).
  Wired into `setRowCount` / remove / `fillRows`; "Add another player" jumps to the new row's page;
  "Set" + fill reset to page 1. Submit validator jumps to the first errored row's page so its hint
  is visible even on a hidden page.
- CSS: `.bulk-cols`/`.bulk-col` (two side-by-side textareas, wrap on narrow), `.hint-warning`
  (`#9a6a00`), `.player-row.row-hidden { display:none }`, `.row-pager` (centered, disabled-dim).

**Verification (Playwright MCP, Chromium):** `parseBulkTwo` over 6 input pairs (clean 3+3; 3/2 and
2/3 mismatches naming the right entry; IDs-only no-warning; bad-ID+bad-date blocked; empty-IDs
blocked) — all correct. Pagination 12 players: pages `1–5 / 6–10 / 11–12 of 12`, correct disabled
states, back/forward works, all 12 IDs submit. Warning amber `rgb(154,106,0)`, panel stays open;
two textareas side-by-side; pager hidden at ≤5 players. (Screenshot tool hit a transient font-load
timeout; visuals confirmed via computed styles.)

### 2026-06-17 (follow-up 2) — future-birthday guard (not viable)
User feedback: when a pasted/typed birthday is past the current date, notify them it's not viable
and highlight it red. Today is read live (`new Date()` client, `datetime.now()` server).

- **`webapp/templates/index.html`** — new `isFutureDob(norm)` (strictly-after-today; today is OK).
  - Bulk: `parseBulkTwo` adds a hard error `Birthday #N (MM/DD/YYYY) is in the future — not a viable
    birthday.` → blocks the fill (red status), alongside the existing malformed-date check.
  - Per-row "normal feature": each `.dob-input` validates on `focusout` (`validateDobField` +
    `markDobError`/`clearDobError`/`dobHintEl`). A future/malformed date adds `.input-error` (red)
    and a `.dob-hint` message; the `input` handler clears it while editing; a valid date is
    normalized to `MM/DD/YYYY` in place. Added a `.dob-hint` span to the row template.
  - Submit: the analyze-form validator now runs `validateDobField` over every `dob_*`, blocks on
    any failure, flags the field red, and jumps to the first offending row's page.
- **`webapp/forms.py`** — `validate_dob` also rejects `parsed.date() > datetime.now().date()` with
  the same message (defense for crafted POSTs; today accepted, strictly-future not).
- **`webapp/static/styles.css`** — `input[type="text"].input-error{,:focus}` (red border + `#fdecea`).

**Verification (Playwright MCP, Chromium, today=2026-06-17):** Bulk `12/25/2030` → blocked, status
`Birthday #2 (12/25/2030) is in the future — not a viable birthday.`, nothing filled. Per-row: typing
`12/25/2030` + blur → `.input-error` red (`rgb(192,57,43)`) + hint shown; submit blocked
(`defaultPrevented`); fixing to `3/9/2012` clears the flag and normalizes to `03/09/2012`. Server
unit: future→error, today/past→accepted, blank→optional, bad-format→format error.

### 2026-06-17 (follow-up 3) — Enter no longer starts the analysis
User request: typing a USCF ID and hitting Enter should not start the analysis; only the Analyze
button should. Added a `keydown` handler on `#analyze-form` that `preventDefault()`s `Enter` when
`e.target.tagName === 'INPUT'` (ID/search, DOB, count number). Textareas are excluded so Enter still
inserts newlines in the bulk boxes, and the search dropdown's Enter-to-select is unaffected (its
`rowsContainer` keydown handler bubbles first and runs `chooseOption` regardless of `preventDefault`).
`webapp/templates/index.html` only. **Verified (Playwright):** Enter in the ID, DOB, and count fields
is prevented and triggers 0 submits; Enter in a textarea is not prevented; the Analyze button still
submits exactly once.

### 2026-06-17 (follow-up 4) — Cache freshness / TTL (auto re-scrape after 7 days)
User concern about the shared `scrape_cache`: it de-dupes scrapes across users so we don't hammer
USCF/FIDE (good), but serving a cache hit forever means a player who has since played a new
tournament shows **stale ratings**. Requested: if the cached result is over a week old, re-scrape.

**Change (reuse-decision only — no schema, timeline, or record-dict change):**
- `config.py`: added `CACHE_TTL_DAYS = 7` (the freshness window; `0`/`None` disables expiry).
- `webapp/cache.py`: added pure helper `is_timeline_stale(timeline, max_age_days)` — parses the
  timeline's `scraped_at` (ISO-8601 UTC, from `datetime.now(timezone.utc).isoformat()`), returns
  `True` when older than the window. `max_age_days` of `None`/`<=0` → never stale; missing or
  unparseable `scraped_at` → treated as stale (re-scrape rather than serve unknown-age data); naive
  timestamps assumed UTC.
- `webapp/routes.py`: `scrape_stream`'s worker (the only auto-reuse path) now reuses the cache only
  on a **fresh** hit (`timeline is not None and not is_timeline_stale(...)`). A stale hit falls
  through to `_fetch_timeline` + `save_timeline` and emits a status line: *"Cached data is over 7
  days old — re-scraping for the latest ratings."* Imports `CACHE_TTL_DAYS` + `is_timeline_stale`.

**Deliberately unchanged:** `POST /player/<source>/<player_id>/refresh` still force-re-scrapes at any
age. Read/compute paths (`_record_for`, `player`, `/analyze`, `/export.csv`, `save_to_library`) keep
using plain `get_timeline` and never trigger network — only the analyze/scrape flow can re-fetch.

**Context:** prompted by a deployment-architecture discussion (Vercel serverless can't persist the
on-disk SQLite cache; a VPS or persistent-disk PaaS like Railway/Render/Fly fits the 007 design
as-written). The TTL check is host-independent — it only reads `scraped_at` off the row.

**Verification:** `is_timeline_stale` unit-tested via the `uscf-scraper` env across 9 cases (fresh
1d, edge 6d, stale 8d/30d, missing ts, bad ts, ttl=0, ttl=None, naive ts) — all pass. `create_app()`
imports cleanly and `main.scrape_stream` is wired. Files: `config.py`, `webapp/cache.py`,
`webapp/routes.py`.

### 2026-06-17 (follow-up 5) — removed the All/USCF/FIDE source filter
User request: drop the All/USCF/FIDE filter buttons from the library + analyze pages (USCF-only for
now). Removed the `.filter-btns` group from the index "My library" header (`index.html`) and the
`/analyze` picker sidebar (`analyze.html`), plus the `empty-filter`/`picker-empty` "no matches" lines.
JS: index `renderList()` no longer filters (dropped `activeFilter`/`emptyMsg` — it just sorts and
re-appends); analyze dropped its entire source-filter handler block (`pickerItems`/`pickerEmpty`).
CSS: deleted the now-dead `.filter-btns` / `.filter-btn{,.active,:hover}` / `.empty-filter` rules.
Per-source `badge-uscf/-fide` badges stay (they just label existing rows). **Verified:** grep finds no
remaining `filter-btn`/`data-filter`/`activeFilter`/`empty-filter`/`picker-empty` refs; `/` and
`/analyze` both render 200; the page loads with no console errors (besides the pre-existing favicon
404). To restore when FIDE returns, re-add the button group + click handlers.

### 2026-06-17 (follow-up 5) — Remove the manual "Refresh from USCF" button
User asked to remove the manual refresh button entirely. With the 7-day cache TTL (follow-up 4) now
auto-refreshing stale scrapes during analyze, the manual force-refresh was redundant.

**Removed:**
- `webapp/templates/player.html`: the refresh `<form>`/button ("Refresh from {{ source|upper }}").
  Save/remove controls and the "View charts" link are untouched.
- `webapp/routes.py`: the `refresh_player` view + its `POST /player/<source>/<player_id>/refresh`
  route. Grep confirmed nothing else (py/html/js/tests) referenced `refresh_player` or `main.refresh`.
  No dangling imports — the helpers it used (`_fetch_timeline`, `save_timeline`, `get_timeline`, the
  `PlayerNotFound`/FIDE exception classes) are still used by the `scrape_stream` worker.

**Comment/doc cleanup (the TTL is now the *only* re-scrape path for a cached player):**
- `config.py`: reworded the `CACHE_TTL_DAYS` comment (dropped the "manual /refresh button" mention).
- `webapp/routes.py`: reworded the cache-reuse comment in the worker.
- `CLAUDE.md`: overview line ("manual refresh button" → auto-refresh via `CACHE_TTL_DAYS`), repo-layout
  route list (dropped `/player/<id>/refresh`), and the two Cache-rules bullets.
- `specs/*/plan.md` left as-is on purpose — they're historical design records, not current-state docs.

**Consequence (called out for future work):** there is no longer any UI way to force an immediate
re-scrape of a cached player. A cached player is re-fetched only when it ages past `CACHE_TTL_DAYS`
and someone re-analyzes it. Re-adding on-demand refresh later is a small revert (route + button).

**Verification (`uscf-scraper` env):** `create_app()` imports cleanly; `main.refresh_player` is no
longer in the URL map while `main.player` and `main.apply_milestones` remain; `grep` for
`refresh_player`/`Refresh from` across `webapp/` + `config.py` returns nothing.
Files: `webapp/templates/player.html`, `webapp/routes.py`, `config.py`, `CLAUDE.md`.

### 2026-06-17 (follow-up 6) — empty player rows must be filled or removed before analyzing
User: the default form shows 2 rows, but you could fill just one ID and hit Analyze — the blank row
was silently skipped, which felt wrong. Now an unpopulated row **blocks** Analyze with a small note
telling the user to remove it (or fill it). `webapp/templates/index.html` only.

- The analyze-form submit validator no longer does `if (!raw) return` (skip blank rows). Instead it
  flags **every** row that lacks a USCF ID, sets `ok = false`, and shows a red field-hint on that row:
  - `filledCount === 0` (nothing entered anywhere) → `Enter a USCF ID to analyze.`
  - otherwise (some rows filled, this one blank) → `Empty row — enter a USCF ID, or click Remove if
    not adding another player.`
- Refactored around a `noteError(slot)` helper (tracks the min errored slot) + a pre-computed
  `filledCount`; removed the old `anyFilled` fallback block (now redundant). The validator still also
  checks unresolved typed names and malformed/future birthdays, and jumps to the first errored row's
  page (works with pagination). Typing an ID clears the note (existing `input` handler); clicking
  Remove drops the row (and its note). Bulk-fill only ever creates filled rows, so it's unaffected.
- **Verified (Playwright, corrected harness — listener registered *after* the validator so
  `defaultPrevented` is meaningful):** both rows empty → blocked, each shows `Enter a USCF ID to
  analyze.`; row 0 filled + row 1 empty → blocked, row 0 clean and row 1 shows the remove note;
  removing the empty row (1 filled row left) → **not** blocked, submits.

### 2026-06-17 (follow-up 7) — spec 005 extended with OlimpBase pre-2003 findings
Research into getting FIDE rating data before 2003. Confirmed FIDE's `a_chart_data.phtml` truncates at
`2003-Apr` for **every** player (Kasparov, rated since 1979, peak 2851 — his chart starts `2003-Apr` @
2830, all pre-2003 history absent). Found **OlimpBase** (`olimpbase.org`) as the fill: reconstructed
FIDE rating lists 1971–2001, per-player cards giving rating + per-period games (no `score_pct`, no
per-game/tournament data — neither existed pre-2003). Folded into `specs/005-fide-source/research.md`
(new "Pre-2003 history via OlimpBase" section, 2003-floor evidence, split coverage table, probes) and
`plan.md` (new "Phase 6 — Pre-2003 backfill via OlimpBase", Non-goals, player-page caveat pointer).
Docs only — no code; `scraper/olimpbase.py` + the timeline merge are deferred to when Phase 6 is scheduled.

### 2026-06-17 (follow-up 6) — Production-readiness pass (publish blockers #1/#2/#3 + cookie flags)
First batch of the "what blocks publishing?" assessment. Makes the app deployable + safe to expose;
**nothing is deployed yet**, and no scraper/record/cache-logic changed.

**#1 — Real WSGI server, debug off:**
- `run.py`: `debug` now defaults OFF (the Werkzeug debugger is RCE if exposed); opt in via
  `FLASK_DEBUG=1`. `HOST`/`PORT` are env-overridable. The `__main__` block is dev-only.
- New `Procfile`: `web: gunicorn -c gunicorn.conf.py run:app`.
- New `gunicorn.conf.py`: `worker_class="gthread"` + `threads=4` (the SSE scrape-progress stream
  would tie up a sync worker), `workers=2` (network-bound + single-writer SQLite), `timeout=120`
  (long scrapes / HTML-Cloudflare fallback), stdout access/error logs, `0.0.0.0:$PORT` bind. All
  values env-overridable (`WEB_CONCURRENCY`, `THREADS`, `TIMEOUT`, `LOG_LEVEL`, `PORT`).

**#2 — Fail-closed SECRET_KEY:** `webapp/__init__.py` adds `_is_production()` (`APP_ENV=production`).
`create_app()` now raises `RuntimeError` at boot if production and `FLASK_SECRET_KEY` is unset, instead
of silently using the forgeable `"dev-only-not-secure"`. Dev keeps the default fallback.

**#3 — Portable deps:** new `requirements.txt` (flask, requests, beautifulsoup4, lxml, python-dateutil,
curl_cffi, gunicorn). `environment.yml` stays for local conda but is a macOS-ARM export that can't
recreate on a Linux host — `pip install -r requirements.txt` is the deploy path.

**Cookie hardening:** `SESSION_COOKIE_HTTPONLY=True` (explicit), `SESSION_COOKIE_SAMESITE="Lax"`,
`SESSION_COOKIE_SECURE=_is_production()` (HTTPS-only cookie in prod; off in dev so http://localhost
keeps the session). Added `ProxyFix(x_for=1,x_proto=1,x_host=1)` in production so the single upstream
proxy's `X-Forwarded-*` yields correct scheme/client-IP (the IP also feeds a future scrape throttle).

**Docs:** CLAUDE.md gained a "Run in production" section (pip + gunicorn + the `APP_ENV`/secret env
vars + persistent-disk/no-Vercel reminder) and a dev-only note on `run.py`.

**Verification (`uscf-scraper` env):** `py_compile` clean on run.py/gunicorn.conf.py/webapp.__init__.
Behavior matrix: dev (no APP_ENV) → dev secret, `SESSION_COOKIE_SECURE` False, no ProxyFix;
`APP_ENV=production` without secret → boots with `RuntimeError`; with secret → `SECURE=True`, ProxyFix
applied, real secret used. gunicorn is deploy-only (not in the local env) so `--check-config` skipped;
config is import-clean. Files: `run.py`, `webapp/__init__.py`, `requirements.txt`, `Procfile`,
`gunicorn.conf.py`, `CLAUDE.md`.

**Still open from the publish list:** #4 host choice, #5 scraping-from-datacenter-IP / Cloudflare (must
test on the real host), #6 rate-limit `/scrape` (IP-ban risk — recommended next), #8 DOB-of-minors
privacy + uschess.org ToS, plus polish (no tests, custom error pages, stale cache-reset flash text).

### 2026-06-17 (follow-up 7) — Remove the source ("USCF") label next to player names
User asked to drop the source tag beside player names (UI is USCF-only for now, so it's redundant).
Removed in the three places it showed:
- `webapp/templates/analyze.html`: the Chart.js dataset label (`p.name + ' (' + p.source.toUpperCase()
  + ')'` → `p.name`) and the `<span class="badge badge-{{ p.source }}">` in the analyze player picker.
- `webapp/templates/index.html`: the same badge span next to each saved player in the "My library" list.

Left alone on purpose: `compare.html` (same badge + legend pattern, but it's a dead template — `/compare`
redirects to `/analyze`); the `.badge`/`badge-uscf` CSS (harmless, still used by the dead template).
**Verified (`uscf-scraper` env):** all four templates (analyze/index/compare/player) compile via the
Jinja env; `GET /` returns 200 with no `badge-uscf` in the HTML. Files: `analyze.html`, `index.html`.

### 2026-06-18 — Remove the "How many players?" count input + "Set" button
User wanted the index form less cluttered: players should only be added via the
"+ Add another player" button or the "Paste a list" bulk panel — no manual count entry.
- `webapp/templates/index.html`: dropped the `#player-count` number `<input>` and the
  `#set-count-btn` "Set" button from the `.count-control` block; rewrote the help text
  ("Add players one at a time, or paste a whole list."). Removed the now-dead JS: the
  `countInput` const, the `set-count-btn` click + `countInput` change listeners, and the
  two `countInput.value = rowCount()` syncs in `setRowCount`/the remove-row handler.
  `setRowCount(n)` is unchanged and still drives the "+ Add another player" button,
  `fillRows` (bulk paste), and `initRows` (starts at 2 rows / prefill). The 1–100 clamp
  inside `setRowCount` is retained.
- `webapp/static/styles.css`: removed the now-unused `.count-control label` and
  `.count-control input[type="number"]` rules; kept the `.count-control` flex container
  (still holds the "Paste a list" button + help text).
**Verified (`uscf-scraper` env):** `index.html` compiles via the Jinja env; no remaining
references to `countInput`/`player-count`/`set-count` in the template.

### 2026-06-18 (follow-up) — Per-player "use FIDE birth year" opt-out toggle
Made the previously-silent FIDE-birth-year age fallback an explicit, per-player choice.
Before, `compute_record` always synthesized `01/01/<fide_birth_year>` when no DOB was
entered (dob_source="fide"). Now each player row has a checkbox (checked by default):
- **Checked + no DOB** → use the FIDE birth year if one exists; age counts from Jan 1, so
  milestone ages are approximate (UI says so).
- **Unchecked + no DOB** → no age stats at all (dob_source="none").
- A typed DOB always wins regardless of the checkbox.

Changes:
- `scraper/core.py`: `compute_record(..., use_fide_birth_year=True)` — gate the FIDE-year
  branch on the flag. `fide_birth_year` is still returned in the record either way (so the
  UI can still show it). Threaded the kwarg through the `scrape_player` wrapper.
- `scraper/uscf_api.py` / `scraper/fide.py`: same `use_fide_birth_year=True` kwarg on the
  `scrape_player_api` / `scrape_fide_player` wrappers → `compute_record`.
- `webapp/forms.py`: `parse_player_inputs` now returns 3-tuples
  `(player_id, dob, use_fide_birth)`. Reads `use_fide_birth_N` (an unchecked HTML checkbox
  submits nothing → absent == False). Dedup updated to 3-tuples.
- `webapp/routes.py`: `scrape()` stores `use_fide_birth` on each `recent` entry (kept
  `pending_scrape.players` as 2-element `[pid, dob]` — the fetch worker is timeline-only and
  birth-year-independent, so it's untouched). `_record_for` and the ☆-save flow now read
  `entry["use_fide_birth"]` (default True for legacy entries) and pass it to `compute_record`.
- `webapp/templates/index.html`: added the `use_fide_birth___IDX__` checkbox (checked) +
  caption under the DOB field in the row template; `reindexRows()` renames it per row.
- `webapp/templates/player.html` + `_milestone_table.html`: clarified the FIDE-birth-year
  note ("Jan 1, so ages are approximate") and the age-hidden reason.
- `webapp/static/styles.css`: `.fide-birth-opt` flex layout (checkbox inline with caption).
**Verified (`uscf-scraper` env):** py_compile clean on all 5 modules; `compute_record`
on/off/user-DOB gating asserted (ON→fide age 11, OFF→none/no age, USER→user age, fide_birth_year
still surfaced when OFF); `parse_player_inputs` returns correct 3-tuples for checked/unchecked/
typed-DOB rows; all 4 templates Jinja-parse; `GET /` 200 with the checkbox markup present.

### 2026-06-18 (follow-up 2) — Reword the FIDE-birth-year checkbox caption
`webapp/templates/index.html`: caption is now "No birthday? Use FIDE birth year if one
exists (01/01/{birth_year}), provides approximate ages. Uncheck to skip age stats."
(`{birth_year}` is literal — the row template is JS-cloned before a player is chosen, so
there's no concrete year to interpolate; Jinja leaves single-brace text untouched.)
Verified `GET /` still 200 with the new caption present.

Note (answered a user Q, no code change): re-analyzing the same player with a different DOB
*does* update ages — the cache holds a DOB-independent raw timeline and `compute_record`
recomputes age per request from the entry's DOB; `scrape()` overwrites `session["recent"]`.
Caveat: `_entry_for` prefers `_saved()` over `_recent()`, so a player already in the ☆ library
keeps its saved DOB/milestones/use_fide_birth until removed & re-saved — a re-analysis doesn't
update a saved entry. Potential future fix if desired.

### 2026-06-18 (follow-up 3) — Tweak FIDE-birth-year caption wording
`webapp/templates/index.html`: "(01/01/{birth_year})" → "(MM/DD Jan 1)". Verified GET / 200.

### 2026-06-18 — Railway cache persistence + `CACHE_TTL_DAYS` env override
Prompted by a user question: the app is deployed on Railway (elojourney.com), and the SQLite
cache (`instance/cache.sqlite3`) was on the container's **ephemeral** disk — so it was wiped on
every redeploy/restart and the 7-day TTL never got to elapse. Investigation confirmed the TTL
*code* (`is_timeline_stale` + the `scrape_stream` worker re-scrape path) is correct; only
durability was missing. Live-checked on elojourney.com via Playwright: analyze + player page
render from the cache with a persisted `Scraped at` timestamp.

Fix (user attached a Railway Volume at `/app/instance`; I did the code/doc side):
- `config.py`: `CACHE_TTL_DAYS` now reads the `CACHE_TTL_DAYS` env var via a new `_env_int(name,
  default)` helper (blank/unparseable → default 7; `0` disables expiry). Read once at import, so a
  change needs a redeploy/restart. No call-site change — `routes.py` still imports it from `config`.
- `railway.json` (new, committed): pins `NIXPACKS` builder, `gunicorn -c gunicorn.conf.py run:app`
  start command, `ON_FAILURE` restart policy (max 10). Railway volumes are dashboard/CLI-only (no
  config-as-code field), so the volume mount is documented, not declared here.
- `CLAUDE.md`: new "Railway deploy" subsection (volume must mount at `/app/instance`; `APP_ENV`/
  `FLASK_SECRET_KEY` service vars; `CACHE_TTL_DAYS` env override) + cache-TTL bullet now notes the
  persistent-disk requirement. `handoff.md`: new 2026-06-18 session entry.

This commit also lands the prior uncommitted working-tree changes (FIDE source wiring across
`scraper/*` and `webapp/*`, plus template/CSS tweaks) — swept into main at the user's request.

### 2026-06-18 (follow-up 4) — Cap analyze charts at 5 players at a time
Prompted by a user concern: with a large batch (e.g. 100 players) the `/analyze` charts would draw
every line at once and become unreadable. Added a configurable cap (default 5):
- `webapp/routes.py`: new `ANALYZE_CHART_LIMIT = 5`; `analyze()` truncates the initial on-chart
  selection (`requested[:ANALYZE_CHART_LIMIT]`) so a big batch doesn't load all-checked, and passes
  `chart_limit` to the template. The picker still lists *every* player — the cap only limits what's
  drawn.
- `webapp/templates/analyze.html`: `MAX_CHARTED = {{ chart_limit }}`; new `enforceLimit()` disables
  the unchecked checkboxes once the cap is reached (and shows a "Showing the max of N players…"
  note), called from `refreshCharts()`. "Select all" → "Select first N" and stops at the cap. Intro
  copy notes the limit.
Client-side only + a server-side initial-selection truncation; no scraper/cache/contract changes.
Not yet verified in-browser (user will check locally).
