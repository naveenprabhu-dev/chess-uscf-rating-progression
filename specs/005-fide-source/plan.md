# Spec 005 — FIDE as a second source

> **Correction (2026-05-28):** this plan was written assuming FIDE exposes no per-event W/D/L, so it specifies `score_pct: None` for every FIDE milestone (see Goals, Non-goals, and the §"What's NOT available" reasoning). That assumption was wrong. FIDE's `a_indv_calculation.php` endpoint returns per-period W/D/L, and `scraper.fide.get_fide_calculations` now computes a real `score_pct` for FIDE. Wherever this plan says FIDE score is `None` / unavailable / blurred, treat it as superseded — see `research.md` endpoint #4 and `docs/scraping.md`. Two related claims were also corrected: the "~30 s rate-limit" was unverified (no throttle observed), and the site uses a *site-wide* source toggle, not the per-row toggle this plan describes.

## Context

Spec 001 built the Flask MVP around USCF only. The codebase already has a small bridge into FIDE — `scraper.core.get_fide_birth_year` walks `MbrDtlMain` → FIDE `/profile/<id>` to recover a B-Year when the user leaves DOB blank (see progress.md §6). This spec turns FIDE into a full first-class source so a user can analyze a FIDE-only player the same way they analyze a USCF player, with a per-row source toggle (USCF / FIDE) on the index form.

The endpoint evidence behind every claim below is in [`research.md`](research.md). Read it once; this plan refers back rather than restating findings.

## Goals

