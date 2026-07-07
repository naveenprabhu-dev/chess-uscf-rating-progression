"""FIDE official archive rating lists — the Jan 2002 – Apr 2003 backfill.

FIDE's per-player chart endpoint (`a_chart_data.phtml`) is floored at Apr 2003,
and OlimpBase's per-player cards end at the Oct 2001 list, which used to leave
Jan 2002 – Jan 2003 as an accepted gap. But FIDE itself publishes the complete
historical rating lists as downloadable zips (`ratings.fide.com/download/`),
including the five quarterly lists inside that gap — verified against
2700chess.com's graphs (2026-07): the archive values match exactly.

The Apr 2003 list is fetched too, for a different reason: the chart's floor row
sometimes carries a rating that never appeared on a published list (a later
FIDE recalculation) — e.g. Carlsen's chart says 2356 for Apr 2003 while the
published list (and 2700chess) say 2315. The archive row, when present,
overrides the chart's floor-row value.

Each list is one ~1 MB zip holding one fixed-width TXT of ~30–45k players.
Lists are immutable, so a parsed list is cached FOREVER (per list, not per
player — the first veteran scrape pays the ~6 downloads, everyone after is
free) via an injected adapter with `.has_list(date)` / `.get_player(date,
fide_id)` / `.put_list(date, players)` (the `fide_archive_lists` table; same
inject-an-adapter pattern as the crosstable and OlimpBase caches, keeping this
module framework-agnostic). Transient failures (network, non-200, unparseable
zip) are never cached — that list is simply skipped this scrape and retried on
the next one. Everything here is strictly best-effort: it must never fail a
FIDE scrape.

Framework-agnostic: no Flask imports here.
"""
import io
import re
import time
import zipfile

FIDE_DOWNLOAD_BASE = "https://ratings.fide.com/download"
ARCHIVE_TIMEOUT_SECONDS = 30
ARCHIVE_INTER_REQUEST_DELAY_SECONDS = 1.0

# The six official lists covering the chart endpoint's blind spot. The first
# five fill the Jan 2002 – Jan 2003 gap; the Apr 2003 one corrects the chart's
# floor row. FIDE was on a quarterly cadence then, so this is the complete set.
ARCHIVE_LISTS = [
    ("2002-01-01", "jan02frl.zip"),
    ("2002-04-01", "apr02frl.zip"),
    ("2002-07-01", "jul02frl.zip"),
    ("2002-10-01", "oct02frl.zip"),
    ("2003-01-01", "jan03frl.zip"),
    ("2003-04-01", "apr03frl.zip"),
]

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Column layouts drift between lists (ID padding, title column, 2- vs 4-digit
# birth years), so rows are parsed by token, not offset — same approach as
# olimpbase.py: the "3-letter federation + 3/4-digit rating" pair anchors the
# row (last match, in case a name ever contains an uppercase triple); the games
# column is the next all-digit token after it (birthdays contain dots).
_FED_RTNG_RE = re.compile(r"\b([A-Z]{3})\s+(\d{3,4})\b")


def fetch_archive_list(session, zip_name):
    """Download and parse one official list zip.

    Returns {fide_id: {"rating": int, "games": int}} for every player on the
    list, or None on ANY failure (network after one polite retry, non-200,
    bad zip, or a body that parses to zero rows). None is always transient —
    callers must not cache it.
    """
    url = f"{FIDE_DOWNLOAD_BASE}/{zip_name}"
    response = None
    for attempt in range(2):
        try:
            response = session.get(
                url, headers={"User-Agent": _UA}, timeout=ARCHIVE_TIMEOUT_SECONDS
            )
            break
        except Exception:
            # curl_cffi and requests raise different exception trees; one
            # polite retry either way (mirrors olimpbase.py).
            if attempt == 0:
                time.sleep(ARCHIVE_INTER_REQUEST_DELAY_SECONDS)
    if response is None or response.status_code != 200:
        return None

    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        members = [n for n in archive.namelist() if n.lower().endswith(".txt")]
        if not members:
            return None
        # latin-1 never raises and these pre-Unicode-era lists are 8-bit.
        text = archive.read(members[0]).decode("latin-1")
    except Exception:
        return None

    players = {}
    for line in text.splitlines():
        tokens = line.split()
        if not tokens or not tokens[0].isdigit():
            continue  # header / blank / malformed
        matches = list(_FED_RTNG_RE.finditer(line))
        if not matches:
            continue
        anchor = matches[-1]
        after = line[anchor.end():].split()
        games = int(after[0]) if after and after[0].isdigit() else 0
        players[tokens[0]] = {"rating": int(anchor.group(2)), "games": max(games, 0)}
    if not players:
        return None  # a 200 that parses to nothing is an error page, not a list
    return players


def lookup_archive_ratings(session, fide_id, archive_cache=None, status_cb=None):
    """Return this player's {date: {"rating", "games"}} across ARCHIVE_LISTS.

    Cached lists cost zero requests. An uncached list is downloaded, parsed,
    and — only on success — stored whole via `archive_cache.put_list` so no
    list is ever fetched twice. A failed list is skipped (absent from the
    result) and left uncached for a later scrape to retry. Players simply not
    on a list (unrated/inactive that period) are absent from the result too —
    e.g. Aronian missed the Jul 2002 list, matching 2700chess.
    """
    fide_id = str(fide_id)
    found = {}
    for list_date, zip_name in ARCHIVE_LISTS:
        if archive_cache is not None and archive_cache.has_list(list_date):
            row = archive_cache.get_player(list_date, fide_id)
        else:
            if status_cb:
                status_cb(f"Fetching FIDE {list_date[:7]} archive list…")
            time.sleep(ARCHIVE_INTER_REQUEST_DELAY_SECONDS)  # politeness gap
            players = fetch_archive_list(session, zip_name)
            if players is None:
                continue
            if archive_cache is not None:
                archive_cache.put_list(list_date, players)
            row = players.get(fide_id)
        if row is not None:
            found[list_date] = row
    return found
