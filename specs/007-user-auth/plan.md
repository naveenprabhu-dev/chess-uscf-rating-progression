# Spec 007 — User accounts & saved-analysis limits (Firebase Auth + SQLite)

> Status: **plan / not started.** This document is a learning-oriented design doc as much as
> an implementation plan — the requester wanted to understand how the current "cache" actually
> works, how much space it really uses, and what the trade-offs are between authentication
> approaches before committing to one. Primary auth direction chosen: **Firebase Authentication
> for identity only; all application data stays in SQLite.**

## Context & the goal in one line

Let anonymous visitors save a small number of player analyses (proposed: **5**), then prompt them
to sign in to save more (proposed cap: **100** total). No accounts exist today.

Before designing that, we have to correct a mental-model mismatch: **there is no browser-side
cache of player data in this app.** Understanding what is actually stored, and where, changes
what the 5/100 limit is even *for*. Part 1 and Part 2 below exist to make that explicit; the
implementation plan starts at Part 3.

---

## Part 1 — How the "cache" actually works today

There are exactly two places this app keeps state. Neither is a "browser cache" in the sense of
the browser deciding to evict things when it runs low on space.

### 1a. The server-side SQLite database (where player data lives)

All scraped player analyses live in **one SQLite file on the server**: `instance/cache.sqlite3`
(`webapp/cache.py`). The instance folder is gitignored. The schema is a single table:

```sql
CREATE TABLE players (
    source     TEXT NOT NULL,   -- "uscf" | "fide"
    player_id  TEXT NOT NULL,
    name       TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    data       TEXT NOT NULL,    -- the full record dict, JSON-encoded
    PRIMARY KEY (source, player_id)
);
```

Two consequences that matter a lot for this feature:

1. **It is server-side, not in anyone's browser.** When a user analyzes a player, the result is
   written to a file *on the machine running Flask*. Closing the browser, clearing browser data,
   or switching devices does **not** affect it. The browser only ever receives rendered HTML.

2. **It is global and shared across every visitor.** The cache is keyed on `(source, player_id)`
   with **no user/owner column**. If visitor A analyzes Magnus Carlsen, visitor B sees Magnus in
   the "previously analyzed" list too. There is currently **no concept of "my players" vs "your
   players."** This is the single biggest gap between today's design and "5 saved per user."

> **Two things this spec changes about the table above.** (a) The `data` blob today is a
> **computed** record — the analyzer's DOB and milestone ladder are baked into it (`scrape_player`
> & friends weave both directly into what they return and cache). (b) The cache is **visible to
> every visitor**. This spec overturns both: the cache becomes a **raw, DOB/milestone-independent
> rating timeline** that is **invisible to users** (backend infrastructure only), with the
> milestone/age view recomputed per request. See Part 2c, Part 3/3b, and Part 5a for the new shape.

### 1b. The Flask session cookie (where settings live)

The only thing stored *in the browser* is the **Flask session cookie** — a small,
cryptographically *signed* (not encrypted) cookie set in `webapp/__init__.py` via `SECRET_KEY`.
Today it holds only lightweight settings (`webapp/routes.py`):

- `session["source"]` — the active rating body ("uscf" / "fide")
- `session["milestones_uscf"]`, `session["milestones_fide"]` — the custom milestone ladders
- `session["pending_scrape"]` — a transient hand-off between `/scrape` and the SSE stream

That's it. No player data, no identity, nothing security-sensitive. "Signed" means the server can
detect tampering, but anyone can *read* the contents — so it must never hold secrets.

### 1c. So what does "save an analysis" mean today?

Right now, **"analyze a player" and "save a player" are the same action** — analyzing writes to
the shared `players` table and it stays there until someone deletes it
(`POST /player/<source>/<player_id>/delete`). There is no per-user list to cap. Building the 5/100
feature therefore means *introducing the concept of ownership*, not just adding a counter.

---

## Part 2 — How much space is actually used (the size question, answered)

