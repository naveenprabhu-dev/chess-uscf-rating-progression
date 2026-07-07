import csv
import io
import json
import queue
import threading

from flask import (
    Blueprint, Response, abort, current_app, flash, jsonify, redirect,
    render_template, request, session, stream_with_context, url_for,
)

from config import ANON_SAVE_LIMIT, CACHE_TTL_DAYS, USCF_INCLUDE_FOREIGN
from scraper import (
    DEFAULT_USCF_MILESTONES, DEFAULT_FIDE_MILESTONES,
    make_session, compute_record, fetch_history, fetch_fide_history,
    search_players, search_fide_players,
)
from scraper.core import NoClassicalTournaments, PlayerNotFound
from scraper.fide import (
    FIDE_TIMELINE_VERSION, FideNoRatedHistory, FidePlayerNotFound, FideScrapeError,
)
from scraper.uscf_api import TIMELINE_API_VERSION

from .cache import (
    get_timeline, is_timeline_stale, save_timeline,
    SqliteCrosstableCache, SqliteFideArchiveCache, SqliteOlimpbaseCache,
)
from .forms import normalize_source, parse_milestones, parse_player_inputs
from .presets import FEATURED_FIDE, FEATURED_USCF, PHOTO_CREDITS

bp = Blueprint("main", __name__)

VALID_SOURCES = ("uscf", "fide")

# Max players charted at once. The picker still lists everyone, but only this
# many lines are drawn so the charts stay legible (100 overlapping lines are
# unreadable). Enforced server-side on the initial selection and client-side
# on toggles in analyze.html.
ANALYZE_CHART_LIMIT = 5


def _milestone_session_key(source):
    return "milestones_fide" if source == "fide" else "milestones_uscf"


def _default_milestones(source):
    return DEFAULT_FIDE_MILESTONES if source == "fide" else DEFAULT_USCF_MILESTONES


def _active_milestones(source):
    return list(session.get(_milestone_session_key(source)) or _default_milestones(source))


# ── Per-user library (anonymous: lives in the signed session cookie) ────────────
# Spec 007 Part 3a Option A / Part 5c. `saved` is the user's capped library;
# `recent` is the most-recently analyzed batch (so just-scraped players can be
# viewed and then saved). Each entry carries the view params {dob, milestones}
# so compute_record re-derives that user's own milestone/age view from the one
# shared raw timeline. The shared scrape_cache is never listed globally.

def _saved():
    return session.get("saved") or []


def _recent():
    return session.get("recent") or []


def _same(entry, source, player_id):
    return entry["source"] == source and str(entry["player_id"]) == str(player_id)


def _is_saved(source, player_id):
    return any(_same(e, source, player_id) for e in _saved())


def _entry_for(source, player_id):
    """This player's view params (dob, milestones), preferring the saved library
    over the recent batch. Returns the entry dict or None."""
    for lst in (_saved(), _recent()):
        for e in lst:
            if _same(e, source, player_id):
                return e
    return None


def _record_for(source, player_id, entry=None):
    """Compute the public record for a player from the cached timeline + this
    user's view params. Returns None if the timeline isn't cached."""
    timeline = get_timeline(source, player_id)
    if timeline is None:
        return None
    if entry is None:
        entry = _entry_for(source, player_id)
    if entry is not None:
        dob = entry.get("dob")
        milestones = entry.get("milestones")
        use_fide_birth = entry.get("use_fide_birth", True)
    else:
        dob = None
        milestones = _active_milestones(source)
        use_fide_birth = True
    return compute_record(timeline, dob=dob, milestones=milestones,
                          use_fide_birth_year=use_fide_birth)


