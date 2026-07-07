"""FIDE scraper — second source alongside USCF.

See specs/005-fide-source/plan.md and research.md. Rating history comes from a
single JSON endpoint (`a_chart_data.phtml`). Plain `requests` plus the
XHR/Referer headers — no Cloudflare handling needed for FIDE.

Score % was DROPPED for FIDE by design (2026-07): the per-period
`a_indv_calculation.php` calls (one per rated period) made veteran scrapes
slow and fragile for a single table column. Every FIDE timeline event now
carries `score_numerator: None, score_games: None`, so `compute_record`
degrades `score_pct` to None per-cell, and a FIDE scrape is ~2-3 requests
total.

Pre-2003 history: `a_chart_data.phtml` has a hard server-side floor of Apr
2003 for every player. When a player's earliest rated period is exactly that
floor, `fetch_fide_history` backfills Jan 1990 – Oct 2001 from OlimpBase's
per-player rating cards (see scraper/olimpbase.py) and Jan 2002 – Jan 2003
from FIDE's own downloadable archive rating lists (see scraper/fide_archive.py)
— both best-effort and cached permanently. The archive's Apr 2003 list also
overrides the chart's floor-row rating, which sometimes carries a later FIDE
recalculation instead of the published value (Carlsen: chart 2356 vs published
2315; found by diffing against 2700chess.com, 2026-07).

Framework-agnostic: no Flask imports here.
"""
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.fide_archive import lookup_archive_ratings
from scraper.olimpbase import fetch_olimpbase_events

FIDE_BASE = "https://ratings.fide.com"
FIDE_INTER_REQUEST_DELAY_SECONDS = 1.0
FIDE_TIMEOUT_SECONDS = 20

# Bump when the FIDE timeline shape/semantics change so the scrape worker can
# re-scrape stale cached timelines (mirrors uscf_api.TIMELINE_API_VERSION).
# v2 (2026-07): score fields dropped by design; OlimpBase pre-2003 backfill.
# v3 (2026-07): Jan 2002 – Jan 2003 gap filled + Apr 2003 floor row corrected
#               from FIDE's official archive rating lists (fide_archive.py).
FIDE_TIMELINE_VERSION = 3

# a_chart_data.phtml never returns periods before Apr 2003, for any player.
# A history that starts exactly here may be truncated -> try the backfill.
FIDE_CHART_FLOOR = "2003-04-01"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class FidePlayerNotFound(Exception):
    pass


class FideNoRatedHistory(Exception):
    pass


class FideScrapeError(Exception):
    pass


def _fide_headers(referer):
    return {
        "User-Agent": _UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
    }