The worry was "several hundred players may be too much." Let's put real numbers on it, because the
answer reframes the whole feature.

### 2a. Size of one analysis record

Each record is a JSON blob: name, DOB, source IDs, first-tournament data, and a `milestones` map.
USCF has up to 27 thresholds (400–3000), each with 4 small numbers, plus a dozen metadata fields.
Measured shape is roughly **2–6 KB per player**, call it ~5 KB worst case.

| Players stored | Approx. SQLite size |
|---------------:|--------------------:|
| 5              | ~25 KB              |
| 100            | ~0.5 MB             |
| 1,000          | ~5 MB               |
| 10,000         | ~50 MB              |

SQLite comfortably handles databases into the **hundreds of GB**. So "several hundred players" is
~1–2 MB — utterly trivial for the server. **Disk space is not, and will never be, the real
constraint here.**

### 2b. The browser cookie *does* have a hard limit — but it's not where the data is

A signed Flask session cookie must stay under the **~4 KB** per-cookie browser limit. We are
nowhere near it (a few short strings). Even the *anonymous* saved list we propose — up to 5 ids
like `"uscf:12345678"` — is well under 100 bytes. So the cookie limit only matters as a rule:
**never put bulk player data in the session; keep it to ids and settings.**

### 2c. So what is the 5/100 limit really for?

Since neither disk nor cookie space is the bottleneck, the limit is a **product / abuse-control**
decision, and it's worth being honest about that in the UI copy rather than implying "your browser
is full." The genuine reasons to cap saves:

1. **Scraping politeness / abuse control (the real cost).** Every *new* analysis hits
   uschess.org or ratings.fide.com. The project rule is explicitly "don't get IP-banned"
   (`CLAUDE.md`). Unbounded anonymous scraping is the actual risk — not storage. Note: the cap
   should ideally limit *scrapes*, and cached re-saves are nearly free.
2. **A reason to sign up.** 5 free → login is a classic, fair conversion nudge.
3. **Keeping each user's library navigable.** 100 is a UX ceiling, not a disk ceiling.

This reframing also drives the cache redesign in Part 3b: because the scrape cache holds a **raw,
DOB/milestone-independent rating timeline**, re-analyzing an already-cached player costs **zero
network** — the milestone/age view is recomputed instantly for *this* requester's own DOB and
ladder. So the limit applies to "analyses you've added to *your* library," while the shared
timeline cache silently de-duplicates the expensive, IP-ban-risky scrape work underneath — without
ever being shown to users (each user's experience still feels first-time; see Part 3b).

---

## Part 3 — What "5 free, then login, cap 100" actually requires

The key design move is to **separate two concepts that are currently fused**:

| Concept | What it is | Scope | Table |
|---|---|---|---|
| **Scrape cache** | Expensive **raw rating timeline**, de-duplicated so we don't re-hit USCF/FIDE | **Global / shared raw timeline, invisible to users** (changed — see Part 3b) | `scrape_cache` (repurposed from `players`) |
| **Saved library** | "Which analyses has *this* user chosen to keep?" + their DOB & ladder | **Per-owner**, this is what we cap | `saved_analyses` (new) |

`saved_analyses` is a thin **join table** — it points at rows in the shared `scrape_cache` rather
than duplicating the timeline, and carries the per-user view parameters (DOB, milestone ladder).
The 5/100 limit counts an owner's rows in this table.

```sql
CREATE TABLE saved_analyses (
    owner_id            TEXT NOT NULL,   -- a user uid, OR an anonymous cookie id (see 3a)
    source              TEXT NOT NULL,
    player_id           TEXT NOT NULL,
    dob                 TEXT,            -- this owner's DOB for the player ("MM/DD/YYYY" or NULL)
    milestones_snapshot TEXT,           -- JSON: the ladder this owner used (NULL = source default)
    saved_at            TEXT NOT NULL,
    PRIMARY KEY (owner_id, source, player_id),
    FOREIGN KEY (source, player_id) REFERENCES scrape_cache(source, player_id)
);
```

### 3b. Raw cache + per-request views (the IP-ban fix)

The single most important correction to the original design: **the scrape cache must store a raw,
DOB- and milestone-independent rating timeline, not a computed record.** Today's `players.data`
bakes the analyzer's DOB and ladder into the cached blob, so a second user with a *different* DOB
or ladder can't reuse it — the app would re-scrape, re-hitting uschess.org / ratings.fide.com (the
exact thing `CLAUDE.md` forbids). Caching a raw timeline and recomputing per request is the only
design that gives "**near-instant results that differ only by birthdate or milestones**."

