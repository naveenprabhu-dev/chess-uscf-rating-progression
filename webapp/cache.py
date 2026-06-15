import json
import sqlite3
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
