"""USCF JSON API client — fast path for `scrape_player`.

See specs/006-uscf-api-source/plan.md. Raises ApiUnavailable on any failure
mode so the caller falls back to the HTML scraper in scraper.core.

**Foreign / no-affiliate events (2026-07):** a player's FIDE/foreign tournaments
(Corus, Tal Memorial, Olympiad, the Candidates, …) carry a Regular (`ratingSource
== "R"`) rating record but a `G`/`A`/`F` *section* rating system, and the old
client dropped them by requesting `RatingSource="R"` — collapsing, e.g.,
Caruana's 2008–2014 climb into one apparent jump. We now pull **all** sections
and keep any with an `R` rating record (the true regular-rating timeline). Those
foreign events have no rows in the bulk `/games` endpoint, so their per-game
results (for the games/score stats) come from the event crosstable
(`/rated-events/{id}/sections/{n}/standings`), one small call per foreign event,
memoized via an injected `crosstable_cache`. The pre-2026-07 behavior is kept
verbatim in `uscf_api_legacy.py` and reachable via `USCF_INCLUDE_FOREIGN=0`.
"""
import logging
import time
from datetime import datetime, timezone

import requests

from scraper.core import get_fide_birth_year, make_session

log = logging.getLogger(__name__)

USCF_API_BASE = "https://ratings-api.uschess.org"
API_PAGE_SIZE = 200
# 15s, not the spec's 5s: high-volume players (Caruana) hit upstream 499s
# below 10s; verified all pages clear inside ~1s under normal load.
API_TIMEOUT_SECONDS = 15
# Marks the foreign-inclusive timeline shape so the web worker can re-scrape any
# cached pre-2026-07 (foreign-less) timeline. Bump if the timeline shape changes.
TIMELINE_API_VERSION = 2
# Politeness gap between crosstable (standings) network calls on a cache miss — a
# globetrotter can need ~165, and we don't parallelize (repo rule: don't get
# IP-banned). ~0.03s keeps the burst under ~30/s and adds only a few seconds.
STANDINGS_PACE_SECONDS = 0.03


class ApiUnavailable(Exception):
    """The API couldn't produce a timeline.

    `retryable` (default True) marks failures that waiting might fix — network
    errors, 5xx/499/429, transient non-JSON bodies. `retryable=False` marks
    PERMANENT answers (member not in the API, malformed/empty member data)
    that `fetch_history`'s retry loop must not spin on; `not_found=True`
    additionally identifies the "no such member" case so the caller can raise
    a user-facing PlayerNotFound instead of an availability error.

    Historically this signaled scrape_player to fall back to the HTML scraper;
    that fallback is now opt-in only (config.USCF_HTML_FALLBACK) because the
    MSA HTML pages stopped updating ~Nov 2025.
    """

    def __init__(self, message, retryable=True, not_found=False):
        super().__init__(message)
        self.retryable = retryable
        self.not_found = not_found


class _MemoCache:
    """Default in-process crosstable cache (get/put by event+section+member).
    The web app injects a SQLite-backed one so the cost is paid once, ever."""

    def __init__(self):
        self._d = {}

    def get(self, event_id, section_number, member_id):
        return self._d.get((str(event_id), section_number, str(member_id)))

    def put(self, event_id, section_number, member_id, counts):
        self._d[(str(event_id), section_number, str(member_id))] = counts


# Waits between in-call attempts (so one _get tolerates a ~5 s blip on its
# own); the whole-scrape retry loop in scraper.core.fetch_history layers
# longer waits on top for sustained outages.
_GET_RETRY_WAITS = (1, 4)


def _get(path, **params):
    """GET BASE+path. Returns parsed JSON on 200, None on 404. Retries with
    short waits on ConnectionError/Timeout/5xx/499/429 (honoring Retry-After).
    Raises ApiUnavailable on any other failure.
    """
    url = USCF_API_BASE + path
    last = None
    for attempt in range(len(_GET_RETRY_WAITS) + 1):
        if attempt:
            time.sleep(_GET_RETRY_WAITS[attempt - 1])
        try:
            response = requests.get(url, params=params, timeout=API_TIMEOUT_SECONDS)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            continue
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise ApiUnavailable(f"non-JSON from {url}")
        if response.status_code == 404:
            return None
        # 499 = upstream edge timeout; 429 = rate limited. Both retryable.
        if response.status_code in (429, 499) or 500 <= response.status_code < 600:
            last = f"{response.status_code} from {url}"
            if response.status_code == 429:
                retry_after = (response.headers.get("Retry-After") or "").strip()
                if retry_after.isdigit():
                    time.sleep(min(int(retry_after), 30))
            continue
        raise ApiUnavailable(f"{response.status_code} from {url}")
    raise ApiUnavailable(f"after {len(_GET_RETRY_WAITS) + 1} attempts: {last}")


