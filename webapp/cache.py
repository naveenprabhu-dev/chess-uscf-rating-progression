import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

# Spec 007 Part 5a. The cache is now a RAW, DOB/milestone-independent rating
# timeline (Part 3b) — the per-user milestone/age view is recomputed per request
# by scraper.compute_record. `users`/`saved_analyses` back the per-owner library.
SCRAPE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_cache (
    source TEXT NOT NULL,
    player_id TEXT NOT NULL,
    name TEXT NOT NULL,        -- denormalized for cheap listing
    scraped_at TEXT NOT NULL,
    timeline TEXT NOT NULL,    -- the raw timeline JSON (NO dob, NO milestones_config)
    PRIMARY KEY (source, player_id)
)
"""

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid TEXT PRIMARY KEY,
    email TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT
)
"""

SAVED_ANALYSES_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_analyses (
    owner_id TEXT NOT NULL,
    source TEXT NOT NULL,
    player_id TEXT NOT NULL,
    dob TEXT,                  -- this owner's DOB for the player ("MM/DD/YYYY" or NULL)
    milestones_snapshot TEXT,  -- JSON ladder this owner used (NULL = source default)
    saved_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, source, player_id),
    FOREIGN KEY (source, player_id) REFERENCES scrape_cache(source, player_id)
)
"""

# One member's W/D/L/games in a foreign (no-affiliate) event, harvested from the
# event crosstable during a USCF scrape. Shared/permanent: a finalized event's
# result never changes, so this is fetched once ever — cheap re-scrapes and
# cheap for other players who shared the event.
CROSSTABLE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS crosstable_cache (
    event_id TEXT NOT NULL,
    section_number INTEGER NOT NULL,
    member_id TEXT NOT NULL,
    wins INTEGER NOT NULL,
    draws INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    games INTEGER NOT NULL,
    PRIMARY KEY (event_id, section_number, member_id)
)
"""

# One FIDE player's OlimpBase backfill result (pre-2003 rating rows, floored at
# Jan 1990 — see scraper/olimpbase.py). PERMANENT, no TTL anywhere: OlimpBase
# is a frozen archive (lists end Oct 2001) and pre-2002 data is immutable.
# Negative lookups (found=0, no card / identity guard failed) are cached too,
# so a player without a card never triggers repeat OlimpBase requests.
OLIMPBASE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS olimpbase_cache (
    fide_id TEXT PRIMARY KEY,
    found INTEGER NOT NULL,
    events TEXT NOT NULL,      -- JSON [{date, rating, period_games}, ...]
    fetched_at TEXT NOT NULL
)
"""

# One row per player per official FIDE archive rating list (Jan 2002 – Apr
# 2003 — see scraper/fide_archive.py). Cached per LIST: the first veteran
# FIDE scrape downloads a list once (~45k rows, ~1 MB zip) and stores it
# whole; every later lookup is a local read. PERMANENT — the lists are
# immutable historical publications.
FIDE_ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fide_archive_lists (
    list_date TEXT NOT NULL,   -- the list's period date, e.g. "2002-01-01"
    fide_id TEXT NOT NULL,
    rating INTEGER NOT NULL,
    games INTEGER NOT NULL,
    PRIMARY KEY (list_date, fide_id)
)
"""


def _db_path():
    return Path(current_app.instance_path) / "cache.sqlite3"


