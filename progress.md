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
- ~~Re-enable FIDE in the UI; pre-2003 FIDE backfill via OlimpBase~~ — done 2026-07-01, see below.
- No rate-limiting on `/scrape` (IP-ban risk); no tests; no custom error pages.
- Scraping is intentionally sequential — bulk (~100 players) is slow.
- `webapp/templates/compare.html` is dead (deletable).

## 2026-07-01 — Docs condensed
Condensed `CLAUDE.md`, `progress.md`, and `handoff.md` for brevity (docs only, no code change).

## 2026-07-01 — Prod cache pre-population attempt, then fully reverted
- Tried to pre-populate the Railway cache for the 10 quick-add players so first loads are instant:
  first a scrape-on-boot warm (`webapp/warm.py` + a gunicorn `when_ready` hook), then a bundled
  seed (`webapp/seed.py` + `webapp/seed_data/featured_timelines.json`) to avoid boot-time scraping.
- Prod started hanging (Railway edge → container: gunicorn accepts but never responds; HTTP 499s).
  Ruled out the volume (still hung after deleting it and redeploying volume-less) and the seed
  (booted fine, `[seed] done — seeded=0 skipped=10`), but the site stayed down.
- **Removed all of it** — `webapp/warm.py`, `webapp/seed.py`, `webapp/seed_data/`, and the
  `when_ready` hook (`gunicorn.conf.py` back to its pre-session state). No cache-warming on Railway
  for now; featured players cache lazily when someone analyzes them, as before. Root cause of the
  prod hang still open (suspect the newer app deploy or Railway infra, not the cache code).
- Local cache remains warmed (all 10 as clean API v2 entries) from `python -m webapp.warm` runs.

## 2026-07-01 — FIDE first-class: per-row source, OlimpBase pre-2003 backfill, score % dropped
FIDE went from "coming soon" to fully enabled (spec 005 Phase 6, reworked per owner decisions):
- **Per-row source.** The site-wide source toggle is gone (`session["source"]` removed). Each form
  row carries a USCF/FIDE radio pair (`source_N`); `parse_player_inputs(form)` validates per row and
  dedupes on `(source, id)`, so one Analyze batch mixes sources. `pending_scrape.players` is now
  `[[src, pid, dob], …]` (legacy batch shape tolerated across a deploy); `recent` entries snapshot
  each row's own ladder; SSE `player_start`/`player_done` carry `source`; progress page shows
  per-player badges and a "(N USCF · M FIDE)" header when mixed.
- **Quick add both ways.** Each featured card has USCF + FIDE buttons (same DOB either way; the same
  player can be added once per source). `presets.py` gained `fide_id` for all 10 players, verified
  against FIDE search (exact b_year + GM + USA fed; e.g. Caruana 2020009, Nakamura 2016192,
  Woodward 30953499 — FIDE lists Niemann as "Hans Moke", Shankland as "Sam").
- **Two paste buttons.** "Paste a USCF list" / "Paste a FIDE list" share one bulk panel (per-source
  labels + 8 vs 5–10 digit validation); Fill rows now **appends** into open rows with per-(source,id)
  dedupe and the 100-cap counting existing rows, so a USCF list and a FIDE list can be combined.
- **Score % dropped for FIDE** (owner decision — "not that important"): deleted
  `get_fide_calculations`/`_parse_calculations` and the per-period calc loop; every FIDE event has
  `score_numerator/score_games = None`, a FIDE scrape is ~2-3 requests. `_milestone_table.html`
  hides the Score column for FIDE; analyze's score chart notes FIDE has no score data.
- **Pre-2003 backfill (new `scraper/olimpbase.py`).** FIDE's `a_chart_data.phtml` floors at Apr 2003
  for everyone; when a chart starts exactly at `2003-04-01`, `fetch_fide_history` fetches the
  player's OlimpBase card (`/Elo/player/{L}/{Last},%20{First}.html`, ~10 KB, name from the FIDE
  payload) and prepends Jan 1990–Oct 2001 rows with one continuous `cumulative_games`. Guards:
  FIDE-ID identity check (homonyms), pre-1990 rows dropped (FIDE IDs exist from 1990; owner req.),
  strictly best-effort (any failure → no backfill, never a scrape error), 1 s politeness gap,
  results — negatives included — cached permanently in the new `olimpbase_cache` table
  (`SqliteOlimpbaseCache` injected from routes; scraper stays Flask-free). Jan 2002–Jan 2003 has no
  per-player source anywhere: accepted gap, milestones crossed in 2002 resolve to Apr 2003.
- **Caveats in UI.** FIDE player page: no score %; list cadence (semiannual → quarterly → bimonthly →
  monthly only since Aug 2012, linked to the chess.SE explainer); Jan-1990 floor; OlimpBase
  attribution (Wojciech Bartelski) + 2002-gap note when pre-2003 data is shown. Index hero + a form
  note mention both caveats.
- **Versioning.** FIDE timelines carry `fide_timeline_version = 2`; `_timeline_outdated` re-scrapes
  older cached FIDE timelines once (mirrors USCF `api_version`).