**Cached timeline shape** (DOB/milestone-independent — nothing derived from either lives here):

```
{
  "source", "player_id", "name", "country",
  "first_tournament_date", "initial_rating",
  "fide_birth_year",            # source-derived DOB fallback — player-specific, so cacheable
  "events": [                   # chronological
    { "date": "YYYY-MM-DD",
      "cumulative_games": int,
      "rating": int,            # post-event rating
      "score_numerator": float, # wins + 0.5*draws contributing to score_pct
      "score_games": int|null },# games toward score_pct (null when unavailable, e.g. FIDE calc fetch failed)
    ...
  ],
  "scraped_at": ISO-8601
}
```

**The fetch/compute split.** Each scraper is refactored into two pieces:

- `fetch_history(...) -> timeline` — all network/parsing, produces the timeline above. **Cacheable.**
- `compute_record(timeline, dob, milestones) -> <existing public dict>` — a **pure** function: it
  walks `events`, applies *this* user's DOB to the `age_*` fields and *this* user's ladder to the
  milestone map, reusing the existing `calculate_age` / `months_difference` helpers. No network.
  FIDE `score_pct` still degrades to `None` per-cell when an event's `score_games` is null.

The existing public entry points (`scrape_player` / `scrape_player_api` / `scrape_fide_player`)
stay as thin wrappers — `compute_record(fetch_history(...), dob, milestones)` — so the public dict
shape in `CLAUDE.md` is unchanged. The **fast USCF path already works this way**
(`scraper/uscf_api.py` fetches everything before the pure `_compute_milestones`); the **USCF HTML
fallback** (`scraper/core.py`) and **FIDE** (`scraper/fide.py`) interleave network calls inside the
milestone loop and need de-interleaving so all fetching finishes before the pure math. The
`games_played != 0` divide-by-zero guards move into `compute_record`.

**Invisible by construction.** The timeline cache is pure backend infrastructure. A cache hit just
means the network step is skipped silently — the user still goes through the analyze flow, gets
their own DOB/ladder-specific result, and it lands in *their* library. They never learn anyone else
analyzed the player. See Part 5c for how reads are scoped per-owner.

### 3a. Who is the "owner" before login?

Two clean options for the anonymous (pre-login) 5-save bucket:

