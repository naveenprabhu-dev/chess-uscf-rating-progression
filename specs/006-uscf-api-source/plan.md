# Spec 006 — USCF JSON API as a fast-path source

## Context

US Chess has stood up a JSON API at `ratings-api.uschess.org` exposing the same player data the existing MSA HTML scraper extracts. Two findings drive this work:

1. **MSA dropped post-October-2025 tournaments from the public legacy HTML pages.** The current scraper cannot see any tournament after that cutoff. The JSON API *does* include them — verified: `GET /api/v1/rated-events?FromDate=2025-11-01&SortBy=startDate&Dir=desc` returns events through 2026-05-20 (today). This is the primary business reason to switch: the existing scraper is becoming truth-deficient for any active player.

2. **The API is dramatically faster.** A typical HTML scrape today is one tournament-list page + one tournament-detail page per tournament played, gated by `INTER_PAGE_DELAY_SECONDS = 0.35` and the `curl_cffi` Cloudflare workaround. The API needs ~1 + ⌈N/200⌉ + ⌈G/200⌉ calls (one member detail + paginated sections + paginated games) with no throttle and no Cloudflare gate. For a 100-tournament player this is roughly 5 calls vs. 100+ calls — at least an order of magnitude faster, larger for active players.

The API is currently unauthenticated. A future v2 will require API keys via MUIR login; this spec uses v1. Endpoint evidence and shape probes are in [`research.md`](research.md).

## Goals

- Add `scraper/uscf_api.py` exposing `scrape_player_api(uscf_id, dob=None, milestones=None, progress_cb=None) -> dict` that returns the **exact same dict shape as the existing `scrape_player`**, including `score_pct` for every milestone.
- Make `scrape_player` in `scraper/core.py` try the API first. If the API can produce a complete record, return it. Otherwise fall through to the existing HTML scraping logic, which remains the source of truth when the API doesn't have a player.
- **Leave the entire HTML scraping code path 100% intact.** Not a single line of `_milestone_progress`, `games_played_in_tournament`, `get_first_classical_tournament_details`, `get_tournaments_played`, `get_name`, `make_session`, `_get_msa`, or `_is_cloudflare_challenge` changes. The API attempt is a strict prepend at the top of `scrape_player`; everything below the dispatch is byte-for-byte unchanged.

## Non-goals

- Removing or modifying any HTML scraping code. The HTML path stays as a working, tested fallback indefinitely.
- Removing `beautifulsoup4`, `lxml`, or `curl_cffi` from `environment.yml`. They stay — the HTML fallback uses them.
- Adopting `(source, player_id)` cache keys from Spec 005. Both the API and the HTML scraper produce USCF records keyed on `uscf_id`; cache rows are interchangeable. Spec 005 introduces the multi-source key when FIDE lands.
- Adding API-key support. The v2 surface isn't live.
- Per-call caching. The existing dict-level SQLite cache is sufficient.
- A user-facing setting to choose backend. The dispatch is automatic and invisible.

## Target state

```
scraper/
  __init__.py       # add scrape_player_api and ApiUnavailable to re-exports
  core.py           # scrape_player gains a single API-first try block at the top;
                    # nothing else changes
  uscf_api.py       # NEW — API client + scrape_player_api
specs/006-uscf-api-source/
  plan.md           # this file
  research.md       # endpoint shapes, coverage probes, perf reasoning
```

## API client (`scraper/uscf_api.py`)

Single public function:

```python
def scrape_player_api(uscf_id, dob=None, milestones=None, progress_cb=None) -> dict
```

Returns the dict shape documented in `CLAUDE.md` (and produced by the current `scrape_player`) on success. Raises `ApiUnavailable` to signal the caller should fall back to HTML. No other exception types escape — connection errors, 5xx, missing player, missing games, etc. all map to `ApiUnavailable` with a short reason string.

Module-private helpers:

- `_get(path, **params)` — `requests.get(BASE + path, params=...)` with `timeout=5` and a single retry on `ConnectionError` / `Timeout`. Raises `ApiUnavailable` on any 5xx, any connect/timeout failure, or unexpected response shape. **No `curl_cffi`, no Cloudflare retry** — the JSON API is plain HTTPS.

