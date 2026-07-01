"""Seed the featured-player cache from bundled data — no network.

The 10 quick-add players' raw timelines are shipped in
`seed_data/featured_timelines.json` (regenerate locally by warming the cache with
`python -m webapp.warm`, then re-exporting). On boot we insert any the cache is
missing, so a fresh Railway volume gets the featured players instantly with ZERO
USCF API calls at request time. Idempotent + cheap (a handful of small INSERTs),
so it's safe on every deploy; once the volume holds the data it's a no-op.

This replaces the old scrape-on-boot warm, which hammered the SQLite file on the
volume and made the site slow right after a deploy. Live scraping still exists for
any player a user enters by hand — it just no longer runs at boot.
"""
import json
from pathlib import Path

from webapp.cache import get_timeline, save_timeline

SEED_FILE = Path(__file__).with_name("seed_data") / "featured_timelines.json"


def _is_clean_api(tl):
    """True only for genuine API output: api_version 2 AND a non-ALL-CAPS name."""
    if not tl or tl.get("api_version") != 2:
        return False
    name = (tl.get("name") or "").strip()
    return any(c.islower() for c in name)


def seed_featured(logger=print):
    """Insert bundled featured-player timelines for any not already cached as a
    clean API entry (a fresher real scrape is never clobbered). Returns
    (seeded, skipped). Must run inside an app context."""
    try:
        timelines = json.loads(SEED_FILE.read_text())
    except (OSError, ValueError) as e:
        logger(f"[seed] no seed data ({e}); skipping")
        return (0, 0)
    seeded = skipped = 0
    for tl in timelines:
        pid = str(tl.get("player_id"))
        if _is_clean_api(get_timeline("uscf", pid)):
            skipped += 1
            continue
        save_timeline(tl)
        seeded += 1
        logger(f"[seed] cached {tl.get('name')!r} ({pid}) from bundle")
    logger(f"[seed] done — seeded={seeded} skipped={skipped}")
    return (seeded, skipped)


def main():
    from webapp import create_app
    app = create_app()
    with app.app_context():
        seed_featured()


if __name__ == "__main__":
    main()