- **Option A — store the ≤5 ids directly in the session cookie.** No DB rows for anon users at
  all; `session["saved"] = ["uscf:123", ...]`. Simplest, honest ("your saves live in this
  browser until you sign in"), and maps to the user's original intuition. Downside: cleared if
  the user clears cookies; not shared across devices (which is fine — that's the *point* of
  signing in).
- **Option B — mint an anonymous `owner_id` (a random UUID) in the cookie, store rows in
  `saved_analyses` with that id.** One code path for anon + logged-in. Slightly more plumbing;
  needs periodic cleanup of abandoned anon rows.

**Recommendation: Option A for anon, real rows for logged-in.** It keeps anonymous users
zero-cost on the server and makes the "this lives in your browser, sign in to keep it forever"
story truthful. On first login we **migrate** the cookie list into `saved_analyses` under the
user's uid (Part 5d).

---

## Part 4 — Authentication options & trade-offs

The requester wants to learn something and is open to Firebase. All four options below can keep
application data in SQLite — the choice is really *who manages identity*.

### Option 1 — Firebase Authentication, identity only ✅ (chosen primary)

Firebase handles signup, login, password reset emails, email verification, and "Sign in with
Google" in the browser. Login happens client-side via the Firebase JS SDK; it returns an **ID
token (a signed JWT)**. The browser sends that token to Flask; Flask verifies it with the
`firebase-admin` SDK and extracts a stable `uid`. **Firebase is used for nothing but identity** —
users, saved_analyses, and the player cache all stay in SQLite.

- **Pros:** You never store or hash passwords. Google runs the reset/verification/social-login
  flows you'd otherwise have to build and secure. Generous free tier. Teaches modern token-based
  auth (JWT, OAuth, managed identity) — the stated learning goal. No data migration; SQLite stays
  the source of truth.
- **Cons:** External dependency / Google project required. Firebase Auth is designed for
  JS/SPA frontends, so you bolt a little client-side JS onto the server-rendered Jinja pages
  (a real but small impedance mismatch). One more moving part to reason about (token issued in
  browser → verified in Flask). Local/offline dev needs the Firebase web config.
- **Learning value:** High — this is how a large share of modern apps do auth.

### Option 2 — Roll your own with Flask-Login + werkzeug hashing

Email+password stored in a SQLite `users` table, hashed with `werkzeug.security`
(`generate_password_hash`/`check_password_hash`), sessions managed by Flask-Login.

- **Pros:** Zero external services, everything in one SQLite file, full control, and it teaches
  what auth *actually is* under the hood (hashing, salts, session management, CSRF). Free, no
  lock-in, integrates seamlessly with the existing stdlib-`sqlite3` design.
- **Cons:** **You own the security surface** — password-reset email flow, email verification,
  rate-limiting/lockout, and choosing/maintaining hashing parameters. Sending email needs an SMTP
  provider. Most code of any option, and the easiest to get subtly wrong.
- **Learning value:** Highest for *fundamentals*; lowest for *modern managed practice*.

### Option 3 — "Sign in with Google" only, via Authlib (OAuth)

No passwords at all. Authlib runs the Google OAuth flow inside Flask; you store the returned
Google profile as a `users` row.

- **Pros:** No password storage, no reset emails, minimal deps, data stays in SQLite, teaches the
  OAuth flow directly. Very little code.
- **Cons:** Requires every user to have a Google account. Needs a Google Cloud OAuth consent
  screen setup. No email/password option for users who want one.
- **Learning value:** Good for OAuth specifically.

### Option 4 — Hosted auth+DB platforms (Supabase / Clerk / Auth0)

Drop-in managed auth, sometimes bundled with a database.

- **Supabase** is notable because it's **Postgres-based** — if you ever outgrow SQLite, it gives
  you auth *and* a relational DB that matches your current model far better than Firestore's
  document model would.
- **Clerk / Auth0** are extremely polished drop-ins with generous-ish free tiers.
- **Cons:** Vendor dependency; you learn *their console* more than the underlying mechanics;
  potential cost as you grow. Overkill for current scale.

### Database-structure trade-off (why we are NOT using Firestore)

Going "all-in" on Firebase would mean putting saved_analyses (and maybe the scrape cache) in
**Firestore**, Firebase's NoSQL document store.

- **Against, for now:** It's a big departure from the relational `(source, player_id)` model the
  whole app is built on; you'd run two data stores (scrape cache in SQLite, user data in
  Firestore) or migrate everything to a document model; more lock-in; the real-time/scale benefits
  are irrelevant at this size.
- **Verdict:** Use Firebase **only for auth**. Keep one relational store (SQLite). If scale
  ever forces a managed DB, prefer Postgres/Supabase over Firestore so the relational model
  survives. This matches the project rule against bolting on heavyweight infra prematurely.

---

## Part 5 — Recommended architecture (chosen)

**Firebase Authentication (identity only) + everything else in SQLite.**

