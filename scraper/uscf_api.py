"""USCF JSON API client — fast path for `scrape_player`.

See specs/006-uscf-api-source/plan.md. Raises ApiUnavailable on any failure
mode so the caller falls back to the HTML scraper in scraper.core.
"""
import logging
from datetime import datetime, timezone

import requests

from scraper.core import get_fide_birth_year, make_session

log = logging.getLogger(__name__)

USCF_API_BASE = "https://ratings-api.uschess.org"
API_PAGE_SIZE = 200
# 15s, not the spec's 5s: high-volume players (Caruana) hit upstream 499s
# below 10s; verified all pages clear inside ~1s under normal load.
API_TIMEOUT_SECONDS = 15


class ApiUnavailable(Exception):
    """Signals scrape_player to fall back to the HTML scraper."""


def _get(path, **params):
    """GET BASE+path. Returns parsed JSON on 200, None on 404. Single retry on
    ConnectionError/Timeout/5xx/499. Raises ApiUnavailable on any other failure.
    """
    url = USCF_API_BASE + path
    last = None
    for attempt in range(2):
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
        # 499 = upstream edge timeout; treat as retryable.
        if response.status_code == 499 or 500 <= response.status_code < 600:
            last = f"{response.status_code} from {url}"
            continue
        raise ApiUnavailable(f"{response.status_code} from {url}")
    raise ApiUnavailable(f"after retry: {last}")


def _member_detail(uscf_id):
    data = _get(f"/api/v1/members/{uscf_id}")
    if data is None:
        raise ApiUnavailable("not in api")
    first = (data.get("firstName") or "").strip()
    last = (data.get("lastName") or "").strip()
    if not first and not last:
        raise ApiUnavailable("member missing name")
    return {"name": f"{first} {last}".title()}


def _paginate(path, **params):
    """Walk a paginated endpoint, yielding items. Increments offset by the
    actual page size returned — the API caps at 100 even when Size=200.
    """
    offset = 0
    while True:
        page = _get(path, RatingSource="R", Size=API_PAGE_SIZE, Offset=offset, **params)
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


def _list_sections(uscf_id):
    items = [s for s in _paginate(f"/api/v1/members/{uscf_id}/sections")
             if s.get("ratingSystem") == "R"]
    items.sort(key=lambda s: s.get("startDate") or "")
    return items


def _list_games(uscf_id):
    by_section = {}
    total = 0
    for game in _paginate(f"/api/v1/members/{uscf_id}/games"):
        if game.get("ratingSystem") != "R":
            continue
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
        raise ApiUnavailable("games index empty")
    return by_section


def _build_events(sections, games_by_section, progress_cb=None):
    """Walk sections oldest-first, accumulating games/wins/draws, and emit the
    raw, DOB/milestone-independent event timeline (spec 007 Part 3b). Sections
    with zero cumulative games are skipped — the relocated divide-by-zero guard."""
    events = []
    games = wins = draws = 0
    total = len(sections)

    if progress_cb:
        progress_cb(0, total)

    for i, section in enumerate(sections, start=1):
        event_id = (section.get("event") or {}).get("id")
        section_number = section.get("sectionNumber")
        counts = games_by_section.get((event_id, section_number), {"wins": 0, "draws": 0, "losses": 0, "games": 0})
        games += counts["games"]
        wins += counts["wins"]
        draws += counts["draws"]

        rating_records = section.get("ratingRecords") or []
        post_rating = rating_records[0].get("postRating") if rating_records else None
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


def fetch_history_api(uscf_id, progress_cb=None, status_cb=None):
    """Fetch a USCF player's raw rating timeline via the JSON API (spec 007
    Part 3b). Returns the cacheable, DOB/milestone-independent timeline. Raises
    ApiUnavailable on any failure so the caller can fall back to HTML scraping."""
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
            raise ApiUnavailable("no sections")

        games_by_section = _list_games(uscf_id)

        first = sections[0]
        date_of_first = first.get("startDate")
        rating_records = first.get("ratingRecords") or []
        initial_rating = rating_records[0].get("postRating") if rating_records else None
        if date_of_first is None or initial_rating is None:
            raise ApiUnavailable("first section missing date or postRating")

        events = _build_events(sections, games_by_section, progress_cb=progress_cb)

        return {
            "source": "uscf",
            "player_id": uscf_id,
            "name": member["name"],
            "country": None,
            "first_tournament_date": date_of_first,
            "initial_rating": int(initial_rating),
            "fide_birth_year": fide_birth_year,
            "events": events,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
    except ApiUnavailable as e:
        log.info("USCF API unavailable: %s", e)
        raise
    except Exception as e:
        log.info("USCF API unavailable: unexpected %s: %s", type(e).__name__, e)
        raise ApiUnavailable(f"unexpected: {type(e).__name__}: {e}")


def scrape_player_api(uscf_id, dob=None, milestones=None, progress_cb=None, status_cb=None):
    """Back-compat wrapper: fetch the raw timeline via the API and compute the
    public record. Returns the same dict shape as scraper.core.scrape_player;
    raises ApiUnavailable so the caller can fall back to HTML."""
    from scraper.core import compute_record
    timeline = fetch_history_api(uscf_id, progress_cb=progress_cb, status_cb=status_cb)
    return compute_record(timeline, dob=dob, milestones=milestones)