def _summaries(entries):
    """Lightweight per-player summaries for the library list, computed from each
    entry's own view params."""
    out = []
    for e in entries:
        rec = _record_for(e["source"], e["player_id"], entry=e)
        if rec is None:
            continue
        milestones = rec.get("milestones") or {}
        peak = 0
        for threshold, cell in milestones.items():
            if isinstance(cell, dict) and cell.get("months") is not None:
                try:
                    peak = max(peak, int(threshold))
                except (ValueError, TypeError):
                    pass
        out.append({
            "source": rec["source"],
            "player_id": rec["player_id"],
            "name": rec["name"],
            "scraped_at": rec["scraped_at"],
            "initial_rating": rec.get("initial_rating"),
            "peak_rating": peak,
            "federation": rec.get("country"),
        })
    return out


def _safe_next(default):
    nxt = (request.form.get("next") or "").strip()
    return nxt if nxt.startswith("/") else default


def _fetch_timeline(source, player_id, progress_cb=None, status_cb=None, fallback_cb=None):
    """Dispatch to the right scraper for a single player's RAW timeline.
    `fallback_cb` only applies to USCF (fired when the API drops to HTML scraping)."""
    http = make_session()
    if source == "fide":
        # SQLite-backed OlimpBase + FIDE-archive caches so pre-2003 backfill
        # sources are fetched once, ever.
        return fetch_fide_history(http, player_id, progress_cb=progress_cb, status_cb=status_cb,
                                  olimpbase_cache=SqliteOlimpbaseCache(),
                                  fide_archive_cache=SqliteFideArchiveCache())
    # SQLite-backed crosstable cache so foreign-event game counts are fetched once.
    return fetch_history(http, player_id, progress_cb=progress_cb, status_cb=status_cb,
                         fallback_cb=fallback_cb, crosstable_cache=SqliteCrosstableCache())


def _timeline_outdated(source, timeline):
    """A cached timeline written by an older scraper version should be re-scraped
    even when it isn't time-stale: USCF timelines from before the foreign-event
    fix, and FIDE timelines from before the OlimpBase backfill / score-% removal."""
    if source == "fide":
        return (timeline or {}).get("fide_timeline_version") != FIDE_TIMELINE_VERSION
    if source != "uscf" or not USCF_INCLUDE_FOREIGN:
        return False
    return (timeline or {}).get("api_version") != TIMELINE_API_VERSION


def _valid_source_or_404(source):
    if source not in VALID_SOURCES:
        abort(404)


@bp.route("/")
def index():
    # Source is chosen PER ROW in the form (each row is independently USCF or
    # FIDE), so there's no site-wide source here. Prefill (from search.html's
    # "Use as Player N" links) carries the id's source alongside it.
    prefill = {}
    pid = request.args.get("id", "").strip()
    slot = request.args.get("slot", "0").strip()
    if pid:
        try:
            slot_int = max(0, min(1, int(slot)))
        except ValueError:
            slot_int = 0
        prefill[slot_int] = {
            "id": pid,
            "source": normalize_source(request.args.get("source")),
        }
    cached = _summaries(_saved())
    cached_ids = [f"{p['source']}:{p['player_id']}" for p in cached]
    return render_template(
        "index.html",
        cached=cached,
        cached_ids=cached_ids,
        saved_count=len(cached),
        anon_limit=ANON_SAVE_LIMIT,
        milestones_uscf=_active_milestones("uscf"),
        milestones_fide=_active_milestones("fide"),
        default_uscf=DEFAULT_USCF_MILESTONES,
        default_fide=DEFAULT_FIDE_MILESTONES,
        prefill=prefill,
        featured_fide=FEATURED_FIDE,
        featured_uscf=FEATURED_USCF,
        photo_credits=PHOTO_CREDITS,
    )


