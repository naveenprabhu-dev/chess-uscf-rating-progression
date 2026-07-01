"""Gunicorn config for production.

The app streams scrape progress over Server-Sent Events and runs each scrape in
a background thread, so we use THREADED workers — the default sync worker would
tie up a whole worker process on a single open SSE connection. Every value can
be overridden via env on the host.

Run with:  gunicorn -c gunicorn.conf.py run:app
(Set APP_ENV=production and FLASK_SECRET_KEY in the host env — create_app()
refuses to boot in production without the secret.)
"""
import os

# PaaS platforms inject $PORT; a VPS behind nginx can use the default and let
# nginx proxy to it.
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# Threaded workers so long-lived SSE streams don't starve the pool. Keep the
# worker count modest: scraping (network) is the bottleneck, not CPU, and the
# SQLite cache is a single-writer file — too many concurrent writers invites
# "database is locked".
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.environ.get("THREADS", "4"))

# Scrapes (especially the HTML/Cloudflare fallback) and SSE streams are
# long-lived; don't let gunicorn reap a worker mid-scrape.
timeout = int(os.environ.get("TIMEOUT", "120"))

# Log to stdout/stderr so the host's log collector captures it.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")


def when_ready(server):
    """Seed the featured-player cache from BUNDLED data once the workers are up.

    Runs once per deploy in the gunicorn ARBITER (not per worker), in a daemon
    thread so it never blocks request serving. `seed_featured` inserts the shipped
    timelines for any featured player the cache is missing — no network, just a
    few small INSERTs — so a fresh volume gets them instantly and there are ZERO
    USCF calls at request time. Idempotent: once the volume holds the data it's a
    no-op. Best-effort: any failure is logged and swallowed. Disable with
    SEED_FEATURED_ON_BOOT=0.

    (This replaced a scrape-on-boot warm that hammered the SQLite-on-volume and
    made the site slow right after a deploy.)"""
    if os.environ.get("SEED_FEATURED_ON_BOOT", "1") != "1":
        return
    import threading

    def _run():
        try:
            from webapp import create_app
            from webapp.seed import seed_featured
            app = create_app()
            with app.app_context():
                seed_featured(logger=server.log.info)
        except Exception as e:  # noqa: BLE001 - best-effort, never crash the arbiter
            server.log.warning("featured-cache seed skipped: %s", e)

    threading.Thread(target=_run, daemon=True, name="seed-featured").start()
