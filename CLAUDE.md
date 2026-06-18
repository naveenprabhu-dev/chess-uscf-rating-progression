# CCCWebScraper — Agent Handoff

This repo scrapes US Chess (uschess.org) for a player's rating progression and reports how many months, games, and what age it took them to reach a configurable set of rating milestones, plus their cumulative score percentage at each one. The current build is turning this from a CLI/Google-Sheets script into a small Flask website. ALWAYS UPDATE HANDOFF.MD AFTER EVERY CODING CHANGE

## What you're building

A Flask app where a user enters a list of USCF ID + DOB and sees the rating-milestone insights across multiple players. Scraped data is cached in SQLite so we don't hammer USCF; cached scrapes auto-refresh once they pass a configurable staleness window (`CACHE_TTL_DAYS`). Rating milestones are configurable per session (no accounts). 

**Source of truth for scraping logic:** `scrape_sheets.py` is the latest, correct version of the scraper — treat it as canonical. Port its logic verbatim into `scraper/core.py`, *minus* the Google Sheets I/O (the `gspread`/`oauth2client` imports, `_col_index`/`_cell_a1`/`_read_column_until_blank`/`_validate_config`/`get_uscf_ids`/`get_dobs`, the `sheet.update(...)` call, and the `__main__` block that wires up the sheet client). Also drop the hardcoded `2025-05-16` tournament cutoff — the new app must process all tournaments, including current ones. Keep the `games_played != 0` divide-by-zero guards. The older `archive/scrape_user_input.py` is reference only and is missing those guards — don't copy from it.

**Stack:** Flask + Jinja templates, SQLite via stdlib `sqlite3`, `requests` + `beautifulsoup4` + `lxml` for scraping, `python-dateutil` for date math. Python 3.12.

## Repo layout (target state)

```
scraper/            # sheets-free scraping core — pure functions, no Flask imports
  __init__.py       # exports scrape_player, DEFAULT_RATING_MILESTONES
  core.py           # the scraping/parsing logic (ported from old scrape_sheets.py)
webapp/             # Flask app
  __init__.py       # create_app() factory
  routes.py         # /, /scrape, /player/<id>, /settings, /compare (later)
  cache.py          # SQLite open/init + get/save/invalidate helpers
  forms.py          # input validation (USCF ID, DOB parsing)
  templates/        # base.html, index.html, player.html, _milestone_table.html
  static/styles.css
config.py           # only DEFAULT_RATING_MILESTONES lives here now
run.py              # `flask --app webapp run`
instance/           # gitignored; SQLite DB lives here
archive/            # old CLI scripts kept for reference (not imported anywhere)
```

## Scraper API contract

`scrape_player(session, uscf_id, dob, milestones=None) -> dict` is the single entry point. It must return:

```python
{
  "source": "uscf" | "fide",               # which rating body this record came from
  "player_id": str,                        # canonical ID for the source (== uscf_id or fide_id)
  "uscf_id": str | None,                   # set when source == "uscf"
  "fide_id": str | None,                   # set when source == "fide"
  "name": str,
  "country": str | None,                   # FIDE federation; None for USCF
  "dob": str | None,                       # "MM/DD/YYYY"
  "dob_source": "user" | "fide" | "none",
  "fide_birth_year": int | None,
  "first_tournament_date": str,            # "YYYY-MM-DD" (FIDE: first published period -> YYYY-MM-01)
  "initial_rating": int,                   # FIDE: first published rating, not post-first-event
  "age_at_first_tournament": int | None,
  "milestones": {                          # keys are str(threshold)
    "1000": {"months": int|None, "games": int|None, "age": int|None, "score_pct": float|None},
    ...
  },
  "milestones_config": list[int],          # the thresholds actually used
  "rating_type": "classical",              # reserved; FIDE rapid/blitz out of scope
  "scraped_at": str                        # ISO-8601 UTC
}
```

`None` for any milestone the player hasn't yet reached. FIDE `score_pct` is real — reconstructed from per-period W/D/L (the `a_indv_calculation.php` pages), not `None`. All three record producers — `scraper.core.scrape_player`, `scraper.uscf_api.scrape_player_api`, and `scraper.fide.scrape_fide_player` — emit this exact shape, including `source`/`player_id`. The web app and the cache both round-trip it.

## Scraping internals

For URL patterns, parsing rules, divide-by-zero guards, and the Cloudflare/bot-detection escalation ladder, see [`docs/scraping.md`](docs/scraping.md). Read it before editing anything in `scraper/core.py`.

## FIDE as a second source (spec 005 — implemented)

