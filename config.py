DEFAULT_USCF_MILESTONES = list(range(400, 3100, 100))   # 400, 500, …, 3000
DEFAULT_FIDE_MILESTONES = list(range(1400, 3000, 100))  # 1400, 1500, …, 2900

# Back-compat alias — existing code/imports expect DEFAULT_RATING_MILESTONES.
DEFAULT_RATING_MILESTONES = DEFAULT_USCF_MILESTONES

# Spec 007 — saved-analysis limits. Anonymous (cookie-only) users may keep a
# small library; signing in (a later phase) raises the cap. Easy to tune here.
ANON_SAVE_LIMIT = 5
USER_SAVE_LIMIT = 100

# Cache freshness. A cached scrape older than this is re-scraped on the next
# analyze (USCF/FIDE publish new tournaments, so stale rows would show old
# ratings). This is the only re-scrape path for an already-cached player. Set to
# 0 (or None) to disable expiry and keep cached timelines indefinitely.
CACHE_TTL_DAYS = 7