### 5a. SQLite schema changes

Two changes in `webapp/cache.py`: **repurpose `players` into a raw `scrape_cache`** (Part 3b), and
**add `users` + `saved_analyses`**.

```sql
-- Repurposed from `players`: now a RAW, DOB/milestone-independent timeline (Part 3b).
CREATE TABLE scrape_cache (
    source     TEXT NOT NULL,
    player_id  TEXT NOT NULL,
    name       TEXT NOT NULL,       -- denormalized for cheap listing
    scraped_at TEXT NOT NULL,
    timeline   TEXT NOT NULL,       -- the timeline JSON from Part 3b; NO dob, NO milestones_config
    PRIMARY KEY (source, player_id)
);

CREATE TABLE users (
    uid        TEXT PRIMARY KEY,     -- Firebase uid
    email      TEXT,
    created_at TEXT NOT NULL,
    last_seen  TEXT
);

CREATE TABLE saved_analyses (
    owner_id            TEXT NOT NULL,   -- == users.uid for logged-in owners (or anon cookie id)
    source              TEXT NOT NULL,
    player_id           TEXT NOT NULL,
    dob                 TEXT,            -- this owner's DOB for the player ("MM/DD/YYYY" or NULL)
    milestones_snapshot TEXT,           -- JSON ladder this owner used (NULL = source default)
    saved_at            TEXT NOT NULL,
    PRIMARY KEY (owner_id, source, player_id),
    FOREIGN KEY (source, player_id) REFERENCES scrape_cache(source, player_id)
);
```

**Migration.** Unlike the original plan, this is *not* purely additive: the old `players.data`
stored a **computed** record that cannot be losslessly converted to a raw timeline. So `init_db`
should detect the legacy `players` shape and **drop + recreate** the cache (then create the new
tables), flashing a one-time "Cache was reset to support per-user analysis" notice. The DB is
gitignored in `instance/`, so the reset is safe — this reuses the exact precedent already in
`init_db` for the earlier FIDE-source schema change. `users` and `saved_analyses` are then created
fresh. Anonymous saves stay in the session cookie (Part 3a, Option A) and never touch these tables
until login.

### 5b. Auth flow (request lifecycle)

```
1. Browser loads a page. Firebase JS SDK renders login/signup UI (or "Sign in with Google").
2. User authenticates -> Firebase returns an ID token (JWT) in the browser.
3. Browser POSTs the token to a new Flask route: POST /auth/session
4. Flask verifies it with firebase-admin (verify_id_token) -> gets uid + email.
5. Flask upserts the users row, stores uid in the (server-trusted) Flask session:
   session["uid"] = uid
6. Subsequent requests read session["uid"] to know who the owner is.
7. POST /auth/logout clears session["uid"].
```

Note we still rely on the existing **Flask session cookie** to remember the logged-in uid
server-side after the initial token verification — we verify the Firebase token once and then
issue our own session, rather than verifying a JWT on every request. (Re-verify/refresh policy is
an Open Question.)

### 5c. Save / limit enforcement

A single helper, e.g. `cache.save_for_owner(owner, source, player_id)`:

- **Anonymous** (`session["uid"]` absent): the owner bucket is `session["saved"]` (a list).
  Enforce `len < 5`. If full, return a "limit reached — sign in to save up to 100" signal that the
  route turns into a login prompt.
- **Logged in:** count rows in `saved_analyses WHERE owner_id = uid`. Enforce `< 100`.

Reads ("my library") come from `saved_analyses` joined to `scrape_cache` for logged-in users, or
from the session list joined to `scrape_cache` for anonymous users. For each saved row, the
displayed record is `compute_record(timeline, row.dob, row.milestones_snapshot)` (Part 3b) — so two
users who saved the same player each see *their own* DOB- and ladder-specific result, recomputed
instantly from the one shared timeline.