def _member_detail(uscf_id):
    data = _get(f"/api/v1/members/{uscf_id}")
    if data is None:
        # A 404 here is a definitive "no such member" — retrying won't help.
        raise ApiUnavailable("not in api", retryable=False, not_found=True)
    first = (data.get("firstName") or "").strip()
    last = (data.get("lastName") or "").strip()
    if not first and not last:
        raise ApiUnavailable("member missing name", retryable=False)
    return {"name": f"{first} {last}".title()}


def _paginate(path, **params):
    """Walk a paginated endpoint, yielding items. Increments offset by the
    actual page size returned — the API caps at 100 even when Size=200.
    """
    offset = 0
    while True:
        page = _get(path, Size=API_PAGE_SIZE, Offset=offset, **params)
        if page is None:
            raise ApiUnavailable(f"404 mid-pagination: {path}")
        items = page.get("items") or []
        for item in items:
            yield item
        if not page.get("hasNextPage"):
            return
        if not items:
            return
        offset += len(items)


def _r_record(section):
    """The section's Regular (US Chess) rating record, or None. Present on plain
    Regular sections, dual (`D`) sections, AND foreign (`G`/`A`/`F`) sections that
    updated the regular rating — that last group is what the old client dropped."""
    for rr in section.get("ratingRecords") or []:
        if rr.get("ratingSource") == "R":
            return rr
    return None


def _list_sections(uscf_id):
    """All sections that touched the Regular rating, oldest-first. No
    `RatingSource` filter — that filter drops no-affiliate foreign events even
    though they carry an `R` record."""
    items = [s for s in _paginate(f"/api/v1/members/{uscf_id}/sections") if _r_record(s)]
    items.sort(key=lambda s: s.get("startDate") or "")
    return items


def _list_games(uscf_id):
    """Per-section W/D/L/games from the bulk games endpoint. `RatingSource="R"`
    returns regular-affecting games (plain `R` + dual `D`); count them all (the
    old client skipped dual games). Foreign events have NO rows here — those are
    filled in from the crosstable in `_build_events`."""
    by_section = {}
    total = 0
    for game in _paginate(f"/api/v1/members/{uscf_id}/games", RatingSource="R"):
        event_id = (game.get("event") or {}).get("id")
        section_number = (game.get("section") or {}).get("number")
        key = (event_id, section_number)
        rec = by_section.setdefault(key, {"wins": 0, "draws": 0, "losses": 0, "games": 0})
        outcome = (game.get("player") or {}).get("outcome")
        if outcome == "Win":
            rec["wins"] += 1
            rec["games"] += 1
        elif outcome == "Loss":
            rec["losses"] += 1
            rec["games"] += 1
        elif outcome == "Draw":
            rec["draws"] += 1
            rec["games"] += 1
        elif outcome == "Unknown":
            rec["games"] += 1
        total += 1
    if total == 0:
        # Empty bulk games = treat as an API failure (fall back to HTML). This
        # guard also stops an API hiccup from routing every US event through the
        # per-event crosstable path.
        raise ApiUnavailable("games index empty")
    return by_section


def _count_outcomes(round_outcomes):
    counts = {"wins": 0, "draws": 0, "losses": 0, "games": 0}
    for o in round_outcomes or []:
        oc = o.get("outcome")
        if oc == "Win":
            counts["wins"] += 1
            counts["games"] += 1
        elif oc == "Draw":
            counts["draws"] += 1
            counts["games"] += 1
        elif oc == "Loss":
            counts["losses"] += 1
            counts["games"] += 1
        elif oc == "Unknown":
            counts["games"] += 1
        # Bye/Forfeit/None → not a rated game, skip
    return counts


def _standings_counts(event_id, section_number, member_id, cache):
    """This member's W/D/L/games in a foreign event, from the crosstable
    (`/rated-events/.../standings`). Cached by (event, section, member). A
    standings failure is non-fatal (returns zeros) so one flaky call can't sink
    the whole scrape — the rating still comes from the section record."""
    cached = cache.get(event_id, section_number, member_id)
    if cached is not None:
        return cached
    counts = {"wins": 0, "draws": 0, "losses": 0, "games": 0}
    offset = 0
    definitive = False   # only cache a real answer — never a transient failure,
                         # or a flaky call would poison the cache with 0 games.
    path = f"/api/v1/rated-events/{event_id}/sections/{section_number}/standings"
    while True:
        if STANDINGS_PACE_SECONDS:
            time.sleep(STANDINGS_PACE_SECONDS)
        try:
            page = _get(path, Offset=offset, Size=100)
        except ApiUnavailable:
            break                      # transient — leave uncached so it retries
        definitive = True              # the request itself succeeded (200 or 404)
        if not page:
            break                      # 404: this event has no standings
        rows = page.get("items") or []
        row = next((r for r in rows if str(r.get("memberId") or "") == str(member_id)), None)
        if row is not None:
            counts = _count_outcomes(row.get("roundOutcomes"))
            break
        if not page.get("hasNextPage") or not rows:
            break
        offset += len(rows)
    if definitive:
        cache.put(event_id, section_number, member_id, counts)
    return counts


