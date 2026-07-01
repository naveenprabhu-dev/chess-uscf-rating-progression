"""One-time, idempotent warm of the featured-player scrape cache.

Populates `scrape_cache` for every quick-add player (`webapp.presets.
FEATURED_PLAYERS`) using the JSON API path ONLY (`scraper.uscf_api.
fetch_history_api`, which raises `ApiUnavailable` rather than silently dropping to
the HTML scraper) — so first-time visitors get instant loads and never a stale
ALL-CAPS scraper record.

Idempotent: a player already cached as a fresh, API-shaped timeline (api_version
== 2, title-case name, within `CACHE_TTL_DAYS`) is skipped, so re-running it on
every deploy is cheap. Paced + retried so a burst of USCF 429s can't wedge it.

Runs inside a Flask app context (it uses the SQLite cache). In production it is
triggered once per deploy from gunicorn's `when_ready` hook; it's also runnable
by hand for a one-off warm:  `python -m webapp.warm`.
"""
import time

from config import CACHE_TTL_DAYS
from scraper.uscf_api import ApiUnavailable, fetch_history_api
from webapp.cache import (
    SqliteCrosstableCache, get_timeline, is_timeline_stale, save_timeline,
)
from webapp.presets import FEATURED_PLAYERS

MAX_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 15   # USCF 429s clear in a few seconds; give them room
BETWEEN_PLAYERS_SECONDS = 3   # be polite / don't get IP-throttled (repo rule)


def _is_clean_api(timeline):
    """True only if this cached timeline is genuine API output: api_version 2 AND
    a non-ALL-CAPS name (an ALL-CAPS name is the HTML scraper's tell)."""
    if not timeline or timeline.get("api_version") != 2:
        return False
    name = (timeline.get("name") or "").strip()
    return any(c.islower() for c in name)


def _needs_warm(timeline):
    return (timeline is None
            or not _is_clean_api(timeline)
            or is_timeline_stale(timeline, CACHE_TTL_DAYS))


def _scrape_one(uscf_id, logger):
    xcache = SqliteCrosstableCache()
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            tl = fetch_history_api(uscf_id, crosstable_cache=xcache)
        except ApiUnavailable as e:
            last = f"ApiUnavailable: {e}"
        except Exception as e:   # never let one player wedge the whole warm
            last = f"{type(e).__name__}: {e}"
        else:
            if _is_clean_api(tl):
                save_timeline(tl)
                return tl
            last = f"not API-shaped (name={tl.get('name')!r} v={tl.get('api_version')})"
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS)
    logger(f"[warm] gave up on {uscf_id}: {last}")
    return None


def warm_featured(logger=print, force=False):
    """Warm every featured player's cache via the API. Skips already-fresh,
    API-shaped entries unless force=True. Returns (warmed, skipped, failed).
    Must run inside an app context."""
    warmed = skipped = failed = 0
    total = len(FEATURED_PLAYERS)
    for i, player in enumerate(FEATURED_PLAYERS, start=1):
        uscf_id, name = player["uscf_id"], player["name"]
        # Re-read per player so a redeploy (or a second warmer) doesn't redo work
        # already done — keeps API load down.
        if not force and not _needs_warm(get_timeline("uscf", uscf_id)):
            skipped += 1
            continue
        logger(f"[warm] ({i}/{total}) scraping {name} ({uscf_id})…")
        tl = _scrape_one(uscf_id, logger)
        if tl is not None:
            warmed += 1
            logger(f"[warm] cached {tl['name']!r} "
                   f"(api_version={tl.get('api_version')}, events={len(tl.get('events') or [])})")
        else:
            failed += 1
        if i < total:
            time.sleep(BETWEEN_PLAYERS_SECONDS)
    logger(f"[warm] done — warmed={warmed} skipped={skipped} failed={failed}")
    return warmed, skipped, failed


def main():
    from webapp import create_app
    app = create_app()
    with app.app_context():
        warm_featured()


if __name__ == "__main__":
    main()
