# Handoff

Short "where things stand" summary. For architecture/contracts read `CLAUDE.md`, for scraping
internals `docs/scraping.md`, and for the full change log `progress.md`.

## How to run
```bash
conda activate uscf-scraper            # env at /opt/anaconda3/envs/uscf-scraper
python run.py                          # http://localhost:5050  (:5000 = macOS AirPlay)
```
If `python` isn't found, the interpreter is `/opt/anaconda3/envs/uscf-scraper/bin/python`.

## Current state (2026-07-07)
Flask app: single/bulk player analysis with **per-row USCF/FIDE source** (one batch can mix both),
milestone config per source, a multi-player charts page (`/analyze`), CSV export, and quick-add
presets in **two dropdown sections** ("FIDE" / "USCF" — a card is one button whose bottom tag is the
same source badge as the Analysis page; the same player can sit in both sections). Deployed on Railway at **elojourney.com** (Volume at
`/app/instance` persists the cache).

**Architecture (spec 007, Phases 0 & 1):** the SQLite `scrape_cache` holds a **raw,
DOB/milestone-independent rating timeline**; the per-user milestone/age view is **recomputed per
request** by `scraper.compute_record`. The shared cache is never shown to users — every list is the
user's own **library** (`session["saved"]`, cap 5 per browser) ∪ their most-recent analyzed batch
(`session["recent"]`). Analyzing no longer auto-saves; **saving is a separate ☆ button**. Cached
timelines auto-re-scrape once past `CACHE_TTL_DAYS` (7) on analyze — the only re-scrape path (manual
refresh is gone). Firebase Phases 2–4 (login, 100-cap, anon→uid) are not done.

## Most recent session — 2026-07-07: 2700chess verification → 2002 gap filled + quick-add dropdowns
Verified the FIDE scraper per-period against 2700chess.com (owner request, ground truth) for
Carlsen, Anand, Sindarov, Niemann, Aronian; fixed what was fixable; reworked quick-add.
- **New `scraper/fide_archive.py`:** the old "accepted" Jan 2002 – Jan 2003 gap is now FILLED from
  FIDE's own downloadable archive lists (jan02–jan03 frl zips), and the archive's Apr 2003 list
  **overrides the chart's floor row**, which sometimes carries a later FIDE recalculation (Carlsen:
  chart said 2356; the published list and 2700chess say 2315). Lists cached permanently per LIST in
  the new `fide_archive_lists` table (`SqliteFideArchiveCache`, ~245k rows, one-time ~6 MB download
  on the first veteran scrape). Best-effort like OlimpBase; never cache a failed/partial list.
- **`fide_timeline_version` → 3** — cached v2 FIDE timelines re-scrape once on next analyze.
- **Result:** Carlsen/Anand/Aronian/Caruana now match 2700chess exactly on every common period from
  1990 on. Remaining diffs are FIDE's own post-2003 retro-corrections served by their chart endpoint
  (Sindarov ±1 in 2014, Niemann's corrected 2015 run) — rare, small, documented in docs/scraping.md;
  for Niemann May 2026 we match FIDE's official list and 2700chess doesn't.
- **Quick-add = two dropdown sections** (`presets.py`): `FEATURED_FIDE` — the live FIDE top 15 as of
  2026-07-07 — and `FEATURED_USCF` — the 10 American players. One button per card, source from the
  section, same photo when a player is in both (Caruana, Nakamura). 13 new CC/CC0 portraits from
  Wikimedia Commons (licenses verified via API; CREDITS.md + on-page credits updated).
- **USCF is API-only now** (owner decision, same day): the MSA HTML pages froze ~Nov 2025, so the old
  any-failure→HTML fallback served stale data. `core.fetch_history` now retries the API with waits
  (`USCF_API_RETRY_WAITS = (5,15,30)`, plus 1 s/4 s in-call retries and 429 Retry-After handling) and
  raises user-facing `UscfApiUnavailable` if it stays down; unknown members raise `PlayerNotFound`
  immediately (`ApiUnavailable.retryable/not_found` classification). HTML scraper kept intact behind
  `USCF_HTML_FALLBACK=1` (emergency only). Source badges (USCF/FIDE) now sit next to names on the
  analyze picker, chart legends/tooltips, and the library list (player + progress pages already had
  them).

## Previous session — 2026-07-01 (later): FIDE first-class + OlimpBase pre-2003 backfill
FIDE went from "coming soon" to fully enabled (spec 005 Phase 6 landed, reworked per owner):
- **Per-row source:** the analyze form's USCF/FIDE choice is per row (`source_N`), so one batch mixes
  sources; "Paste a list" is two buttons (USCF / FIDE list) whose fills **append** rather than replace.
- **Score % dropped for FIDE** (owner decision): no more per-period `a_indv_calculation.php` calls; a
  FIDE scrape is ~2-3 requests. FIDE `score_pct` is always None; the Score column hides for FIDE.
- **Pre-2003 backfill:** FIDE's chart JSON floors at Apr 2003, so charts starting exactly there
  backfill Jan 1990–Oct 2001 from OlimpBase per-player cards (`scraper/olimpbase.py`, name-keyed +
  FIDE-ID identity guard, best-effort). Cached permanently — negatives included — in the
  `olimpbase_cache` table (list-cadence caveat on the player page; monthly lists only since Aug 2012).
- Verified end-to-end: Nakamura's FIDE timeline reaches back to Jan 1999 (2500 at age 15), Carlsen's
  to Apr 2001 @ 2064 through the full webapp path; Kasparov's pre-1990 rows correctly dropped;
  mixed-batch scrape + per-row validation + quick-add/paste JS all pass (test client + Playwright).

## Recent history (brief)
- **2026-07-01:** USCF foreign-event fix — `scraper/uscf_api.py` pulls all sections, keeps any with
  an `R` record; foreign W/D/L from event crosstables, memoized in `crosstable_cache`. Timeline
  `api_version = 2` re-scrapes old caches. Revert: `USCF_INCLUDE_FOREIGN=0`. Caruana: 2600 → Jan
  2008, 2800 → Jun 2011. **Never re-add the Regular filter.**
- **2026-07-01:** attempted to pre-populate the Railway cache for the 10 quick-add players (boot
  warm → bundled seed); prod started hanging (Railway edge → container 499s), so **reverted all of
  it** — no cache-warming on Railway now; featured players cache lazily on first analyze. Prod-hang
  root cause still open (suspect newer app deploy or Railway infra). See progress.md.
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
- No `/scrape` rate-limiting (IP-ban risk); no tests / custom error pages.
- `webapp/templates/compare.html` is unused (deletable).
- Prod deploy of the 2026-07-01 + 2026-07-07 changes pending (pushed to main; Railway deploys from
  it). First FIDE veteran scrape in prod will download the six archive lists (~6 MB, one time) into
  the Volume-backed cache.
