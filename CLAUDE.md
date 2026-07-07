# CCCWebScraper — Agent Handoff

Flask app: enter USCF or FIDE IDs + DOBs (bulk supported; source is chosen **per row**, so one
batch can mix both), see how many months, games, and what age a player took to reach a
configurable set of rating milestones, plus cumulative score % at each (USCF only — FIDE has no
score data by design). USCF's primary source is the US Chess JSON API; falls back to HTML scraping
(only ~Oct 2025) if the API fails. Data is cached in SQLite so we don't hammer USCF; cached scrapes auto-refresh once they
pass a staleness window (`CACHE_TTL_DAYS`). Milestones are configurable per session (no accounts).
**ALWAYS UPDATE handoff.md AND progress.md AFTER EVERY CODING CHANGE.**

## Stack
Flask + Jinja, SQLite (stdlib `sqlite3`), `requests`/`curl_cffi` + `beautifulsoup4` + `lxml` for
scraping, `python-dateutil` for date math. Python 3.12.

## Repo layout
```
scraper/                # framework-agnostic scraping core — NO Flask imports
  core.py               # HTML scraper + fetch_history / compute_record
  uscf_api.py           # US Chess JSON API client (fast path, incl. foreign events)
  uscf_api_legacy.py    # pre-foreign-fix snapshot (reached via USCF_INCLUDE_FOREIGN=0)
  fide.py               # FIDE source (chart JSON; pre-2003 backfill via olimpbase.py + fide_archive.py)
  olimpbase.py          # OlimpBase per-player cards — FIDE Jan 1990 – Oct 2001 backfill
  fide_archive.py       # FIDE's own archive lists — Jan 2002 – Jan 2003 gap fill + Apr 2003 floor fix
webapp/                 # Flask app: __init__.py (create_app), routes.py, cache.py,
                        #   forms.py, presets.py, templates/, static/
config.py               # milestone ladders + env-driven flags
run.py                  # dev entry (gunicorn in prod)
instance/               # gitignored; SQLite DB lives here
archive/                # old CLI scripts (reference only, not imported)
```

## Scraper source of truth
`archive/scrape_sheets.py` is the canonical scraping logic — already ported into `scraper/core.py`
minus the Google Sheets I/O and the hardcoded `2025-05-16` cutoff. Keep the `games_played != 0`
divide-by-zero guards. `archive/scrape_user_input.py` lacks those guards — don't copy from it.

## Scraper API contract
All three record producers — `scraper.core.scrape_player`, `scraper.uscf_api.scrape_player_api`,
and `scraper.fide.scrape_fide_player` — emit this exact dict (web app + cache round-trip it):

```python
{
  "source": "uscf" | "fide",
  "player_id": str,                        # == uscf_id or fide_id
  "uscf_id": str | None,
  "fide_id": str | None,
  "name": str,
  "country": str | None,                   # FIDE federation; None for USCF
  "dob": str | None,                       # "MM/DD/YYYY"
  "dob_source": "user" | "fide" | "none",
  "fide_birth_year": int | None,
  "first_tournament_date": str,            # "YYYY-MM-DD" (FIDE: first period -> YYYY-MM-01)
  "initial_rating": int,                   # FIDE: first published rating
  "age_at_first_tournament": int | None,
  "milestones": {                          # keys are str(threshold)
    "1000": {"months": int|None, "games": int|None, "age": int|None,
             "score_pct": float|None, "date": str|None},
    ...
  },
  "milestones_config": list[int],
  "rating_type": "classical",              # reserved; FIDE rapid/blitz out of scope
  "scraped_at": str                        # ISO-8601 UTC
}
```

