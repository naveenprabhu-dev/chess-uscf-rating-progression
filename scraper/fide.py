"""FIDE scraper — second source alongside USCF.

See specs/005-fide-source/plan.md and research.md. Rating history comes from a
single JSON endpoint (`a_chart_data.phtml`). Per-period W/D/L (for the
cumulative score %) comes from `a_indv_calculation.php`, one call per rated
period the player competed in. Plain `requests` plus the XHR/Referer headers —
no Cloudflare handling needed for FIDE.

Framework-agnostic: no Flask imports here.
"""
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

FIDE_BASE = "https://ratings.fide.com"
FIDE_INTER_REQUEST_DELAY_SECONDS = 1.0
FIDE_TIMEOUT_SECONDS = 20

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


def _parse_calculations(html):
    """Sum (score, games) across the per-tournament summary rows of a
    calculations fragment.

    `a_indv_calculation.php` returns one <table> per tournament played in the
    period. Each table has a header row, a summary row, and per-opponent detail
    rows. The summary row's first cell is the average-opponent rating (an
    integer, e.g. "2304"); detail rows start with an opponent name. We only sum
    summary rows to avoid double-counting. Columns: Rc, Ro, _, _, _, w, n, ...
    where `w` is the score (wins + 0.5*draws) and `n` the game count.
    """
    soup = BeautifulSoup(html, "lxml")
    total_w = 0.0
    total_n = 0
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all("td")]
            if len(cells) < 7 or not cells[0].isdigit():
                continue  # header rows have no <td>; detail rows lead with a name
            try:
                total_w += float(cells[5])
                total_n += int(cells[6])
            except (ValueError, IndexError):
                continue
    return total_w, total_n


def get_fide_calculations(session, fide_id, period):
    """GET /a_indv_calculation.php for one rating period; return (score, games).

    `period` is `YYYY-MM-01`. `t=0` selects standard (classical). Returns a
    (total_score, total_games) tuple where total_score already folds draws in as
    0.5 (FIDE's `w` column). Raises FideScrapeError on HTTP errors or a
    persistently empty body, so the caller can decide score is incomplete.
    """
    fide_id = str(fide_id)
    url = f"{FIDE_BASE}/a_indv_calculation.php"
    params = {"id_number": fide_id, "rating_period": period, "t": "0"}
    headers = _fide_headers(f"{FIDE_BASE}/calculations.phtml?id_number={fide_id}")

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
                f"FIDE calc returned HTTP {response.status_code} for {fide_id} {period}."
            )
        if not (response.content or b"").strip():
            time.sleep(FIDE_INTER_REQUEST_DELAY_SECONDS)
            continue
        return _parse_calculations(response.text)

    if last_exc is not None:
        raise FideScrapeError(f"FIDE calc request failed: {last_exc}")
    raise FideScrapeError("FIDE calc rate-limited — empty response")


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


def fetch_fide_history(session, fide_id, progress_cb=None, status_cb=None):
    """Fetch a FIDE player's raw rating timeline (spec 007 Part 3b).

    Returns the cacheable, DOB/milestone-independent timeline. Cumulative score
    data is reconstructed from per-period W/D/L via `get_fide_calculations` (one
    call per rated period the player competed in). Unlike the old milestone-aware
    scraper, this fetches *all* rated periods (no milestone-based early exit) so
    the cached timeline can be re-derived for any ladder. If a period's calc
    can't be fetched, `score_games` is None for that and every later event, so
    `compute_record` degrades score to None per-cell from that point on.
    `progress_cb` fires once per rated period processed.
    """
    fide_id = str(fide_id)

    if status_cb:
        status_cb("Querying FIDE…")

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
    first_tournament_date = first_period[0]
    initial_rating = first_period[1]
    name = first_period[3].get("name") or fide_id
    country = first_period[3].get("country")

    active_total = sum(1 for p in periods if p[2] > 0)
    if progress_cb:
        progress_cb(0, active_total or 1)

    cumulative_games = 0
    cum_score = 0.0          # running sum of FIDE `w` (wins + 0.5*draws)
    cum_score_games = 0      # running sum of FIDE `n` (games) from calc pages
    score_complete = True    # flips False if a period's calc can't be fetched
    processed = 0
    events = []
    for pdate, prating, pgames, _entry in periods:
        cumulative_games += pgames
        if pgames > 0:
            if score_complete:
                try:
                    period_w, period_n = get_fide_calculations(session, fide_id, pdate)
                    cum_score += period_w
                    cum_score_games += period_n
                except FideScrapeError:
                    score_complete = False
            processed += 1
            if progress_cb:
                progress_cb(processed, active_total or 1)
        events.append({
            "date": pdate,
            "cumulative_games": cumulative_games,
            "rating": prating,
            "score_numerator": cum_score if score_complete else None,
            "score_games": cum_score_games if (score_complete and cum_score_games > 0) else None,
        })

    if progress_cb:
        progress_cb(active_total or 1, active_total or 1)

    return {
        "source": "fide",
        "player_id": fide_id,
        "name": name,
        "country": country,
        "first_tournament_date": first_tournament_date,
        "initial_rating": int(initial_rating),
        "fide_birth_year": fide_birth_year,
        "events": events,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_fide_player(session, fide_id, dob=None, milestones=None, progress_cb=None, status_cb=None):
    """Back-compat wrapper: fetch the raw FIDE timeline and compute the public
    record for this DOB + milestone ladder. Returns the canonical dict shape."""
    from scraper.core import compute_record
    timeline = fetch_fide_history(
        session, fide_id, progress_cb=progress_cb, status_cb=status_cb,
    )
    return compute_record(timeline, dob=dob, milestones=milestones)


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