@bp.route("/scrape", methods=["POST"])
def scrape():
    # No batch-level source: every row carries its own (mixed batches are fine).
    entries, errors = parse_player_inputs(request.form)
    for err in errors:
        flash(err, "error")
    if not entries:
        return redirect(url_for("main.index"))
    # pending_scrape only drives the network fetch (raw timeline), which is
    # DOB/birth-year-independent — so it stays [src, pid, dob] and the worker
    # needs nothing else. The per-player use_fide_birth flag is a *view* param
    # and lives on the recent/saved entry that compute_record reads.
    session["pending_scrape"] = {
        "players": [[src, pid, dob] for src, pid, dob, _ufb in entries],
    }
    # Remember this batch's view params so /analyze and /player can show the
    # just-analyzed players (and offer to save them) before they're in the
    # library. Milestones are snapshotted per row's OWN source ladder.
    session["recent"] = [
        {"source": src, "player_id": pid, "dob": dob,
         "use_fide_birth": ufb, "milestones": _active_milestones(src)}
        for src, pid, dob, ufb in entries
    ]
    return redirect(url_for("main.scrape_progress"))


def _pending_entries(raw):
    """Per-player (source, player_id, dob) triples from a pending_scrape payload.
    Tolerates the legacy batch shape {"source": ..., "players": [[pid, dob]]}
    (a scrape pending across a deploy)."""
    legacy_source = normalize_source(raw.get("source"))
    players = []
    for e in (raw.get("players") or []):
        if len(e) >= 3:
            players.append((normalize_source(e[0]), e[1], e[2]))
        else:
            players.append((legacy_source, e[0], e[1]))
    return players


@bp.route("/scrape/progress")
def scrape_progress():
    entries = _pending_entries(session.get("pending_scrape") or {})
    if not entries:
        flash("Nothing to scrape. Submit the form again.", "error")
        return redirect(url_for("main.index"))
    # Show the player's NAME (not the bare ID) in the header. It's known up front
    # for anyone already cached; brand-new players show "#<id>" until the scrape
    # resolves the name (the stream then fills it in).
    players = []
    for src, pid, _dob in entries:
        tl = get_timeline(src, pid)
        players.append({"source": src, "uscf_id": pid, "name": (tl or {}).get("name")})
    return render_template("progress.html", players=players)


@bp.route("/scrape/stream")
def scrape_stream():
    raw = session.pop("pending_scrape", None) or {}
    entries = _pending_entries(raw)
    if not entries:
        return Response("No pending scrape.", status=400)
    app = current_app._get_current_object()

    def generate():
        q = queue.Queue()
        scraped_ids = []
        sentinel = object()

        def worker():
            with app.app_context():
                for idx, (src, player_id, dob) in enumerate(entries):
                    def cb(current, total, _idx=idx):
                        q.put({
                            "type": "progress",
                            "player_idx": _idx,
                            "current": current,
                            "total": total,
                        })
                    def status_cb(message, _idx=idx):
                        q.put({
                            "type": "status",
                            "player_idx": _idx,
                            "message": message,
                        })
                    def fallback_cb(_idx=idx):
                        # USCF API gave up → the slow HTML scraper takes over.
                        # Drives the "slower / no data after Nov 2025" notice and
                        # the Cancel button on the progress page.
                        q.put({"type": "fallback", "player_idx": _idx})
                    try:
                        # Fresh cache hit → reuse the raw timeline, no network
                        # (spec 007 Part 7: only brand-new players trigger a
                        # scrape). A cache older than CACHE_TTL_DAYS is re-scraped
                        # here so new tournaments show up — the only re-scrape path.
                        timeline = get_timeline(src, player_id)
                        cached_name = (timeline or {}).get("name")
                        q.put({"type": "player_start", "player_idx": idx,
                               "source": src, "uscf_id": player_id,
                               "name": cached_name})
                        fresh = (timeline is not None
                                 and not is_timeline_stale(timeline, CACHE_TTL_DAYS)
                                 and not _timeline_outdated(src, timeline))
                        if fresh:
                            # Already analyzed and fresh — no "X / Y tournaments"
                            # bar; the page just says so.
                            q.put({"type": "player_cached", "player_idx": idx,
                                   "name": cached_name})
                        else:
                            if timeline is not None:
                                status_cb("Refreshing cached data — re-scraping for the latest ratings.")
                            timeline = _fetch_timeline(src, player_id,
                                                       progress_cb=cb, status_cb=status_cb,
                                                       fallback_cb=fallback_cb)
                            save_timeline(timeline)
                        scraped_ids.append(f"{src}:{player_id}")
                        q.put({
                            "type": "player_done",
                            "player_idx": idx,
                            "source": src,
                            "uscf_id": player_id,
                            "name": timeline.get("name"),
                        })
                    except (PlayerNotFound, NoClassicalTournaments,
                            FidePlayerNotFound, FideNoRatedHistory, FideScrapeError) as e:
                        q.put({
                            "type": "player_error",
                            "player_idx": idx,
                            "uscf_id": player_id,
                            "error": str(e),
                        })
                    except Exception as e:
                        q.put({
                            "type": "player_error",
                            "player_idx": idx,
                            "uscf_id": player_id,
                            "error": f"{type(e).__name__}: {e}",
                        })
            q.put(sentinel)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        while True:
            item = q.get()
            if item is sentinel:
                if scraped_ids:
                    redirect_url = url_for("main.analyze", ids=scraped_ids)
                else:
                    redirect_url = url_for("main.index")
                payload = {"redirect": redirect_url, "scraped_ids": scraped_ids}
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@bp.route("/player/<source>/<player_id>")
def player(source, player_id):
    _valid_source_or_404(source)
    entry = _entry_for(source, player_id)
    record = _record_for(source, player_id, entry=entry)
    if record is None:
        abort(404)
    active = _active_milestones(source)
    used = list((entry or {}).get("milestones") or record.get("milestones_config") or [])
    stale = used != active
    return render_template(
        "player.html",
        player=record,
        milestones=record.get("milestones_config") or [],
        active_milestones=active,
        stale=stale,
        is_saved=_is_saved(source, player_id),
        saved_count=len(_saved()),
        anon_limit=ANON_SAVE_LIMIT,
    )