- `_member_detail(uscf_id)` → returns `{"name": "...", ...}` on 200, raises `ApiUnavailable("not in api")` on 404. Builds `name` as `f"{firstName} {lastName}".title()` to match how `get_name()` produces names (the HTML pages also serve all-caps, which templates already display without complaint — title-casing is a normalization, not a behavior change).

- `_list_sections(uscf_id)` → list of sections sorted by `startDate` ascending. Paginates `/members/{id}/sections?RatingSource=R&Size=200&Offset=N` until `hasNextPage=false`. Each retained item is regular OTB (`ratingSystem == "R"`), excluding the online variants (`OR/OQ/OB`). This replaces the HTML scraper's `"=>" in classical_td_text and "ONL" not in classical_td_text` filter cleanly. Each item carries `startDate`, `ratingRecords[0].postRating`, `event.id`, `sectionNumber`.

- `_list_games(uscf_id)` → dict keyed by `(event.id, section.number)` mapping to `{wins, draws, losses, games}`. Paginates `/members/{id}/games?RatingSource=R&Size=200&Offset=N`. Counts each item's `player.outcome` ∈ `{"Win","Loss","Draw","Unknown"}`. Treats `Unknown` outcomes as participated games (incremented in `games`) but not counted in W/D/L — matching the HTML scraper's behavior, where regex `\b[WLD]\s+\d+` would simply not match an unknown result and the game wouldn't enter `games_played` either. **Empty result (no items at all) raises `ApiUnavailable("games index empty")`** — we'd be unable to compute `score_pct`, which violates the "API must provide all info" requirement and triggers fallback.

- `_compute_milestones(sections, games_by_section, dob, milestones)` — pure function. Walks sections in chronological order, maintains running `(games, wins, draws, losses)` accumulators, computes `adjusted_win_rate = (wins + 0.5 * draws) / games` **guarded by `games != 0`** (the divide-by-zero guard for the bye-and-withdraw edge case documented in CLAUDE.md). At each section, while `postRating >= milestones[i]`, fill `milestones_reached[i] = {months, games, age, score_pct}`. Reuses `months_difference` and `calculate_age` from `scraper.core` — imports them, doesn't duplicate.

The orchestrator inside `scrape_player_api`:

1. `_member_detail(uscf_id)` → 404 raises `ApiUnavailable`.
2. DOB precedence: caller-supplied → `get_fide_birth_year(...)` fallback → `None` (same as HTML path).
3. `_list_sections(uscf_id)` → if empty, raise `ApiUnavailable("no sections")` (HTML may still have something).
4. `_list_games(uscf_id)` → empty triggers `ApiUnavailable` per above.
5. Fire `progress_cb(0, len(sections))` if provided, then `progress_cb(i, len(sections))` once per section processed, so the SSE stream from `/scrape/stream` behaves identically to the HTML path.
6. Build and return the dict.

`milestones` defaults to `DEFAULT_RATING_MILESTONES` if `None`.

Constants:

```python
USCF_API_BASE = "https://ratings-api.uschess.org"
API_PAGE_SIZE = 200
API_TIMEOUT_SECONDS = 5
```

## Dispatch in `scrape_player` (`scraper/core.py`)

The ONLY change to `core.py`. A single block added at the very top of the existing function:

```python
def scrape_player(session, uscf_id, dob=None, milestones=None, progress_cb=None):
    try:
        from scraper.uscf_api import scrape_player_api, ApiUnavailable
        return scrape_player_api(uscf_id, dob=dob, milestones=milestones, progress_cb=progress_cb)
    except ApiUnavailable:
        pass  # fall through to existing HTML implementation

    # === everything below this line is the EXISTING implementation, byte-for-byte ===
```

The `session` parameter is unused by the API path (it uses a plain `requests` session internally). That asymmetry is intentional — the API doesn't need Cloudflare-impersonating `curl_cffi`, so reusing the existing session would be pointless complication. The HTML path keeps its session usage exactly as today.

## Fallback semantics

The fallback is **per-player, not per-field**. If the API produces a complete record, the API result wins. If anything goes wrong — player 404, sections empty, games index empty, any 5xx, any timeout — the entire request falls through to the HTML scraper. No mixed records.

Rationale: mixing API rating-progression with HTML W/D/L means two sources of truth for the same player and risks subtle inconsistencies (e.g., a tournament present in the API but not yet on the HTML page would corrupt the cumulative game count, or vice versa).

