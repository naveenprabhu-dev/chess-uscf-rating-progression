# Scraping Reference

Reference material for work in `scraper/core.py`. Not loaded into every conversation — read this when editing the scraper or diagnosing fetch failures.

## USCF scraping conventions (the parts that bit us before)

- All scraping hits `https://www.uschess.org/msa/MbrDtlTnmtHst.php?<uscf_id>` and `...?<uscf_id>.<page>` for paginated tournament history (50 per page).
- A tournament row's classical rating change lives in the **third `<td>`** of the row that follows an HTML comment `Detail: <N>` (one-indexed, working backwards from most recent). Cell text looks like `1450 => 1487` or `1450 => 1487 P12`. The `P12` provisional suffix must be stripped before `int()`.
- **Only count classical OTB tournaments**: filter to rows where `"=>" in classical_td_text and "ONL" not in classical_td_text`.
- **Skip duplicate sections of the same tournament**: dedupe consecutive identical `tournament_url`s — they represent multiple sections of one event and would double-count games.
- **No cutoff date.** The original `scrape_sheets.py` has a `cutoff = datetime.strptime('2025-05-16', ...)` early-break in `rating_progress_by_months_games_and_age` — drop it. The web app processes every tournament a player has played, including current ones.
- **Divide-by-zero guard**: a player who withdrew with byes can have `games_played == 0`. Keep both checks from `scrape_sheets.py`. After the spec-007 fetch/compute split they live in two places: the "skip event when `games_played == 0`" guard stays in `_gather_classical_events` / `_build_events` (so the event is never emitted), and the `adjusted_win_rate` divide-by-zero guard moved into the pure `compute_record` (`score_pct = score_numerator / score_games`, `None` when `score_games` is falsy). The archived `scrape_user_input.py` is missing these and will crash on those players.
- USCF doesn't expose an API. Be polite — no parallel scraping in the MVP. One `requests.Session()` per scrape.

## Bot detection on uschess.org

The **modern** uschess.org UI (the public-facing site) shows an interactive Cloudflare check in browsers. The **legacy MSA backend** the scraper hits (`https://www.uschess.org/msa/MbrDtlTnmtHst.php?<id>` and friends) does NOT gate requests — it returns HTTP 200 to plain `python-requests` calls with no challenge. Cloudflare injects a passive JS telemetry sensor (`/cdn-cgi/challenge-platform/scripts/jsd/main.js`) into served pages, but it only collects data when executed in a real browser; ignoring it has no effect on scraping.

Defaults that keep us out of trouble:

- Set a realistic `User-Agent` on the session (e.g. a current Chrome string) instead of `python-requests/...`. Cheap insurance against future tightening.
- One `requests.Session()` per scrape run (we already do this) so connection reuse and any cookies persist.
- Add a small `time.sleep(0.25–0.5)` between page-history requests inside `rating_progress_by_months_games_and_age`. Most players need only 1–3 page fetches, so this adds seconds, not minutes.

If `/msa/` ever does start serving a 403 / "Just a moment…" interstitial, escalate in this order — stop at the first one that works:

1. **Browser-realistic headers**: `User-Agent`, `Accept`, `Accept-Language`, `Accept-Encoding`, `Referer` set to the previous page in the flow.
2. **`curl_cffi`** (`pip install curl_cffi`) — drop-in `requests`-like API that emulates Chrome's TLS/HTTP-2 fingerprint. Defeats Cloudflare TLS-fingerprint gating without running JS.
3. **`cloudscraper`** (`pip install cloudscraper`) — solves Cloudflare's non-interactive IUAM JS challenge in pure Python.
4. **Headless browser** (Playwright, last resort) — actually executes the challenge JS. Slow, heavy; reserve for true interactive challenges or captchas.

Whatever the path, keep the choice isolated inside `scraper/core.py` behind a `make_session()` helper so the web layer never sees the difference.

## FIDE source (`scraper/fide.py`)

FIDE (ratings.fide.com) is the second rating source. There is no Cloudflare gate — plain `requests` with two headers is enough. A full FIDE scrape is **~2-3 requests total**: profile page (B-Year), chart JSON, and occasionally one OlimpBase card (below).