@bp.route("/player/<source>/<player_id>/apply-milestones", methods=["POST"])
def apply_milestones(source, player_id):
    """Re-derive this player's view with the current active milestone ladder —
    no re-scrape (spec 007 Part 7). Updates the entry's milestones snapshot in
    the user's saved/recent lists."""
    _valid_source_or_404(source)
    active = _active_milestones(source)
    updated = False
    for key in ("saved", "recent"):
        lst = session.get(key) or []
        changed = False
        for e in lst:
            if _same(e, source, player_id):
                e["milestones"] = active
                changed = True
                updated = True
        if changed:
            session[key] = lst
    if updated:
        flash("Updated to current milestones — no re-scrape needed.", "info")
    else:
        flash("This player isn't in your library or recent list.", "error")
    return redirect(url_for("main.player", source=source, player_id=player_id))


@bp.route("/library/save", methods=["POST"])
def save_to_library():
    source = normalize_source(request.form.get("source"))
    player_id = (request.form.get("player_id") or "").strip()
    next_url = _safe_next(url_for("main.index"))
    if not player_id:
        flash("Nothing to save.", "error")
        return redirect(next_url)
    timeline = get_timeline(source, player_id)
    if timeline is None:
        flash("Analyze this player before saving it to your library.", "error")
        return redirect(next_url)
    if _is_saved(source, player_id):
        flash("That player is already in your library.", "info")
        return redirect(next_url)
    saved = _saved()
    if len(saved) >= ANON_SAVE_LIMIT:
        flash(
            f"You've reached the maximum of {ANON_SAVE_LIMIT} saved players.",
            "error",
        )
        return redirect(next_url)
    entry = _entry_for(source, player_id)
    if entry is not None:
        dob = entry.get("dob")
        milestones = entry.get("milestones")
        use_fide_birth = entry.get("use_fide_birth", True)
    else:
        dob = None
        milestones = _active_milestones(source)
        use_fide_birth = True
    saved.append({
        "source": source,
        "player_id": str(player_id),
        "name": timeline.get("name"),
        "dob": dob,
        "use_fide_birth": use_fide_birth,
        "milestones": milestones,
    })
    session["saved"] = saved
    flash(
        f"Saved {timeline.get('name') or player_id} to your library "
        f"({len(saved)}/{ANON_SAVE_LIMIT}).",
        "info",
    )
    return redirect(next_url)


