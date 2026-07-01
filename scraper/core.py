import logging
import re
import sys
import time
from datetime import datetime, timezone

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup, Comment
from dateutil.relativedelta import relativedelta

from config import DEFAULT_RATING_MILESTONES, DEFAULT_FIDE_MILESTONES, USCF_INCLUDE_FOREIGN

log = logging.getLogger(__name__)


def _emit(status_cb, msg):
    """Send a human-readable status line to the server log and, if a callback
    is wired (the web SSE stream), to the UI."""
    log.info(msg)
    if status_cb:
        status_cb(msg)


MSA_BASE = "https://www.uschess.org/msa"
TOURNAMENTS_PER_PAGE = 50
INTER_PAGE_DELAY_SECONDS = 0.35
MSA_FETCH_RETRIES = 2
MSA_RETRY_BACKOFF_SECONDS = 1.5


class PlayerNotFound(Exception):
    pass


class NoClassicalTournaments(Exception):
    pass


class CloudflareChallenge(RuntimeError):
    """USCF MSA served a Cloudflare interstitial instead of the requested page."""


def make_session():
    # curl_cffi impersonates Chrome's TLS/HTTP-2 fingerprint, which is what
    # gets us past Cloudflare's gate on /msa/ at scrape-time volume. See
    # docs/scraping.md.
    return cffi_requests.Session(impersonate="chrome")


def _is_cloudflare_challenge(response):
    if response.status_code in (403, 503):
        body = response.text.lower()
        if "just a moment" in body or "cf-chl" in body or "cloudflare" in body:
            return True
    return False


def _get_msa(session, url, **kwargs):
    """GET an MSA URL with Cloudflare-challenge detection and one retry."""
    kwargs.setdefault("timeout", 10)
    last = None
    for attempt in range(MSA_FETCH_RETRIES + 1):
        response = session.get(url, **kwargs)
        last = response
        if response.status_code == 200 and not _is_cloudflare_challenge(response):
            return response
        if attempt < MSA_FETCH_RETRIES:
            time.sleep(MSA_RETRY_BACKOFF_SECONDS * (attempt + 1))
    if _is_cloudflare_challenge(last):
        raise CloudflareChallenge(
            f"USCF MSA returned a Cloudflare challenge for {url} after "
            f"{MSA_FETCH_RETRIES + 1} attempts."
        )
    last.raise_for_status()
    return last


def extract_date(text):
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group() if match else None


def months_difference(date1, date2):
    d1 = datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.strptime(date2, "%Y-%m-%d")
    delta = relativedelta(d2, d1)
    return delta.years * 12 + delta.months


def calculate_age(date_of_birth, reference_date):
    if not date_of_birth:
        return None
    dob = datetime.strptime(date_of_birth, "%m/%d/%Y")
    ref = datetime.strptime(reference_date, "%Y-%m-%d")
    return ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))