- **Verified:** local parser vs saved cards (Kasparov: 20 pre-1990 rows dropped, 25 kept
  1990-01→2001-10, old-scheme Jan-1990 ID handled); live Nakamura 2016192 → 9 pre-2003 events,
  first period 1999-01-01 @ 2182, 2500 at 51 mo / age 15, games continuous across the gap; Mishra →
  0 OlimpBase requests; Carlsen through the full webapp path → first period 2001-04-01 @ 2064 with
  attribution + hidden Score column; mixed-batch POST /scrape + per-row validation errors + prefill
  (22/22 test-client asserts); Playwright JS smoke (quick-add both sources, paste-append, row-toggle
  clearing, reindexing, per-source submit errors). Docs: scraping.md FIDE/OlimpBase sections
  rewritten; CLAUDE.md contract updated (FIDE score now None).
- **Post-review fixes (same session):** an adversarial review pass found two real bugs, both fixed:
  (1) `fetch_olimpbase_events` now returns `(events, definitive)` and `fetch_fide_history` only
  caches DEFINITIVE results — a transient OlimpBase outage (network error/5xx/unparseable 200) is no
  longer permanently cached as "no card", which would have silently masked a veteran's pre-2003
  history forever; (2) `claimOpenRow` returns -1 at the 100-row cap instead of handing back row 100,
  so quick-add/paste can no longer silently overwrite the last player ("The form is full" error).

## 2026-07-07 — 2700chess verification: 2002 gap filled + floor fix; quick-add dropdowns
Verified the FIDE scraper against 2700chess.com graphs for Carlsen, Anand, Sindarov, Niemann,
Aronian (owner request; 2700chess = ground truth), then fixed everything fixable and reworked
quick-add.
- **Verification harness** (scratchpad): 2700chess embeds its full graph as `chartData` inside a
  `window.__NEXT_SSG_SESSION__` JSON blob (curl_cffi Chrome impersonation gets past Cloudflare);
  diffed per-period against `fetch_fide_history` from 1990 onward. Initial results: Anand + Aronian
  perfect; Carlsen wrong at Apr 2003 (chart 2356 vs published 2315); all three veterans missing the
  five Jan 2002 – Jan 2003 periods (2700chess has them); Sindarov ±1 in mid-2014 and Niemann's
  2015 run (up to 37 pts) differ because FIDE retro-corrected — FIDE's own published lists
  (downloaded `frl` zips) adjudicated every dispute: they match 2700chess except Niemann May 2026,
  where OUR value (2742) matches the official list and 2700chess (2728) doesn't.
- **New `scraper/fide_archive.py`.** FIDE's downloadable archive (`ratings.fide.com/download/`) has
  the five quarterly lists inside the old "accepted gap" (jan02/apr02/jul02/oct02/jan03) plus apr03.
  When a chart starts at the floor, the gap periods are backfilled from those lists and the archive's
  Apr 2003 row overrides the chart's floor-row rating/games (fixes Carlsen's phantom 2356). Token-
  based parser (layouts drift between lists), latin-1, best-effort everywhere; verified values match
  2700chess exactly, incl. Aronian's absence from the Jul 2002 list.
- **Per-LIST permanent cache**: new `fide_archive_lists` table (~245k rows for all six lists) via
  `SqliteFideArchiveCache` injected from routes (scraper stays Flask-free); first veteran scrape
  downloads ~6 MB once, every later one is a local lookup (Aronian re-scrape: 2.7 s). Transient
  download/parse failures skip the list and cache nothing.
- **`fide_timeline_version` → 3**; cached v2 FIDE timelines re-scrape once on next analyze.
- **Post-fix diff vs 2700chess:** Carlsen 220/220 periods exact, Anand 244/244, Aronian 233/233,
  Caruana 217/217 (one 5-pt 2006 retro-correction); remaining diffs are only FIDE's own post-2003
  retro-corrections (documented in docs/scraping.md as accepted) — and the 2002 milestone collapse
  is gone (Carlsen: 2100/2200/2300 now land in Jan 2002 / Jul 2002 / Apr 2003 instead of all Apr 2003).
- **Quick-add reworked into two dropdown sections** (owner request): `FEATURED_FIDE` = the live FIDE
  top 15 (scraped from FIDE's top list 2026-07-07: Carlsen, Caruana, Nakamura, Sindarov, Keymer,
  Abdusattorov, So, Giri, Erigaisi, Wei Yi, Praggnanandhaa, Firouzja, Duda, Anand, Ding) and
  `FEATURED_USCF` = the existing 10 American players. A card is ONE button; its source comes from
  its section; the same player can sit in both sections with the same photo (Caruana, Nakamura).
  13 new CC/CC0-licensed portraits fetched from Wikimedia Commons (license + author verified via the
  Commons API; CREDITS.md + on-page credits updated, deduped for shared photos).
- **Player-page note updated**: pre-2003 sourcing now credits OlimpBase (1990–2001) + FIDE's own
  archive (2002–Apr 2003); the "milestones crossed in 2002 resolve to Apr 2003" caveat is gone.
- **Verified end-to-end**: webapp scrape of Carlsen (v2 → outdated → re-scrape → v3 with 2002 rows +
  2315 floor), Aronian cache-hit path, mixed Playwright batch (Carlsen FIDE + Caruana FIDE + Caruana
  USCF via the new cards) through progress → analyze charts; duplicate-add guard; Caruana's fresh
  FIDE timeline now starts Jan 2002 @ 2032 (age 9).