FIDE (ratings.fide.com) is a full first-class source alongside USCF. See [`specs/005-fide-source/plan.md`](specs/005-fide-source/plan.md) and [`specs/005-fide-source/research.md`](specs/005-fide-source/research.md); FIDE scraper internals are in `docs/scraping.md`.

- **`scraper/fide.py`** holds `scrape_fide_player`, `search_fide_players`, `get_fide_history`, `get_fide_calculations`, and `FidePlayerNotFound` / `FideNoRatedHistory` / `FideScrapeError`. Rating history is one JSON call (`a_chart_data.phtml`); `score_pct` adds one `a_indv_calculation.php` call per rated period the player competed in (early-exits once the top milestone is reached). Plain `requests`, no Cloudflare/curl_cffi. Keep `scraper.core.get_fide_birth_year` framework-agnostic.
- FIDE `score_pct` is computed from per-period W/D/L. It can still be `None` for an individual milestone the player hasn't reached, or for all milestones beyond a period whose calc page couldn't be fetched (score degrades gracefully rather than crashing) — so milestone-table and chart logic must still handle `None` per cell.
- **No FIDE rate-limit throttle observed.** An earlier `research.md` claim of a "~30 s empty-body rate limit" was unverified and is wrong (burst-tested 2026-05-28). The scraper stays sequential/polite and keeps a single retry-on-empty as cheap insurance, but does not assume a 30 s window.
- **Site-wide source toggle (deviation from the plan).** The plan specified a *per-row* source toggle; the implemented build uses ONE active source per session (`session["source"]`, "uscf" default or "fide"). Both player rows use the session source — no mixing within a single scrape. Validation, scraper dispatch, and the active milestone list all key off `session["source"]`. Per-row `source_N` fields were dropped.
- **Per-source milestone ladders:** `DEFAULT_USCF_MILESTONES` (400–2200) and `DEFAULT_FIDE_MILESTONES` (1400–2700) in `config.py`. Stored as `session["milestones_uscf"]` / `session["milestones_fide"]`; the active list is chosen by source at scrape time. `DEFAULT_RATING_MILESTONES` remains a back-compat alias for the USCF ladder.
- **`/compare` stays genuinely source-aware** even though scrapes are single-source: cached players from different sources can be compared. `ids` are namespaced `source:player_id`. A mixed-source caveat banner shows when both sources appear. (The Score chart no longer blurs for all-FIDE comparisons — FIDE now has real score data.)

## Date handling

- DOB is entered as `MM/DD/YYYY` and stored that way. `calculate_age` parses with `"%m/%d/%Y"`.
- USCF tournament dates are `YYYY-MM-DD`. `extract_date` pulls them with `r"\d{4}-\d{2}-\d{2}"`.
- Don't mix the two formats inside the scraper.

## Milestone configuration

- Two per-source ladders in `config.py`: `DEFAULT_USCF_MILESTONES` (400–2200) and `DEFAULT_FIDE_MILESTONES` (1400–2700). `DEFAULT_RATING_MILESTONES` is a back-compat alias for the USCF ladder.
- The active list comes from `flask.session["milestones_uscf"]` or `["milestones_fide"]`, chosen by `session["source"]`, falling back to the matching default.
- It's passed explicitly into `scrape_player(..., milestones=...)` / `scrape_fide_player(..., milestones=...)` — the scraper never reads `flask.session` or `config` itself.
- When a cached record's `milestones_config` differs from the current session list, the player page shows a banner offering to re-scrape. Use that banner, not silent recomputation, so cache stays trustworthy.

## Cache rules (updated by spec 007 — raw timeline cache + per-user library)

The cache was repurposed in spec 007 (`specs/007-user-auth/plan.md`, Phases 0 & 1 done). Read that
plan before touching `webapp/cache.py` or the library routes.

- **`scrape_cache` stores a RAW, DOB/milestone-independent timeline**, not a computed record. The
  per-user milestone/age view is recomputed per request by `scraper.compute_record(timeline, dob,
  milestones)`. Two users with different DOBs/ladders reuse one cached scrape — no re-hit on
  USCF/FIDE. The scrape cache is **backend infra, never rendered to users**.
- The scraper is split into **`fetch_history(...)` / `fetch_history_api(...)` / `fetch_fide_history(...)`**
  (network → timeline, cacheable) and the **pure `compute_record(...)`** (timeline + dob + ladder →
  the public dict). The public `scrape_player` / `scrape_player_api` / `scrape_fide_player` are now
  thin `compute_record(fetch_*(...))` wrappers, so the CLAUDE.md dict shape is unchanged.