**There is no global "previously analyzed" view.** The shared `scrape_cache` is never rendered to
users (Part 3b); every list a user sees is scoped to their own library. This is the resolution of
the old Open Question 2 — `index` and `/analyze` read `list_for_owner(...)`, never the global
`list_players()` / `list_full_records()`.

### 5d. Anonymous → logged-in migration

On first successful login, copy the up-to-5 saves from `session["saved"]` into `saved_analyses`
under the new uid — carrying each save's `dob` and `milestones_snapshot` so the migrated rows
recompute identically (dedupe against anything already there, respect the 100 cap) — then clear
`session["saved"]`. (So the anon bucket stores not just ids but `{source, player_id, dob,
milestones_snapshot}` per save.) This is the "sign in to keep your saves forever" payoff and must
be in the same code path as `POST /auth/session`.

### 5e. New / changed files

```
scraper/             # CHANGED — fetch/compute split (Part 3b), but public dict shape unchanged
  uscf_api.py        # expose fetch_history()/compute_record(); already fetch-then-pure-compute
  core.py            # de-interleave: move per-tournament fetch (core.py:292) out of the loop;
                     #       move games_played != 0 guard (core.py:299) into compute_record
  fide.py            # de-interleave: move per-period calc fetch (fide.py:293) out of the loop;
                     #       move guard (fide.py:305) into compute_record
webapp/
  auth.py            # NEW — firebase-admin init, verify token, /auth/session, /auth/logout,
                     #       login_required-style helper, current_owner() resolver
  cache.py           # scrape_cache (raw timeline) + users + saved_analyses; save_for_owner /
                     #       list_for_owner / unsave_for_owner / count_for_owner; migrate_anon_saves
  routes.py          # scope index & /analyze to list_for_owner (never the global list);
                     #       gate "save" through save_for_owner; turn limit-reached into a login prompt
  templates/
    base.html        # nav: login/logout state, Firebase JS SDK include
    login.html       # NEW — Firebase login/signup widget
    library.html     # NEW (or fold into index) — the per-user saved list
  static/auth.js     # NEW — Firebase web SDK init + post token to /auth/session
config.py            # ANON_SAVE_LIMIT = 5; USER_SAVE_LIMIT = 100
environment.yml      # add firebase-admin
```

**Correction to the earlier draft:** `scraper/` is *not* untouched — Part 3b's fetch/compute split
changes it. But the change stays **framework-agnostic** (no Flask import in `scraper/`) and keeps
the public dict shape identical (the public functions become thin wrappers), so the `CLAUDE.md`
contract holds. Auth itself remains a webapp-only concern.

---

## Part 6 — Phased implementation plan

Each phase is independently shippable and testable.

- **Phase 0 — Raw cache + fetch/compute split + invisible cache (no auth yet).** Refactor each
  scraper into `fetch_history()` + pure `compute_record()` (Part 3b); repurpose `players` into the
  raw `scrape_cache` table (Part 5a migration); recompute the per-request view from the cached
  timeline; and scope every list the user sees to themselves (no global "previously analyzed"
  view). This is valuable on its own — it delivers IP-ban protection and instant re-analysis, and
  makes analyzing always feel first-time — and it's a **prerequisite** for the per-user-view story
  the auth phases build on. Ship and verify this before any Firebase work.
- **Phase 1 — Ownership split (still no auth).** Introduce `saved_analyses` and the
  anon-session-list bucket. Make "save" a distinct action from "analyze," enforce the **5-save
  anon limit** purely in the session cookie. This delivers the limit behavior and proves the
  data model *before* any Firebase work. Decide here whether analyzing auto-saves or save is a
  separate button (Open Questions).
- **Phase 2 — Firebase Auth wiring.** Create the Firebase project; add `firebase-admin`;
  build `auth.py`, `/auth/session`, `/auth/logout`, `static/auth.js`, `login.html`; store uid in
  the Flask session. No limit changes yet — just "you can log in and we know who you are."
