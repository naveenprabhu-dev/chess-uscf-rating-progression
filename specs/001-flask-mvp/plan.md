# Spec 001 — Flask MVP

## Context

The project began as a CLI script (`scrape_sheets.py`) that scraped US Chess for a player's classical rating progression and wrote per-milestone results (months, games, age, cumulative score%) into a Google Sheet. The Sheets coupling makes the tool unfriendly to share — every user needs a service account JSON, share permissions on a sheet, and a local Python env.

This spec migrates the scraper behind a small Flask web app so a user can paste a USCF ID + DOB into a form and see the milestone insights in a browser. SQLite caches scraped records keyed by USCF ID so repeat views don't hammer USCF's `/msa/` pages. The scraper logic is unchanged in behavior — just lifted out of the Sheets-coupled script into a pure module.

## Goals

- Web form accepts up to **2 players** per session (USCF ID + DOB).
- For each player, render a milestones table with `months / games / age / score%` per threshold.
- Scrapes are cached in SQLite, keyed on USCF ID. `GET /player/<id>` reads from cache; never auto-scrapes.
- Manual refresh path (`POST /player/<id>/refresh`) re-scrapes a player on demand.
- Rating milestones are configurable **per session** (no accounts).

## Non-goals

- Side-by-side player comparison view — deferred to `specs/002-comparison-view/`.
- Migration to the new US Chess API at `beta-ratings-api.uschess.org` — deferred to `specs/003-api-migration/`. Research seed: legacy MSA pages stopped updating 2025-10-29 and will be retired in early 2026, so a follow-up spec will be needed.
- User accounts, persistence beyond the cache, multi-user infrastructure.
- Parallel scraping. One `requests.Session()` per scrape run.

## Target state

```
scraper/            # sheets-free scraping core — pure functions, no Flask imports
  __init__.py       # exports scrape_player, DEFAULT_RATING_MILESTONES
  core.py           # the scraping/parsing logic (ported from old scrape_sheets.py)
webapp/             # Flask app
  __init__.py       # create_app() factory
  routes.py         # /, /scrape, /player/<id>, /player/<id>/refresh, /settings
  cache.py          # SQLite open/init + get/save/invalidate helpers
  forms.py          # input validation (USCF ID, DOB parsing)
  templates/        # base.html, index.html, player.html, _milestone_table.html
  static/styles.css
config.py           # only DEFAULT_RATING_MILESTONES lives here now
run.py              # `flask --app webapp run`
instance/           # gitignored; SQLite DB lives here
archive/            # old CLI scripts kept for reference (not imported anywhere)
```

## Scraper API contract

`scrape_player(session, uscf_id, dob, milestones=None) -> dict` is the single entry point. Returns:

```python
{
  "uscf_id": str,
  "name": str,
  "dob": str,                              # "MM/DD/YYYY"
  "first_tournament_date": str,            # "YYYY-MM-DD"
  "initial_rating": int,
  "age_at_first_tournament": int,
  "milestones": {                          # keys are str(threshold)
    "1000": {"months": int|None, "games": int|None, "age": int|None, "score_pct": float|None},
    ...
  },
  "milestones_config": list[int],          # the thresholds actually used
  "scraped_at": str                        # ISO-8601 UTC
}
```

`None` for any milestone the player hasn't reached. The web app and the cache both round-trip this exact shape.

## Implementation phases

### Phase 1 — Extract `scraper/core.py`

Port `scrape_sheets.py` logic into `scraper/core.py`. Drop everything Sheets-coupled:

- Imports: `gspread`, `oauth2client.service_account`
- Helpers: `_col_index`, `_cell_a1`, `_read_column_until_blank`, `_validate_config`, `get_uscf_ids`, `get_dobs`
- The `sheet.update(...)` call and the `__main__` block
- The hardcoded `cutoff = datetime.strptime('2025-05-16', ...)` early-break

Keep verbatim:

- `extract_date`, `months_difference`, `calculate_age`
- `get_tournaments_played`, `get_name`, `get_first_classical_tournament_details`
- `games_played_in_tournament`
- `rating_progress_by_months_games_and_age` (minus the cutoff)
- Both `if games_played != 0:` divide-by-zero guards

Add `make_session()` helper that sets a realistic User-Agent (see `docs/scraping.md`).

**Verification:** `scrape_player(session, "<known_id>", "<known_dob>")` returns the documented dict, and milestone numbers match what the legacy `scrape_sheets.py` produced for the same player.

### Phase 2 — SQLite cache (`webapp/cache.py`)

Schema: one row per USCF ID, columns for the dict shape above (store the milestones dict as JSON). `init_db()`, `get(uscf_id) -> dict | None`, `save(record: dict)`, `invalidate(uscf_id)`. DB lives at `instance/cache.sqlite3`.

**Verification:**
- Round-trip: `save(record)` then `get(record["uscf_id"])` returns an equal dict.
- Repeat `GET /player/<id>` after a first scrape issues zero HTTP requests to uschess.org (check `requests.Session` call count or log).

### Phase 3 — Flask routes + templates

Routes:
- `GET /` — form for 1–2 players.
- `POST /scrape` — validate input, scrape any uncached players, redirect to a results view.
- `GET /player/<uscf_id>` — read from cache, render `player.html`.
- `GET /settings` / `POST /settings` — edit `flask.session["milestones"]`.

Templates use `_milestone_table.html` for the per-player table; `player.html` embeds it.

**Verification:**
- `POST /scrape` with two valid USCF IDs renders a page with two milestone tables.
- Invalid input (bad USCF ID format, malformed DOB) renders the form with inline errors, no crash.
- Changing milestones in `/settings` and revisiting a cached player shows a "milestones changed — refresh?" banner instead of silently recomputing.

### Phase 4 — Manual refresh

`POST /player/<uscf_id>/refresh` calls `cache.invalidate(uscf_id)`, re-scrapes, saves, redirects back to the player page. Linked from the milestones-changed banner and from a button on every player page.

**Verification:**
- Submitting refresh updates `scraped_at` to the current time.
- The page survives a refresh on a player whose underlying USCF record hasn't changed (no false errors, identical milestone numbers).

## Reference

- Scraping internals & bot-detection escalation: `docs/scraping.md`
- Universal project conventions: `CLAUDE.md`