- **Per-user library, not a global list.** "Analyze" no longer saves. Saving is a separate ☆ action.
  Anonymous saves live in the signed session cookie: `session["saved"]` (capped at
  `config.ANON_SAVE_LIMIT`, 5) + `session["recent"]` (last analyzed batch). Each entry carries that
  user's `{dob, milestones}`. `index` / `/analyze` / `/export.csv` read library ∪ recent — never a
  global "previously analyzed" view.
- Cache keyed on `(source, player_id)` — composite PK. `GET` never scrapes; an already-cached player
  is re-scraped only by the TTL path below (the manual "Refresh from USCF" button + its
  `/player/<source>/<player_id>/refresh` route were removed 2026-06-17 — freshness is fully
  automatic now). `POST /library/remove` removes a `saved` entry only — it never deletes the shared
  `scrape_cache` row.
- **Cache freshness / TTL.** Cached timelines expire after `config.CACHE_TTL_DAYS` (default 7; set
  `0`/`None` to keep forever). The analyze flow (`scrape_stream` worker) reuses a cache hit only when
  `cache.is_timeline_stale(timeline, CACHE_TTL_DAYS)` is False; a stale hit re-scrapes and re-saves
  so new tournaments show up. Staleness is read from the timeline's `scraped_at` (a missing/unparseable
  value counts as stale). **This is the only path that re-scrapes an already-cached player** (the
  manual refresh button is gone); the read/compute paths (`compute_record` callers) use plain
  `get_timeline` and never trigger network.
- `POST /player/<source>/<player_id>/apply-milestones` re-derives the view with the current ladder
  with **no re-scrape** (recompute from the cached timeline).
- `init_db` detects the **legacy `players` table** (computed records, not losslessly convertible to a
  timeline) and drops+recreates the cache, flashing a one-time "Cache was reset to support per-user
  analysis" notice. It also creates `users` + `saved_analyses` (reserved for the deferred Firebase
  phases — currently unused; the anon path is cookie-only). The DB is gitignored in `instance/`.

## Run locally

```bash
conda env create -f environment.yml   # or: pip install flask requests beautifulsoup4 lxml python-dateutil curl_cffi
conda activate uscf-scraper           # NOTE: env is named uscf-scraper
python run.py                         # serves on http://localhost:5050
```

Open http://localhost:5050. (`:5000` is hijacked by macOS AirPlay Receiver.)

`python run.py` is **dev-only**: `debug` defaults OFF (set `FLASK_DEBUG=1` to enable the reloader/debugger locally), and `HOST`/`PORT` are env-overridable.

## Run in production

Don't use `python run.py` / the Flask dev server in production. Deploy with the pip
`requirements.txt` (the conda `environment.yml` is a macOS-ARM export that won't recreate on Linux)
and the bundled gunicorn config:

```bash
pip install -r requirements.txt
export APP_ENV=production               # turns on the prod safeguards below
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
gunicorn -c gunicorn.conf.py run:app    # or: honor the Procfile (web: …)
```

`APP_ENV=production` makes `create_app()` **fail to boot without `FLASK_SECRET_KEY`** (the key signs
the session cookie), sets `SESSION_COOKIE_SECURE` (HTTPS-only cookie), and wraps the app in
`ProxyFix` (trusts one upstream proxy's `X-Forwarded-*`). `gunicorn.conf.py` uses **threaded**
(`gthread`) workers because scrape progress streams over SSE — the default sync worker would block a
whole process per open stream. Needs a **persistent disk** for `instance/cache.sqlite3` (so a VPS or
persistent-disk PaaS like Railway/Render/Fly — not serverless/Vercel).

## What NOT to do

- **Don't re-add Google Sheets.** Sheets is intentionally gone. If a future feature needs bulk import, it should be CSV upload, not gspread.
- **Don't import Flask from `scraper/`.** The scraping core must stay framework-agnostic so we can reuse it from a CLI or notebook.
- **Don't parallelize the USCF requests** in the MVP. We're not trying to get IP-banned.
- **Don't change the public dict shape of `compute_record` / `scrape_player`** without updating the templates in the same change. (Note: the SQLite `scrape_cache` now serializes the *raw timeline*, not this dict — see the Cache rules section. If you change the **timeline** shape, update `compute_record` and all three `fetch_*` producers together.)
- **Don't drop the `games_played != 0` guards.** They're the only thing keeping the scraper from crashing on players who withdrew with byes.
- **Don't re-introduce the `2025-05-16` cutoff.** It was a one-off scope filter in the legacy script; the web app must show all tournaments.
