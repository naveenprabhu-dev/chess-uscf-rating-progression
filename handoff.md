# Handoff

Running notes for whoever picks this up next. For architecture/contracts read `CLAUDE.md`,
for scraping internals `docs/scraping.md`, and for the full change log `progress.md`. This file
is the short "where things stand and what just changed" summary.

## How to run
```bash
conda activate uscf-scraper            # env lives at /opt/anaconda3/envs/uscf-scraper
python run.py                          # http://localhost:5050  (:5000 is taken by macOS AirPlay)
```
If `python` isn't found, the interpreter is `/opt/anaconda3/envs/uscf-scraper/bin/python`.

## Current state (2026-05-30)
The Flask app supports: single/bulk player analysis (USCF + FIDE), milestone config per source, a
multi-player charts page, and CSV export. All four chart types render and are savable.

**Spec 007 (Phases 0 & 1) landed — the big model change:** the SQLite cache is now a **raw,
DOB/milestone-independent rating timeline** (`scrape_cache`), and the per-user milestone/age view is
**recomputed per request** by `scraper.compute_record`. The shared cache is **never shown to users** —
every list is scoped to the user's own **library** (`session["saved"]`, capped at 5 per browser) ∪
their most-recent analyzed batch (`session["recent"]`). Analyzing no longer auto-saves; **saving is a
separate ☆ button**. Phases 2–4 (Firebase login, 100-cap, anon→uid migration) are **not done** — see
`specs/007-user-auth/plan.md` and the latest `progress.md` session.

**Most recent session: see the top of `progress.md` (2026-05-30, spec 007 Phases 0 & 1).** The
2026-05-28 notes below predate the cache/library rework — treat `progress.md` + `CLAUDE.md` as
canonical where they differ.

---

## Session 2026-05-28 — bulk entry, CSV export, `/analyze` page, button restyle, download fix

Delivered in this session (web/UI layer only — `scraper/` internals, the record dict shape, and
the cache schema were NOT touched). Full detail is in `progress.md` sections 12–14.

### 1. Bulk player entry — up to 100, count chosen first
- `webapp/forms.py`: `MAX_PLAYERS_PER_REQUEST` 2 → 100; `parse_player_inputs` now scans the form for
  `uscf_id_*` / `dob_*` indices instead of assuming exactly 2 rows (kept per-row errors, dedup, and
  the "enter at least one" fallback).
- `webapp/templates/index.html`: a **"How many players?"** input (1–100, default 2) + "Set" button
  renders that many rows from a hidden `<template>`; plus "Add another player" / per-row "Remove".
  The live name/ID search autocomplete was **refactored to event delegation** so dynamically added
  rows search correctly. Verified: searching in a freshly-added row returns live USCF results.

### 2. CSV dataset export
- `webapp/cache.py`: new `list_full_records()`.
- `webapp/routes.py`: new `GET /export.csv` — `?ids=` (namespaced `source:player_id`) exports those,
  else all cached. One row per player; base columns then 4 columns per milestone
  (`m{t}_months/_games/_age/_score_pct`) over the **union** of thresholds. `None`→empty cell,
  `score_pct` kept as a raw fraction. `text/csv` attachment `chess_players.csv`.

### 3. Multi-player `/analyze` page (supersedes the 2-player `/compare` cap)
- `webapp/routes.py`: `GET /analyze` serializes **all** cached players (shared union milestone axis)
  so add/remove is fully client-side; `?ids=` marks the initial selection. `/compare` now redirects
  to `/analyze`; the scrape-done SSE redirect and the index "Compare" button both target `/analyze`
  (2-pick cap removed).
- `webapp/templates/analyze.html` (new): player-picker sidebar (checkbox per player, source filter,
  Select all/none) + the 4-chart grid. Toggling a player adds/removes its line from every chart live.
  Stable per-player HSL colors with matching legend swatches. Rich hover tooltips show the exact
  value (e.g. `Rating 700 · Hikaru Nakamura (USCF): 1`). Per-chart "Save" + "Save all charts".
- `webapp/templates/base.html`: added an "Analyze" nav link.
- Index "Previously analyzed" header gained **"Analyze all"** (all cached ids pre-selected) and a
  JS **"Select all"**.

### 4. Button restyle (user request)
- `webapp/static/styles.css`: `.link-button` is now a real **secondary button** (surface fill,
  border, rounded, no underline, hover state) instead of underlined accent text. Underlined/clickable
  text is reserved for genuine navigation only. Applies everywhere the class is used
  (Set / Add / Remove / Reset / Save / Save all / Select all / Download CSV / Analyze all / Re-scrape).
  The milestone-editor "edit" affordance also lost its underline (now a small bordered chip).

### 5. Cross-browser download fix (Safari) — extensions were missing
- **Symptom:** saved charts/CSVs landed in Downloads with no `.png`/`.csv` and wouldn't open on Safari.
- **Cause:** Safari ignores the `<a download="…">` filename when the href is a `data:` URL (charts
  used `toBase64Image()`) and is unreliable naming a direct server link. Chromium honored both, which
  is why initial verification missed it.
- **Fix:** all downloads now go through a Blob + `URL.createObjectURL` object URL (Safari honors
  `download` on `blob:` URLs). `webapp/templates/analyze.html` uses `chart.canvas.toBlob(...)` for PNGs
  and `fetch`→blob for the CSV; `webapp/templates/index.html` got the same blob interceptor for its
  CSV link. The `/export.csv` route is unchanged (server `Content-Disposition` kept for right-click-save).

### Verification done this session (Playwright MCP, Chromium)
Count control renders N rows; autocomplete works on dynamically-added rows; `/analyze` charts all
cached players; toggling adds/removes lines live; hover tooltips show exact values; Save downloads a
valid non-transparent `chart-<name>.png`; CSV links download a valid `chess_players.csv` (selected vs
all reflected correctly; comma-in-name quoting intact). **Caveat:** the Safari-specific download fix
was validated by the established blob technique + Chromium regression check, not on Safari directly —
worth a real Safari smoke test if one is available.

---

## Known follow-ups / loose ends
- `webapp/templates/compare.html` is now **unused** (`/compare` redirects to `/analyze`). Harmless;
  delete if you want to tidy up.
- Scraping is intentionally **sequential** (no parallel USCF requests). Bulk-scraping ~100 players
  will be slow; the SSE progress page handles arbitrary counts but there's no batching/throttle UI.
- "Save all charts" fires 4 downloads ~300ms apart; browsers may prompt to allow multiple downloads.
- The repo root has several stray `*.png` screenshots from earlier work (e.g. `compare-charts.png`,
  `fide-*.png`); not added by this session.

## Don'ts (from CLAUDE.md — still apply)
No Google Sheets; no Flask imports in `scraper/`; no parallel USCF requests; don't change the
`scrape_player` dict shape without updating `cache.py` + templates; keep the `games_played != 0`
guards; don't reintroduce the `2025-05-16` cutoff.
