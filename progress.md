# Progress log

Chronological record of turning this repo from a Google-Sheets CLI scraper into a Flask charts
website. Terse by design — see `CLAUDE.md` for current contracts and `docs/scraping.md` for scraping
internals. Older detail lives in git history.

## Foundation (initial port)
- Ported `scrape_sheets.py` → framework-agnostic `scraper/core.py` (dropped gspread + the
  `2025-05-16` cutoff; kept the `games_played != 0` guards). `config.py` reduced to milestone ladders.
- Swapped `requests` → `curl_cffi` (Chrome impersonation) + added `_get_msa` Cloudflare-challenge
  detection/retry after `/msa/` started gating at scrape volume (root cause of the old transient
  `find_next_sibling` crash). `curl_cffi` pinned `>=0.9,<0.10` (0.10 wheel fails to load on this mac).
- Flask scaffolding: `create_app()`, SQLite cache, `forms.py` validation, `main` blueprint, dark →
  beige/teal theme (`webapp/static/styles.css`).
- Charts: multi-player `/analyze` (superseded the 2-player `/compare`, now a redirect) renders
  Months / Games / Age / Score line charts (Chart.js v4 CDN), client-side add/remove, PNG + CSV
  export (with a Safari `blob:` download fix).
- Optional DOB with FIDE birth-year fallback; USCF name search (inline autocomplete + `/api/search`).

## Sources
- **USCF JSON API fast-path** (spec 006, `scraper/uscf_api.py`): tries `ratings-api.uschess.org`
  first, HTML fallback on any failure, with a visible `status_cb` signal. Caruana ~6 min → ~8 s.
- **FIDE second source** (spec 005, `scraper/fide.py`): site-wide source toggle; real `score_pct`
  reconstructed from the `a_indv_calculation.php` W/D/L pages. (FIDE is currently disabled in the
  analyze UI — "coming soon" — but the scraper/search code stays intact.)

## Spec 007 — raw timeline cache + per-user library (Phases 0 & 1)
- Split the scraper into `fetch_history` / `fetch_history_api` / `fetch_fide_history`
  (network → timeline, cached) + the pure `compute_record` (timeline + dob + ladder → public dict).
- `scrape_cache` now stores the raw DOB/milestone-independent timeline; the per-user view is
  recomputed per request. Cache is backend-only, never shown to users.
- Per-user library: analyzing no longer auto-saves; ☆ save is a separate action. `session["saved"]`
  (cap 5) + `session["recent"]`, each entry carrying its own `{dob, milestones, use_fide_birth}`.
- Firebase Phases 2–4 deferred (`users`/`saved_analyses` tables exist but are unused; anon path is
  cookie-only).

## Deploy / production
- Production-readiness: gunicorn (`gthread`) + `Procfile` + `requirements.txt`; fail-closed
  `FLASK_SECRET_KEY` under `APP_ENV=production`; cookie hardening + `ProxyFix`; `run.py` debug-off.
- Cache TTL: `CACHE_TTL_DAYS` (default 7, env-overridable, `0` = off) auto-re-scrapes stale cached
  timelines on analyze — the only re-scrape path (the manual "Refresh" button was removed).
- Railway (elojourney.com): `railway.json` + a Volume mounted at `/app/instance` for cache
  persistence. README rewritten for the web app; repo published to `origin/main`.

## UI / UX
- Bulk entry: paste two lists (IDs + birthdays, paired by position), 5-per-page pagination,
  future-birthday guard (client + server), Enter-doesn't-submit, empty-row-blocks-analyze.
- Featured-player quick-add gallery: 10 verified US players (`webapp/presets.py`), CC-licensed photos
  in `webapp/static/players/` (credits in `CREDITS.md`).
- Progress page shows player names (not IDs) and "already analyzed" for cached players; the API→HTML
  fallback shows a "slower / no data after Nov 2025" notice + a Cancel button.
- Per-player "use FIDE birth year" opt-out checkbox; typed DOB always wins.
- Milestone editing is inline on the home page (Milestones nav link removed). Analyze charts capped
  at 5 lines. Source label hidden. Default USCF ladder is 400–3000 by 200s.
- Milestone-date hover: each milestone cell gained a `date` key; charts show "achieved Mon YYYY".

