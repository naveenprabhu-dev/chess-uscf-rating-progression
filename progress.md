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
