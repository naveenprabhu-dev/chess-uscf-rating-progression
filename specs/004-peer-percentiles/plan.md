# Spec 004 — Peer Percentiles

## Context

Spec 001 (Flask MVP) answers *"when did this player hit each milestone?"* — useful but isolated. The number `1600 at age 13 after 142 games` only means something with a reference point.

This spec adds a curated reference set of well-known USCF players, pre-scrapes their milestone records, and computes percentiles for any user-entered player against that set. The output reframes a player's progression from absolute numbers into peer-relative ones: *"27% of reference players who reached 2200+ hit 1600 by age 13. Your games-to-1600 is below the median (median = 198)."*

This is the project's unique angle — no public tool (official US Chess MUIR, ChessGraphs, USCF-Stats, rating estimators) provides retrospective `(age, games, win%)` peer comparison.

## Worktree contract

This spec lives in worktree `feature/reference-set` and owns:

- New top-level `reference/` package
- New `specs/004-peer-percentiles/` (this file)
- Data files under `reference/snapshots/`

It must **not** edit `scraper/`, `webapp/`, `config.py`, or `CLAUDE.md`. UI integration happens in a follow-up that merges this module into `main`.

## Goals

- Curated reference set of 50–100 USCF players checked into the repo as a JSON list.
- Build script (`reference/build.py`) that scrapes any reference player whose snapshot is missing, using the existing `scrape_player` from spec 001 — no scraping changes.
- Snapshots committed to git as one JSON file per player. Deterministic, hand-inspectable, no DB needed inside this worktree.
- `compute_percentiles(player_record, reference_set, filter=None) -> dict` returns per-milestone percentile ranks for `age`, `games`, `score_pct`, plus the median for each.
- `nearest_neighbors(player_record, reference_set, k=3) -> list[dict]` returns the `k` reference players whose `(age, games)` trajectory is closest to the input player's at the milestones they've both reached.
- A defined public API (functions + return shapes) that the Flask layer can consume later without re-reading this spec.

## Non-goals

- Flask templates, routes, or any UI rendering — owned by `main` in a follow-up merge.
- Automated discovery of "famous players" (FIDE crawl, Wikipedia scraping, etc.). Curation is manual and dev-time for now.
- Letting end-users add players to the reference set at runtime.
- Recomputing percentiles on the fly from live data. The reference set is a versioned snapshot.
- Adapting the scraper to the new US Chess API — that's spec 003.

## Target state

```
reference/
  __init__.py             # exports compute_percentiles, nearest_neighbors, load_reference_set
  players.json            # curated list: [{"uscf_id": str, "name": str, "tags": [str, ...]}, ...]
  snapshots/              # one file per player; output of scrape_player() round-tripped to JSON
    12345678.json
    ...
  build.py                # scrape missing snapshots; run as `python -m reference.build`
  percentile.py           # compute_percentiles, nearest_neighbors
specs/004-peer-percentiles/plan.md
```

## Public API

```python
load_reference_set(snapshots_dir: Path = ...) -> list[dict]
# Loads every snapshot JSON. Each entry has the scrape_player dict shape from spec 001
# plus an injected "tags" list copied from players.json.

compute_percentiles(
    player_record: dict,          # output of scrape_player for the input player
    reference_set: list[dict],    # output of load_reference_set
    filter_tag: str | None = None # e.g. "us_gm" — restrict comparison to tagged subset
) -> dict
# Returns, for each milestone the input player has reached AND at least one ref player has reached:
# {
#   "1600": {
#     "age":       {"value": 13,   "percentile": 27,  "median": 15.5, "n": 48},
#     "games":     {"value": 142,  "percentile": 31,  "median": 198,  "n": 48},
#     "score_pct": {"value": 0.58, "percentile": 64,  "median": 0.55, "n": 48},
#   }, ...
# }
# Percentile semantics: % of reference players whose value at this milestone is GREATER than
# the input player's, for age and games (lower-is-better metrics). For score_pct,
# % whose value is LESS than the input player's (higher-is-better).
# "n" = ref players who reached this milestone (denominator).

nearest_neighbors(
    player_record: dict,
    reference_set: list[dict],
    k: int = 3
) -> list[dict]
# Distance = euclidean over normalized (age, games) at every milestone both players reached.
# Normalize each axis by the std-dev across the full ref set so age and games are comparable.
# Returns [{"name": str, "uscf_id": str, "distance": float, "shared_milestones": list[int]}, ...]
# sorted ascending by distance.
```