## 2026-07-01 — USCF foreign-event fix (current)
A player's no-affiliate FIDE/foreign events (Corus, Tal Memorial, Olympiad, the Candidates, …) carry
a Regular (`ratingSource == "R"`) rating record and move the US Chess rating, but the old
`RatingSource="R"` request dropped them (their *section* system is `G`/`A`/`F`) — collapsing
Caruana's 2600/2800 climb into one 2014 jump.
- `scraper/uscf_api.py` (rewritten): pulls all sections, keeps any with an `R` record, uses that
  record's `postRating`; also picks up dual-rated (`D`) US events. Foreign events' W/D/L come from
  the event crosstable `/rated-events/{eventId}/sections/{n}/standings` (`roundOutcomes`), one paced
  call each. A transient standings failure returns 0 and is **not** cached (only a definitive
  200/404 is).
- `webapp/cache.py`: new `crosstable_cache` table + `get/save_crosstable_counts` +
  `SqliteCrosstableCache` adapter (get/put, injected into the scraper so it stays Flask-free).
- `scraper/uscf_api_legacy.py` (new): verbatim pre-fix snapshot; reached via `USCF_INCLUDE_FOREIGN=0`
  (`config._env_bool`). `scraper/core.fetch_history(..., crosstable_cache=)` picks the client by flag.
- `webapp/routes.py`: `_fetch_timeline` injects `SqliteCrosstableCache()`; the timeline carries
  `api_version = TIMELINE_API_VERSION (2)` and the worker re-scrapes any cached USCF timeline lacking
  it (auto-invalidates old foreign-less caches).
- Result (Caruana): 741 events (was 560); 2600 → Jan 2008, 2800 → Jun 2011; milestones ≤2200
  unchanged. First scrape ~30 s (crosstable calls), cached re-scrape ~7 s. HTML-fallback path
  unchanged. **Never re-add the Regular filter.**

## Known follow-ups
- MSA / `player-search.php` endpoints will be retired for MUIR (`ratings.uschess.org`) — re-target
  the scraper + search when that happens.
- Re-enable FIDE in the UI; pre-2003 FIDE backfill via OlimpBase (spec 005 Phase 6, deferred).
- No rate-limiting on `/scrape` (IP-ban risk); no tests; no custom error pages.
- Scraping is intentionally sequential — bulk (~100 players) is slow.
- `webapp/templates/compare.html` is dead (deletable).

## 2026-07-01 — Docs condensed
Condensed `CLAUDE.md`, `progress.md`, and `handoff.md` for brevity (docs only, no code change).

## 2026-07-01 — Featured-cache warm on boot
- `webapp/warm.py` (new): `warm_featured()` pre-populates `scrape_cache` for every quick-add player
  (`FEATURED_PLAYERS`) via the API path ONLY (`fetch_history_api`, no HTML fallback), guarding on
  api_version 2 + a non-ALL-CAPS name so no scraper record is ever cached for them. Idempotent
  (skips fresh, API-shaped entries), paced + retried against USCF 429s. Runnable by hand:
  `python -m webapp.warm`.
- `gunicorn.conf.py`: `when_ready` hook fires the warm once per deploy (arbiter only, not per
  worker), in a daemon thread so it never blocks serving. `WARM_FEATURED_ON_BOOT=0` disables it.
  This is how the Railway volume cache gets warmed — there's no direct write path to it.
- Warmed the local cache: all 10 featured players now cached as clean API v2 entries (Caruana 741,
  Nakamura 476, Niemann 442, Liang 341, Sevian 269, Shankland 315, Robson 268, Xiong 424, Mishra
  325, Woodward 199 events). The stale ALL-CAPS HTML entry for Xiong (and Niemann) is gone.

## 2026-07-01 — Replaced boot-warm with a bundled seed (fixes post-deploy slowness)
- The scrape-on-boot warm scraped 10 heavy players on every deploy and wrote hundreds of rows to the
  SQLite file on the freshly-attached Railway volume, contending with request-serving reads → the
  live site went slow / returned HTTP 499 (client-closed) right after deploys.
- Replaced it: `webapp/seed_data/featured_timelines.json` (380 KB) bundles the 10 players' raw
  timelines (exported from the warmed local cache). `webapp/seed.py:seed_featured` inserts any the
  cache is missing — no network, a few small INSERTs, idempotent (never clobbers a fresher real
  scrape). `when_ready` now calls the seed, not the warm (`SEED_FEATURED_ON_BOOT=0` to disable).
- `webapp/warm.py` stays for LOCAL/manual warming (`python -m webapp.warm`) and to regenerate the
  seed bundle; it no longer runs at boot. Live scraping still handles hand-entered players.
- Net: fresh volume gets all 10 featured players instantly on deploy, zero USCF calls at request
  time, no boot-time DB contention.