def get_fide_history(session, fide_id):
    """GET /a_chart_data.phtml?event=<id>&period= and return the parsed JSON list.

    Raises FidePlayerNotFound on an empty/invalid response (even after one
    retry), FideScrapeError on HTTP errors. The XHR header + a profile/chart
    Referer improve reliability. Retries once after FIDE_INTER_REQUEST_DELAY_SECONDS
    if the first response is empty (a transient empty-body response; cheap
    insurance — see the rate-limit note in research.md).
    """
    fide_id = str(fide_id)
    url = f"{FIDE_BASE}/a_chart_data.phtml"
    params = {"event": fide_id, "period": ""}
    headers = _fide_headers(f"{FIDE_BASE}/profile/{fide_id}/chart")

    last_exc = None
    for attempt in range(2):
        try:
            response = session.get(url, params=params, headers=headers, timeout=FIDE_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(FIDE_INTER_REQUEST_DELAY_SECONDS)
            continue
        if response.status_code != 200:
            raise FideScrapeError(
                f"FIDE returned HTTP {response.status_code} for chart data of {fide_id}."
            )
        body = response.content or b""
        if not body.strip():
            # Empty 200 body is occasionally returned transiently. Retry once.
            time.sleep(FIDE_INTER_REQUEST_DELAY_SECONDS)
            continue
        try:
            data = response.json()
        except ValueError:
            time.sleep(FIDE_INTER_REQUEST_DELAY_SECONDS)
            continue
        if not isinstance(data, list):
            raise FidePlayerNotFound(f"FIDE ID {fide_id} returned no rating history.")
        return data

    if last_exc is not None:
        raise FideScrapeError(f"FIDE chart request failed: {last_exc}")
    raise FideScrapeError("FIDE rate-limited — try again in a minute")


def search_fide_players(session, query, limit=20):
    """GET /incl_search_l.php?search=<q>; parse <table id="table_results">.

    Returns [{fide_id, name, title, fed, std, rpd, blz, b_year}, ...].
    """
    query = (query or "").strip()
    if not query:
        return []
    url = f"{FIDE_BASE}/incl_search_l.php"
    headers = _fide_headers(f"{FIDE_BASE}/")
    try:
        response = session.get(url, params={"search": query}, headers=headers, timeout=FIDE_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise FideScrapeError(f"FIDE search request failed: {e}")
    if response.status_code != 200:
        raise FideScrapeError(f"FIDE search returned HTTP {response.status_code}.")

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table", id="table_results")
    if table is None:
        return []

    results = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 9:
            continue
        texts = [c.get_text(strip=True) for c in cells]
        fide_id = texts[0]
        if not fide_id.isdigit():
            continue
        results.append({
            "fide_id": fide_id,
            "name": texts[1],
            "title": texts[2],
            "fed": texts[4],
            "std": texts[5],
            "rpd": texts[6],
            "blz": texts[7],
            "b_year": texts[8],
        })
        if len(results) >= limit:
            break
    return results


def _period_date(date_2):
    """'2003-Apr' -> '2003-04-01'. Raises ValueError on unparseable input."""
    return datetime.strptime(date_2, "%Y-%b").strftime("%Y-%m-01")


def fetch_fide_history(session, fide_id, progress_cb=None, status_cb=None,
                       olimpbase_cache=None, fide_archive_cache=None):
    """Fetch a FIDE player's raw rating timeline (spec 007 Part 3b).

    Returns the cacheable, DOB/milestone-independent timeline: ~2-3 requests
    (profile B-Year + chart JSON, occasionally one OlimpBase card). Every event
    carries `score_numerator: None, score_games: None` — FIDE score % was
    dropped by design (2026-07, see module docstring), so `compute_record`
    yields `score_pct: None` for every FIDE milestone.

    Pre-2003 backfill: FIDE's chart data is floored at Apr 2003 server-side.
    If the earliest rated period is exactly "2003-04-01" (i.e. the player MAY
    have older history), we backfill from two sources (both best-effort — a
    miss never breaks the scrape) and prepend those events, with
    `cumulative_games` running continuously across the whole merged sequence:

    * Jan 1990 – Oct 2001 from the player's OlimpBase card;
    * Jan 2002 – Jan 2003 from FIDE's official archive rating lists
      (scraper/fide_archive.py), whose Apr 2003 list also overrides the
      chart's floor-row rating when it disagrees (the chart sometimes serves
      a later FIDE recalculation there instead of the published value).

    `first_tournament_date` / `initial_rating` then come from the merged first
    event. `olimpbase_cache` is an optional `.get(fide_id)`/`.put(fide_id,
    payload)` adapter (payload `{"found": bool, "events": [...]}`) injected by
    the web layer (mirrors the crosstable-cache pattern; keeps this module
    Flask-free); hits — including negative ones — skip the OlimpBase request
    entirely, and pre-2002 data is immutable so entries never expire.
    `fide_archive_cache` is the analogous per-LIST adapter (`.has_list` /
    `.get_player` / `.put_list`) for the archive lists — each ~45k-player list
    is fetched once ever, then every later veteran scrape is free.

    Without the old per-period calc loop there is no long phase to report, so
    `progress_cb` just fires (0, 1) at the start and (1, 1) at the end to keep
    the SSE progress UI fed. The timeline carries
    `fide_timeline_version: FIDE_TIMELINE_VERSION` so the scrape worker can
    detect and re-scrape pre-v2 cached timelines.
    """
    fide_id = str(fide_id)

    if status_cb:
        status_cb("Querying FIDE…")
    if progress_cb:
        progress_cb(0, 1)

    # FIDE birth year is player-specific, so it's cacheable on the timeline and
    # applied as a DOB fallback by compute_record.
    try:
        fide_birth_year = get_fide_birth_year_from_profile(session, fide_id)
    except Exception:
        fide_birth_year = None

    history = get_fide_history(session, fide_id)
    if not history:
        raise FideNoRatedHistory(f"FIDE ID {fide_id} has no rated history.")

    # Periods are oldest-first in the payload. Be defensive and sort by date.
    periods = []
    for entry in history:
        date_2 = entry.get("date_2")
        rating = entry.get("rating")
        if not date_2 or rating in (None, ""):
            continue
        try:
            pdate = _period_date(date_2)
            prating = int(rating)
        except (ValueError, TypeError):
            continue
        try:
            pgames = int(entry.get("period_games") or 0)
        except (ValueError, TypeError):
            pgames = 0
        periods.append((pdate, prating, pgames, entry))
    if not periods:
        raise FideNoRatedHistory(f"FIDE ID {fide_id} has no rated history.")
    periods.sort(key=lambda p: p[0])

    first_period = periods[0]
    name = first_period[3].get("name") or fide_id
    country = first_period[3].get("country")

    # ── Pre-2003 backfill (OlimpBase + FIDE archive lists) ─────────────────
    # "2003-04-01" as the earliest period is FIDE's server-side floor, so the
    # player MAY have older history; any later start means they simply began
    # after the floor and no lookup is needed.
    olimp_events = []
    archive_rows = {}
    if first_period[0] == FIDE_CHART_FLOOR:
        cached = olimpbase_cache.get(fide_id) if olimpbase_cache is not None else None
        if cached is not None:
            # Cache hit — positive or negative — costs zero OlimpBase requests.
            olimp_events = (cached.get("events") or []) if cached.get("found") else []
        else:
            if status_cb:
                status_cb("Checking OlimpBase for pre-2003 history…")
            time.sleep(1.0)  # politeness gap after the FIDE calls
            fetched, definitive = fetch_olimpbase_events(session, fide_id, name)
            if olimpbase_cache is not None and definitive:
                # DEFINITIVE results are cached forever — negatives (found=0)
                # included, so card-less players never trigger repeat OlimpBase
                # hits. Transient failures (network blip, 5xx) are NOT cached:
                # the no-TTL negative cache would otherwise permanently mask a
                # veteran's pre-2003 history; instead the next TTL re-scrape
                # simply retries the card.
                olimpbase_cache.put(
                    fide_id, {"found": fetched is not None, "events": fetched or []}
                )
            olimp_events = fetched or []

        # OlimpBase ends at the Oct 2001 list; FIDE's own archive lists cover
        # Jan 2002 – Apr 2003 (best-effort; cached per LIST, so only the first
        # veteran scrape ever downloads them).
        archive_rows = lookup_archive_ratings(
            session, fide_id, archive_cache=fide_archive_cache, status_cb=status_cb
        )

    # The archive's Apr 2003 row corrects the chart's floor row (rating AND
    # games): the chart sometimes serves a later recalculation there that
    # never appeared on a published list. The rest fill the 2002 gap.
    floor_fix = archive_rows.pop(FIDE_CHART_FLOOR, None)
    gap_events = [
        {"date": d, "rating": row["rating"], "period_games": row["games"]}
        for d, row in sorted(archive_rows.items())
    ]

    # Merge: OlimpBase events (all <= Oct 2001) then archive gap rows (2002 –
    # Jan 2003) are strictly older than the FIDE chart periods — prepend, with
    # ONE cumulative_games running total across the whole sequence. The sort +
    # date guard are belt-and-braces against a malformed cached payload ever
    # overlapping the FIDE range.
    cumulative_games = 0
    events = []
    for oev in sorted(olimp_events + gap_events, key=lambda e: e["date"]):
        if oev["date"] >= first_period[0]:
            continue
        cumulative_games += int(oev.get("period_games") or 0)
        events.append({
            "date": oev["date"],
            "cumulative_games": cumulative_games,
            "rating": int(oev["rating"]),
            "score_numerator": None,
            "score_games": None,
        })
    for pdate, prating, pgames, _entry in periods:
        if floor_fix is not None and pdate == FIDE_CHART_FLOOR:
            prating, pgames = int(floor_fix["rating"]), int(floor_fix["games"])
        cumulative_games += pgames
        events.append({
            "date": pdate,
            "cumulative_games": cumulative_games,
            "rating": prating,
            "score_numerator": None,
            "score_games": None,
        })

    if progress_cb:
        progress_cb(1, 1)

    return {
        "source": "fide",
        "player_id": fide_id,
        "name": name,
        "country": country,
        "first_tournament_date": events[0]["date"],
        "initial_rating": int(events[0]["rating"]),
        "fide_birth_year": fide_birth_year,
        "events": events,
        "fide_timeline_version": FIDE_TIMELINE_VERSION,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_fide_player(session, fide_id, dob=None, milestones=None, progress_cb=None,
                       status_cb=None, use_fide_birth_year=True, olimpbase_cache=None,
                       fide_archive_cache=None):
    """Back-compat wrapper: fetch the raw FIDE timeline and compute the public
    record for this DOB + milestone ladder. Returns the canonical dict shape."""
    from scraper.core import compute_record
    timeline = fetch_fide_history(
        session, fide_id, progress_cb=progress_cb, status_cb=status_cb,
        olimpbase_cache=olimpbase_cache, fide_archive_cache=fide_archive_cache,
    )
    return compute_record(timeline, dob=dob, milestones=milestones,
                          use_fide_birth_year=use_fide_birth_year)


def get_fide_birth_year_from_profile(session, fide_id):
    """Return the FIDE-listed birth year by hitting the profile page directly.

    scraper.core.get_fide_birth_year walks USCF -> FIDE; here we already have
    the FIDE ID, so go straight to the profile. Returns None on any failure.
    """
    try:
        r = session.get(
            f"{FIDE_BASE}/profile/{fide_id}",
            headers={"User-Agent": _UA},
            timeout=FIDE_TIMEOUT_SECONDS,
        )
        soup = BeautifulSoup(r.text, "lxml")
        year_label = soup.find(string=lambda t: t and "B-Year" in t)
        if not year_label:
            return None
        parent = year_label.parent
        sib = parent.find_next_sibling()
        if sib is None:
            return None
        year = sib.text.strip()
        if year.isdigit() and 1900 < int(year) < 2025:
            return int(year)
    except Exception:
        return None
    return None
