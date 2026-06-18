import os

DEFAULT_USCF_MILESTONES = list(range(400, 3100, 100))   # 400, 500, …, 3000
DEFAULT_FIDE_MILESTONES = list(range(1400, 3000, 100))  # 1400, 1500, …, 2900

# Back-compat alias — existing code/imports expect DEFAULT_RATING_MILESTONES.
DEFAULT_RATING_MILESTONES = DEFAULT_USCF_MILESTONES

# Spec 007 — saved-analysis limits. Anonymous (cookie-only) users may keep a
# small library; signing in (a later phase) raises the cap. Easy to tune here.
ANON_SAVE_LIMIT = 5
USER_SAVE_LIMIT = 100

# Cache freshness, in days. A cached scrape older than this is re-scraped on the
# next analyze (USCF/FIDE publish new tournaments, so stale rows would show old
# ratings). This is the only re-scrape path for an already-cached player.
#
# Override per-host with the CACHE_TTL_DAYS env var (e.g. on Railway) — set it to
# 0 to disable expiry and keep cached timelines indefinitely. A blank or
# unparseable value falls back to the default so a host-config typo can't crash
# boot. Read once at import; the server reads it at startup, so changing the env
# var requires a redeploy/restart.
def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


CACHE_TTL_DAYS = _env_int("CACHE_TTL_DAYS", 7)
