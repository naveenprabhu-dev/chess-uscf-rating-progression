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

FIDE (ratings.fide.com) is the second rating source. There is no Cloudflare gate — plain `requests` with two headers is enough. Rating history is a single JSON call; cumulative score adds one calculation call per rated period.

- **Rating history (the workhorse):** `GET https://ratings.fide.com/a_chart_data.phtml?event=<fide_id>&period=` returns a JSON array of every rating period since the player's first published rating. Each entry: `date_2` (`"2003-Apr"`), `rating` (str), `period_games` (str), `name`, `country`, plus rapid/blitz fields we ignore (classical-only MVP). Cast `rating`/`period_games` to int. Send `X-Requested-With: XMLHttpRequest` and `Referer: .../profile/<id>/chart` for reliability.
- **Per-period W/D/L (for `score_pct`):** `GET https://ratings.fide.com/a_indv_calculation.php?id_number=<id>&rating_period=<YYYY-MM-01>&t=0` returns one `<table>` per tournament in that period. Each table's summary row (first cell = integer avg-opponent rating) carries `w` (score = wins + 0.5·draws) and `n` (games). `get_fide_calculations` sums `w`/`n` across the period's tables; `scrape_fide_player` accumulates them and divides for the cumulative `score_pct` at each milestone. It calls this once per rated period the player competed in, and **early-exits the period loop once the top milestone is filled**, so a player who topped out early costs far fewer calls than their full career length.
- **Rate limit:** none observed at this scraper's volume (burst-tested 2026-05-28: 54 back-to-back requests, zero empties). An earlier "~30 s empty-body" claim was unverified and wrong. `get_fide_history`/`get_fide_calculations` keep a single retry-on-empty as cheap insurance against a *transient* empty 200, but assume no fixed window. Stay sequential and polite anyway; never parallelize.
- **Search:** `GET https://ratings.fide.com/incl_search_l.php?search=<q>` returns an HTML fragment with `<table id="table_results">`. `search_fide_players` parses rows into `{fide_id, name, title, fed, std, rpd, blz, b_year}`.
- **Date approximation:** the first period's `date_2` becomes `first_tournament_date = YYYY-MM-01`. Periods were quarterly through 2011, monthly since 2013, so "months to milestone" is bucketed to ±3 months for older players. The player page surfaces this caveat.
- **Initial rating semantics:** FIDE's `initial_rating` is the *first published* rating (the player already had unrated games before it), NOT a post-first-event rating like USCF. Don't try to align the two scales.
- **`score_pct` degradation:** if a period's calc page can't be fetched, score is reported up to that period and `None` afterward — the scrape never crashes over a missing calc page. `_milestone_table.html` always shows the Score column now; the compare Score chart renders normally for all-FIDE comparisons.
- **DOB:** user-supplied → FIDE B-Year (`get_fide_birth_year_from_profile`, which hits the profile page directly since we already have the FIDE ID) → none. Reuses `scraper.core.calculate_age` and `months_difference`.

Exceptions: `FidePlayerNotFound` (empty/invalid history), `FideNoRatedHistory` (no rated periods), `FideScrapeError` (HTTP error, or a persistently empty body on history/calc).