def get_db():
    if "db" not in g:
        Path(current_app.instance_path).mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(_db_path())
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _has_table(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def init_db(app):
    """Create the schema. The legacy `players` table stored a *computed* record
    that can't be losslessly converted to a raw timeline (spec 007 Part 5a), so
    if it's present we drop it and recreate the cache fresh, stashing a one-time
    reset flag for base.html to surface. The DB is gitignored in instance/, so
    the reset is safe."""
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    reset = False
    with sqlite3.connect(Path(app.instance_path) / "cache.sqlite3") as conn:
        if _has_table(conn, "players"):
            conn.execute("DROP TABLE players")
            reset = True
        conn.executescript(SCRAPE_CACHE_SCHEMA)
        conn.executescript(USERS_SCHEMA)
        conn.executescript(SAVED_ANALYSES_SCHEMA)
        conn.executescript(CROSSTABLE_CACHE_SCHEMA)
        conn.executescript(OLIMPBASE_CACHE_SCHEMA)
        conn.executescript(FIDE_ARCHIVE_SCHEMA)
    app.config["CACHE_WAS_RESET"] = reset


# ── Raw scrape-cache (timeline) helpers ─────────────────────────────────────────
# These store/read the DOB/milestone-independent timeline. They are backend
# infrastructure only — never rendered directly to users (spec 007 Part 3b).

def get_timeline(source, player_id):
    db = get_db()
    row = db.execute(
        "SELECT timeline FROM scrape_cache WHERE source = ? AND player_id = ?",
        (str(source), str(player_id)),
    ).fetchone()
    return json.loads(row["timeline"]) if row else None


def save_timeline(timeline):
    db = get_db()
    db.execute(
        "INSERT INTO scrape_cache (source, player_id, name, scraped_at, timeline) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(source, player_id) DO UPDATE SET name=excluded.name, "
        "scraped_at=excluded.scraped_at, timeline=excluded.timeline",
        (
            timeline["source"],
            str(timeline["player_id"]),
            timeline["name"],
            timeline["scraped_at"],
            json.dumps(timeline),
        ),
    )
    db.commit()


def delete_timeline(source, player_id):
    db = get_db()
    db.execute(
        "DELETE FROM scrape_cache WHERE source = ? AND player_id = ?",
        (str(source), str(player_id)),
    )
    db.commit()


def is_timeline_stale(timeline, max_age_days):
    """True if a cached timeline is older than max_age_days and should be
    re-scraped (USCF/FIDE publish new tournaments, so old rows go stale).

    max_age_days of None or <= 0 disables expiry (never stale). A missing or
    unparseable `scraped_at` is treated as stale, so we re-scrape rather than
    serve data of unknown age."""
    if not max_age_days or max_age_days <= 0:
        return False
    ts = (timeline or {}).get("scraped_at")
    if not ts:
        return True
    try:
        scraped = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return True
    if scraped.tzinfo is None:
        scraped = scraped.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - scraped
    return age.total_seconds() > max_age_days * 86400


# ── Foreign-event crosstable cache ──────────────────────────────────────────────
# Backs `SqliteCrosstableCache`, injected into the USCF scraper so a foreign
# event's per-member game counts are fetched once, ever.

def get_crosstable_counts(event_id, section_number, member_id):
    db = get_db()
    row = db.execute(
        "SELECT wins, draws, losses, games FROM crosstable_cache "
        "WHERE event_id = ? AND section_number = ? AND member_id = ?",
        (str(event_id), int(section_number or 0), str(member_id)),
    ).fetchone()
    if row is None:
        return None
    return {"wins": row["wins"], "draws": row["draws"],
            "losses": row["losses"], "games": row["games"]}


def save_crosstable_counts(event_id, section_number, member_id, counts):
    db = get_db()
    db.execute(
        "INSERT INTO crosstable_cache "
        "(event_id, section_number, member_id, wins, draws, losses, games) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(event_id, section_number, member_id) DO UPDATE SET "
        "wins=excluded.wins, draws=excluded.draws, losses=excluded.losses, games=excluded.games",
        (str(event_id), int(section_number or 0), str(member_id),
         counts["wins"], counts["draws"], counts["losses"], counts["games"]),
    )
    db.commit()


class SqliteCrosstableCache:
    """get/put adapter the scraper calls (it stays Flask-agnostic — it only sees
    these two methods). Must be used inside an app context (the scrape worker is)."""

    def get(self, event_id, section_number, member_id):
        return get_crosstable_counts(event_id, section_number, member_id)

    def put(self, event_id, section_number, member_id, counts):
        save_crosstable_counts(event_id, section_number, member_id, counts)


# ── OlimpBase pre-2003 backfill cache ───────────────────────────────────────────
# Backs `SqliteOlimpbaseCache`, injected into `fetch_fide_history` so a FIDE
# player's OlimpBase card (or its confirmed absence) is fetched once, ever.

def get_olimpbase_events(fide_id):
    db = get_db()
    row = db.execute(
        "SELECT found, events FROM olimpbase_cache WHERE fide_id = ?",
        (str(fide_id),),
    ).fetchone()
    if row is None:
        return None
    return {"found": bool(row["found"]), "events": json.loads(row["events"])}


def save_olimpbase_events(fide_id, payload):
    db = get_db()
    db.execute(
        "INSERT INTO olimpbase_cache (fide_id, found, events, fetched_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(fide_id) DO UPDATE SET found=excluded.found, "
        "events=excluded.events, fetched_at=excluded.fetched_at",
        (
            str(fide_id),
            1 if payload.get("found") else 0,
            json.dumps(payload.get("events") or []),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    db.commit()


class SqliteOlimpbaseCache:
    """get/put adapter for the FIDE scraper's OlimpBase backfill (same pattern
    as SqliteCrosstableCache: the scraper stays Flask-agnostic and only sees
    these two methods). Must be used inside an app context (the scrape worker
    is). Entries are permanent — pre-2002 data is immutable, so no TTL."""

    def get(self, fide_id):
        return get_olimpbase_events(fide_id)

    def put(self, fide_id, payload):
        save_olimpbase_events(fide_id, payload)


# ── FIDE archive rating-list cache (Jan 2002 – Apr 2003) ────────────────────────
# Backs `SqliteFideArchiveCache`, injected into `fetch_fide_history` so each
# official archive list is downloaded once, ever — then every veteran's gap
# backfill is a local lookup.

class SqliteFideArchiveCache:
    """Per-list adapter for scraper/fide_archive.py (scraper stays Flask-
    agnostic — it only sees these three methods). Must be used inside an app
    context (the scrape worker is). Lists are immutable publications, so
    entries are permanent — no TTL."""

    def has_list(self, list_date):
        db = get_db()
        return db.execute(
            "SELECT 1 FROM fide_archive_lists WHERE list_date = ? LIMIT 1",
            (str(list_date),),
        ).fetchone() is not None

    def get_player(self, list_date, fide_id):
        db = get_db()
        row = db.execute(
            "SELECT rating, games FROM fide_archive_lists "
            "WHERE list_date = ? AND fide_id = ?",
            (str(list_date), str(fide_id)),
        ).fetchone()
        if row is None:
            return None
        return {"rating": row["rating"], "games": row["games"]}

    def put_list(self, list_date, players):
        db = get_db()
        db.executemany(
            "INSERT OR REPLACE INTO fide_archive_lists "
            "(list_date, fide_id, rating, games) VALUES (?, ?, ?, ?)",
            [
                (str(list_date), str(fid), int(p["rating"]), int(p["games"]))
                for fid, p in players.items()
            ],
        )
        db.commit()