@bp.route("/library/remove", methods=["POST"])
def remove_from_library():
    source = normalize_source(request.form.get("source"))
    player_id = (request.form.get("player_id") or "").strip()
    next_url = _safe_next(url_for("main.index"))
    session["saved"] = [e for e in _saved() if not _same(e, source, player_id)]
    flash("Removed from your library.", "info")
    return redirect(next_url)


@bp.route("/api/search")
def api_search():
    source = normalize_source(request.args.get("source"))
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"error": "Enter at least two characters.", "results": []}), 400
    try:
        http = make_session()
        if source == "fide":
            results = search_fide_players(http, query)
        else:
            results = search_players(http, query)
    except Exception as e:
        return jsonify({"error": f"Search failed: {e}", "results": []}), 502
    return jsonify({"source": source, "results": results})


@bp.route("/search")
def search():
    source = normalize_source(request.args.get("source"))
    query = request.args.get("q", "").strip()
    results = []
    error = None
    if query:
        if len(query) < 2:
            error = "Enter at least two characters."
        else:
            try:
                http = make_session()
                if source == "fide":
                    results = search_fide_players(http, query)
                else:
                    results = search_players(http, query)
            except Exception as e:
                error = f"Search failed: {e}"
    return render_template(
        "search.html",
        query=query,
        results=results,
        error=error,
        source=source,
    )


def _parse_namespaced_id(raw):
    """Split a 'source:player_id' string. Tolerate bare ids by assuming uscf."""
    if ":" in raw:
        src, _, pid = raw.partition(":")
    else:
        src, pid = "uscf", raw
    return normalize_source(src), pid


@bp.route("/compare")
def compare():
    # Permanent home for charts is now /analyze; keep this for old bookmarks.
    return redirect(url_for("main.analyze", ids=request.args.getlist("ids")))


def _chart_player(p, milestones):
    """Serialize one computed record into the per-player chart shape."""
    return {
        "source": p.get("source", "uscf"),
        "player_id": p.get("player_id"),
        "name": p["name"],
        "months": [p["milestones"].get(str(m), {}).get("months") for m in milestones],
        "games": [p["milestones"].get(str(m), {}).get("games") for m in milestones],
        "age": [p["milestones"].get(str(m), {}).get("age") for m in milestones],
        "score": [p["milestones"].get(str(m), {}).get("score_pct") for m in milestones],
        "dates": [p["milestones"].get(str(m), {}).get("date") for m in milestones],
    }


def _library_view_entries():
    """The user's per-request view set: saved library ∪ most-recent batch,
    deduped. The shared scrape_cache is never listed globally (spec 007 Part 5c)."""
    entries = []
    seen = set()
    for e in (_saved() + _recent()):
        key = (e["source"], str(e["player_id"]))
        if key in seen:
            continue
        seen.add(key)
        entries.append(e)
    return entries