def _build_events(uscf_id, sections, games_by_section, crosstable_cache,
                  progress_cb=None, status_cb=None):
    """Walk sections oldest-first, accumulating games/wins/draws, and emit the
    raw, DOB/milestone-independent event timeline (spec 007 Part 3b). Affiliated
    events use the bulk games map; foreign events (absent from it) pull their
    crosstable. Sections with zero cumulative games are skipped (divide-by-zero
    guard)."""
    events = []
    games = wins = draws = 0
    total = len(sections)

    foreign = sum(1 for s in sections
                  if ((s.get("event") or {}).get("id"), s.get("sectionNumber")) not in games_by_section)
    if status_cb and foreign:
        status_cb(f"Including {foreign} FIDE/foreign events — fetching their crosstables…")
    if progress_cb:
        progress_cb(0, total)

    for i, section in enumerate(sections, start=1):
        r = _r_record(section)
        event_id = (section.get("event") or {}).get("id")
        section_number = section.get("sectionNumber")
        counts = games_by_section.get((event_id, section_number))
        if counts is None:
            counts = _standings_counts(event_id, section_number, uscf_id, crosstable_cache)
        games += counts["games"]
        wins += counts["wins"]
        draws += counts["draws"]

        post_rating = r.get("postRating") if r else None
        date = section.get("startDate")
        if post_rating is not None and date is not None and games != 0:
            events.append({
                "date": date,
                "cumulative_games": games,
                "rating": int(post_rating),
                "score_numerator": wins + 0.5 * draws,
                "score_games": games,
            })

        if progress_cb:
            progress_cb(i, total)

    return events


def fetch_history_api(uscf_id, progress_cb=None, status_cb=None, crosstable_cache=None):
    """Fetch a USCF player's raw rating timeline via the JSON API (spec 007
    Part 3b). Returns the cacheable, DOB/milestone-independent timeline. Raises
    ApiUnavailable on any failure so the caller can fall back to HTML scraping.

    `crosstable_cache` (get/put by event+section+member) memoizes foreign-event
    crosstable lookups; defaults to a per-call in-process cache."""
    if crosstable_cache is None:
        crosstable_cache = _MemoCache()
    try:
        uscf_id = str(uscf_id)

        member = _member_detail(uscf_id)
        if status_cb:
            status_cb("US Chess API: fetching sections + games…")

        # The FIDE birth year is player-specific, so it's cacheable on the
        # timeline and applied as a DOB fallback by compute_record.
        try:
            fide_birth_year = get_fide_birth_year(make_session(), uscf_id)
        except Exception:
            fide_birth_year = None

        sections = _list_sections(uscf_id)
        if not sections:
            raise ApiUnavailable("no rated sections for this member", retryable=False)

        games_by_section = _list_games(uscf_id)

        first = sections[0]
        date_of_first = first.get("startDate")
        first_r = _r_record(first)
        initial_rating = first_r.get("postRating") if first_r else None
        if date_of_first is None or initial_rating is None:
            raise ApiUnavailable("first section missing date or postRating",
                                 retryable=False)

        events = _build_events(uscf_id, sections, games_by_section, crosstable_cache,
                               progress_cb=progress_cb, status_cb=status_cb)
        if not events:
            raise ApiUnavailable("no events built", retryable=False)

        return {
            "source": "uscf",
            "player_id": uscf_id,
            "name": member["name"],
            "country": None,
            "first_tournament_date": date_of_first,
            "initial_rating": int(initial_rating),
            "fide_birth_year": fide_birth_year,
            "events": events,
            "api_version": TIMELINE_API_VERSION,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
    except ApiUnavailable as e:
        log.info("USCF API unavailable: %s", e)
        raise
    except Exception as e:
        log.info("USCF API unavailable: unexpected %s: %s", type(e).__name__, e)
        raise ApiUnavailable(f"unexpected: {type(e).__name__}: {e}")


def scrape_player_api(uscf_id, dob=None, milestones=None, progress_cb=None,
                      status_cb=None, use_fide_birth_year=True):
    """Back-compat wrapper: fetch the raw timeline via the API and compute the
    public record. Returns the same dict shape as scraper.core.scrape_player;
    raises ApiUnavailable so the caller can fall back to HTML."""
    from scraper.core import compute_record
    timeline = fetch_history_api(uscf_id, progress_cb=progress_cb, status_cb=status_cb)
    return compute_record(timeline, dob=dob, milestones=milestones,
                          use_fide_birth_year=use_fide_birth_year)
