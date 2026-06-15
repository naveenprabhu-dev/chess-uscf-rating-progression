import re
from datetime import datetime

USCF_ID_RE = re.compile(r"^\d{8}$")
FIDE_ID_RE = re.compile(r"^\d{5,10}$")
MAX_PLAYERS_PER_REQUEST = 100

VALID_SOURCES = ("uscf", "fide")


def normalize_source(raw):
    """Return 'uscf' or 'fide'. Anything unrecognized falls back to 'uscf'."""
    s = (raw or "").strip().lower()
    return s if s in VALID_SOURCES else "uscf"


def validate_uscf_id(raw):
    s = (raw or "").strip()
    if not USCF_ID_RE.match(s):
        return None, "USCF ID must be exactly 8 digits."
    return s, None


def validate_fide_id(raw):
    s = (raw or "").strip()
    if not FIDE_ID_RE.match(s):
        return None, "FIDE ID must be 5 to 10 digits."
    return s, None


def validate_player_id(raw, source):
    if source == "fide":
        return validate_fide_id(raw)
    return validate_uscf_id(raw)


def validate_dob(raw, required=False):
    """Validate a date string. When required=False, blank input is accepted and
    returned as (None, None)."""
    s = (raw or "").strip()
    if not s:
        if required:
            return None, "DOB is required."
        return None, None
    try:
        datetime.strptime(s, "%m/%d/%Y")
    except ValueError:
        return None, "DOB must be in MM/DD/YYYY format."
    return s, None


def parse_player_inputs(form, source):
    """Parse an arbitrary number of (uscf_id_N, dob_N) rows from a form,
    validating IDs against the site-wide `source`.

    Returns (entries, errors). Empty rows are skipped. Each entry is
    (player_id, dob_or_None) — DOB is optional.
    """
    label = "FIDE ID" if source == "fide" else "USCF ID"
    entries = []
    errors = []
    # Collect every submitted row index from the form keys so we don't assume a
    # fixed number of rows. Cap at MAX_PLAYERS_PER_REQUEST.
    indices = set()
    for key in form.keys():
        if key.startswith("uscf_id_"):
            suffix = key[len("uscf_id_"):]
            if suffix.isdigit():
                indices.add(int(suffix))
        elif key.startswith("dob_"):
            suffix = key[len("dob_"):]
            if suffix.isdigit():
                indices.add(int(suffix))
    for i in sorted(indices)[:MAX_PLAYERS_PER_REQUEST]:
        raw_id = form.get(f"uscf_id_{i}", "").strip()
        raw_dob = form.get(f"dob_{i}", "").strip()
        if not raw_id and not raw_dob:
            continue
        player_id, id_err = validate_player_id(raw_id, source)
        dob, dob_err = validate_dob(raw_dob)
        if id_err:
            errors.append(f"Row {i + 1}: {id_err}")
        if dob_err:
            errors.append(f"Row {i + 1}: {dob_err}")
        if player_id and not id_err and not dob_err:
            entries.append((player_id, dob))

    if not entries and not errors:
        errors.append(f"Enter at least one {label}.")

    seen = set()
    deduped = []
    for player_id, dob in entries:
        if player_id in seen:
            continue
        seen.add(player_id)
        deduped.append((player_id, dob))

    return deduped, errors


def parse_milestones(raw):
    """Accept comma- or whitespace-separated integers. Returns (list, error)."""
    s = (raw or "").strip()
    if not s:
        return None, "Enter at least one milestone."
    try:
        values = sorted({int(p) for p in re.split(r"[\s,]+", s) if p})
    except ValueError:
        return None, "Milestones must be integers."
    if not values:
        return None, "Enter at least one milestone."
    if any(v < 100 or v > 3500 for v in values):
        return None, "Milestones must be between 100 and 3500."
    return values, None