@bp.route("/analyze")
def analyze():
    """Multi-player chart hub, scoped to THIS user's library + recent batch.
    `initial_selected` marks the ids passed in via ?ids= (or the recent batch)."""
    entries = _library_view_entries()
    records = []
    for e in entries:
        rec = _record_for(e["source"], e["player_id"], entry=e)
        if rec is not None:
            records.append(rec)
    if not records:
        flash("No analyzed players yet. Analyze a player first.", "error")
        return redirect(url_for("main.index"))

    have = {f"{r['source']}:{r['player_id']}" for r in records}

    requested = []
    missing = []
    for raw in request.args.getlist("ids"):
        src, pid = _parse_namespaced_id(raw)
        key = f"{src}:{pid}"
        if key in have:
            requested.append(key)
        else:
            missing.append(raw)
    for raw in missing:
        flash(f"{raw} is not in your library yet.", "error")
    # No explicit selection → default to the most-recently analyzed batch.
    if not requested:
        for e in _recent():
            k = f"{e['source']}:{e['player_id']}"
            if k in have and k not in requested:
                requested.append(k)

    # Cap the initial on-chart selection so a big batch (e.g. 100 players)
    # doesn't render every line at once. Everyone stays in the picker list;
    # the cap only limits what's checked/drawn on load.
    requested = requested[:ANALYZE_CHART_LIMIT]

    # Shared milestone axis = union of every shown player's thresholds.
    milestone_set = set()
    for r in records:
        for m in (r.get("milestones") or {}).keys():
            try:
                milestone_set.add(int(m))
            except (ValueError, TypeError):
                pass
    milestones = sorted(milestone_set)

    chart_data = {
        "milestones": milestones,
        "players": [_chart_player(r, milestones) for r in records],
    }

    saved_keys = [f"{e['source']}:{e['player_id']}" for e in _saved()]

    return render_template(
        "analyze.html",
        chart_data=chart_data,
        initial_selected=requested,
        chart_limit=ANALYZE_CHART_LIMIT,
        saved_keys=saved_keys,
        saved_count=len(_saved()),
        anon_limit=ANON_SAVE_LIMIT,
    )


@bp.route("/export.csv")
def export_csv():
    """Flatten records to one CSV row per player. With ?ids= (namespaced), export
    those; otherwise export the user's saved library."""
    raw_ids = request.args.getlist("ids")
    if raw_ids:
        players = []
        for raw in raw_ids:
            src, pid = _parse_namespaced_id(raw)
            rec = _record_for(src, pid)
            if rec:
                players.append(rec)
    else:
        players = []
        for e in _saved():
            rec = _record_for(e["source"], e["player_id"], entry=e)
            if rec:
                players.append(rec)

    if not players:
        flash("No players to export. Save some to your library first.", "error")
        return redirect(url_for("main.index"))

    # Union of all milestone thresholds across the exported players.
    threshold_set = set()
    for p in players:
        for m in (p.get("milestones") or {}).keys():
            try:
                threshold_set.add(int(m))
            except (ValueError, TypeError):
                pass
    thresholds = sorted(threshold_set)

    base_cols = [
        "source", "player_id", "name", "country", "dob", "dob_source",
        "first_tournament_date", "initial_rating", "age_at_first_tournament",
        "scraped_at",
    ]
    header = list(base_cols)
    for t in thresholds:
        header += [f"m{t}_months", f"m{t}_games", f"m{t}_age", f"m{t}_score_pct"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)

    def cell(v):
        return "" if v is None else v

    for p in players:
        row = [cell(p.get(c)) for c in base_cols]
        milestones = p.get("milestones") or {}
        for t in thresholds:
            m = milestones.get(str(t)) or {}
            row += [
                cell(m.get("months")),
                cell(m.get("games")),
                cell(m.get("age")),
                cell(m.get("score_pct")),
            ]
        writer.writerow(row)

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="chess_players.csv"'},
    )


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        which = request.form.get("which", "uscf")
        which = which if which in VALID_SOURCES else "uscf"
        values, err = parse_milestones(request.form.get("milestones"))
        wants_json = (
            request.headers.get("X-Requested-With") == "fetch"
            or "application/json" in request.headers.get("Accept", "")
        )
        if not err:
            session[_milestone_session_key(which)] = values

        if wants_json:
            if err:
                return jsonify({"ok": False, "error": err}), 400
            return jsonify({"ok": True, "which": which, "values": values})

        next_url = request.form.get("next", "").strip()
        if not next_url or not next_url.startswith("/"):
            next_url = url_for("main.settings")
        flash(err if err else f"{which.upper()} milestones updated.",
              "error" if err else "info")
        return redirect(next_url)
    return render_template(
        "settings.html",
        uscf_milestones=_active_milestones("uscf"),
        fide_milestones=_active_milestones("fide"),
        default_uscf=DEFAULT_USCF_MILESTONES,
        default_fide=DEFAULT_FIDE_MILESTONES,
    )
