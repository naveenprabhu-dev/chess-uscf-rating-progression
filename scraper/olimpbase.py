"""OlimpBase backfill for pre-2003 FIDE rating history.

OlimpBase (olimpbase.org) is a volunteer chess-statistics archive maintained by
Wojciech Bartelski. Among much else it republishes the historical FIDE rating
lists (through Oct 2001) with a fixed-width per-player "rating card" page for
every listed player. FIDE's own `a_chart_data.phtml` has a hard server-side
floor of Apr 2003 for *every* player, so these cards are the only per-player
source for a veteran's earlier climb (see specs/005-fide-source/plan.md Phase 6
and research.md).

Coverage and floors:

- Lists on OlimpBase in our window: Jan+Jul 1990–1999; Jan/Jul/Oct 2000;
  Jan/Apr/Jul/Oct 2001. Nothing after Oct 2001 (the Jan 2002 – Jan 2003 gap has
  no per-player source anywhere and is accepted by design).
- Cards go back to the 1970s, but rows are **floored at Jan 1990** here: FIDE
  IDs exist from Jan 1990, and OlimpBase itself flags pre-1990 lists as
  manually compiled (dupes, misspellings). Owner requirement: keep 1990-01-01+.

Politeness: the site is small, static, and frozen (last lists ~2001), so we
fetch at most ONE ~10 KB page per player, sequentially, after a 1 s gap
(enforced by the caller), and the result — including "no card found" — is
cached permanently (`olimpbase_cache` table); pre-2002 data is immutable, so a
player is never fetched twice. robots.txt is empty (no restrictions).

Framework-agnostic: no Flask imports here.
"""
import html as _html
import re
import time
from datetime import datetime
from urllib.parse import quote

OLIMPBASE_BASE = "https://www.olimpbase.org"
OLIMPBASE_TIMEOUT_SECONDS = 20
OLIMPBASE_RETRY_DELAY_SECONDS = 1.0
OLIMPBASE_MIN_DATE = "1990-01-01"   # FIDE IDs exist from Jan 1990 (owner req.)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# One data row of the card's <pre> block: a link to the source list whose text
# is the list date ("Oct 2001"), then the fixed-width columns.
_ROW_RE = re.compile(
    r'<a [^>]*href="[^"]*Elo\d+e?\.html"[^>]*>\s*([A-Z][a-z]{2} \d{4})\s*</a>(.*)$'
)
# Right-hand anchor inside a cleaned row: 3-letter federation + 3/4-digit
# rating. Everything before it is pos/Player_ID/Name/Title; everything after
# is +/-, Gms, Birthday. Take the LAST match in case a name ever contains an
# uppercase triple.
_FED_RTNG_RE = re.compile(r"\b([A-Z]{3})\s+(\d{3,4})\b")
_BDAY_RE = re.compile(r"\d{4}\.\d{2}\.\d{2}")
_TAG_RE = re.compile(r"<[^>]+>")
# Header-table fallback identity check: "Most recent ID" links to the player's
# live FIDE profile.
_PROFILE_ID_RE = re.compile(r"ratings\.fide\.com/profile/(\d+)")


def card_url(name):
    """Build the per-player card URL from the FIDE 'Last, First' name string.

    Pattern (verified live 2026-07-01):
        https://www.olimpbase.org/Elo/player/{L}/{urlencoded name}.html
    where {L} is the uppercase first letter of the surname and the name is the
    exact string FIDE's chart JSON returns (comma kept literal, spaces %20).
    Returns None if a URL can't be formed (empty/non-alphabetic name).
    """
    name = (name or "").strip()
    if not name or not name[0].isalpha():
        return None
    letter = name[0].upper()
    return f"{OLIMPBASE_BASE}/Elo/player/{quote(letter)}/{quote(name, safe=',')}.html"


def _clean_row(fragment):
    """Strip tags and entities from the part of a row after the date link.

    Rows are littered with `&nbsp;` and `<span style="color: red;">…</span>`
    wrappers around negative +/- values; the span *contents* keep their padding
    so the fixed-width feel survives, but we parse by token, not offset.
    """
    return _TAG_RE.sub("", _html.unescape(fragment)).replace("\xa0", " ")


