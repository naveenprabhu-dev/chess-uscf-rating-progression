# Spec 006 — Research

Endpoint probes against `https://ratings-api.uschess.org` on 2026-05-21, unauthenticated. The OpenAPI spec lives at `/swagger/v1/swagger.json`; the Swagger UI page renders client-side and is useless to non-browser fetchers — go straight to the JSON.

## Authentication

None required for v1. US Chess's published notice describes a future v2 that will require API keys via MUIR login. v1 surface is what this spec uses.

## Coverage caveat

Of 9 probed 8-digit IDs, 5 returned 200 and 4 returned 404 (13783281, 14102461, 30000001, 16000000). **A 404 here does not prove a coverage gap** — these IDs may simply never have been assigned to real members. A real coverage probe needs IDs known to exist on MSA; the verification step in `plan.md` runs this against the user's actual cached IDs.

## GET /api/v1/members/{id}

200 for indexed players, 404 otherwise. Sample (USCF 15218438, Magnus):

```json
{
  "fideId": "1503014",
  "gender": "Male",
  "ratings": [
    {"rating":2914,"ratingSystem":"R","isProvisional":false,"floor":2100},
    {"rating":2881,"ratingSystem":"Q","isProvisional":false,"floor":2100},
    ...
  ],
  "id":"15218438","firstName":"MAGNUS","lastName":"CARLSEN",
  "fideCountry":"USA","status":"None"
}
```

Fields used by this spec: `firstName`, `lastName`. The `ratings[]` ladder is NOT used — per-section `postRating` is the chronologically accurate source.

## GET /api/v1/members/{id}/sections?RatingSource=R

Paginated via `Offset` / `Size`. Sort order across pages is not guaranteed by the API — sort client-side by `startDate` ascending. Sample item (USCF 12910880):

```json
{
  "id": "01K8M1GWWDTM8TGVM1H4PWN2CX",
  "sectionNumber": 1,
  "startDate": "2003-06-07",
  "endDate": "2003-06-07",
  "format": "Swiss",
  "ratingSystem": "R",
  "ratingRecords": [
    {"eventId":"01K8M1GWWDYA6WH1MHMDSHXZ4V","sectionNumber":1,
     "postRating":100,"postRatingDecimal":0,"ratingSource":"R","postProvisionalGameCount":4}
  ],
  "event": {"id":"200306078330","name":"SPRING 2003 SO CAL CHALLENGE C",
            "startDate":"2003-06-07","endDate":"2003-06-07","stateCode":"CA"}
}
```

The `ratingSystem == "R"` filter cleanly replaces the HTML scraper's `"=>" in classical_td_text and "ONL" not in classical_td_text` check. Online classical lives under `OR`, not `R`. Distinct `ratingSystem` enum values: `R`, `Q`, `B` (offline regular/quick/blitz) and `OR`, `OQ`, `OB` (online variants).

The `postRating` field is plain int — no `P12` provisional suffix to strip, unlike the HTML page text. The provisional state is exposed separately via `isProvisional` in `/members/{id}` and via `postProvisionalGameCount` here.

## GET /api/v1/members/{id}/games?RatingSource=R

Paginated. Each item is one game from the player's perspective:

```json
{
  "section": {"id":"...","number":1,"name":"ROUND ROBIN"},
  "event": {"id":"202209118352","name":"2022 GCT SINQUEFIELD CUP",
            "startDate":"2022-09-02","endDate":"2022-09-11","stateCode":"MO"},
  "ratingSystem": "R",
  "player": {"color":"White","outcome":"Win"},
  "opponent": {"id":"13468661","firstName":"IAN","lastName":"NEPOMNIACHTCHI",
               "color":"Black","outcome":"Loss"}
}
```

Outcomes observed: `Win`, `Loss`, `Draw`, `Unknown`. Magnus's `/games?Size=50` returned 12 Win / 7 Loss / 31 Draw / 0 Unknown — clean data. To reconstruct per-section counts for `score_pct`, group items by `(event.id, section.number)`.

One reliability note: an early probe of USCF 12910880 returned `HTTP 500` on `/games`, then later 200 on retry. The `_get` helper's "single retry on connection errors / 5xx" handles this. A second 500 escalates to `ApiUnavailable` and falls back to HTML.

## GET /api/v1/rated-events

Used here only to verify the API has post-October-2025 data that MSA dropped:

```
GET /api/v1/rated-events?FromDate=2025-11-01&SortBy=startDate&Dir=desc&Size=5
```

Returned events through `startDate: 2026-05-20`. Confirmed: the API is current; the HTML pages are not.

## Performance

| Player profile | HTML scraper | API |
|---|---|---|
| 50-tournament player | 1 list page + 50 detail pages + Cloudflare retries + 0.35s/page throttle ≈ 20-40s | 1 member + 1 sections page + 1-3 games pages ≈ 2-3s |
| 1000-tournament veteran | 20 list pages + 1000 detail pages (Cloudflare gate fires) ≈ many minutes | 1 + ~5 sections pages + ~30 games pages ≈ 15-30s |

Speedup is at least 10× for typical players, larger for active ones. Plus no Cloudflare gate — the JSON API is plain HTTPS with no bot-detection layer observed.

## Endpoints intentionally NOT used

- `/api/v1/members/{id}/events` — returns the events list but without per-section `postRating`. The `/sections` endpoint is strictly richer for our purpose.
- `/api/v1/members/{id}/rating-supplements` — official monthly snapshots; not needed when we have per-tournament ratings.
- `/api/v1/rated-events/{eventId}` — would let us fetch full tournament data, but per-player `/games` is more efficient for our use case (we need only the user's own games).