`None` for any unreached milestone. `date` = "YYYY-MM-DD" of the first event reaching the threshold
(drives the charts' "achieved …" hover). FIDE `score_pct` is **always `None`** — score was dropped
for FIDE by design (2026-07: the per-period `a_indv_calculation.php` calls made veteran scrapes slow
and fragile for one column). Only USCF records carry real score data; the FIDE player page hides the
Score column and says so.

## Cache / compute split (spec 007)
See `specs/007-user-auth/plan.md` (Phases 0 & 1 done; Firebase Phases 2–4 deferred) before touching
`webapp/cache.py` or the library routes.

- **`scrape_cache` stores a RAW, DOB/milestone-independent timeline**, not the computed record. It's
  backend infra, never rendered to users. Two users with different DOBs/ladders reuse one scrape.
- Scraper is split: `fetch_history` / `fetch_history_api` / `fetch_fide_history` (network → timeline,
  cacheable) + the pure `compute_record(timeline, dob, milestones)` (→ the public dict). Public
  `scrape_*` are thin `compute_record(fetch_*(...))` wrappers, so the dict shape above is unchanged.
- Cache keyed on `(source, player_id)`. GET never scrapes. The **only** re-scrape path for a cached
  player is the TTL path in the `scrape_stream` worker: it reuses a hit only when
  `cache.is_timeline_stale(timeline, CACHE_TTL_DAYS)` is False (default 7, env `CACHE_TTL_DAYS`,
  `0`/`None` = keep forever; staleness read from `scraped_at`, missing/unparseable = stale); a stale
  hit re-scrapes + re-saves. Read/compute paths use plain `get_timeline` and never hit the network.
- **Per-user library, not a global list.** Analyzing does NOT save; saving is a separate ☆ action.
  Anonymous saves live in the signed cookie: `session["saved"]` (cap `config.ANON_SAVE_LIMIT` = 5) +
  `session["recent"]` (last analyzed batch), each entry carrying that user's
  `{dob, milestones, use_fide_birth}`. Every user-facing list = library ∪ recent, never a global view.
- `POST /player/<source>/<player_id>/apply-milestones` re-derives the view from the cached timeline
  with **no re-scrape**. A cached record whose `milestones_config` differs from the session shows a
  banner offering this (not silent recomputation).
- `init_db` drops+recreates the cache if it detects the legacy `players` table (one-time reset
  notice). `users` + `saved_analyses` tables exist but are unused (deferred Firebase phases). DB is
  gitignored in `instance/`.

## USCF foreign / no-affiliate events (2026-07)
The USCF timeline includes a player's FIDE/foreign events (Corus, Tal Memorial, Olympiad, the
Candidates, …). They carry a Regular (`ratingSource == "R"`) rating record but a `G`/`A`/`F`
*section* system, so the old `RatingSource="R"` request dropped them — collapsing, e.g., Caruana's
2600→2800 climb into one 2014 jump. `scraper/uscf_api.py` now pulls **all** sections and keeps any
with an `R` record. Foreign events have no rows in the bulk `/members/{id}/games` endpoint, so their
per-game W/D/L come from the event crosstable
(`/api/v1/rated-events/{eventId}/sections/{n}/standings`), memoized in the **`crosstable_cache`**
table via a `SqliteCrosstableCache` injected from `webapp/routes.py` (scraper stays Flask-free — it
only sees `.get`/`.put`). First scrape of a globetrotter adds ~10–15s; cached re-scrapes are as fast
as before. Timeline carries `api_version: TIMELINE_API_VERSION` (2); the worker re-scrapes any cached
USCF timeline lacking it. `USCF_INCLUDE_FOREIGN=0` reverts to `scraper/uscf_api_legacy.py`.
**Never re-add a Regular filter (`RatingSource="R"` or `section.ratingSystem == "R"`) — that's the bug.**

## FIDE as a first-class source (2026-07)
The analyze form's source is **per row** (radio pair `source_N`; `parse_player_inputs` reads it per
row and dedupes on `(source, id)`), so one batch mixes USCF and FIDE freely — there is no site-wide
source and no `session["source"]` anymore. Quick-add is two dropdown sections (`FEATURED_FIDE` =
the current FIDE top 15, added as FIDE analyses; `FEATURED_USCF` = American stars, added as USCF)
— a card is ONE button whose source comes from its section, and the same player can sit in both
sections with the same photo (Caruana, Nakamura). "Paste a list" is two buttons (USCF list /
FIDE list) feeding one shared panel that now **appends** rows instead of replacing them.
- **FIDE timeline** = one `a_chart_data.phtml` call + the profile B-Year (~2-3 requests total).
- **Pre-2003 backfill:** FIDE's chart JSON is floored at **Apr 2003** server-side for every player.
  When a chart starts exactly at `2003-04-01`, `fetch_fide_history` backfills **Jan 1990–Oct 2001**
  from the player's OlimpBase card (`scraper/olimpbase.py`: name-keyed URL built from the FIDE
  payload's "Last, First" name + a FIDE-ID identity guard against homonyms) and **Jan 2002–Jan 2003**
  from FIDE's own downloadable archive lists (`scraper/fide_archive.py`, added 2026-07 after diffing
  against 2700chess.com), prepending events with one continuous `cumulative_games`. The archive's
  Apr 2003 list also **overrides the chart's floor-row rating/games** — the chart sometimes serves a
  later FIDE recalculation there (Carlsen: 2356 vs published 2315). Rows before 1990-01-01 are
  dropped (FIDE IDs exist from 1990).
- Both backfills are **best-effort**: any failure → no backfill, never a scrape error. OlimpBase:
  **definitive** results — parsed card, confirmed 404, identity-guard failure — are memoized
  **permanently** (negatives included) in the `olimpbase_cache` table via `SqliteOlimpbaseCache`
  injected from `webapp/routes.py` (scraper stays Flask-free). Archive lists are cached permanently
  **per list** (~45k rows each, six lists) in `fide_archive_lists` via `SqliteFideArchiveCache` —
  first veteran scrape downloads them once, everyone after is free. Transient failures (network
  blip, 5xx) are NOT cached in either — else one outage would permanently mask a veteran's history.
