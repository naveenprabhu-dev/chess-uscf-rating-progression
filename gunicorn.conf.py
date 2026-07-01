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