def _parse_card(html_text, fide_id):
    """Parse a card page into (rows, identity_confirmed).

    Each returned row: {"date": "YYYY-MM-01", "rating": int,
    "period_games": int, "player_id": str|None} — newest-first as on the page.
    Identity: the card is looked up by NAME, so we require proof it belongs to
    this FIDE ID — either a row's Player_ID column equals the ID (rows carry it
    from ~1990 on) or the header's "Most recent ID" profile link matches.
    """
    fide_id = str(fide_id)
    identity = False

    m = _PROFILE_ID_RE.search(html_text)
    if m and m.group(1) == fide_id:
        identity = True

    rows = []
    for line in html_text.splitlines():
        row_m = _ROW_RE.search(line)
        if row_m is None:
            continue
        date_text, rest = row_m.group(1), row_m.group(2)
        cleaned = _clean_row(rest)
        if "no data available" in cleaned:
            continue  # e.g. Kasparov's struck 1994 lists
        fed_matches = list(_FED_RTNG_RE.finditer(cleaned))
        if not fed_matches:
            continue  # malformed row — skip, don't fail the card
        anchor = fed_matches[-1]
        try:
            date = datetime.strptime(date_text, "%b %Y").strftime("%Y-%m-01")
            rating = int(anchor.group(2))
        except ValueError:
            continue

        # Left of the anchor: pos, then (from ~1990) the Player_ID column.
        # Pre-1990 rows leave it blank or 'x'; early-1990 lists used a
        # different numbering scheme, so per-row IDs are only used as
        # identity evidence, never as a per-row filter.
        head_tokens = cleaned[: anchor.start()].split()
        player_id = None
        if len(head_tokens) > 1 and head_tokens[1].isdigit():
            player_id = head_tokens[1]
            if player_id == fide_id:
                identity = True

        # Right of the anchor: [+/-] [Gms] [Birthday] [Sex] [Flag], any of
        # which can be blank. Drop the birthday, then disambiguate:
        #   2 tokens -> (+/-, Gms); 1 signed token -> +/- only (Gms blank);
        #   1 unsigned "0" -> a zero +/- (blank Gms only happens with +/- 0);
        #   1 unsigned non-zero -> Gms with a blank +/- (oldest rows).
        tail_tokens = _BDAY_RE.sub("", cleaned[anchor.end():]).split()
        games = 0
        try:
            if len(tail_tokens) >= 2:
                games = int(tail_tokens[1])
            elif len(tail_tokens) == 1:
                tok = tail_tokens[0]
                if tok.isdigit() and tok != "0":
                    games = int(tok)
        except ValueError:
            games = 0

        rows.append({
            "date": date,
            "rating": rating,
            "period_games": max(games, 0),
            "player_id": player_id,
        })

    return rows, identity


def fetch_olimpbase_events(session, fide_id, name, status_cb=None):
    """Fetch and parse a player's OlimpBase rating card. Strictly best-effort.

    `name` is the "Last, First" string from FIDE's chart payload (the card URL
    is name-keyed). Returns `(events, definitive)`:

    - `events` is a list of timeline-shaped events — `{"date": "YYYY-MM-01",
      "rating": int, "period_games": int}` — filtered to dates >= 1990-01-01
      and sorted oldest-first (may be empty: card found and identity confirmed,
      but no rows in the window), or None when there is no usable backfill.
    - `definitive` says whether that answer is PERMANENT and safe to cache
      forever: True for a parsed card and for confirmed negatives (HTTP 404 =
      no card; identity guard failed on a well-formed page — homonym /
      spelling-drift protection). False for transient failures (network error
      after one polite retry, non-200/404 status, unparseable body) — callers
      MUST NOT cache those, or an olimpbase.org blip would permanently mask a
      veteran's pre-2003 history behind the no-TTL negative cache.

    Callers treat `events=None` as "no backfill", never as an error: this must
    never break a FIDE scrape.
    """
    url = card_url(name)
    if url is None:
        return None, True  # unbuildable name never changes — definitive
    if status_cb:
        status_cb("Checking OlimpBase for pre-2003 history…")

    response = None
    for attempt in range(2):
        try:
            response = session.get(
                url,
                headers={"User-Agent": _UA},
                timeout=OLIMPBASE_TIMEOUT_SECONDS,
            )
            break
        except Exception:
            # curl_cffi and requests raise different exception trees; the
            # backfill is best-effort either way. One polite retry.
            if attempt == 0:
                time.sleep(OLIMPBASE_RETRY_DELAY_SECONDS)
    if response is None:
        return None, False               # network trouble — try again someday
    if response.status_code == 404:
        return None, True                # no card for this name — permanent
    if response.status_code != 200:
        return None, False               # 5xx/403/… — transient, don't cache

    try:
        rows, identity = _parse_card(response.text, fide_id)
    except Exception:
        # A 200 that crashes the parser is likelier a parser gap (or an error
        # page served as 200) than a real card shape — leave uncached so a
        # later fix / healthy response can self-heal on the next re-scrape.
        return None, False
    if not identity:
        return None, True                # provably not this player's card

    events = [
        {"date": r["date"], "rating": r["rating"], "period_games": r["period_games"]}
        for r in rows
        if r["date"] >= OLIMPBASE_MIN_DATE
    ]
    events.sort(key=lambda e: e["date"])
    return events, True