- Known, accepted: for a few post-2003 months FIDE's chart serves retro-corrected values that differ
  slightly from the originally published lists (2700chess shows the published ones). Rare and small;
  fixing it would mean downloading every monthly list. See docs/scraping.md.
- Timeline carries `fide_timeline_version: FIDE_TIMELINE_VERSION` (3); the scrape worker re-scrapes
  any cached FIDE timeline that doesn't match (mirrors the USCF `api_version` path).
- Rating-list cadence caveat (shown on the FIDE player page): lists were semiannual through 1999,
  quarterly 2000–mid-2009, bimonthly to mid-2012, monthly only since **Aug 2012** — so "months to
  milestone" is coarse for older periods.

## Date handling
DOB is `MM/DD/YYYY` (`calculate_age` parses `%m/%d/%Y`). USCF tournament dates are `YYYY-MM-DD`
(`extract_date` uses `r"\d{4}-\d{2}-\d{2}"`). Don't mix the two formats inside the scraper.

## Milestones
Two ladders in `config.py`: `DEFAULT_USCF_MILESTONES` (400–3000 by 200s) and `DEFAULT_FIDE_MILESTONES`
(1400–2900). `DEFAULT_RATING_MILESTONES` aliases the USCF ladder. Active list =
`session["milestones_uscf"|"milestones_fide"]` chosen by each row's/entry's **own** source (the
site-wide `session["source"]` is gone), falling back to the matching default. Passed explicitly into `compute_record` / `scrape_*(..., milestones=...)` — the
scraper never reads `flask.session` or `config` itself.

## Scraping internals
For URL patterns, parsing rules, guards, and the Cloudflare/bot-detection escalation ladder, see
[`docs/scraping.md`](docs/scraping.md). Read it before editing `scraper/core.py`.

## Run locally
```bash
conda env create -f environment.yml    # env is named uscf-scraper
conda activate uscf-scraper
python run.py                          # http://localhost:5050  (:5000 = macOS AirPlay)
```
`run.py` is dev-only: `debug` defaults OFF (`FLASK_DEBUG=1` to enable); `HOST`/`PORT` env-overridable.

## Run in production
Don't use the Flask dev server. Deploy with pip `requirements.txt` (the conda `environment.yml` is a
macOS-ARM export that won't recreate on Linux) + the bundled gunicorn config:
```bash
pip install -r requirements.txt
export APP_ENV=production
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
gunicorn -c gunicorn.conf.py run:app    # or honor the Procfile
```
`APP_ENV=production` makes `create_app()` fail to boot without `FLASK_SECRET_KEY`, sets
`SESSION_COOKIE_SECURE`, and wraps the app in `ProxyFix`. `gunicorn.conf.py` uses threaded
(`gthread`) workers (scrape progress streams over SSE). Needs a **persistent disk** for
`instance/cache.sqlite3` (VPS or persistent-disk PaaS — not serverless).

**Railway (current host: elojourney.com).** `railway.json` pins the NIXPACKS builder, the gunicorn
start command, and a restart policy. Volumes are dashboard/CLI-only (not in `railway.json`) — attach
a Volume mounted at `/app/instance` or the SQLite cache is wiped every redeploy (attached as of
2026-06-18). Set `APP_ENV` + `FLASK_SECRET_KEY` (and optionally `CACHE_TTL_DAYS`) as service vars;
they're read once at process start, so changes need a redeploy.

## What NOT to do
- **Don't re-add Google Sheets** (future bulk import = CSV upload, not gspread).
- **Don't import Flask from `scraper/`** (core must stay framework-agnostic).
- **Don't parallelize USCF requests** (IP-ban risk).
- **Don't change the `compute_record` / `scrape_player` public dict shape** without updating templates
  in the same change. The cache serializes the raw **timeline**, not this dict — if you change the
  timeline shape, update `compute_record` + all three `fetch_*` producers together.
- **Don't drop the `games_played != 0` guards.**
- **Don't re-introduce the `2025-05-16` cutoff.**
- **Don't re-add a Regular-rating filter to the USCF section fetch** (drops foreign events).
- **Don't re-add FIDE score calls** (`a_indv_calculation.php`) — dropped by design 2026-07.
- **Keep the OlimpBase + FIDE-archive backfills best-effort** (they must never fail a FIDE scrape);
  definitive results are cached forever (negatives included) — but never cache a transient failure
  as a negative, and never cache a partially downloaded archive list.