- **Phase 3 — Per-user library + 100 cap + migration.** Add `users`/`saved_analyses` writes for
  logged-in owners, the 100-cap enforcement, the migration of anon saves on first login, and the
  "my library" view. Turn the 5-save wall into a login prompt.
- **Phase 4 — Polish.** Limit-reached UX copy (honest framing per Part 2c), logout everywhere,
  "remove from library," empty states, and basic abuse throttling on the scrape endpoint
  (the actual cost center).

---

## Part 7 — Edge cases & details to get right

- **Token verification failure / expiry:** `/auth/session` must fail closed (reject, stay
  anonymous) — never trust an unverified token.
- **The session cookie is signed, not encrypted:** fine for `uid` (not a secret) and the anon
  saved-id list. Never store Firebase secrets or service-account keys there.
- **Service account key:** `firebase-admin` needs a service-account JSON. It must be gitignored
  (`.gitignore` already ignores `path_to_service_account.json` and `instance/`) and loaded from an
  env var / path, never committed.
- **Shared timeline vs per-user view:** because `scrape_cache` holds one raw timeline per player,
  two users "saving" the same player share one cached scrape but get independent `saved_analyses`
  rows — each recomputed with their own DOB and ladder (Part 3b/5c). Deleting from your library
  removes the `saved_analyses` row, **not** the shared `scrape_cache` row (unless no one references
  it — optional GC later). The shared timeline is never shown directly to any user.
- **Limit counts saves, not scrapes:** re-analyzing an already-cached player is **network-free**
  (recomputed from the timeline), so only a brand-new player triggers a scrape. The cap should
  ideally throttle **new scrapes** — cached recomputes are free and shouldn't feel rationed.
  Consider counting both against the cap for simplicity in v1, and revisit (Open Questions).
- **DOB/milestones live with the save, not the cache:** a user's DOB and ladder are stored on their
  `saved_analyses` row, never in `scrape_cache`. Changing milestones in `/settings` re-derives the
  view from the cached timeline with **no re-scrape** — it does not invalidate the shared cache.
- **DOB privacy:** records store DOB. With real user accounts this becomes mildly more sensitive
  than in a single-user tool — worth a privacy note in the UI, though it's player data the user
  supplied, not account data.

---

## Part 8 — Cost & scale outlook

At the assumed "not a ton of users at first" scale: Firebase Auth free tier is far more than
enough, SQLite on a single server is far more than enough, and there is no recurring cost beyond
hosting. The first thing that would actually break under growth is **not storage** but (a) the
single-server SQLite write model under real concurrency and (b) scraping volume against
USCF/FIDE. Revisit a managed Postgres (e.g. Supabase) only if/when concurrent writes or
multi-instance hosting become real — at which point the relational model ports cleanly because we
never went NoSQL.

---

## Open questions (resolve during Phase 1)

1. **Auto-save vs explicit save?** Today analyzing == saving. Should the new "save" be a separate
   button (so users can analyze freely and only the *saved* ones count toward 5/100), or should
   analyzing still auto-save (simpler, but the limit then caps analyses directly)? This changes the
   UX and what the cap means.
2. **~~Does the global "previously analyzed" list stay visible?~~ RESOLVED — no.** The shared
   `scrape_cache` is invisible backend infrastructure (Part 3b/5c); every list a user sees is
   scoped to their own library, and analyzing always feels first-time. No "recently analyzed by
   anyone" section.
3. **Count cap by saves or by scrapes?** See Part 7. With the raw timeline cache, re-analyzing a
   cached player is network-free, so the *scrape* cost only applies to genuinely new players — which
   strengthens the case for throttling **new scrapes** specifically. Simplest v1 is still "saves."
4. **Re-verify Firebase token cadence?** Verify once and ride the Flask session (simpler), or
   re-verify/refresh periodically (more robust to revoked accounts)? Default: verify once per
   login; document the trade-off.
5. **Exact limit numbers.** 5 / 100 are placeholders from the request — easy to tune via
   `config.py`.
