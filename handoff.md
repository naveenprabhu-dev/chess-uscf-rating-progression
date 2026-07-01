# Handoff

Short "where things stand" summary. For architecture/contracts read `CLAUDE.md`, for scraping
internals `docs/scraping.md`, and for the full change log `progress.md`.

## How to run
```bash
conda activate uscf-scraper            # env at /opt/anaconda3/envs/uscf-scraper
python run.py                          # http://localhost:5050  (:5000 = macOS AirPlay)
```
If `python` isn't found, the interpreter is `/opt/anaconda3/envs/uscf-scraper/bin/python`.

## Current state (2026-07-01)
Flask app: single/bulk USCF player analysis, milestone config per source, a multi-player charts page
(`/analyze`), CSV export, and featured-player quick-add presets. FIDE is fully implemented but
**disabled in the analyze UI** ("coming soon"). Deployed on Railway at **elojourney.com** (Volume at
`/app/instance` persists the cache).

**Architecture (spec 007, Phases 0 & 1):** the SQLite `scrape_cache` holds a **raw,
DOB/milestone-independent rating timeline**; the per-user milestone/age view is **recomputed per
request** by `scraper.compute_record`. The shared cache is never shown to users — every list is the
user's own **library** (`session["saved"]`, cap 5 per browser) ∪ their most-recent analyzed batch
(`session["recent"]`). Analyzing no longer auto-saves; **saving is a separate ☆ button**. Cached
timelines auto-re-scrape once past `CACHE_TTL_DAYS` (7) on analyze — the only re-scrape path (manual
refresh is gone). Firebase Phases 2–4 (login, 100-cap, anon→uid) are not done.

## Most recent session — 2026-07-01: USCF foreign-event fix
A player's no-affiliate FIDE/foreign events carry a Regular (`ratingSource == "R"`) record and move
the US Chess rating, but the old `RatingSource="R"` request dropped them (their section system is
`G`/`A`/`F`) — collapsing Caruana's 2600/2800 into one 2014 jump. Fix:
- `scraper/uscf_api.py` (rewritten) pulls all sections, keeps any with an `R` record; foreign events'
  W/D/L come from the event crosstable `/standings` endpoint, memoized in a new `crosstable_cache`
  table (`SqliteCrosstableCache` injected from routes, so the scraper stays Flask-free).
- Timeline carries `api_version = 2`; the worker re-scrapes any cached USCF timeline lacking it.
- Revert with `USCF_INCLUDE_FOREIGN=0` (→ verbatim `scraper/uscf_api_legacy.py`).
- Result: Caruana 741 events (was 560); 2600 → Jan 2008, 2800 → Jun 2011. First scrape ~30 s
  (crosstable calls), cached re-scrape ~7 s. **Never re-add the Regular filter.**

## Recent history (brief)
- **2026-07-01:** boot-time featured-cache warm (`webapp/warm.py` + gunicorn `when_ready` hook) so
  prod pre-caches all 10 quick-add players via the API on deploy (idempotent, paced, background;
  `WARM_FEATURED_ON_BOOT=0` to disable). Also warmed the local cache for all featured players.
- **2026-06-30:** featured-player presets; progress-page UX (names, "already analyzed", API→HTML
  fallback notice + Cancel); milestone-date "achieved Mon YYYY" hover; default USCF ladder 400–3000
  by 200s; save-limit copy reworded.
- **2026-06-18:** Railway persistence (`railway.json` + Volume); `CACHE_TTL_DAYS` env override; cap
  analyze charts at 5 lines; per-player "use FIDE birth year" opt-out; removed the count input.
- **2026-06-17:** production-readiness (gunicorn/Procfile, fail-closed SECRET_KEY, cookies); cache
  TTL; removed manual refresh + source filter; bulk paste-a-list + pagination + future-birthday
  guard; FIDE disabled in the UI; hide source label.
- **2026-06-15:** README rewrite + repo published.
- **2026-05-30:** spec 007 Phases 0 & 1 (raw cache + per-user library); visible API/HTML fallback.
- **2026-05-28:** FIDE second source; bulk entry; CSV export; multi-player `/analyze` page.

## Don'ts (from CLAUDE.md)
No Google Sheets; no Flask imports in `scraper/`; no parallel USCF requests; don't change the
`compute_record`/`scrape_player` dict shape without updating `cache.py` + templates; keep the
`games_played != 0` guards; don't reintroduce the `2025-05-16` cutoff; **don't re-add the USCF
Regular-rating filter** (drops foreign events).

## Known follow-ups
- MSA endpoints → MUIR (`ratings.uschess.org`) migration pending; re-target scraper + search.
- Re-enable FIDE in the UI; pre-2003 FIDE backfill via OlimpBase (spec 005 Phase 6, deferred).
- No `/scrape` rate-limiting (IP-ban risk); no tests / custom error pages.
- `webapp/templates/compare.html` is unused (deletable).