- **Rating history (the workhorse):** `GET https://ratings.fide.com/a_chart_data.phtml?event=<fide_id>&period=` returns a JSON array of every rating period since the player's first published rating **or Apr 2003, whichever is later** (see the floor below). Each entry: `date_2` (`"2003-Apr"`), `rating` (str), `period_games` (str), `name`, `country`, plus rapid/blitz fields we ignore (classical-only MVP). Cast `rating`/`period_games` to int. Send `X-Requested-With: XMLHttpRequest` and `Referer: .../profile/<id>/chart` for reliability.
- **Score % is DROPPED by design (2026-07).** The old path reconstructed cumulative score from `a_indv_calculation.php`, one call per rated period — slow (a veteran = 200+ requests) and fragile, all for one table column. Those calls are gone (`get_fide_calculations`/`_parse_calculations` deleted); every FIDE timeline event carries `score_numerator: None, score_games: None`, so `compute_record` yields `score_pct: None` per-cell for FIDE. Don't re-add the calc loop.
- **The Apr 2003 floor:** `a_chart_data.phtml` never returns periods before `2003-Apr`, for **any** player (server-side; verified with Kasparov, FIDE-rated since 1979). `fetch_fide_history` treats an earliest period of exactly `2003-04-01` (`FIDE_CHART_FLOOR`) as "may be truncated" and attempts the OlimpBase + archive-list backfills; any later start means the player genuinely began after the floor and no lookup happens.
- **The Jan 2002 – Jan 2003 gap is FILLED from FIDE's archive lists (2026-07):** OlimpBase's per-player cards end at Oct 2001 and the chart starts at Apr 2003, but FIDE's own downloadable archive has the five quarterly lists in between — see `scraper/fide_archive.py` below. The archive's Apr 2003 list also **overrides the chart's floor-row rating and games**: the chart sometimes serves a later FIDE recalculation there that never appeared on a published list (Carlsen: chart 2356 vs published 2315 — found by diffing against 2700chess.com, whose graphs matched the published lists exactly).
- **FIDE retro-corrections (known, accepted):** for a handful of post-2003 months the chart JSON returns a slightly different value than the list FIDE originally published (e.g. Sindarov mid-2014 ±1, Niemann's corrected 2015 run, ~37 pts) — FIDE recalculated later and the chart reflects the correction. We show the chart values; matching originally-published values would mean downloading every monthly list (~4–13 MB each). Verified rare and small (2026-07: 9 of ~990 periods across five test players).
- **Rate limit:** none observed at this scraper's volume (burst-tested 2026-05-28: 54 back-to-back requests, zero empties). An earlier "~30 s empty-body" claim was unverified and wrong. `get_fide_history` keeps a single retry-on-empty as cheap insurance against a *transient* empty 200, but assume no fixed window. Stay sequential and polite anyway; never parallelize.
- **Search:** `GET https://ratings.fide.com/incl_search_l.php?search=<q>` returns an HTML fragment with `<table id="table_results">`. `search_fide_players` parses rows into `{fide_id, name, title, fed, std, rpd, blz, b_year}`.
- **Date approximation:** the merged first event's date becomes `first_tournament_date = YYYY-MM-01`. Periods were semiannual (OlimpBase era) / quarterly through 2011, monthly since 2013, so "months to milestone" is bucketed to ±3–6 months for older players. The player page surfaces this caveat.
- **Initial rating semantics:** FIDE's `initial_rating` is the *first published* rating (the player already had unrated games before it), NOT a post-first-event rating like USCF. For pre-2003 veterans it comes from the first OlimpBase list row (≥ Jan 1990). Don't try to align the two scales.
- **DOB:** user-supplied → FIDE B-Year (`get_fide_birth_year_from_profile`, which hits the profile page directly since we already have the FIDE ID) → none. Reuses `scraper.core.calculate_age` and `months_difference`.
- **Versioning:** the timeline carries `fide_timeline_version: FIDE_TIMELINE_VERSION` (3). The scrape worker re-scrapes any cached FIDE timeline whose version doesn't match (v2 lacked the archive-list gap fill + floor-row fix; pre-v2 timelines have real score data, no backfill, and no version key).

Exceptions: `FidePlayerNotFound` (empty/invalid history), `FideNoRatedHistory` (no rated periods), `FideScrapeError` (HTTP error, or a persistently empty history body).

## OlimpBase pre-2003 backfill (`scraper/olimpbase.py`)

OlimpBase (olimpbase.org, a volunteer archive by Wojciech Bartelski) republishes the historical FIDE rating lists **through Oct 2001** with a per-player "rating card" page — the only per-player source for pre-2003 FIDE history. `fetch_olimpbase_events` fetches ONE card per player, strictly best-effort: **any** failure (404 = no card, network error after one polite retry, parse failure, identity-guard failure) returns `None` and the FIDE scrape proceeds without backfill.

- **URL pattern** (verified live 2026-07-01): `https://www.olimpbase.org/Elo/player/{L}/{urlencoded name}.html`, where `{L}` is the uppercase first letter of the surname and the name is exactly the "Last, First" string from FIDE's chart JSON, comma literal, spaces `%20` (e.g. `/Elo/player/K/Kasparov,%20Garry.html`). Pages are ~10 KB.
- **Page structure:** a small header table (federation, DOB, "Most recent ID" linking to `ratings.fide.com/profile/{id}`), then one `<pre>` block of fixed-width rows, **newest first**: `list-date-link | pos | Player_ID | Name | Title | Fed | Rtng | +/- | Gms | Birthday | Sex | Flag`. The date lives in the `<a>` text as `"Mon YYYY"`.
- **Parsing gotchas:** rows are littered with `&nbsp;` entities and `<span style="color: …">` wrappers around +/- values — unescape + strip tags before parsing. Parse by anchoring on `FED RTNG` (3 uppercase letters + 3-4 digit rating; take the last match) rather than column offsets. Pre-1990 rows have a blank/`x` Player_ID; the +/- and Gms columns can be blank on the oldest rows (a lone signed token or `"0"` is the +/-; a lone non-zero unsigned token is Gms). Kasparov's struck 1994 lists render as `--- no data available ---` rows — skip them. Blank/unparseable Gms → 0.
- **Identity guard:** the card is looked up by NAME, so require proof it belongs to the requested FIDE ID — a row's Player_ID equals it, or the header's profile link matches. Otherwise return `None` (homonym/spelling-drift protection). Once confirmed, keep ALL rows regardless of per-row ID: early-1990 lists used different ID schemes (Kasparov's Jan 1990 row says `58971`).
- **Jan 1990 floor:** rows before `1990-01-01` are discarded (`OLIMPBASE_MIN_DATE`) — FIDE IDs exist from Jan 1990, and OlimpBase itself flags pre-1990 lists as manually compiled (dupes, misspellings). Lists in the kept window: Jan+Jul 1990–1999; Jan/Jul/Oct 2000; Jan/Apr/Jul/Oct 2001.
- **Permanent cache — definitive answers only:** `fetch_olimpbase_events` returns `(events, definitive)`. Definitive results — a parsed card, a confirmed 404 (no card), a failed identity guard — live forever in the `olimpbase_cache` table, negatives (`found=0`) included (via `webapp.cache.SqliteOlimpbaseCache`, injected into `fetch_fide_history` like the crosstable cache so the scraper stays Flask-free). No TTL: the site is frozen (last lists ~2001) and pre-2002 data is immutable. **Transient failures (network error, 5xx, unparseable 200) are NOT cached** — the no-TTL negative cache would otherwise permanently mask a veteran's pre-2003 history behind one olimpbase.org blip; the next TTL re-scrape just retries the card.
- **Politeness:** robots.txt is empty (no restrictions), but be gentle regardless — sequential only, a `time.sleep(1.0)` gap after the FIDE calls before the (single, small) card request, cache-first always.

## FIDE archive-list backfill, Jan 2002 – Apr 2003 (`scraper/fide_archive.py`)

FIDE publishes every historical rating list as a downloadable zip at `https://ratings.fide.com/download/<name>.zip`. Six of them cover the chart endpoint's blind spot: `jan02frl.zip`, `apr02frl.zip`, `jul02frl.zip`, `oct02frl.zip`, `jan03frl.zip` fill the Jan 2002 – Jan 2003 gap, and `apr03frl.zip` corrects the chart's floor row. Verified against 2700chess.com graphs (2026-07): archive values match exactly for Carlsen, Anand, and Aronian (including Aronian's absence from the Jul 2002 list).

- **Format:** each zip holds one fixed-width TXT (~30–45k players, ~1 MB zipped). Column layouts drift between lists (ID padding, title column, 2- vs 4-digit birth years), so rows are parsed **by token**, olimpbase.py-style: first token all-digits = FIDE ID; anchor on the last `FED RTNG` match (3 uppercase + 3-4 digit rating); games = the next all-digit token after the anchor (birthdays contain dots so they never match). Decode latin-1 (pre-Unicode era, never raises). A few lists contain duplicate IDs (~1–18 rows) — last row wins.
- **Cached per LIST, not per player:** `SqliteFideArchiveCache` (`fide_archive_lists` table, ~245k rows / ~10 MB for all six) — the first veteran scrape downloads+stores each list once (~6 s + parse), then every later veteran's backfill is a local lookup. PERMANENT, no TTL: published lists are immutable.
- **Best-effort, transient-safe:** any failure (network after one polite retry, non-200, bad zip, zero-row parse) skips that list for THIS scrape and caches nothing, so the next re-scrape retries. A player absent from a cached list is just "not rated that period" (absence of rows is trustworthy once a list is cached whole).
- **Politeness:** sequential, 1 s gap before each download; at most six ~1 MB downloads ever per deployment.