## Implementation phases

### Phase 1 — Reference player list

Hand-curate `reference/players.json`. Seed with ~50 US-affiliated titled players spanning rating tiers (well-known GMs, IMs, and a few high-rated experts/masters), each with a short list of tags: e.g. `us_gm`, `junior_prodigy`, `late_bloomer`, `women`, `2200_floor`, `2400_floor`. Tags are free-form; they're the slicing axis for `filter_tag`.

Each entry:

```json
{"uscf_id": "12345678", "name": "Player Name", "tags": ["us_gm", "junior_prodigy"]}
```

**Verification:** every entry parses; `uscf_id` is digits-only; no duplicates.

### Phase 2 — Snapshot build script

`reference/build.py` reads `players.json`, and for each player without a `snapshots/<id>.json`, runs `scrape_player(session, id, dob=None, milestones=DEFAULT_RATING_MILESTONES)` and writes the result as pretty-printed JSON. DOB is only known for some reference players (titled-player bios) — when missing, store `null` and skip age fields downstream.

Be polite: same `make_session()` helper, same `time.sleep(0.25–0.5)` between page-history requests as production. Build is idempotent: only scrape players whose snapshot is missing OR whose existing snapshot has a `milestones_config` that differs from the current default.

**Verification:**
- First run on a 5-player subset writes 5 snapshot files.
- Second run on the same subset writes 0 (idempotent).
- A snapshot file round-trips through `json.load` → `compute_percentiles` without errors.

### Phase 3 — Percentile computation

`reference/percentile.py`:

- `compute_percentiles` filters the reference set (by tag if given), iterates each milestone the input player reached, collects reference values at that milestone, computes percentile rank and median.
- Skip a milestone entirely if `n < 5` reference players have reached it (statistic is too noisy).
- `nearest_neighbors` computes the distance defined above. If the input player shares < 2 milestones with a reference player, exclude that ref player from candidates.

**Verification:**
- Test fixture: build a tiny hand-coded reference set of 10 known records, then assert the percentiles produced for a specific input match hand-computed values within 0.1%.
- Sanity: an input player matching a known prodigy in the reference set comes back as a near-neighbor of themself with distance ≈ 0.
- Filter sanity: `filter_tag="us_gm"` reduces `n` to the count of tagged players in `players.json`.

### Phase 4 — Snapshot the initial reference set

Run `python -m reference.build` for real against the full curated list. Commit `snapshots/` to git as the v1 reference dataset. **This is data; don't gitignore it.**

**Verification:**
- `git status` shows new JSON files under `reference/snapshots/`.
- `load_reference_set()` returns a list whose length matches `players.json`.
- Spot-check: pick a known prodigy from the reference set, confirm their `age_at_first_tournament` and a couple of milestone ages look plausible.

## Handoff to the Flask layer (out of scope here, but defining the contract)

The follow-up `main`-worktree merge will:

1. Import `compute_percentiles` and `nearest_neighbors` from `reference`.
2. Call them on the cached `scrape_player` record at `GET /player/<uscf_id>` render time. The result is *not* cached in SQLite — it's cheap and depends on the reference set version, which lives in git.
3. Render a "Peer Comparison" block in `player.html` (or new `_peer_comparison.html` partial).
4. Optionally surface the `filter_tag` as a dropdown ("Compare against: All / US GMs only / Juniors only").

That follow-up is a separate spec (likely `005-peer-comparison-ui`) and a separate worktree.

## Reference

- Scraper contract & milestone shape: `CLAUDE.md` and `specs/001-flask-mvp/plan.md`
- Scraping internals: `docs/scraping.md`
