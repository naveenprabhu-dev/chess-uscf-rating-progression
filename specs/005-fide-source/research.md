# Spec 005 — FIDE source research

Verified probe of `ratings.fide.com` for whether we can produce the same milestone insights (months / games / age / cumulative score%) we already produce for USCF. This is the *evidence* for `plan.md`; the plan references findings here rather than re-stating them.

## Verified endpoints

### 1. Rating-history JSON  (the workhorse)

```
GET https://ratings.fide.com/a_chart_data.phtml?event=<FIDE_ID>&period=
```

Returns a JSON array of every rating period the player has been in the FIDE pool since their first rated period — the entire career in one HTTP call.

Per-entry shape (verified for FIDE IDs `2016192` Nakamura and `1503014` Carlsen):

```json
{
  "date_2": "2003-Apr",
  "id_number": null,
  "rating": "2561",
  "period_games": "36",
  "rapid_rtng": null,
  "rapid_games": null,
  "blitz_rtng": null,
  "blitz_games": null,
  "name": "Nakamura, Hikaru",
  "country": "USA"
}
```

Carlsen's payload was 210 periods, ~41 KB, going back to `2003-Apr` at rating 2356.

**Caveats**

- **Rate limit (corrected 2026-05-28):** an earlier draft of this doc claimed "a second hit within ~30 s returns empty bytes." That figure was never measured — it was an assumption. A burst test on 2026-05-28 (54 requests across `a_chart_data.phtml` and `a_indv_calculation.php`, including 40 distinct-period calls back-to-back with **zero** delay) returned **zero** empty bodies and zero non-200s, avg ~0.6 s/req. No throttle is observable at the volume this scraper generates. The code keeps a single retry-on-empty as cheap insurance against a *transient* empty response, but there is no known 30-second window. (A real limit may exist at far higher/parallel volume — untested. Stay sequential and polite anyway.)
- The `period` query parameter must be present but can be empty. POST and GET both work; XHR header `X-Requested-With: XMLHttpRequest` and a `Referer: https://ratings.fide.com/profile/<id>/chart` improve reliability.
- `rating` / `period_games` come back as strings — cast to int.

### 2. Player search

```
GET https://ratings.fide.com/incl_search_l.php?search=<query>
Headers: X-Requested-With: XMLHttpRequest, Referer: https://ratings.fide.com/
```

Returns an HTML fragment (not a full page) with `<table id="table_results">`. Columns: `FIDE ID, Name, Title, Tr.T., Fed, Std., Rpd., Blz., B-Year`. Each `Name` cell anchors `/profile/<fide_id>`. Verified: "Nakamura" → 23 records, "Carlsen, M" → 2 records. (No throttle observed — see the corrected rate-limit note above.)

### 3. Profile page (already used)

```
GET https://ratings.fide.com/profile/<fide_id>
```

Holds the `B-Year` label that `scraper.core.get_fide_birth_year` already parses. Keep using as-is.

### 4. Per-period calculations — W/D/L (added 2026-05-28)

**This corrects an earlier claim in this doc that FIDE exposes no W/D/L.** It does. The `/calculations` web page renders client-side, but it fetches its data from an AJAX endpoint:

```
GET https://ratings.fide.com/a_indv_calculation.php?id_number=<FIDE_ID>&rating_period=<YYYY-MM-01>&t=0
Headers: X-Requested-With: XMLHttpRequest, Referer: https://ratings.fide.com/calculations.phtml?id_number=<id>
```

`t=0` selects standard (classical); `rating_period` is the period date as `YYYY-MM-01` (the same period the chart returns as `date_2`, e.g. `2003-Apr` → `2003-10-01`). It returns an HTML fragment with **one `<table>` per tournament** the player competed in during that period. Each table has a header row, a summary row, and per-opponent detail rows:

```
Rc    Ro                   w     n    chg  K   K*chg
2304  2356                 2.5   4    ...              <- summary row (Rc = avg opp rating)
Opponent, Name  m   2418 NOR  1.00  1   ...           <- per-opponent detail row
```

- `w` = score (wins + 0.5·draws), `n` = games. Summing `w`/`n` across the period's tournament tables yields that period's total score and games. Detail rows even give per-opponent results (1.00 win / 0.50 draw / 0.00 loss) if exact W/D/L counts are ever wanted.
- Parse only the summary rows (first cell is an integer rating) to avoid double-counting the detail rows.
- Verified for Carlsen 1503014: Oct-2003 period summed to 24.0 / 36 = 66.7%; full-career cumulative scrape reaches 2700 at 60.8% in ~12 s (early-exits once the top milestone is filled).

This is what `scraper.fide.get_fide_calculations` consumes to compute a real `score_pct` for FIDE — one call per rated period the player competed in (capped by an early exit once all milestones are reached).

## What's NOT available

- **Pre-rated history** — FIDE only starts tracking once a player has a published rating. Carlsen's first entry is already `2356` with 19 games (those 19 games happened *before* he was rated). `initial_rating` for FIDE therefore semantically means "first published rating", not "rating after first event ever". USCF doesn't have this gap.

## Period cadence over time

From Carlsen's series (`per-year` count of entries):

```
2003-2008 : 3-4   (quarterly era)
2009-2011 : 5-6
2012      : 9
2013-now  : 12    (monthly)
```

So "months to milestone" is bucketed to ±3 months in the quarterly era. Document on the player page when source = FIDE.

## Rating floor

FIDE has had floors (1000, later 1400 in March 2024). The current USCF default ladder of `[400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200]` is wrong for FIDE — most slots would always be unreachable. The plan defines a separate `DEFAULT_FIDE_MILESTONES` starting at 1400.

## Coverage summary

| Milestone column | Source       | FIDE? |
| ---------------- | ------------ | ----- |
| `months`         | period dates | ✓     |
| `games`          | cumulative `period_games` | ✓ |
| `age`            | `B-Year` (year-only) | ✓ |
| `score_pct`      | per-period W/D/L via `a_indv_calculation.php` | ✓ (corrected — see endpoint #4) |

All four columns. The earlier conclusion that FIDE could only fill 3 of 4 (and the resulting Score-chart "blur + overlay" treatment) was based on the now-corrected assumption that W/D/L was unavailable. FIDE records now carry real `score_pct`, and the Score chart renders normally for all-FIDE comparisons.

## Reproducing the probes

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ..."

# Full rating history JSON (one call returns the whole career)
curl -sL -A "$UA" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Referer: https://ratings.fide.com/profile/1503014/chart" \
  "https://ratings.fide.com/a_chart_data.phtml?event=1503014&period="

# Per-period W/D/L (one call per rated period; powers score_pct)
curl -sL -A "$UA" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Referer: https://ratings.fide.com/calculations.phtml?id_number=1503014" \
  "https://ratings.fide.com/a_indv_calculation.php?id_number=1503014&rating_period=2003-10-01&t=0"

# Player search (HTML fragment)
curl -sL -A "$UA" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Referer: https://ratings.fide.com/" \
  "https://ratings.fide.com/incl_search_l.php?search=Carlsen,%20M"

# Profile (for B-Year)
curl -sL -A "$UA" "https://ratings.fide.com/profile/1503014"
```