def get_fide_birth_year(session, uscf_id):
    """Return the FIDE-listed birth year for this USCF player, or None.

    Walks: USCF MbrDtlMain -> FIDE ratings.fide.com profile -> B-Year. Any
    network/parse failure returns None so the caller can fall back cleanly.
    """
    try:
        r = session.get(f"{MSA_BASE}/MbrDtlMain.php?{uscf_id}", timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        fide_label = soup.find("td", string=lambda t: t and "FIDE ID" in t)
        if not fide_label:
            return None
        sibling = fide_label.find_next_sibling("td")
        if not sibling:
            return None
        bold = sibling.find("b")
        fide_id = (bold.text if bold else sibling.text).strip()
        if not fide_id.isdigit():
            return None

        r2 = session.get(f"https://ratings.fide.com/profile/{fide_id}", timeout=10)
        soup2 = BeautifulSoup(r2.text, "lxml")
        year_label = soup2.find(string=lambda t: t and "B-Year" in t)
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


def search_players(session, query, limit=20):
    """Search USCF MSA for players by name. Returns a list of result dicts."""
    query = (query or "").strip()
    if not query:
        return []
    url = "https://www.uschess.org/datapage/player-search.php"
    response = _get_msa(session, url, params={"name": query})
    soup = BeautifulSoup(response.text, "lxml")

    results = []
    for anchor in soup.find_all("a", href=re.compile(r"MbrDtlMain\.php\?(\d{8})")):
        href = anchor.get("href", "")
        m = re.search(r"\?(\d{8})", href)
        if not m:
            continue
        uscf_id = m.group(1)
        name = anchor.get_text(strip=True)
        row = anchor.find_parent("tr")
        cells = [c.get_text(strip=True) for c in row.find_all("td")] if row else []
        rating = cells[1] if len(cells) > 1 else ""
        state = cells[7] if len(cells) > 7 else ""
        exp = cells[8] if len(cells) > 8 else ""
        results.append({
            "uscf_id": uscf_id,
            "name": name,
            "rating": rating,
            "state": state,
            "expires": exp,
        })
        if len(results) >= limit:
            break
    return results


def get_tournaments_played(session, uscf_id):
    url = f"{MSA_BASE}/MbrDtlTnmtHst.php?{uscf_id}"
    response = _get_msa(session, url)
    soup = BeautifulSoup(response.text, "lxml")

    if soup.find("b", string=lambda t: t and "Could not retrieve " in t):
        raise PlayerNotFound(f"USCF ID {uscf_id} does not exist.")

    if soup.find("b", string=lambda t: t and "There are no tournament results" in t):
        return None

    b_tag = soup.find("b", string=lambda t: t and "Events for this player since late 1991" in t)
    if b_tag:
        matches = re.findall(r"\d+", b_tag.text)
        return int(matches[1])
    return None


def get_name(session, uscf_id):
    url = f"{MSA_BASE}/MbrDtlMain.php?{uscf_id}"
    response = _get_msa(session, url)
    soup = BeautifulSoup(response.text, "lxml")
    return soup.find("b").text.split(" ", 1)[1]


def get_first_classical_tournament_details(session, uscf_id, total_tournaments_played):
    last_page = (total_tournaments_played - 1) // TOURNAMENTS_PER_PAGE + 1
    url = f"{MSA_BASE}/MbrDtlTnmtHst.php?{uscf_id}.{last_page}"
    response = _get_msa(session, url)
    soup = BeautifulSoup(response.text, "lxml")

    remaining = total_tournaments_played
    while remaining > 0:
        comment = soup.find(string=lambda t: isinstance(t, Comment) and f"Detail: {remaining}" in t)
        tournament_row = comment.find_next_sibling("tr")
        td_tags = tournament_row.find_all("td")
        classical_td_text = td_tags[2].text.strip()

        if "=>" in classical_td_text and "ONL" not in classical_td_text:
            first_tournament_date = extract_date(td_tags[0].text)
            initial_rating = classical_td_text.split("=>")[-1].strip()
            if "P" in initial_rating:
                initial_rating = initial_rating.split(" ")[0].strip()
            return first_tournament_date, int(initial_rating)

        remaining -= 1
        if remaining % TOURNAMENTS_PER_PAGE == 0 and remaining > 0:
            last_page -= 1
            url = f"{MSA_BASE}/MbrDtlTnmtHst.php?{uscf_id}.{last_page}"
            time.sleep(INTER_PAGE_DELAY_SECONDS)
            response = _get_msa(session, url)
            soup = BeautifulSoup(response.text, "lxml")

    return None, None


def games_played_in_tournament(session, uscf_id, tournament_url):
    modified_url = tournament_url.split("-")[0] + ".0"
    new_url = f"{MSA_BASE}/{modified_url}"
    response = _get_msa(session, new_url)
    soup = BeautifulSoup(response.text, "lxml")
    sections = soup.find_all("pre")

    games = wins = draws = losses = 0
    pattern = rf"(.+\|\s*{uscf_id}\s*/\s*R:.*->.*)"

    for section in sections:
        section_text = section.get_text()
        lines = section_text.splitlines()
        match = re.search(pattern, section_text)
        if not match:
            continue

        player_row = match.group(1)
        index = lines.index(player_row)
        game_row = lines[index - 1]

        games_pattern = re.findall(r"\b[WLD]\s+\d+", game_row)
        for game in games_pattern:
            if "W" in game:
                wins += 1
            elif "L" in game:
                losses += 1
            else:
                draws += 1
        games += len(games_pattern)

    return games, wins, draws, losses


def _gather_classical_events(session, uscf_id, total_tournaments_played, progress_cb=None):
    """Walk a player's tournament history and return the raw, chronological
    classical-event timeline — DOB- and milestone-independent (spec 007 Part 3b).

    Each event is a dict: date, cumulative_games, rating (post-event),
    score_numerator (cumulative wins + 0.5*draws), score_games (cumulative games
    toward score). All network I/O happens here; the milestone/age math is the
    pure `compute_record`. Events with zero cumulative games are skipped — that
    is the home of the original `games_played != 0` divide-by-zero guard.
    """
    games_played = 0
    wins = 0
    draws = 0
    events = []

    last_page_index = (total_tournaments_played - 1) // TOURNAMENTS_PER_PAGE + 1
    url = f"{MSA_BASE}/MbrDtlTnmtHst.php?{uscf_id}.{last_page_index}"
    response = _get_msa(session, url)
    soup = BeautifulSoup(response.text, "lxml")

    prev_tournament_url = None
    remaining = total_tournaments_played

    while remaining > 0:
        comment = soup.find(string=lambda t: isinstance(t, Comment) and f"Detail: {remaining}" in t)
        tournament_row = comment.find_next_sibling("tr")
        td_tags = tournament_row.find_all("td")
        classical_td_text = td_tags[2].text.strip()

        if "=>" in classical_td_text and "ONL" not in classical_td_text:
            tournament_date = extract_date(td_tags[0].text)
            post_tournament_rating = classical_td_text.split("=>")[-1].strip()
            if "P" in post_tournament_rating:
                post_tournament_rating = post_tournament_rating.split(" ")[0].strip()

            tournament_url = td_tags[1].find("a")["href"]

            if tournament_url != prev_tournament_url:
                t_games, t_wins, t_draws, _ = games_played_in_tournament(session, uscf_id, tournament_url)
                games_played += t_games
                wins += t_wins
                draws += t_draws

            prev_tournament_url = tournament_url

            if games_played != 0:
                events.append({
                    "date": tournament_date,
                    "cumulative_games": games_played,
                    "rating": int(post_tournament_rating),
                    "score_numerator": wins + 0.5 * draws,
                    "score_games": games_played,
                })

        remaining -= 1
        if progress_cb:
            progress_cb(total_tournaments_played - remaining, total_tournaments_played)
        if remaining % TOURNAMENTS_PER_PAGE == 0 and remaining > 0:
            last_page_index -= 1
            url = f"{MSA_BASE}/MbrDtlTnmtHst.php?{uscf_id}.{last_page_index}"
            time.sleep(INTER_PAGE_DELAY_SECONDS)
            response = _get_msa(session, url)
            soup = BeautifulSoup(response.text, "lxml")

    return events


def _fetch_history_html(session, uscf_id, progress_cb=None):
    """Slow-path HTML scrape → raw timeline (spec 007 Part 3b). No DOB/milestone
    math here; that is `compute_record`. The FIDE birth year is player-specific,
    so it's fetched once and cached on the timeline (used as a DOB fallback)."""
    uscf_id = str(uscf_id)
    fide_birth_year = get_fide_birth_year(session, uscf_id)

    total_tournaments = get_tournaments_played(session, uscf_id)
    if total_tournaments is None:
        raise NoClassicalTournaments(f"USCF ID {uscf_id} has no tournament results.")

    date_of_first, initial_rating = get_first_classical_tournament_details(
        session, uscf_id, total_tournaments
    )
    if date_of_first is None or initial_rating is None:
        raise NoClassicalTournaments(f"USCF ID {uscf_id} has no classical OTB tournaments.")

    name = get_name(session, uscf_id)

    if progress_cb:
        progress_cb(0, total_tournaments)

    events = _gather_classical_events(
        session, uscf_id, total_tournaments, progress_cb=progress_cb
    )

    return {
        "source": "uscf",
        "player_id": uscf_id,
        "name": name,
        "country": None,
        "first_tournament_date": date_of_first,
        "initial_rating": int(initial_rating),
        "fide_birth_year": fide_birth_year,
        "events": events,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_history(session, uscf_id, progress_cb=None, status_cb=None, fallback_cb=None,
                  crosstable_cache=None):
    """Fetch a USCF player's raw rating timeline (spec 007 Part 3b). Tries the
    fast JSON API first, then falls back to HTML scraping. Returns the cacheable,
    DOB/milestone-independent timeline; call `compute_record` to derive the
    per-user milestone view.

    Uses the foreign-inclusive API client unless `config.USCF_INCLUDE_FOREIGN` is
    off, in which case the pre-2026-07 `uscf_api_legacy` client is used (the same
    `ApiUnavailable` class either way, so the fallback below still catches it).
    `crosstable_cache` is passed through only to the foreign-inclusive client.

    `fallback_cb()` (no args) fires once if the API is unavailable and we drop to
    the slow HTML scraper — the web UI uses it to surface the "slower / no data
    after Nov 2025" notice and a Cancel button."""
    _emit(status_cb, "Trying US Chess API…")
    try:
        from scraper.uscf_api import ApiUnavailable
        if USCF_INCLUDE_FOREIGN:
            from scraper.uscf_api import fetch_history_api
            timeline = fetch_history_api(
                uscf_id, progress_cb=progress_cb, status_cb=status_cb,
                crosstable_cache=crosstable_cache,
            )
        else:
            from scraper.uscf_api_legacy import fetch_history_api
            timeline = fetch_history_api(
                uscf_id, progress_cb=progress_cb, status_cb=status_cb,
            )
        _emit(status_cb, "US Chess API succeeded")
        return timeline
    except ApiUnavailable as e:
        _emit(status_cb, f"US Chess API unavailable ({e}) — falling back to HTML scraping")
        if fallback_cb:
            fallback_cb()

    _emit(status_cb, "Scraping US Chess tournament pages (this is the slow path)…")
    return _fetch_history_html(session, uscf_id, progress_cb=progress_cb)


def compute_record(timeline, dob=None, milestones=None, use_fide_birth_year=True):
    """Pure function (spec 007 Part 3b): turn a raw cached timeline + this user's
    DOB and milestone ladder into the canonical public record dict (the shape in
    CLAUDE.md). No network. Works for any source.

    DOB precedence: a user-supplied `dob` wins; else, when `use_fide_birth_year`
    is True, the timeline's cached FIDE birth year synthesizes `01/01/<year>`
    (dob_source="fide") so ages are approximate (to the calendar year); else age
    fields are None (dob_source="none"). `use_fide_birth_year=False` opts a player
    out of that fallback, so a player with no user-supplied DOB gets no age stats
    at all. `score_pct` is `score_numerator / score_games`, guarded against the
    missing/zero denominator (the relocated divide-by-zero guard) so it degrades
    to None per-cell when score data is unavailable.
    """
    source = timeline.get("source", "uscf")
    default = DEFAULT_FIDE_MILESTONES if source == "fide" else DEFAULT_RATING_MILESTONES
    milestones = list(milestones) if milestones else list(default)

    fide_birth_year = timeline.get("fide_birth_year")
    if dob:
        dob_source = "user"
    elif fide_birth_year and use_fide_birth_year:
        dob = f"01/01/{fide_birth_year}"
        dob_source = "fide"
    else:
        dob_source = "none"

    first_date = timeline["first_tournament_date"]
    age_at_first = calculate_age(dob, first_date) if dob else None

    n = len(milestones)
    months_reached = [None] * n
    games_reached = [None] * n
    ages_reached = [None] * n
    scores_reached = [None] * n
    dates_reached = [None] * n

    start_index = 0
    for event in timeline.get("events", []):
        if start_index >= n:
            break
        rating = event.get("rating")
        if rating is None:
            continue
        score_num = event.get("score_numerator")
        score_games = event.get("score_games")
        if score_games and score_num is not None:
            score_pct = score_num / score_games
        else:
            score_pct = None
        while start_index < n and int(rating) >= milestones[start_index]:
            months_reached[start_index] = months_difference(first_date, event["date"])
            games_reached[start_index] = event.get("cumulative_games")
            ages_reached[start_index] = calculate_age(dob, event["date"]) if dob else None
            scores_reached[start_index] = score_pct
            dates_reached[start_index] = event["date"]
            start_index += 1

    milestone_data = {}
    for i, threshold in enumerate(milestones):
        milestone_data[str(threshold)] = {
            "months": months_reached[i],
            "games": games_reached[i],
            "age": ages_reached[i],
            "score_pct": scores_reached[i],
            # First event whose rating reached this threshold ("YYYY-MM-DD"),
            # so the charts can show WHEN it was achieved (on hover).
            "date": dates_reached[i],
        }

    player_id = str(timeline["player_id"])
    return {
        "source": source,
        "player_id": player_id,
        "uscf_id": player_id if source == "uscf" else None,
        "fide_id": player_id if source == "fide" else None,
        "name": timeline["name"],
        "country": timeline.get("country"),
        "dob": dob,
        "dob_source": dob_source,
        "fide_birth_year": fide_birth_year,
        "first_tournament_date": first_date,
        "initial_rating": int(timeline["initial_rating"]),
        "age_at_first_tournament": age_at_first,
        "milestones": milestone_data,
        "milestones_config": milestones,
        "rating_type": "classical",
        "scraped_at": timeline["scraped_at"],
    }


def scrape_player(session, uscf_id, dob=None, milestones=None, progress_cb=None,
                  status_cb=None, use_fide_birth_year=True):
    """Back-compat wrapper: fetch the raw timeline (API-first, HTML fallback) and
    compute the public record for this DOB + milestone ladder. The fetch/compute
    split (spec 007) lets the timeline be cached and re-derived per user."""
    timeline = fetch_history(
        session, uscf_id, progress_cb=progress_cb, status_cb=status_cb,
    )
    return compute_record(timeline, dob=dob, milestones=milestones,
                          use_fide_birth_year=use_fide_birth_year)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scraper.core <USCF_ID> [DOB MM/DD/YYYY]")
        sys.exit(1)
    session = make_session()
    dob = sys.argv[2] if len(sys.argv) >= 3 else None
    result = scrape_player(session, sys.argv[1], dob)
    import json
    print(json.dumps(result, indent=2))