- Per-row source toggle (USCF / FIDE) on the index and search forms. Up to 2 players per request, independently sourced.
- New `scraper/fide.py` with `scrape_fide_player(session, fide_id, dob=None, milestones=None, progress_cb=None) -> dict` returning the same dict shape as `scrape_player`, with `score_pct: None` for every milestone (FIDE doesn't expose W/D/L).
- New `search_fide_players(session, query, limit=20)` mirroring the USCF version.
- Cache, routes, and templates become source-aware: keyed on `(source, player_id)` rather than `uscf_id`.
- Separate default milestone ladder for FIDE (starts at 1400, the current floor).
- `/compare` allows mixing USCF and FIDE players with a banner explaining the rating-scale caveat. The Score chart blurs out when all selected players are FIDE (same treatment as the existing "no age data" overlay in §8a of `progress.md`).

## Non-goals

- Rapid / blitz ratings. The chart-data JSON exposes them but the MVP stays classical-only. `rating_type: "classical"` is reserved on the record shape so a future spec can add a per-source rating-type toggle without breaking cache.
- Inferring `score_pct` from per-period Elo math. Lossy, surprising; not worth it.
- A standalone "all-FIDE" comparison route. Mixing in `/compare` covers both cases with one route.
- Real-time autocomplete on the search box. `/api/search?source=fide&q=...` returns JSON; UI integration stays the same as USCF.
- Migration of the existing cache. The SQLite DB lives in gitignored `instance/` and has no users; the cache will be dropped and rebuilt on first run of the new schema. Flash a one-time notice if old rows are detected.

## Target state

```
scraper/
  __init__.py       # re-exports scrape_player, scrape_fide_player,
                    # search_players, search_fide_players,
                    # DEFAULT_USCF_MILESTONES, DEFAULT_FIDE_MILESTONES,
                    # DEFAULT_RATING_MILESTONES (alias for USCF, kept for back-compat)
  core.py           # USCF scraper (unchanged behavior; constants renamed where overlapping)
  fide.py           # NEW — FIDE scraper, search, exceptions
webapp/
  cache.py          # schema bumped: PK is (source, player_id)
  forms.py          # validate_fide_id; per-row source field; per-source milestone settings
  routes.py         # /player/<source>/<player_id>, /player/<source>/<player_id>/refresh,
                    # /player/<source>/<player_id>/delete; /api/search?source=...
  templates/
    index.html      # per-row source radio buttons; FIDE caveat help text
    player.html     # source badge; FIDE-specific notes (no pre-rated history, period cadence)
    _milestone_table.html  # hides Score column when source == "fide"
    compare.html    # mixed-source banner; Score-chart blur when all FIDE
    search.html     # source toggle, source-specific result columns
    settings.html   # two milestone editors (USCF / FIDE)
config.py           # DEFAULT_USCF_MILESTONES + DEFAULT_FIDE_MILESTONES
specs/005-fide-source/
  plan.md           # this file
  research.md       # endpoint evidence
```

## Scraper API contract (extended)

Both `scrape_player` and `scrape_fide_player` return the same dict shape, source-tagged:

```python
{
  "source": "uscf" | "fide",
  "player_id": str,                        # canonical ID for the source
  "uscf_id": str | None,                   # populated when source == "uscf"
  "fide_id": str | None,                   # populated when source == "fide"
  "name": str,
  "country": str | None,                   # FIDE only; None for USCF
  "dob": str | None,                       # "MM/DD/YYYY" — synthesized "01/01/<B-Year>" if FIDE-only
  "dob_source": "user" | "fide" | "none",
  "fide_birth_year": int | None,
  "first_tournament_date": str,            # "YYYY-MM-DD"; FIDE synthesizes "<YYYY>-<MM>-01" from first period
  "initial_rating": int,                   # FIDE: first published rating, NOT post-first-event
  "age_at_first_tournament": int | None,
  "milestones": {                          # keys are str(threshold)
    "<threshold>": {
      "months": int | None,
      "games": int | None,
      "age": int | None,
      "score_pct": float | None,           # always None when source == "fide"
    },
    ...
  },
  "milestones_config": list[int],
  "rating_type": "classical",              # reserved; FIDE rapid/blitz are out of scope
  "scraped_at": str,                       # ISO-8601 UTC
}
```

The shape change is additive plus a rename: `uscf_id` is no longer the primary identifier in code paths — `(source, player_id)` is. Old `uscf_id`-keyed cache rows will not survive; see the migration note in §Cache below.

## Source-specific behavior

### IDs and validation

- USCF: 8 digits (unchanged: `r"^\d{8}$"`).
- FIDE: digits only, length 5–10 inclusive. Real-world IDs span ~3-digit historical values up to 9 digits; capping at 10 catches typos. Implementation: `r"^\d{5,10}$"` in `validate_fide_id`. Don't pad or normalize.

### Default milestones

```python
DEFAULT_USCF_MILESTONES = [400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200]
DEFAULT_FIDE_MILESTONES = [1400, 1600, 1800, 2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700]
DEFAULT_RATING_MILESTONES = DEFAULT_USCF_MILESTONES   # back-compat alias
```

Stored in session as two separate lists: `session["milestones_uscf"]` and `session["milestones_fide"]`. The active list passed to the scraper is chosen by source at scrape time.

### FIDE scraper internals

`scraper/fide.py` is small because the heavy lifting is one JSON endpoint. Constants:

```python
FIDE_BASE = "https://ratings.fide.com"
FIDE_INTER_REQUEST_DELAY_SECONDS = 1.0   # for sequential FIDE calls (chart_data + search)
```

Public functions and exceptions:

```python
class FidePlayerNotFound(Exception): ...
class FideNoRatedHistory(Exception): ...
class FideScrapeError(Exception): ...

def get_fide_history(session, fide_id) -> list[dict]:
    """GET /a_chart_data.phtml?event=<id>&period= and return parsed JSON.
    Raises FidePlayerNotFound on empty/invalid response, FideScrapeError on HTTP errors.
    Sets X-Requested-With and Referer headers; retries once after FIDE_INTER_REQUEST_DELAY_SECONDS
    if the first response is empty (the rate-limit fingerprint)."""

def search_fide_players(session, query, limit=20) -> list[dict]:
    """GET /incl_search_l.php?search=<q>; parse <table id="table_results">.
    Returns [{fide_id, name, title, fed, std, rpd, blz, b_year}, ...]."""

def scrape_fide_player(session, fide_id, dob=None, milestones=None, progress_cb=None) -> dict:
    """Single entry point. progress_cb fires (0, 1) at start and (1, 1) at end —
    there's no per-tournament loop to report on."""
```

`scrape_fide_player` flow:

1. `dob` precedence: user-supplied → reuse `scraper.core.get_fide_birth_year` (synthesize `01/01/<year>`) → none. The existing `dob_source` field captures which path was taken. The B-Year from search results is already inside the history-call payload's siblings, so we could optimize, but reusing the existing helper keeps one source of truth.
2. Pull history via `get_fide_history`.
3. Reject if history is empty → `FideNoRatedHistory`.
4. First period's `date_2` → `first_tournament_date` = `YYYY-MM-01` (the published-list date; document this approximation in `player.html`).
5. First period's `rating` → `initial_rating` (cast to int).
6. Iterate periods in chronological order, cumulating `period_games`. For each milestone threshold:
   - Find the first period where `int(rating) >= threshold`.
   - `months` = `months_difference(first_tournament_date, period_date)` (reuse the existing helper).
   - `games` = cumulative `period_games` up through and including that period.
   - `age` = `calculate_age(dob, period_date)` (None when `dob` is None; year-only granularity when dob came from B-Year).
   - `score_pct` = `None`.
7. Return the dict.

### USCF scraper

No behavioral change. Tweak only the returned dict to populate the new fields: set `source="uscf"`, `player_id=str(uscf_id)`, `fide_id=None`, `country=None`, `rating_type="classical"`.

## Cache

Schema change — single new table that replaces the existing `players`:

```sql
CREATE TABLE IF NOT EXISTS players (
    source TEXT NOT NULL,
    player_id TEXT NOT NULL,
    name TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (source, player_id)
);
```

`init_db` detects the old single-column-PK schema (via `PRAGMA table_info(players)`); if found, drops the table and recreates with the new schema, then flashes a one-time notice on next request ("Cache was reset to support the new FIDE source"). This is acceptable because cache is gitignored and unowned by anyone besides the current dev.

Helpers gain a `source` argument:

```python
get_player(source, player_id) -> dict | None
save_player(record)  # uses record["source"] and record["player_id"]
list_players() -> list[dict]  # rows include source so the template can show a badge
invalidate(source, player_id) -> None
```

## Routes

| Old                                  | New                                                  |
| ------------------------------------ | ---------------------------------------------------- |
| `GET /player/<uscf_id>`              | `GET /player/<source>/<player_id>`                   |
| `POST /player/<uscf_id>/refresh`     | `POST /player/<source>/<player_id>/refresh`          |
| `POST /player/<uscf_id>/delete`      | `POST /player/<source>/<player_id>/delete`           |
| `GET /search?q=...`                  | `GET /search?source=...&q=...`                       |
| `GET /api/search?q=...`              | `GET /api/search?source=...&q=...`                   |
| `GET /compare?ids=12345678&ids=...`  | `GET /compare?ids=uscf:12345678&ids=fide:2016192`    |

`<source>` is `uscf` or `fide`. Anything else → 404. The compare route's `ids` are namespaced strings (`source:id`); this keeps the URL order-stable across players and lets the chart legend label sources without a parallel array.

`POST /scrape` accepts `source_N` form fields (per row) and dispatches to the right scraper per row. The SSE progress stream already streams per-player events; no protocol change needed.

## Forms & UX

- **Index form**: each row gains two radio buttons (USCF default). USCF rows show the existing 8-digit hint; FIDE rows show "5–10 digits, FIDE ID". DOB field stays optional for both — the FIDE B-Year fallback already works on USCF rows, and FIDE rows can use it too (the player profile we already hit is the same one).
- **Settings page**: two `<textarea>`s under labeled sections, "USCF milestones" and "FIDE milestones". Each posts independently. Don't merge into one global list — the rating scales aren't comparable.
- **Player page**: a `<span class="badge">USCF</span>` or `<span class="badge">FIDE</span>` next to the name. When `source == "fide"`, add a small note: "FIDE rating history starts at the first published rating, not the first event. Periods were quarterly through 2011, monthly since 2013." When `source == "fide"`, the milestone table hides the Score column (the `_milestone_table.html` partial already takes a `show_age` flag — add a parallel `show_score`).
- **Search page**: a source toggle above the query input. USCF results show the existing columns. FIDE results show `Name / FIDE ID / Title / Fed / Std / Rpd / Blz / B-Year` with "Use as Player 1/2" links that also propagate `source=fide` to the index prefill.
- **Compare**: when `players` contains both sources, render `<p class="banner">Comparing USCF and FIDE ratings — note the scales are not equivalent.</p>` above the chart grid. When *all* players are FIDE, mark the Score `chart-cell` with the existing `no-data` class so the overlay & blur kick in.

## Implementation phases

### Phase 1 — Scraper module

Add `scraper/fide.py` with `get_fide_history`, `search_fide_players`, `scrape_fide_player`, plus the three exceptions. Update `scraper/__init__.py` to re-export. Update `scraper/core.py` only enough to add the new dict fields (`source`, `player_id`, `fide_id`, `country`, `rating_type`).

**Verification**

- `scrape_fide_player(make_session(), "1503014")` returns a dict whose `name == "Carlsen, Magnus"`, `initial_rating == 2356`, `first_tournament_date == "2003-04-01"`, and whose `milestones["2400"]["months"]` is a small positive int.
- `search_fide_players(make_session(), "Carlsen, M")` returns ≥1 row with `fide_id == "1503014"`.
- A second call to `get_fide_history` for the same `fide_id` within 30 s hits the retry path (assert via a stub session that two GETs were issued).
- `scrape_player(...)` for a known USCF ID still returns the same milestone numbers as before the refactor (regression check against an existing cached snapshot or a known-good record).

### Phase 2 — Cache schema migration

Update `webapp/cache.py` to the new composite-key schema and helpers. `init_db` detects the legacy schema and drops + recreates. Add a small `migration_notice` flag in the app context that `base.html` renders once if set.

**Verification**

- Fresh-instance startup creates the new schema and `list_players()` returns `[]`.
- Starting against an `instance/cache.sqlite3` produced by the current code triggers the drop/recreate path and surfaces the notice. After one request, the notice is cleared.
- `save_player → get_player → invalidate` round-trips for both `source="uscf"` and `source="fide"` records.

### Phase 3 — Routes & forms

Rewrite route signatures to take `<source>/<player_id>`. Update `parse_player_inputs` to read `source_N` form fields and to validate per source. Update `_scrape_and_cache` and the SSE worker to dispatch on source. Move `_active_milestones()` to take a `source` argument and to read the right session key.

**Verification**

- `POST /scrape` with one USCF row and one FIDE row produces two cached records, one of each source, and lands on `/compare?ids=uscf:<id>&ids=fide:<id>`.
- All old single-source URLs (`/player/<uscf_id>`) return 404 — confirm there's no surviving template link to them.
- `/api/search?source=fide&q=Carlsen` returns `[{fide_id, name, ...}, ...]`; `?source=uscf&q=Carlsen` returns the existing USCF shape.

### Phase 4 — Templates

Update every template that referenced `player.uscf_id` to use `player.source` + `player.player_id`. Add the source badge, the FIDE caveat note, the `show_score` flag in `_milestone_table.html`. Source toggle on `index.html` and `search.html`. Split `settings.html` into two milestone editors.

**Verification**

- Scrape a USCF player, scrape a FIDE player, open both player pages — neither template throws, the FIDE page shows no Score column, the USCF page is visually unchanged from before.
- `/compare` with one of each renders all four charts; the mixed-source banner appears; the Score chart is normal (not blurred) because at least one player is USCF.
- `/compare` with two FIDE players renders the Score chart with the no-data overlay and no banner.
- Search page with `source=fide` shows the FIDE column set; `source=uscf` is unchanged.

### Phase 5 — Polish & docs

Update `progress.md` with a §10 once code lands (this spec's §9 entry covers planning only). Update `CLAUDE.md` to canonicalize the new dict shape and the `(source, player_id)` cache key. Add a short "FIDE source" subsection to `docs/scraping.md` mirroring the format of the existing USCF section.

## Cross-cutting risks

- **Rate limiting**: both FIDE endpoints return empty payloads when polled too fast. `get_fide_history` retries once after `FIDE_INTER_REQUEST_DELAY_SECONDS`. If empty after retry, raise `FideScrapeError("FIDE rate-limited — try again in a minute")`. The user already lives with USCF taking 10s–2min per scrape, so a one-call FIDE scrape with a polite delay is a net speedup.
- **Granularity drift**: quarterly periods (pre-2013) make "months to milestone" imprecise for older players. The player-page caveat note makes this visible; no algorithmic fix.
- **Initial-rating semantics**: USCF's `initial_rating` is post-first-tournament; FIDE's is first-published. Charts label both as "initial rating" — the player-page caveat note covers this. Don't try to align them.
- **Cache reset on first run**: documented in §Cache, surfaced via a flash notice. Acceptable because there are no production users.

## Reference

- Endpoint evidence: [`research.md`](research.md)
- Existing dict shape & MVP rules: `specs/001-flask-mvp/plan.md`
- Existing FIDE bridge: `scraper.core.get_fide_birth_year`
- Progress log: `progress.md` (§6 covers the existing DOB→FIDE fallback this spec builds on)