The cache is keyed on `uscf_id` only. A cache row produced by the API path is indistinguishable from one produced by HTML — both conform to the documented dict shape. The `/player/<uscf_id>/refresh` button works the same regardless of which backend produced the cached record. When refresh re-runs `scrape_player`, it again tries API first — so a previously-HTML-cached player gets upgraded to the API path the moment the API starts indexing them.

## Performance claim

Confirmed empirically during research:

| Player profile | HTML scraper | API |
|---|---|---|
| 50-tournament player | 1 list page + 50 detail pages + Cloudflare retries + 0.35s/page throttle ≈ 20-40s | 1 member + 1 sections page + 1-3 games pages ≈ 2-3s |
| 1000+ tournament veteran | many minutes (Cloudflare gate fires) | 1 + ~5 sections pages + ~30 games pages ≈ 15-30s |

Speedup is at least 10×. The API also covers tournaments the HTML pages no longer show (post-October-2025), so for any active player the API is the only path that produces a *current* record.

## Files touched

| File | Change |
|---|---|
| `scraper/uscf_api.py` | NEW — full API client + `scrape_player_api` + `ApiUnavailable` |
| `scraper/core.py` | One try/except block prepended to `scrape_player`. No other edits. |
| `scraper/__init__.py` | Add `scrape_player_api` and `ApiUnavailable` to re-exports |
| `specs/006-uscf-api-source/plan.md` | NEW — this file |
| `specs/006-uscf-api-source/research.md` | NEW — probes and shape evidence |
| `progress.md` | Append entry per project rule |

## Files explicitly untouched

- `webapp/cache.py`, `webapp/routes.py`, `webapp/__init__.py`, `webapp/forms.py` — all transparent to backend swap.
- All templates.
- `config.py`.
- `environment.yml` — HTML deps stay; API path uses already-installed `requests`.
- `docs/scraping.md` — accurate for the HTML fallback; do not edit.
- Every other function in `scraper/core.py`.

## Verification

End-to-end checks the implementer should run before merging:

1. **Parity test.** Pick 3-5 players from the user's existing SQLite cache. For each, scrape twice: once with the API path enabled (default), once with `scrape_player_api` short-circuited via monkey-patch to raise `ApiUnavailable("forced")` so only HTML runs. Diff the resulting dicts. Acceptable diffs: `scraped_at` timestamps, post-October-2025 sections appearing only in the API run, possibly small floating-point differences on `score_pct`. Unacceptable: any milestone `months`/`games`/`age` differing for milestones reached **before** October 2025, or `initial_rating` differing.

2. **`score_pct` correctness on a real player.** For one player, count W/D/L by hand from `/games` (or a small sample), recompute `adjusted_win_rate` at the milestone boundary, and confirm it matches the dict value. Magnus probe showed 12W/7L/31D — clean data, no `Unknown` outcomes — but verify the implementation handles `Unknown` (counted in `games`, excluded from W/D/L).

3. **Coverage probe against real data.** For every USCF ID currently in `instance/<db>.sqlite`, call `_member_detail`. Count 200 vs `ApiUnavailable`. Any 404 means that player falls back to HTML on next refresh — should be a quiet, working fallback, not a crash.

4. **Post-October-2025 visibility.** Pick a player known to have played a tournament after October 2025 (one of the user's regular players). Scrape with both backends. Confirm the API run includes the recent tournament's section and the HTML run does not — proving the migration's motivating feature.

5. **SSE progress stream.** Hit `/scrape/stream` with a fresh scrape under the API path. Confirm `progress_cb` fires `(0, N)` then `(1, N) … (N, N)` — identical event count to the HTML path so the existing progress UI doesn't need changes.

6. **HTML fallback smoke.** Temporarily monkey-patch `_member_detail` to always raise `ApiUnavailable`. Confirm a fresh scrape completes via HTML and produces a record indistinguishable from before this spec landed.

7. **No new dependencies.** `conda list` (or `pip list`) is byte-identical to before. The API path uses `requests`, already present for indirect use by `curl_cffi`.

## Future work (out of scope here)

- v2 API-key support when MUIR-gated keys become available.
- Eventual retirement of the HTML scraper if the API proves to have parity coverage for all real users. Probably a separate spec at least 6 months out.
- Aligning with Spec 005's `(source, player_id)` cache key. The API and HTML are both `source=uscf`; no schema change needed here.
