"""
coach_tools.py — working implementations of every tool in references/tools_schema.json.

This is the glue between the agent and the deterministic code: SQLite
access (schema.sql), the file parsers, analysis.py, and the two program
generators (race_plan.py / general_fitness.py). Every function takes
plain JSON-able arguments and returns a JSON-able dict, so it can be
wired to any function-calling platform OR driven from a shell:

    python3 coach_tools.py init_db
    python3 coach_tools.py --list                       # tool names
    python3 coach_tools.py get_athlete_summary '{"athlete_id": "..."}'
    python3 coach_tools.py get_athlete_summary '{"chat_ref": "telegram:123456789"}'

Arguments are one JSON object (inline, or @path to a file). Output is
JSON on stdout; on error {"ok": false, "error": ...} and exit code 1.

The database lives at $COACH_DB; installed as a Hermes skill it defaults
to $HERMES_HOME/run_coach/coach.db (outside the skill, so updating the
skill never touches data). Created from schema.sql on first use.

Zero dependencies beyond the standard library.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone

import analysis
import history
import plan_review
import safety_checks
import hevy
import strava
from fit_parser import parse_fit
from general_fitness import (DEFAULT_WEEKS as GF_WEEKS, WALK_RUN_LADDER, _walk_run_label,
                             generate_general_fitness_plan)
from gpx_parser import parse_gpx
from race_plan import MIN_BASE_LONGEST_KM, MIN_BASE_WEEKLY_KM, _profile as race_profile, generate_race_plan
from trends import build_trends

KIT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_TYPES = {"easy", "quality", "long", "time_trial", "race", "rest", "walk_run"}
HARD_TYPES = {"quality", "long", "time_trial", "race"}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---- plumbing ---------------------------------------------------

def hermes_home():
    """$HERMES_HOME, else the home this skill is installed under
    (<home>/skills/<category>/<skill>/scripts), else None."""
    if os.environ.get("HERMES_HOME"):
        return os.path.expanduser(os.environ["HERMES_HOME"])
    skills_dir = os.path.dirname(os.path.dirname(os.path.dirname(KIT_DIR)))
    if os.path.basename(skills_dir) == "skills":
        return os.path.dirname(skills_dir)
    return None


def db_path():
    """$COACH_DB, else <hermes home>/run_coach/coach.db when installed as a
    Hermes skill (outside the skill dir, so skill updates never touch the
    data), else coach.db next to this file."""
    if os.environ.get("COACH_DB"):
        return os.environ["COACH_DB"]
    home = hermes_home()
    if home:
        os.makedirs(os.path.join(home, "run_coach"), exist_ok=True)
        return os.path.join(home, "run_coach", "coach.db")
    return os.path.join(KIT_DIR, "coach.db")


# Every connection a tool opens is closed when the outermost tool call
# returns. Windows locks open SQLite files, so leaked connections break
# backups, temp-dir cleanup and file moves there.
_OPEN = []
_DEPTH = [0]


def _track(conn):
    _OPEN.append(conn)
    return conn


def _hconnect():
    return _track(history.connect(db_path()))


def connect():
    conn = _track(sqlite3.connect(db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT name FROM sqlite_master WHERE name='athlete_profile'").fetchone():
        with open(os.path.join(KIT_DIR, "schema.sql")) as f:
            conn.executescript(f.read())
        conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(athlete_profile)")}
    if cols and "welcomed_at" not in cols:
        conn.execute("ALTER TABLE athlete_profile ADD COLUMN welcomed_at TEXT")
        conn.commit()
    for table, col in (("athlete_profile", "safety_screen_status"), ("athlete_profile", "safety_screen_json"),
                       ("race_target", "verification_status"), ("race_target", "verification_json"),
                       ("run_log", "external_id"), ("run_log", "feedback_sent_at"),
                       ("other_activity", "strength_json")):
        tcols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if tcols and col not in tcols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
            conn.commit()
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(program_revision)")}
    if rcols and "constraints_json" not in rcols:
        conn.execute("ALTER TABLE program_revision ADD COLUMN constraints_json TEXT")
        conn.commit()
    for table in ("plan_review", "strava_connection", "other_activity"):
        if not conn.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone():
            with open(os.path.join(KIT_DIR, "schema.sql")) as f:
                ddl = f.read()
            start = ddl.index(f"CREATE TABLE {table}")
            conn.executescript(ddl[start:ddl.index(");", start) + 2])
            conn.commit()
    if not conn.execute("SELECT name FROM sqlite_master WHERE name='signal_event'").fetchone():
        # Databases created before trends existed: add the history table.
        with open(os.path.join(KIT_DIR, "schema.sql")) as f:
            ddl = f.read()
        start = ddl.index("CREATE TABLE signal_event")
        conn.executescript(ddl[start:ddl.index(");", start) + 2]
                           + "\nCREATE INDEX IF NOT EXISTS idx_signal_event_athlete "
                             "ON signal_event(athlete_id, occurred_at);")
        conn.commit()
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today():
    # COACH_TODAY lets tests (and backfills) pin "today".
    return date.fromisoformat(os.environ["COACH_TODAY"]) if os.environ.get("COACH_TODAY") else date.today()


def _id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row(r):
    return dict(r) if r is not None else None


def _next_monday(d):
    return d + timedelta(days=(7 - d.weekday()) % 7)


class ToolError(Exception):
    pass


def _get_athlete(conn, athlete_id):
    a = conn.execute("SELECT * FROM athlete_profile WHERE athlete_id=?", (athlete_id,)).fetchone()
    if not a:
        raise ToolError(f"no athlete {athlete_id!r}; call list_athletes")
    return dict(a)


def _active_goal(conn, athlete_id):
    return _row(conn.execute(
        "SELECT * FROM race_target WHERE athlete_id=? AND status='active' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (athlete_id,)).fetchone())


def _current_revision(conn, athlete_id):
    return _row(conn.execute(
        "SELECT * FROM program_revision WHERE athlete_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (athlete_id,)).fetchone())


def _latest_intake(conn, athlete_id):
    r = conn.execute("SELECT normalized_json FROM intake_response WHERE athlete_id=? "
                     "ORDER BY received_at DESC, rowid DESC LIMIT 1", (athlete_id,)).fetchone()
    return json.loads(r["normalized_json"]) if r else {}


# ---- intake parsing ---------------------------------------------
# Form question text -> canonical key. Each matcher is a tuple of
# substrings that must ALL appear in the lower-cased question; the
# first canonical key whose matcher hits wins, so order matters.
# Canonical keys are also accepted directly.

INTAKE_FIELDS = [
    ("goal",                   ("what are you training for",)),
    ("telegram_user_id",       ("telegram",)),
    ("uses_strava",            ("strava",)),
    ("race_name",              ("race name",)),
    ("race_date",              ("race date",)),
    ("race_distance",          ("race distance",)),
    ("course_url",             ("website",)),
    ("course_url",             ("entry page",)),
    ("race_start_time",        ("start time",)),
    ("continuous_run",         ("without stopping",)),
    ("weekly_run_minutes",     ("minutes a week",)),
    ("sessions_per_week",      ("days a week",)),
    ("fitter_meaning",         ("fitter", "mean")),
    ("health_screen",          ("health check",)),
    ("email",                  ("email",)),
    ("weight_kg",              ("weight",)),
    ("injury_history",         ("injur",)),
    ("fixed_commitments",      ("commitments",)),
    ("longest_run_km",         ("longest run",)),
    ("total_km_4wk",           ("total running km",)),
    ("best_effort_distance",   ("best recent", "distance")),
    ("best_effort_time",       ("best recent", "time")),
    ("best_effort_when",       ("best recent", "when")),
    ("has_hr_zones",           ("heart rate zones",)),
    ("hr_zone_upload",         ("upload",)),
    ("easy_pace_pref",         ("easy", "pace")),
    ("long_run_day",           ("long-run day",)),
    ("long_run_day",           ("long run day",)),
    ("avoid_days",             ("rather avoid",)),
    ("delivery_preference",    ("how should i send",)),
    ("anything_else",          ("anything else",)),
    ("name",                   ("name",)),          # last: "race name" must win first
]
CANONICAL_KEYS = {k for k, _ in INTAKE_FIELDS}


def normalize_intake(form):
    out = {}
    for key, value in form.items():
        if key in CANONICAL_KEYS:
            out[key] = value
            continue
        q = key.lower()
        for canon, parts in INTAKE_FIELDS:
            if all(p in q for p in parts):
                # Sheets carry both form branches as columns; a blank answer
                # from the other branch must not hide the real one.
                if canon not in out or (out[canon] in ("", None, []) and value not in ("", None, [])):
                    out[canon] = value
                break
        else:
            out.setdefault("unmapped", {})[key] = value
    return out


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def _band(v, under_value):
    """'1–5 min' -> 1, '30+ min' -> 30, 'under 30' -> under_value, 'None'/'I don't run yet' -> 0."""
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").lower()
    if not s or "none" in s or "don't" in s or "dont" in s or "not yet" in s:
        return 0
    if "under" in s or "less than" in s:
        return under_value
    n = _num(s)
    return int(n) if n is not None else 0


def _distance_km(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").lower().replace(" ", "")
    if "half" in s:
        return 21.1
    if "marathon" in s:
        return 42.2
    n = _num(s)
    if n is None:
        return None
    return round(n * 1.609, 1) if "mi" in s else n


def _days(v):
    if not v:
        return []
    items = v if isinstance(v, list) else re.split(r"[,;/]", str(v))
    out = []
    for item in items:
        t = str(item).strip()[:3].title()
        if t in DAY_NAMES:
            out.append(t)
    return out


def _parse_date(v):
    """Form/Sheet dates arrive as ISO, '17/01/2027', '1/17/2027', or an Excel
    serial number. Returns (iso_date or None, warning or None). A date that
    could be either day/month or month/day is read as day/month and flagged."""
    if v is None or v == "":
        return None, None
    if isinstance(v, (int, float)) or re.fullmatch(r"\d{5}(\.\d+)?", str(v).strip()):
        return (date(1899, 12, 30) + timedelta(days=int(float(v)))).isoformat(), None
    t = str(v).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat(), None
    m = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", t)
    if not m:
        raise ToolError(f"can't read the date {t!r}; use YYYY-MM-DD")
    a, b, y = int(m[1]), int(m[2]), int(m[3])
    y = y + 2000 if y < 100 else y
    if a > 12:
        return date(y, b, a).isoformat(), None
    if b > 12:
        return date(y, a, b).isoformat(), None
    warn = (f"Race date {t!r} could be day/month or month/day; read it as {date(y, b, a).isoformat()} "
            f"(day/month). Confirm with the athlete." if a != b else None)
    return date(y, b, a).isoformat(), warn


def _telegram_id(v):
    """Numeric Telegram user id from the form, or (None, warning)."""
    t = re.sub(r"\s", "", str(v or ""))
    if not t:
        return None, None
    if re.fullmatch(r"\d{5,15}", t):
        return t, None
    return None, (f"Telegram field {v!r} isn't a numeric user id (usernames like @name can't be used). "
                  f"Ask the athlete to message @userinfobot and send you the number.")


def _goal_type(norm):
    g = str(norm.get("goal") or "").lower()
    if "fitter" in g or "no event" in g or "general" in g:
        return "general_fitness", False
    if "race" in g or "event" in g:
        return "race", False
    # No answer: infer, and tell the agent to confirm.
    if norm.get("race_date") and norm.get("race_distance"):
        return "race", True
    return "general_fitness", True


# ---- tools ------------------------------------------------------

def init_db():
    conn = connect()
    _hconnect()
    return {"ok": True, "db_path": db_path(), "history_db_path": history.history_path(db_path()),
            "tables": [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]}


def list_athletes():
    conn = connect()
    rows = conn.execute("SELECT athlete_id, name, email, chat_ref, created_at FROM athlete_profile "
                        "ORDER BY created_at").fetchall()
    return {"ok": True, "athletes": [dict(r) for r in rows]}


def ingest_intake_form(form_response_json, hr_zone_file_url=None, start_date=None):
    conn = connect()
    norm = normalize_intake(form_response_json)
    if not norm.get("name") and not norm.get("email"):
        raise ToolError("form response has neither a name nor an email; can't identify the athlete")
    now = _now()
    warnings, next_steps = [], []
    goal_type, inferred = _goal_type(norm)
    if inferred:
        warnings.append(f"The 'What are you training for?' answer was missing; inferred "
                        f"goal_type={goal_type!r}. Confirm this with the athlete in your first reply.")

    # Sanity checks on the numbers, before anything is written.
    pre_iso = pre_dist = None
    if goal_type == "race":
        pre_dist = _distance_km(norm.get("race_distance"))
        try:
            pre_iso, _ = _parse_date(norm.get("race_date"))
        except ToolError:
            pre_iso = None
    checks = safety_checks.intake_checks(norm, goal_type, pre_iso, pre_dist, _today())
    blocks = [c["message"] for c in checks if c["level"] == "block"]
    if blocks:
        raise ToolError(" ".join(blocks) + " Fix this row in the Sheet and send it again.")
    warnings += [c["message"] for c in checks]
    norm["checks"] = checks

    health = norm.get("health_screen") or []
    if isinstance(health, str):
        health = [h.strip() for h in re.split(r"[,;]", health) if h.strip()]
    health = [h for h in health if "none" not in h.lower()]

    tg_id, tg_warn = _telegram_id(norm.get("telegram_user_id"))
    if tg_warn:
        warnings.append(tg_warn)

    existing = None
    if norm.get("email"):
        existing = _row(conn.execute("SELECT * FROM athlete_profile WHERE lower(email)=lower(?)",
                                     (norm["email"],)).fetchone())
    athlete_id = existing["athlete_id"] if existing else (
        re.sub(r"[^a-z0-9]+", "-", str(norm.get("name") or norm["email"]).lower()).strip("-")[:24]
        + "-" + uuid.uuid4().hex[:6])

    # Free-text health screen: anything the athlete wrote gets an independent review.
    screen_text = safety_checks.free_text(norm)
    old_screen = json.loads((existing or {}).get("safety_screen_json") or "{}")
    screen_changed = not existing or old_screen.get("text") != screen_text
    if screen_changed:
        hits = safety_checks.keyword_hits(screen_text) if screen_text else []
        screen_status = "pending_review" if screen_text else "clear"
        screen_json = json.dumps({"text": screen_text, "keyword_hits": hits})
    # Flags added by a free-text review survive a re-sent Sheet row.
    kept = [f for f in json.loads((existing or {}).get("health_screen_flags") or "[]")
            if f.startswith("free text:")] if not screen_changed else []
    profile = {
        "name": norm.get("name") or (existing or {}).get("name") or norm["email"],
        "email": norm.get("email"),
        "weight_kg": _num(norm.get("weight_kg")),
        "weight_source": "self_reported" if norm.get("weight_kg") else None,
        "weight_updated_at": now[:10] if norm.get("weight_kg") else None,
        "injury_history": norm.get("injury_history"),
        "health_screen_flags": json.dumps(health + kept),
        "safety_screen_status": screen_status if screen_changed else None,
        "safety_screen_json": screen_json if screen_changed else None,
        "baseline_continuous_run_min": _band(norm.get("continuous_run"), 0) if "continuous_run" in norm else None,
        "baseline_weekly_run_min": _band(norm.get("weekly_run_minutes"), 15) if "weekly_run_minutes" in norm else None,
    }
    if norm.get("easy_pace_pref"):
        warnings.append(f"Athlete gave an easy-pace preference ({norm['easy_pace_pref']!r}). Convert to "
                        f"sec/km and store it with update_athlete_profile only if it's a genuine "
                        f"override; sanity-check it against logged HR over the first weeks.")
    if existing:
        sets = {k: v for k, v in profile.items() if v is not None}
        sets["updated_at"] = now
        conn.execute(f"UPDATE athlete_profile SET {', '.join(f'{k}=?' for k in sets)} WHERE athlete_id=?",
                     (*sets.values(), athlete_id))
    else:
        profile.update(athlete_id=athlete_id, created_at=now, updated_at=now)
        conn.execute(f"INSERT INTO athlete_profile ({', '.join(profile)}) VALUES ({', '.join('?' * len(profile))})",
                     tuple(profile.values()))

    raw = json.dumps(form_response_json, sort_keys=True)
    if existing and conn.execute("SELECT 1 FROM intake_response WHERE athlete_id=? AND raw_json=?",
                                 (athlete_id, raw)).fetchone():
        conn.rollback()
        return {"ok": True, "duplicate": True, "athlete_id": athlete_id,
                "note": "this exact submission was already ingested; nothing changed"}
    conn.execute("INSERT INTO intake_response VALUES (?,?,?,?,?)",
                 (_id("intake"), athlete_id, now, raw, json.dumps(norm)))

    # Goal row. A new submission replaces the active goal.
    race_id = _id("goal")
    start = date.fromisoformat(start_date) if start_date else _next_monday(_today())
    if goal_type == "race":
        dist = _distance_km(norm.get("race_distance"))
        race_iso, date_warn = _parse_date(norm.get("race_date"))
        if date_warn:
            warnings.append(date_warn)
        if not race_iso or not dist:
            raise ToolError("goal is 'race' but race date or distance is missing/unreadable: "
                            f"date={norm.get('race_date')!r} distance={norm.get('race_distance')!r}. "
                            "Ask the athlete, then resubmit with canonical keys race_date / race_distance.")
        goal = dict(race_id=race_id, athlete_id=athlete_id, goal_type="race",
                    race_name=norm.get("race_name") or f"{dist:g} km race",
                    race_date=race_iso, start_time_local=norm.get("race_start_time"),
                    distance_km=dist, course_url=norm.get("course_url"), verification_status="unverified",
                    created_at=now)
    else:
        goal = dict(race_id=race_id, athlete_id=athlete_id, goal_type="general_fitness",
                    race_name="General fitness",
                    review_date=(start + timedelta(weeks=GF_WEEKS)).isoformat(), created_at=now)
    old = _active_goal(conn, athlete_id)
    same_goal = bool(old) and old["goal_type"] == goal["goal_type"] and (
        goal["goal_type"] == "general_fitness" or
        (old["race_date"], old["distance_km"], old["race_name"]) ==
        (goal["race_date"], goal["distance_km"], goal["race_name"]))
    if same_goal:
        race_id = old["race_id"]            # re-sent / edited row, same goal: keep the goal and its plan
    else:
        conn.execute(f"INSERT INTO race_target ({', '.join(goal)}) VALUES ({', '.join('?' * len(goal))})",
                     tuple(goal.values()))
        if old:
            conn.execute("UPDATE race_target SET status='dropped', superseded_by=? WHERE race_id=?",
                         (race_id, old["race_id"]))
            warnings.append(f"Goal changed: replaced {old['race_name']!r} ({old['race_id']}). The old plan "
                            f"stays until you generate a new one -- confirm with the owner first.")
    # Telegram id on the form -> link the chat now, so the athlete is recognised on their first message.
    linked = False
    if tg_id:
        ref = f"telegram:{tg_id}"
        other = conn.execute("SELECT athlete_id FROM athlete_profile WHERE chat_ref=? AND athlete_id!=?",
                             (ref, athlete_id)).fetchone()
        if other:
            warnings.append(f"Telegram id {tg_id} is already linked to athlete {other['athlete_id']}; not linked "
                            f"here. Check the Sheet for a copy-paste mistake.")
        elif (existing or {}).get("chat_ref") != ref:
            conn.execute("UPDATE athlete_profile SET chat_ref=? WHERE athlete_id=?", (ref, athlete_id))
            linked = True                   # newly linked (re-sent rows don't repeat this step)
    conn.commit()

    # Tell the agent exactly what's left to do.
    if screen_changed and screen_text:
        next_steps.append("SAFETY REVIEW: the athlete wrote free text (injuries / anything else). Call "
                          "get_review_tasks, run the reviewer it gives you with delegate_task, read the text "
                          "yourself too, then record_safety_review. The plan waits until this is done.")
    if health:
        next_steps.append("MEDICAL: health check flagged " + ", ".join(health) + ". Ask the athlete to get "
                          "medical clearance; once they confirm, call update_athlete_profile with "
                          "medical_clearance_at. generate_program will refuse until then.")
    if goal_type == "race":
        if not same_goal:
            next_steps.append("RACE CHECK: call get_review_tasks and follow it -- look up the race yourself "
                              "(source 1), have a separate agent look it up independently (source 2), then "
                              "verify_race_info. The plan waits until the race details are verified or the "
                              "owner confirms them.")
    if hr_zone_file_url or norm.get("hr_zone_upload"):
        next_steps.append(f"Read the HR zone upload ({hr_zone_file_url or norm.get('hr_zone_upload')}) "
                          f"yourself, work out whether it's %max or %LTHR, and call "
                          f"update_athlete_profile with the hr_zone_* fields, hr_zone_source and "
                          f"hr_zone_basis_notes.")
    if norm.get("fixed_commitments") and not (existing or {}).get("fixed_commitments"):
        next_steps.append("Convert the fixed-commitments free text to a JSON list "
                          "[{day, activity, moveable}] and call update_athlete_profile(fixed_commitments=...). "
                          "Pass any day that can't hold a run as constraints.avoid_days to generate_program.")
    if goal_type == "race" and (norm.get("longest_run_km") is None or norm.get("total_km_4wk") is None):
        next_steps.append("Longest run / 4-week km missing: ask the athlete before generating a race plan.")
    has_plan = bool(conn.execute("SELECT 1 FROM program_revision WHERE race_id=?", (race_id,)).fetchone())
    if not has_plan:
        next_steps.append(f"Call generate_program(athlete_id={athlete_id!r}, race_id={race_id!r}, "
                          f"reason='initial plan from intake').")
    elif existing:
        next_steps.append("This athlete already has a plan for this goal; the row only updated their details. "
                          "Tell the owner what changed; regenerate only if they ask.")
    if linked:
        next_steps.append("Telegram id linked. Run sync_telegram_allowlist so they can reach the bot (the owner then "
                          "restarts the gateway). When they first message you, send the welcome (playbook A, step 6).")
    elif not (existing or {}).get("chat_ref"):
        next_steps.append("No usable Telegram id: when the athlete messages you, link their chat with "
                          "link_chat(email=..., chat_ref=...), then send the welcome.")
    return {"ok": True, "duplicate": False, "athlete_id": athlete_id, "race_id": race_id, "goal_type": goal_type,
            "is_update_of_existing_athlete": bool(existing), "goal_changed": not same_goal and bool(old),
            "telegram_linked": linked or bool(tg_id and (existing or {}).get("chat_ref") == f"telegram:{tg_id}"),
            "health_screen_flags": health,
            "needs_medical_clearance": bool(health), "normalized": norm,
            "warnings": warnings, "next_steps": next_steps}


def update_athlete_profile(athlete_id, fields):
    allowed = {"name", "email", "weight_kg", "weight_source", "injury_history", "fixed_commitments",
               "distance_unit", "easy_pace_pref_sec_per_km", "easy_pace_pref_source",
               "hr_zone_source", "hr_zone_basis_notes", "hr_max", "hr_lthr",
               "hr_zone1_low", "hr_zone1_high", "hr_zone2_low", "hr_zone2_high", "hr_zone3_low",
               "hr_zone3_high", "hr_zone4_low", "hr_zone4_high", "hr_zone5_low",
               "health_screen_flags", "medical_clearance_at", "welcomed_at",
               "baseline_continuous_run_min", "baseline_weekly_run_min"}
    bad = set(fields) - allowed
    if bad:
        raise ToolError(f"not updatable: {sorted(bad)}; allowed: {sorted(allowed)}")
    if any(k.startswith("hr_zone") and k not in ("hr_zone_source", "hr_zone_basis_notes") for k in fields) \
            and not (fields.get("hr_zone_source") and fields.get("hr_zone_basis_notes")):
        raise ToolError("setting HR zones requires hr_zone_source and hr_zone_basis_notes "
                        "(explain how the numbers were derived)")
    if fields.get("easy_pace_pref_sec_per_km") and not fields.get("easy_pace_pref_source"):
        fields["easy_pace_pref_source"] = "athlete_specified"
    if "weight_kg" in fields:
        fields.setdefault("weight_updated_at", _now()[:10])
    vals = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in fields.items()}
    vals["updated_at"] = _now()
    conn = connect()
    _get_athlete(conn, athlete_id)
    conn.execute(f"UPDATE athlete_profile SET {', '.join(f'{k}=?' for k in vals)} WHERE athlete_id=?",
                 (*vals.values(), athlete_id))
    conn.commit()
    return {"ok": True, "athlete": _get_athlete(conn, athlete_id)}


def record_course_info(race_id, elevation_gain_m=None, terrain_notes=None, distance_km=None,
                       location=None, source=None):
    conn = connect()
    goal = _row(conn.execute("SELECT * FROM race_target WHERE race_id=?", (race_id,)).fetchone())
    if not goal:
        raise ToolError(f"no race_target {race_id!r}")
    if goal["goal_type"] != "race":
        raise ToolError("this goal is general fitness; there is no course")
    notes = terrain_notes
    if source:
        notes = f"{terrain_notes or ''} [source: {source}]".strip()
    sets = {k: v for k, v in dict(elevation_gain_m=elevation_gain_m, terrain_notes=notes,
                                  distance_km=distance_km, location=location).items() if v is not None}
    if sets:
        conn.execute(f"UPDATE race_target SET {', '.join(f'{k}=?' for k in sets)} WHERE race_id=?",
                     (*sets.values(), race_id))
        conn.commit()
    return {"ok": True, "race_target": _row(conn.execute("SELECT * FROM race_target WHERE race_id=?",
                                                         (race_id,)).fetchone())}


def _last_week_level(conn, athlete_id, before):
    """The general-fitness level of the 7 days before `before`, so a week
    can be repeated without the agent reverse-engineering ladder rungs."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM program WHERE athlete_id=? AND status!='superseded' AND session_date BETWEEN ? AND ?",
        (athlete_id, (before - timedelta(days=7)).isoformat(), (before - timedelta(days=1)).isoformat()))]
    if not rows:
        raise ToolError(f"no sessions in the week before {before}; pass general_fitness_start_level explicitly")
    walk = [r for r in rows if r["session_type"] == "walk_run"]
    if walk:
        for i, rung in enumerate(WALK_RUN_LADDER):
            if _walk_run_label(*rung) in (walk[0]["session_label"] or ""):
                return {"start_step": i}
        raise ToolError("couldn't identify the walk/run step; pass general_fitness_start_level.start_step")
    return {"start_weekly_min": sum(r["prescribed_duration_min"] or 0 for r in rows)}


def generate_program(athlete_id, race_id, reason, constraints=None, general_fitness_start_level=None,
                     start_date=None, changed_by="agent"):
    conn = connect()
    if constraints is None:
        # Reuse the settings of this goal's last plan, so a rebuild (weekly review,
        # repeat week, time trial) never forgets whole-km distances, days/week, etc.
        prev = conn.execute("SELECT constraints_json FROM program_revision WHERE race_id=? AND constraints_json "
                            "IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT 1", (race_id,)).fetchone()
        constraints = json.loads(prev["constraints_json"]) if prev else {}
    # Baseline overrides (a review rebuilding from actual running) apply to this
    # build only; they're never saved as a lasting plan setting.
    constraints = dict(constraints)
    overrides = {k: constraints.pop(k) for k in ("baseline_weekly_km", "longest_recent_km") if k in constraints}
    athlete = _get_athlete(conn, athlete_id)
    goal = _row(conn.execute("SELECT * FROM race_target WHERE race_id=? AND athlete_id=?",
                             (race_id, athlete_id)).fetchone())
    if not goal:
        raise ToolError(f"no goal {race_id!r} for athlete {athlete_id!r}")
    if goal["status"] != "active":
        raise ToolError(f"goal {race_id!r} is {goal['status']!r}; generate for the active goal")
    if athlete.get("safety_screen_status") == "pending_review":
        return {"ok": False, "reason": "the athlete's free-text answers haven't had their safety review yet",
                "suggestion": "Call get_review_tasks, run the reviewer with delegate_task, then record_safety_review."}
    if goal["goal_type"] == "race" and goal.get("verification_status") in ("unverified", "mismatch"):
        return {"ok": False, "reason": ("the race details haven't been verified yet" if goal["verification_status"]
                                        == "unverified" else "the two race sources disagree: " +
                                        "; ".join(json.loads(goal.get("verification_json") or "{}")
                                                  .get("differences", []))),
                "suggestion": ("Call get_review_tasks and verify_race_info." if goal["verification_status"] ==
                               "unverified" else "Show the owner the differences; they confirm the right details "
                                                 "with confirm_race_info.")}
    flags = json.loads(athlete.get("health_screen_flags") or "[]")
    if flags and not athlete.get("medical_clearance_at"):
        return {"ok": False, "reason": "health screen flagged " + ", ".join(flags)
                + " and no medical clearance recorded",
                "suggestion": "Ask the athlete for medical clearance; then update_athlete_profile("
                              "medical_clearance_at=<date>) and call generate_program again."}
    intake = _latest_intake(conn, athlete_id)
    start = date.fromisoformat(start_date) if start_date else _next_monday(_today())
    long_run_day = constraints.get("fixed_long_run_day") or (
        str(intake.get("long_run_day"))[:3].title() if intake.get("long_run_day") else None)
    avoid_days = constraints.get("avoid_days") or _days(intake.get("avoid_days"))

    if goal["goal_type"] == "general_fitness":
        level = dict(general_fitness_start_level or {})
        if level.pop("repeat_last_week", False):
            level = _last_week_level(conn, athlete_id, start)
        spw = constraints.get("sessions_per_week") or int(_num(intake.get("sessions_per_week")) or 3)
        if athlete.get("baseline_continuous_run_min") is None:
            raise ToolError("baseline_continuous_run_min is unknown; ask the athlete how long they can "
                            "run without stopping and set it with update_athlete_profile")
        plan = generate_general_fitness_plan(
            start.isoformat(), athlete["baseline_continuous_run_min"],
            athlete.get("baseline_weekly_run_min") or 0, sessions_per_week=spw,
            long_run_day=long_run_day, avoid_days=avoid_days,
            hr_zone2_low=athlete.get("hr_zone2_low"), hr_zone2_high=athlete.get("hr_zone2_high"),
            start_step=level.get("start_step"), start_weekly_min=level.get("start_weekly_min"))
        plan["ok"], plan["time_trial_row_indexes"] = True, []
    else:
        real = _recent_running(conn, athlete_id, start)
        real_note = None
        if overrides.get("baseline_weekly_km") is None and real:
            overrides = {"baseline_weekly_km": real["weekly_km"], "longest_recent_km": real["longest_km"]}
            real_note = (f" Starting point from {real['runs']} logged runs in the 4 weeks before the plan: "
                         f"{real['weekly_km']:g} km/week, longest {real['longest_km']:g} km (form said "
                         f"{_num(intake.get('total_km_4wk') or 0) / 4:g} km/week, longest "
                         f"{_num(intake.get('longest_run_km') or 0):g} km).")
        override = overrides.get("baseline_weekly_km") is not None
        if not override and (intake.get("total_km_4wk") is None or intake.get("longest_run_km") is None):
            raise ToolError("race plan needs the athlete's longest run and total km over the last "
                            "4 weeks; ask them, then resubmit the intake with longest_run_km / total_km_4wk")
        plan = generate_race_plan(
            start.isoformat(), goal["race_date"], goal["distance_km"],
            baseline_weekly_km=(overrides["baseline_weekly_km"] if override
                                else _num(intake["total_km_4wk"]) / 4),
            longest_recent_km=(overrides.get("longest_recent_km") or _num(intake.get("longest_run_km")) or 3.0)
            if override else _num(intake["longest_run_km"]),
            elevation_gain_m=goal.get("elevation_gain_m"),
            days_per_week=(constraints.get("max_days_per_week") or constraints.get("min_days_per_week")
                           or int(_num(intake.get("sessions_per_week")) or 4)),
            long_run_day=long_run_day or "Sun", avoid_days=avoid_days,
            whole_number_distances=bool(constraints.get("whole_number_distances")),
            min_session_km=constraints.get("min_session_km") or 3.0,
            hr_zones={"zone2_high": athlete.get("hr_zone2_high"),
                      "zone4_low": athlete.get("hr_zone4_low"),
                      "zone4_high": athlete.get("hr_zone4_high")})
        if not plan["ok"]:
            return plan
        if real_note:
            plan["summary"] += real_note
        if goal.get("course_url") and goal.get("elevation_gain_m") is None:
            plan["summary"] += (" WARNING: course elevation not recorded yet -- generated as a flat "
                                "course; call record_course_info and regenerate if it's hilly.")

    now = _now()
    revision_id = _id("rev")
    conn.execute("INSERT INTO program_revision (revision_id, athlete_id, race_id, created_at, reason, changed_by, "
                 "summary, constraints_json) VALUES (?,?,?,?,?,?,?,?)",
                 (revision_id, athlete_id, race_id, now, reason, changed_by, plan["summary"],
                  json.dumps(constraints)))
    # Never create sessions in the past: a rebuild that starts on a Monday
    # already under way only replaces today onward.
    first_day = max(start, _today()).isoformat()
    tt_rows = {id(plan["rows"][i]) for i in plan.get("time_trial_row_indexes", [])}
    plan["rows"] = [r for r in plan["rows"] if r["session_date"] >= first_day]
    superseded = conn.execute(
        "UPDATE program SET status='superseded' WHERE athlete_id=? AND status='pending' AND session_date>=?",
        (athlete_id, first_day)).rowcount
    cancelled = conn.execute(
        "UPDATE checkin_trigger SET status='stale_cancelled' WHERE athlete_id=? AND status='pending' "
        "AND origin='generator'", (athlete_id,)).rowcount

    row_ids = []
    for r in plan["rows"]:
        rid = _id("prog")
        row_ids.append(rid)
        conn.execute(
            "INSERT INTO program (program_row_id, athlete_id, race_id, revision_id, week_num, session_date, "
            "day_of_week, session_type, session_label, prescribed_distance_km, prescribed_duration_min, "
            "prescribed_effort_desc, prescribed_hr_low, prescribed_hr_high, elevation_target_m, week_phase, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, athlete_id, race_id, revision_id, r["week_num"], r["session_date"], r["day_of_week"],
             r["session_type"], r["session_label"], r["prescribed_distance_km"], r["prescribed_duration_min"],
             r["prescribed_effort_desc"], r["prescribed_hr_low"], r["prescribed_hr_high"],
             r["elevation_target_m"], r["week_phase"], now))

    # Check-ins that come with every plan: end of each week (missed
    # sessions can only be learned by asking), after each time trial,
    # and the end-of-block review for general fitness.
    triggers = []
    last_by_week = {}
    for r in plan["rows"]:
        last_by_week[r["week_num"]] = max(last_by_week.get(r["week_num"], ""), r["session_date"])
    gf = goal["goal_type"] == "general_fitness"
    for wk, last in sorted(last_by_week.items()):
        wk_monday = date.fromisoformat(last) - timedelta(days=date.fromisoformat(last).weekday())
        prompt = (f"Week {wk} check-in (week of {wk_monday.isoformat()}): ask three quick questions -- how did the "
                  f"week feel (comfortable / hard / too hard), any pain (none / a niggle / pain that changes how "
                  f"you walk or run), and have you been ill? Ask for files of any runs not yet sent. Call "
                  f"get_trends and include the 1-3 most useful feedback lines (or, if building_baseline, when "
                  f"trends start). When they answer, call review_week with week_start={wk_monday.isoformat()} and "
                  f"their feedback -- it readjusts next week's plan -- then tell them what changes and why. ")
        triggers.append(("scheduled_date", (date.fromisoformat(last) + timedelta(days=1)).isoformat(), None, prompt))
        backstop = (f"Backstop for week of {wk_monday.isoformat()}: call review_week with "
                    f"week_start={wk_monday.isoformat()} and no feedback (data only). If it says already_reviewed, "
                    f"reply [SILENT]. Otherwise tell the athlete in one or two lines what changed in their plan and "
                    f"why, and invite them to reply if something's up.")
        triggers.append(("scheduled_date", (wk_monday + timedelta(days=9)).isoformat(), None, backstop))
    for r, rid in zip(plan["rows"], row_ids):
        if id(r) not in tt_rows:
            continue
        triggers.append(("after_program_row", None, rid,
                         "Time trial done: get the file, compare it, then reset effort/pace targets as a RANGE "
                         "from this result AND long-run HR drift (never a Riegel-style conversion alone). "
                         "Record the change with write_program_revision."))
    if gf:
        triggers.append(("scheduled_date", goal["review_date"] or (start + timedelta(weeks=GF_WEEKS)).isoformat(),
                         None, "End of general-fitness block: ask how they feel and what's next -- another "
                               "block from their current level, or switch to a race goal (new intake)."))
    for ttype, fires, dep_row, prompt in triggers:
        conn.execute("INSERT INTO checkin_trigger (trigger_id, athlete_id, trigger_type, fires_at_date, "
                     "depends_on_program_row, revision_id_at_creation, prompt_template, origin, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (_id("trig"), athlete_id, ttype, fires, dep_row, revision_id, prompt, "generator", now))
    if gf:
        conn.execute("UPDATE race_target SET review_date=? WHERE race_id=?",
                     ((start + timedelta(weeks=GF_WEEKS)).isoformat(), race_id))
    conn.commit()

    weeks = {}
    for r in plan["rows"]:
        w = weeks.setdefault(r["week_num"], {"week": r["week_num"], "phase": r["week_phase"],
                                             "sessions": 0, "total_km": 0.0, "total_min": 0.0})
        w["sessions"] += 1
        w["total_km"] += r["prescribed_distance_km"] or 0
        w["total_min"] += r["prescribed_duration_min"] or 0
    unit = "total_min" if gf else "total_km"
    overview = [{"week": w["week"], "phase": w["phase"], "sessions": w["sessions"],
                 unit: round(w[unit], 1)} for w in weeks.values()]
    return {"ok": True, "revision_id": revision_id, "goal_type": goal["goal_type"],
            "mode": plan.get("mode", "race"), "start_date": start.isoformat(), "summary": plan["summary"],
            "weeks": overview, "first_week": [r for r in plan["rows"] if r["week_num"] == 1],
            "rows_written": len(row_ids), "rows_superseded": superseded,
            "checkins_created": len(triggers), "old_generator_checkins_cancelled": cancelled}


def parse_run_file(file_path, athlete_id, file_format="auto", athlete_notes=None):
    """Parse a run file, log it in coach.db AND record it permanently in
    history.db (original file, all samples, the upload attempt). Every
    outcome -- recorded, duplicate, or unreadable -- is kept in history."""
    conn = connect()
    athlete = _get_athlete(conn, athlete_id)
    if not os.path.exists(file_path):
        raise ToolError(f"file not found: {file_path}")
    hconn = _hconnect()
    name, sha = os.path.basename(file_path), history.file_sha256(file_path)
    fmt = file_format
    if fmt == "auto":
        with open(file_path, "rb") as f:
            head = f.read(64)
        fmt = "fit" if b".FIT" in head[:14] else "gpx" if b"<" in head else os.path.splitext(file_path)[1][1:].lower()
    if fmt not in ("fit", "gpx"):
        history.record_attempt(hconn, athlete_id, name, sha, "parse_failed", detail="unrecognised format")
        hconn.commit()
        raise ToolError(f"can't tell the format of {file_path}; pass file_format 'fit' or 'gpx'")
    try:
        parsed = parse_fit(file_path) if fmt == "fit" else parse_gpx(file_path)
    except Exception as e:                      # corrupt / truncated file
        history.record_attempt(hconn, athlete_id, name, sha, "parse_failed", detail=f"{type(e).__name__}: {e}")
        hconn.commit()
        return {"ok": False, "error": f"couldn't read this {fmt} file ({type(e).__name__}); ask the athlete to "
                                      f"re-export it. The attempt is recorded in history."}
    summary = {k: v for k, v in parsed.items() if k != "points"}
    if not summary.get("recorded_at"):
        history.record_attempt(hconn, athlete_id, name, sha, "parse_failed", detail=summary.get("parser_notes"))
        hconn.commit()
        return {"ok": False, "parser_notes": summary.get("parser_notes"),
                "error": "file has no usable timestamps/records; ask the athlete for a different export"}
    dup = _find_duplicate_run(conn, athlete_id, summary["recorded_at"], summary.get("distance_km"))
    if dup:
        # Make sure it's in history too (e.g. logged before history existed).
        hid, _ = history.record_run(hconn, athlete, file_path, fmt, parsed, dup["run_log_id"], athlete_notes)
        history.record_attempt(hconn, athlete_id, name, sha, "duplicate", hid)
        hconn.commit()
        return {"ok": True, "duplicate": True, "run_log_id": dup["run_log_id"], "history_id": hid,
                "summary": summary, "note": "this run was already logged; not inserted again"}
    rid, hid = _log_run(conn, hconn, athlete, parsed, fmt, name, athlete_notes, path=file_path)
    history.record_attempt(hconn, athlete_id, name, sha, "recorded", hid)
    hconn.commit()
    return {"ok": True, "duplicate": False, "run_log_id": rid, "history_id": hid, "summary": summary,
            "data_quality_warning": summary.get("parser_notes")}


def _find_duplicate_run(conn, athlete_id, recorded_at, distance_km):
    """Same run from two sources (a Strava sync and an uploaded file) starts within
    a couple of minutes and has about the same distance."""
    t = datetime.fromisoformat(recorded_at)
    for r in conn.execute("SELECT run_log_id, recorded_at, distance_km FROM run_log WHERE athlete_id=? AND "
                          "substr(recorded_at,1,10) BETWEEN ? AND ?",
                          (athlete_id, (t - timedelta(days=1)).date().isoformat(),
                           (t + timedelta(days=1)).date().isoformat())):
        dt = abs((datetime.fromisoformat(r["recorded_at"]) - t).total_seconds())
        if dt <= 180 and (distance_km is None or r["distance_km"] is None or
                          abs(r["distance_km"] - distance_km) <= max(0.3, 0.05 * distance_km)):
            return r
    return None


def _log_run(conn, hconn, athlete, parsed, fmt, source_name, athlete_notes, path=None, raw=None, external_id=None):
    """Write one run to history.db (first -- it's the permanent record) and coach.db."""
    summary = {k: v for k, v in parsed.items() if k != "points"}
    rid = _id("run")
    hid, _ = history.record_run(hconn, athlete, path, fmt, parsed, rid, athlete_notes, raw=raw,
                                source_name=source_name)
    hconn.commit()
    conn.execute(
        "INSERT INTO run_log (run_log_id, athlete_id, source_file, source_format, recorded_at, distance_km, "
        "elapsed_time_min, moving_time_min, avg_hr, max_hr, elevation_gain_m, elevation_loss_m, "
        "avg_pace_sec_per_km, splits_json, first_half_avg_hr, second_half_avg_hr, hr_drift_delta, "
        "parser_notes, athlete_notes, created_at, external_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, athlete["athlete_id"], source_name, fmt, summary["recorded_at"], summary.get("distance_km"),
         summary.get("elapsed_time_min"), summary.get("moving_time_min"), summary.get("avg_hr"),
         summary.get("max_hr"), summary.get("elevation_gain_m"), summary.get("elevation_loss_m"),
         summary.get("avg_pace_sec_per_km"), json.dumps(summary.get("splits"), default=str),
         summary.get("first_half_avg_hr"), summary.get("second_half_avg_hr"), summary.get("hr_drift_delta"),
         summary.get("parser_notes"), athlete_notes, _now(), external_id))
    conn.commit()
    return rid, hid


def match_run_to_program(athlete_id, run_log_id=None, run_recorded_at=None, run_distance_km=None):
    conn = connect()
    if run_log_id:
        run = _row(conn.execute("SELECT * FROM run_log WHERE run_log_id=?", (run_log_id,)).fetchone())
        if not run:
            raise ToolError(f"no run_log {run_log_id!r}")
        run_recorded_at, run_distance_km = run["recorded_at"], run["distance_km"]
    if not run_recorded_at:
        raise ToolError("pass run_log_id (preferred) or run_recorded_at")
    d = datetime.fromisoformat(run_recorded_at).date()
    candidates = [dict(r) for r in conn.execute(
        "SELECT * FROM program WHERE athlete_id=? AND status='pending' AND session_date BETWEEN ? AND ?",
        (athlete_id, (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()))]
    match = analysis.match_run_to_program_row(run_recorded_at, run_distance_km, candidates)
    if run_log_id:
        hconn = _hconnect()
        hid = history.history_id_for(hconn, run_log_id)
        if hid:
            history.add_event(hconn, hid, "matched" if match else "unmatched",
                              {"program_row_id": match["program_row_id"], "session_type": match["session_type"],
                               "session_date": match["session_date"]} if match else None)
            hconn.commit()
    return {"ok": True, "match": match,
            "note": None if match else "No pending session within a day. Ask the athlete what this run "
                                       "was; don't guess. You can still call compare_run_to_program "
                                       "without a program_row_id to log it."}


def compare_run_to_program(run_log_id, program_row_id=None):
    """Compares, links the run to the session (marking it done), and
    updates every pattern signal in one go -- so the streaks can't be
    skipped or double-counted."""
    conn = connect()
    run = _row(conn.execute("SELECT * FROM run_log WHERE run_log_id=?", (run_log_id,)).fetchone())
    if not run:
        raise ToolError(f"no run_log {run_log_id!r}")
    prog = None
    if program_row_id:
        prog = _row(conn.execute("SELECT * FROM program WHERE program_row_id=?", (program_row_id,)).fetchone())
        if not prog:
            raise ToolError(f"no program row {program_row_id!r}")
        if prog["status"] not in ("pending", "done", "missed"):
            raise ToolError(f"program row is {prog['status']!r}; match against a current session")
    comp = analysis.compare_run_to_program(run, prog)
    now = _now()
    if prog:
        conn.execute("UPDATE program SET status='done', matched_run_log_id=? WHERE program_row_id=?",
                     (run_log_id, program_row_id))
        conn.execute("UPDATE run_log SET matched_program_row_id=? WHERE run_log_id=?",
                     (program_row_id, run_log_id))

    signals = {}
    for name in analysis.SIGNALS:
        cur = _row(conn.execute("SELECT * FROM signal_state WHERE athlete_id=? AND signal_name=?",
                                (run["athlete_id"], name)).fetchone())
        if cur and cur["last_run_log_id"] == run_log_id:
            signals[name] = {**cur, "note": "already counted this run"}
            continue
        new = analysis.update_signal_state(cur, name, now, comparison=comp, run_log_id=run_log_id)
        if new["updated"]:
            _save_signal(conn, run["athlete_id"], new)
            new["flagged_this_event"] = new["current_streak"] > (cur["current_streak"] if cur else 0)
            _log_signal_event(conn, run["athlete_id"], new, run["recorded_at"],
                              run_log_id=run_log_id, program_row_id=program_row_id)
        signals[name] = new
    conn.commit()
    hconn = _hconnect()
    hid = history.history_id_for(hconn, run_log_id)
    if hid:
        history.add_event(hconn, hid, "compared", {
            "program_row_id": program_row_id,
            "planned": {k: prog.get(k) for k in ("session_type", "session_label", "prescribed_distance_km",
                                                 "prescribed_duration_min", "session_date")} if prog else None,
            "comparison": comp.__dict__,
            "signals": {n: {k: v.get(k) for k in ("current_streak", "action_threshold_met")}
                        for n, v in signals.items()}})
        hconn.commit()
    return {"ok": True, "comparison": comp.__dict__, "session": prog, "signals": signals,
            "actions_needed": [n for n, s in signals.items() if s.get("action_threshold_met")]}


def _log_signal_event(conn, athlete_id, st, occurred_at, run_log_id=None, program_row_id=None):
    """occurred_at = when it happened (run start / session date), not when logged."""
    conn.execute("INSERT INTO signal_event VALUES (?,?,?,?,?,?,?,?,?)",
                 (_id("sig"), athlete_id, st["signal_name"], occurred_at, run_log_id, program_row_id,
                  int(bool(st.get("flagged_this_event"))),
                  st["current_streak"], int(st["action_threshold_met"])))


def _save_signal(conn, athlete_id, st):
    conn.execute("INSERT INTO signal_state (athlete_id, signal_name, current_streak, last_run_log_id, "
                 "last_updated_at) VALUES (?,?,?,?,?) ON CONFLICT(athlete_id, signal_name) DO UPDATE SET "
                 "current_streak=excluded.current_streak, last_run_log_id=excluded.last_run_log_id, "
                 "last_updated_at=excluded.last_updated_at",
                 (athlete_id, st["signal_name"], st["current_streak"], st["last_run_log_id"], st["last_updated_at"]))


def record_missed_session(program_row_id, athlete_note=None):
    conn = connect()
    prog = _row(conn.execute("SELECT * FROM program WHERE program_row_id=?", (program_row_id,)).fetchone())
    if not prog:
        raise ToolError(f"no program row {program_row_id!r}")
    if prog["status"] != "pending":
        raise ToolError(f"session is {prog['status']!r}, not pending")
    conn.execute("UPDATE program SET status='missed' WHERE program_row_id=?", (program_row_id,))
    name = "missed_easy_run_streak"
    cur = _row(conn.execute("SELECT * FROM signal_state WHERE athlete_id=? AND signal_name=?",
                            (prog["athlete_id"], name)).fetchone())
    st = analysis.update_signal_state(cur, name, _now(), missed_program_row=prog)
    if st["updated"]:
        _save_signal(conn, prog["athlete_id"], st)
        st["flagged_this_event"] = True
        _log_signal_event(conn, prog["athlete_id"], st, prog["session_date"], program_row_id=program_row_id)
    if athlete_note:
        conn.execute("UPDATE signal_state SET notes=? WHERE athlete_id=? AND signal_name=?",
                     (athlete_note, prog["athlete_id"], name))
    conn.commit()
    return {"ok": True, "session": {**prog, "status": "missed"}, "signal": st,
            "reminder": "Missed volume is simply missed -- never add it to another session."}


def get_upcoming_sessions(athlete_id, start_date=None, end_date=None):
    conn = connect()
    s = start_date or _today().isoformat()
    e = end_date or (date.fromisoformat(s) + timedelta(days=6)).isoformat()
    rows = conn.execute("SELECT * FROM program WHERE athlete_id=? AND session_date BETWEEN ? AND ? "
                        "AND status NOT IN ('superseded') ORDER BY session_date", (athlete_id, s, e)).fetchall()
    return {"ok": True, "start_date": s, "end_date": e, "sessions": [dict(r) for r in rows]}


def write_program_revision(athlete_id, race_id, reason, changed_by, summary,
                           affected_program_row_ids=None, new_sessions=None, dry_run=False):
    """Supersede some sessions and add replacements (a move = supersede
    the old row + add one on the new date). dry_run=True only reports
    warnings (e.g. two hard sessions on adjacent days) so you can check
    before agreeing to an athlete's request."""
    affected_program_row_ids = affected_program_row_ids or []
    new_sessions = new_sessions or []
    conn = connect()
    _get_athlete(conn, athlete_id)
    affected = []
    for pid in affected_program_row_ids:
        r = _row(conn.execute("SELECT * FROM program WHERE program_row_id=? AND athlete_id=?",
                              (pid, athlete_id)).fetchone())
        if not r:
            raise ToolError(f"no program row {pid!r} for this athlete")
        if r["status"] != "pending":
            raise ToolError(f"{pid} is {r['status']!r}; only pending sessions can be changed")
        affected.append(r)
    remaining = [dict(r) for r in conn.execute(
        "SELECT * FROM program WHERE athlete_id=? AND status IN ('pending','done')", (athlete_id,))
        if r["program_row_id"] not in affected_program_row_ids]

    warnings, prepared = [], []
    for s in new_sessions:
        if s.get("session_type") not in SESSION_TYPES:
            raise ToolError(f"session_type must be one of {sorted(SESSION_TYPES)}: {s}")
        if not s.get("session_date"):
            raise ToolError(f"session_date required: {s}")
        d = date.fromisoformat(s["session_date"])
        week_num = s.get("week_num")
        if week_num is None:
            same = [a for a in affected if a["session_date"] == s["session_date"]] or affected
            ref = same[0] if same else (remaining[0] if remaining else None)
            week_num = (ref["week_num"] + (d - date.fromisoformat(ref["session_date"])).days // 7) if ref else 1
        if s["session_type"] in HARD_TYPES:
            for o in remaining + prepared:
                if o["session_type"] in HARD_TYPES and abs((date.fromisoformat(o["session_date"]) - d).days) <= 1:
                    warnings.append(f"{s['session_type']} on {s['session_date']} is next to "
                                    f"{o['session_type']} on {o['session_date']} -- two hard days back to back.")
        for o in remaining + prepared:
            if o["session_date"] == s["session_date"] and o["session_type"] != "rest":
                warnings.append(f"{s['session_date']} already has a {o['session_type']} session.")
        prepared.append({**s, "week_num": week_num, "day_of_week": DAY_NAMES[d.weekday()]})
    if dry_run:
        return {"ok": True, "dry_run": True, "warnings": warnings, "would_supersede": affected_program_row_ids,
                "would_add": prepared}

    now = _now()
    revision_id = _id("rev")
    prev_c = conn.execute("SELECT constraints_json FROM program_revision WHERE race_id=? ORDER BY created_at DESC, "
                          "rowid DESC LIMIT 1", (race_id,)).fetchone()
    conn.execute("INSERT INTO program_revision (revision_id, athlete_id, race_id, created_at, reason, changed_by, "
                 "summary, constraints_json) VALUES (?,?,?,?,?,?,?,?)",
                 (revision_id, athlete_id, race_id, now, reason, changed_by, summary,
                  prev_c["constraints_json"] if prev_c else None))
    for pid in affected_program_row_ids:
        conn.execute("UPDATE program SET status='superseded' WHERE program_row_id=?", (pid,))
    added = []
    for s in prepared:
        rid = _id("prog")
        added.append(rid)
        conn.execute(
            "INSERT INTO program (program_row_id, athlete_id, race_id, revision_id, week_num, session_date, "
            "day_of_week, session_type, session_label, prescribed_distance_km, prescribed_duration_min, "
            "prescribed_effort_desc, prescribed_hr_low, prescribed_hr_high, elevation_target_m, week_phase, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, athlete_id, race_id, revision_id, s["week_num"], s["session_date"], s["day_of_week"],
             s["session_type"], s.get("session_label"), s.get("prescribed_distance_km"),
             s.get("prescribed_duration_min"), s.get("prescribed_effort_desc"), s.get("prescribed_hr_low"),
             s.get("prescribed_hr_high"), s.get("elevation_target_m"), s.get("week_phase"), now))
    # A replacement of the same type counts as a move: record where it went
    # and re-point any pending check-in (e.g. after a time trial) to it.
    repointed = 0
    for a in affected:
        k = next((i for i, p in enumerate(prepared) if p["session_type"] == a["session_type"]), None)
        if k is None:
            continue
        conn.execute("UPDATE program SET moved_to_date=? WHERE program_row_id=?",
                     (prepared[k]["session_date"], a["program_row_id"]))
        repointed += conn.execute("UPDATE checkin_trigger SET depends_on_program_row=? WHERE status='pending' "
                                  "AND depends_on_program_row=?", (added[k], a["program_row_id"])).rowcount
    conn.commit()
    return {"ok": True, "revision_id": revision_id, "superseded": affected_program_row_ids,
            "added_program_row_ids": added, "checkins_repointed": repointed, "warnings": warnings}


def schedule_checkin(athlete_id, trigger_type, prompt_template, fires_at_date=None,
                     depends_on_program_row=None, depends_on_signal=None, depends_on_streak_count=None):
    if trigger_type not in ("scheduled_date", "after_program_row", "after_signal_streak"):
        raise ToolError("trigger_type must be scheduled_date | after_program_row | after_signal_streak")
    need = {"scheduled_date": fires_at_date, "after_program_row": depends_on_program_row,
            "after_signal_streak": depends_on_signal and depends_on_streak_count}[trigger_type]
    if not need:
        raise ToolError(f"{trigger_type} needs its matching field(s)")
    conn = connect()
    rev = _current_revision(conn, athlete_id)
    if not rev:
        raise ToolError("athlete has no program yet; generate_program first")
    tid = _id("trig")
    conn.execute("INSERT INTO checkin_trigger (trigger_id, athlete_id, trigger_type, fires_at_date, "
                 "depends_on_program_row, depends_on_signal, depends_on_streak_count, revision_id_at_creation, "
                 "prompt_template, origin, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (tid, athlete_id, trigger_type, fires_at_date, depends_on_program_row, depends_on_signal,
                  depends_on_streak_count, rev["revision_id"], prompt_template, "agent", _now()))
    conn.commit()
    return {"ok": True, "trigger_id": tid, "revision_id_at_creation": rev["revision_id"]}


def get_due_checkins(as_of_date=None, athlete_id=None):
    """What should fire now. Call on a schedule (e.g. daily); for each
    result, call check_trigger_staleness BEFORE acting on it."""
    conn = connect()
    today = as_of_date or _today().isoformat()
    q = "SELECT * FROM checkin_trigger WHERE status='pending'" + (" AND athlete_id=?" if athlete_id else "")
    due = []
    for t in conn.execute(q, (athlete_id,) if athlete_id else ()):
        t = dict(t)
        if t["trigger_type"] == "scheduled_date":
            ready = t["fires_at_date"] <= today
        elif t["trigger_type"] == "after_program_row":
            p = conn.execute("SELECT status, session_date FROM program WHERE program_row_id=?",
                             (t["depends_on_program_row"],)).fetchone()
            ready = bool(p) and (p["status"] in ("done", "missed", "superseded") or p["session_date"] < today)
        else:
            s = conn.execute("SELECT current_streak FROM signal_state WHERE athlete_id=? AND signal_name=?",
                             (t["athlete_id"], t["depends_on_signal"])).fetchone()
            ready = bool(s) and s["current_streak"] >= (t["depends_on_streak_count"] or 0)
        if ready:
            due.append(t)
    return {"ok": True, "as_of": today, "due": due}


def check_trigger_staleness(trigger_id):
    """Call FIRST on every fired check-in. Marks it 'fired', or
    'stale_cancelled' if the session it depends on was replaced. If
    `stale` is true the plan has changed since it was written: tell the
    athlete, and answer from get_athlete_summary, not the old prompt."""
    conn = connect()
    t = _row(conn.execute("SELECT * FROM checkin_trigger WHERE trigger_id=?", (trigger_id,)).fetchone())
    if not t:
        raise ToolError(f"no trigger {trigger_id!r}")
    rev = _current_revision(conn, t["athlete_id"])
    stale = analysis.check_program_revision_staleness(t["revision_id_at_creation"],
                                                      rev["revision_id"] if rev else None)
    dep_superseded = False
    if t["depends_on_program_row"]:
        p = conn.execute("SELECT status FROM program WHERE program_row_id=?", (t["depends_on_program_row"],)).fetchone()
        dep_superseded = bool(p) and p["status"] == "superseded"
    # Only cancel when the session it depends on no longer exists. Any other
    # check-in still fires after a revision (e.g. one moved session), flagged
    # as stale so the agent answers from the current plan. Full regenerations
    # cancel their predecessor's generator check-ins up front.
    cancel = dep_superseded
    status = "stale_cancelled" if cancel else "fired"
    conn.execute("UPDATE checkin_trigger SET status=? WHERE trigger_id=?", (status, trigger_id))
    conn.commit()
    return {"ok": True, "stale": stale or dep_superseded, "status": status, "trigger": t,
            "current_revision": rev,
            "instruction": ("Plan changed since this check-in was written: tell the athlete, and answer "
                            "from the current plan (get_athlete_summary), not the prompt below."
                            if (stale or dep_superseded) else "Plan unchanged; act on the prompt.")}


def _onboarding_status(conn, athlete):
    goal = _active_goal(conn, athlete["athlete_id"])
    flags = json.loads(athlete.get("health_screen_flags") or "[]")
    has_plan = bool(goal) and bool(conn.execute("SELECT 1 FROM program_revision WHERE race_id=?",
                                                (goal["race_id"],)).fetchone())
    if not goal:
        return "no_goal"
    if athlete.get("safety_screen_status") == "pending_review":
        return "awaiting_safety_review"
    if flags and not athlete.get("medical_clearance_at"):
        return "awaiting_medical_clearance"
    if goal["goal_type"] == "race" and goal.get("verification_status") in ("unverified", "mismatch"):
        return "awaiting_race_check" if goal["verification_status"] == "unverified" else "race_details_disputed"
    if not has_plan:
        return "plan_not_generated"
    if not athlete.get("chat_ref"):
        return "awaiting_telegram_link"
    if not athlete.get("welcomed_at"):
        return "welcome_pending"
    return "active"


def get_athlete_summary(athlete_id=None, email=None, chat_ref=None):
    """Everything the agent needs before replying: profile, active goal,
    current plan revision, this week and next, signals, recent runs."""
    conn = connect()
    if not athlete_id:
        if chat_ref:
            r = conn.execute("SELECT athlete_id FROM athlete_profile WHERE chat_ref=?", (chat_ref,)).fetchone()
            if not r:
                return {"ok": False, "error": f"no athlete linked to chat {chat_ref!r}",
                        "suggestion": "Ask for the email they used on the intake form, then link_chat. "
                                      "If they haven't filled it in, send them the form link."}
        else:
            r = conn.execute("SELECT athlete_id FROM athlete_profile WHERE lower(email)=lower(?)",
                             (email or "",)).fetchone()
            if not r:
                raise ToolError("pass athlete_id, chat_ref or a known email")
        athlete_id = r["athlete_id"]
    athlete = _get_athlete(conn, athlete_id)
    today = _today()
    wk_start = today - timedelta(days=today.weekday())
    return {"ok": True, "today": today.isoformat(), "athlete": athlete, "active_goal": _active_goal(conn, athlete_id),
            "current_revision": _current_revision(conn, athlete_id),
            "this_week": get_upcoming_sessions(athlete_id, wk_start.isoformat(),
                                               (wk_start + timedelta(days=6)).isoformat())["sessions"],
            "next_week": get_upcoming_sessions(athlete_id, (wk_start + timedelta(days=7)).isoformat(),
                                               (wk_start + timedelta(days=13)).isoformat())["sessions"],
            "signals": [dict(r) for r in conn.execute("SELECT * FROM signal_state WHERE athlete_id=?", (athlete_id,))],
            "recent_runs": [dict(r) for r in conn.execute(
                "SELECT run_log_id, recorded_at, distance_km, moving_time_min, avg_hr, hr_drift_delta, "
                "matched_program_row_id, parser_notes FROM run_log WHERE athlete_id=? "
                "ORDER BY recorded_at DESC LIMIT 5", (athlete_id,))],
            "pending_checkins": conn.execute("SELECT count(*) FROM checkin_trigger WHERE athlete_id=? AND "
                                             "status='pending'", (athlete_id,)).fetchone()[0],
            "latest_intake": _latest_intake(conn, athlete_id),
            "recent_other_activities": [
                {**{k: o[k] for k in ("start_at", "category", "sport_type", "name", "elapsed_min", "perceived_exertion")},
                 "focus": json.loads(o["strength_json"])["focus"] if o.get("strength_json") else None}
                for o in _other_activities(conn, athlete_id, (today - timedelta(days=14)).isoformat())],
            "strava": _row(conn.execute("SELECT status, connected_at, last_sync_at, last_error FROM strava_connection "
                                        "WHERE athlete_id=?", (athlete_id,)).fetchone()),
            "onboarding_status": _onboarding_status(conn, athlete)}


def ingest_sheet_rows(values, start_date=None):
    """Ingest Google Form responses read from the form's response Sheet
    (e.g. google-workspace `sheets get SHEET_ID "Form responses 1"`).
    `values` is the 2-D array: first row = question headers. Rows already
    ingested are skipped, so it's safe to pass the whole sheet every time."""
    if not values or len(values) < 2:
        return {"ok": True, "ingested": [], "duplicates": 0, "note": "no response rows"}
    header = [str(h).strip() for h in values[0]]
    ingested, dup, errors = [], 0, []
    for i, row in enumerate(values[1:], start=2):
        form = {h: (row[j] if j < len(row) else "") for j, h in enumerate(header) if h}
        # Checkbox answers arrive comma-joined in Sheets.
        for k, v in list(form.items()):
            if "health check" in k.lower() or "rather avoid" in k.lower():
                form[k] = [x.strip() for x in str(v).split(",") if x.strip()]
        res = call_tool("ingest_intake_form", {"form_response_json": form, "start_date": start_date})
        if not res.get("ok"):
            errors.append({"sheet_row": i, "error": res.get("error")})
        elif res.get("duplicate"):
            dup += 1
        else:
            ingested.append({"sheet_row": i, "athlete_id": res["athlete_id"], "race_id": res["race_id"],
                             "goal_type": res["goal_type"], "next_steps": res["next_steps"],
                             "warnings": res["warnings"]})
    return {"ok": True, "ingested": ingested, "duplicates": dup, "errors": errors}


def link_chat(chat_ref, athlete_id=None, email=None):
    """Tie an athlete to the chat they message from. chat_ref = the platform and
    user id shown in your Current Session Context (e.g. 'telegram:123456789')."""
    conn = connect()
    if not athlete_id:
        r = conn.execute("SELECT athlete_id FROM athlete_profile WHERE lower(email)=lower(?)", (email or "",)).fetchone()
        if not r:
            raise ToolError("no athlete with that email -- have they submitted the intake form?")
        athlete_id = r["athlete_id"]
    _get_athlete(conn, athlete_id)
    other = conn.execute("SELECT athlete_id FROM athlete_profile WHERE chat_ref=? AND athlete_id!=?",
                         (chat_ref, athlete_id)).fetchone()
    if other:
        raise ToolError(f"chat {chat_ref!r} is already linked to athlete {other['athlete_id']!r}")
    conn.execute("UPDATE athlete_profile SET chat_ref=?, updated_at=? WHERE athlete_id=?", (chat_ref, _now(), athlete_id))
    conn.commit()
    return {"ok": True, "athlete_id": athlete_id, "chat_ref": chat_ref}


GATE_TEMPLATE = '''#!/usr/bin/env python3
# Generated by run-coach install_checkin_gate. Cron pre-run gate for {athlete_id}:
# syncs their Strava (if connected), then wakes the agent only when there's
# something to say -- new activities to comment on, or a check-in due -- and
# never during quiet hours. Safe to delete (then remove the matching cron job).
import json, os, sys
from datetime import datetime
sys.path.insert(0, {scripts_dir!r})
os.environ.setdefault("COACH_DB", {db!r})
import coach_tools
QUIET_FROM, QUIET_UNTIL = 21, 7          # local time: no messages 21:00-07:00
aid = {athlete_id!r}
ctx = {{"athlete_id": aid}}
st = coach_tools.call_tool("strava_status", {{"athlete_id": aid}})
if any(c["status"] == "connected" for c in st.get("connections", [])):
    sync = coach_tools.call_tool("strava_sync", {{"athlete_id": aid}})
    err = ([a.get("error") for a in sync.get("athletes", []) if a.get("error")] or [sync.get("error")])[0]
    if err:
        ctx["strava_error"] = err
hour = int(os.environ.get("COACH_NOW_HOUR", datetime.now().hour))
if QUIET_UNTIL <= hour < QUIET_FROM:
    due = coach_tools.call_tool("get_due_checkins", {{"athlete_id": aid}}).get("due", [])
    pending = coach_tools.call_tool("get_pending_feedback", {{"athlete_id": aid}})
    if due:
        ctx["due_checkins"] = [{{"trigger_id": t["trigger_id"], "prompt": t["prompt_template"]}} for t in due]
    if pending.get("count"):
        ctx["new_activities"] = {{"runs": pending["runs"], "other": pending["other"]}}
    if due or pending.get("count") or ("strava_error" in ctx and "revoked" in ctx["strava_error"]):
        print(json.dumps({{"wakeAgent": True, "context": ctx}}, default=str))
        sys.exit(0)
print(json.dumps({{"wakeAgent": False}}))
'''


def install_checkin_gate(athlete_id):
    """Write the athlete's cron gate script into $HERMES_HOME/scripts and
    return the exact cronjob call to create. Call this FROM THE ATHLETE'S
    CHAT so the job's default delivery ('origin') is that chat."""
    conn = connect()
    athlete = _get_athlete(conn, athlete_id)
    home = hermes_home() or os.path.expanduser("~/.hermes")
    scripts = os.path.join(home, "scripts")
    os.makedirs(scripts, exist_ok=True)
    name = f"run-coach-due-{athlete_id}.py"
    with open(os.path.join(scripts, name), "w") as f:
        f.write(GATE_TEMPLATE.format(athlete_id=athlete_id, scripts_dir=KIT_DIR, db=db_path()))
    workdir = os.path.join(home, "run_coach")
    prompt = (f"Run-coach update for athlete {athlete_id} ({athlete['name']}). Load the run-coach skill and "
              f"follow playbook F with the pre-run context. new_activities: give short feedback on each new run "
              f"(real numbers and the plan comparison notes first, 2-3 lines each; group several), acknowledge "
              f"strength / cross-training briefly and plan around it, then mark_feedback_sent for every item you "
              f"covered. due_checkins: for EACH trigger_id call check_trigger_staleness first, then act. "
              f"strava_error saying revoked: ask them to reconnect (strava_connect_link). Your final response is "
              f"sent straight to the athlete: one friendly, short message with only what's for them. If nothing "
              f"needs saying, reply with only [SILENT].")
    return {"ok": True, "gate_script": os.path.join(scripts, name),
            "cronjob_call": {"action": "create", "name": f"run-coach checkins {athlete_id}",
                             "schedule": "0 * * * *", "script": name, "skills": ["run-coach"],
                             "workdir": workdir, "attach_to_session": True, "prompt": prompt},
            "instruction": "Create the job with your cronjob tool using cronjob_call exactly, from this "
                           "athlete's chat (delivery defaults to origin). It runs hourly: the gate script syncs "
                           "Strava and only wakes you for new activities or due check-ins, never 21:00-07:00. "
                           "If this athlete already has the job, the script was just updated in place; edit the "
                           "existing job's schedule to '0 * * * *' and its prompt to cronjob_call.prompt instead "
                           "of creating a second job."}


def _recent_running(conn, athlete_id, before):
    """Real running in the 4 weeks before `before` (e.g. imported from Strava),
    if there's enough of it (3+ runs) to plan from instead of the form answers."""
    rows = conn.execute("SELECT distance_km FROM run_log WHERE athlete_id=? AND substr(recorded_at,1,10) >= ? AND "
                        "substr(recorded_at,1,10) < ?", (athlete_id, (before - timedelta(days=28)).isoformat(),
                                                         before.isoformat())).fetchall()
    kms = [r[0] or 0 for r in rows]
    if len(kms) < 3:
        return None
    return {"runs": len(kms), "weekly_km": round(sum(kms) / 4, 1), "longest_km": round(max(kms), 1)}


def _week_numbers(conn, athlete_id, ws, gf):
    we = ws + timedelta(days=6)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM program WHERE athlete_id=? AND status IN ('pending','done','missed') AND session_type!='rest' "
        "AND session_date BETWEEN ? AND ?", (athlete_id, ws.isoformat(), we.isoformat()))]
    runs = [dict(r) for r in conn.execute(
        "SELECT distance_km, elapsed_time_min, moving_time_min FROM run_log WHERE athlete_id=? AND "
        "substr(recorded_at,1,10) BETWEEN ? AND ?", (athlete_id, ws.isoformat(), we.isoformat()))]
    if gf:
        planned = sum(r["prescribed_duration_min"] or 0 for r in rows)
        actual = sum((r["elapsed_time_min"] or r["moving_time_min"] or 0) for r in runs)
    else:
        planned = sum(r["prescribed_distance_km"] or 0 for r in rows)
        actual = sum(r["distance_km"] or 0 for r in runs)
    return {"planned": round(planned, 1), "actual": round(actual, 1), "sessions_planned": len(rows),
            "sessions_done": sum(1 for r in rows if r["status"] == "done"),
            "phases": sorted({r["week_phase"] for r in rows if r["week_phase"]}),
            "longest_planned": max((r["prescribed_distance_km"] or 0 for r in rows), default=0),
            "longest_actual": max((r["distance_km"] or 0 for r in runs), default=0)}


def review_week(athlete_id=None, chat_ref=None, week_start=None, feedback=None, apply=True, force=False):
    """The weekly readjustment. Reviews a finished week (default: last week)
    against the plan and, if needed, rebuilds the plan from the next Monday
    using what the athlete actually did. Runs at every end-of-week check-in
    (with the athlete's answers) and again as a data-only backstop 3 days
    later; a week is only ever reviewed once unless force=true.
    feedback: {"felt": comfortable|hard|too_hard, "pain": none|niggle|gait_changing, "ill": bool}"""
    conn = connect()
    if not athlete_id:
        r = conn.execute("SELECT athlete_id FROM athlete_profile WHERE chat_ref=?", (chat_ref or "",)).fetchone()
        if not r:
            raise ToolError("pass athlete_id, or a linked chat_ref")
        athlete_id = r["athlete_id"]
    _get_athlete(conn, athlete_id)
    goal = _active_goal(conn, athlete_id)
    if not goal:
        raise ToolError("athlete has no active goal")
    gf = goal["goal_type"] == "general_fitness"
    today = _today()
    ws = date.fromisoformat(week_start) if week_start else today - timedelta(days=today.weekday() + 7)
    ws -= timedelta(days=ws.weekday())
    if ws + timedelta(days=6) >= today:
        raise ToolError(f"the week of {ws} hasn't finished yet; review it from {ws + timedelta(days=7)}")
    done = conn.execute("SELECT * FROM plan_review WHERE athlete_id=? AND week_start=?",
                        (athlete_id, ws.isoformat())).fetchone()
    if done and not force:
        return {"ok": True, "already_reviewed": True, "review": {**dict(done),
                "detail": json.loads(done["detail_json"])}}
    week = _week_numbers(conn, athlete_id, ws, gf)
    if not week["sessions_planned"]:
        return {"ok": True, "decision": "no_plan_that_week", "week_start": ws.isoformat()}
    prev = _week_numbers(conn, athlete_id, ws - timedelta(days=7), gf)
    prev = prev if prev["sessions_planned"] else None
    active = [r["signal_name"] for r in conn.execute(
        "SELECT signal_name, current_streak FROM signal_state WHERE athlete_id=?", (athlete_id,))
        if r["signal_name"] in analysis.SIGNALS and r["current_streak"] >= analysis.SIGNALS[r["signal_name"]]["threshold"]]
    next_start = ws + timedelta(days=7) if today <= ws + timedelta(days=10) else _next_monday(today + timedelta(days=1))
    taper = False
    if not gf:
        taper_weeks = len(race_profile(goal["distance_km"])[2])
        taper = (date.fromisoformat(goal["race_date"]) - next_start).days < 7 * taper_weeks
    d = plan_review.decide(goal["goal_type"], week, prev, active, feedback, taper_protected=taper)

    def next_week_total():
        rows = conn.execute("SELECT prescribed_distance_km, prescribed_duration_min FROM program WHERE athlete_id=? "
                            "AND status='pending' AND session_date BETWEEN ? AND ?",
                            (athlete_id, next_start.isoformat(), (next_start + timedelta(days=6)).isoformat()))
        return round(sum((r[1] if gf else r[0]) or 0 for r in rows), 1)

    before = next_week_total()
    applied, gen_result = None, None
    if d["decision"] in ("repeat", "hold"):
        # Repeat/hold means "not harder than this week". If next week is already a
        # planned easier week at or below that level, leave it alone.
        ref = prev if (not gf and "cutback" in week["phases"] and prev) else week
        if before and before <= ref["planned"]:
            d["reasons"].append("next week is already a planned easier week at or below this level; "
                                "it stays as planned")
            apply = False
    if apply and d["decision"] in ("repeat", "step_back", "hold"):
        reason = f"weekly review ({ws}): {d['decision']}"
        if gf:
            level = _last_week_level(conn, athlete_id, ws + timedelta(days=7))
            if d["decision"] == "step_back":
                level = ({"start_step": max(0, level["start_step"] - 1)} if "start_step" in level
                         else {"start_weekly_min": max(45, round(level["start_weekly_min"] * 0.85))})
            gen_result = generate_program(athlete_id, goal["race_id"], reason, general_fitness_start_level=level,
                                          start_date=next_start.isoformat())
        else:
            stored = conn.execute("SELECT constraints_json FROM program_revision WHERE race_id=? AND constraints_json "
                                  "IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT 1", (goal["race_id"],)).fetchone()
            c = json.loads(stored["constraints_json"]) if stored else {}
            if d["decision"] == "hold":
                ref = prev if ("cutback" in week["phases"] and prev) else week
                c.update(baseline_weekly_km=ref["planned"], longest_recent_km=ref["longest_planned"] or None)
            else:
                recent = [w["actual"] for w in (week, prev) if w]
                actual_avg = round(sum(recent) / len(recent), 1)
                longest = max([week["longest_actual"]] + ([prev["longest_actual"]] if prev else []))
                if actual_avg < MIN_BASE_WEEKLY_KM:
                    # Too little running for any race build: rebuild at the most cautious plan the
                    # race builder allows, and flag the goal as at risk for the coach to discuss.
                    d["reasons"].append(
                        f"race goal at risk: only {actual_avg:g} km/week lately, below the {MIN_BASE_WEEKLY_KM} km "
                        f"minimum for a race build. Plan rebuilt at that minimum; talk to the athlete about "
                        f"switching to general fitness or a later race.")
                    d["goal_at_risk"] = True
                c.update(baseline_weekly_km=max(actual_avg, MIN_BASE_WEEKLY_KM),
                         longest_recent_km=max(longest, MIN_BASE_LONGEST_KM))
            gen_result = generate_program(athlete_id, goal["race_id"], reason, constraints=c,
                                          start_date=next_start.isoformat())
        if gen_result.get("ok"):
            applied = gen_result["revision_id"]
    after = next_week_total()
    other = _other_activities(conn, athlete_id, ws.isoformat())
    other = [o for o in other if o["start_at"][:10] <= (ws + timedelta(days=6)).isoformat()]
    other_week = {}
    for o in other:
        c = other_week.setdefault(o["category"], {"sessions": 0, "minutes": 0.0})
        c["sessions"] += 1
        c["minutes"] = round(c["minutes"] + (o["elapsed_min"] or 0))
    detail = {"week": week, "previous_week": prev, "reasons": d["reasons"], "feedback": feedback,
              "other_training": other_week,
              "active_signals": active, "next_week_before": before, "next_week_after": after,
              "rebuild_refused": None if not gen_result or gen_result.get("ok") else gen_result}
    conn.execute("INSERT OR REPLACE INTO plan_review VALUES (?,?,?,?,?,?,?,?)",
                 (_id("rev_w"), athlete_id, ws.isoformat(), _now(), d["decision"], d["completion"],
                  json.dumps(detail, default=str), applied))
    conn.commit()
    unit = "min" if gf else "km"
    return {"ok": True, "week_start": ws.isoformat(), "decision": d["decision"], "reasons": d["reasons"],
            "completion": d["completion"], "planned": week["planned"], "actual": week["actual"], "unit": unit,
            "other_training": other_week,
            "next_week_start": next_start.isoformat(),
            "next_week_total_before": before, "next_week_total_after": after,
            "plan_rebuilt": bool(applied), "revision_id": applied, "goal_at_risk": bool(d.get("goal_at_risk")),
            "rebuild_refused": detail["rebuild_refused"],
            "say": {"pause": "Tell them to stop running and get the pain checked; the plan waits until they're cleared.",
                    "progress": "Well done -- next week steps up as planned.",
                    "on_track": "On track -- next week continues as planned.",
                    "repeat": "Next week repeats this level; that's normal and part of the plan.",
                    "hold": "Next week holds this volume before building again.",
                    "step_back": "The plan is rebuilt from what they've actually been running."}[d["decision"]]}


def _ladder_progress(sessions):
    """Walk/run athletes: which ladder rung they've reached, from sessions done."""
    done = sorted((s for s in sessions if s["session_type"] == "walk_run" and s["status"] == "done"),
                  key=lambda s: s["session_date"])
    rungs = []
    for s in done:
        for i, rung in enumerate(WALK_RUN_LADDER):
            if _walk_run_label(*rung) in (s["session_label"] or ""):
                rungs.append(i)
                break
    if len(rungs) < 2:
        return None
    first, best = rungs[0], max(rungs)
    total = len(WALK_RUN_LADDER)
    run_min = lambda i: WALK_RUN_LADDER[i][0] * WALK_RUN_LADDER[i][2] if WALK_RUN_LADDER[i][1] else WALK_RUN_LADDER[i][0]
    return {"sessions_done": len(rungs), "first_step": first + 1, "highest_step": best + 1, "steps_total": total,
            "text": f"Walk/run: from step {first + 1} to step {best + 1} of {total} "
                    f"({run_min(first):g} -> {run_min(best):g} min of running per session) "
                    f"over {len(rungs)} completed sessions."}


def _trend_inputs(conn, athlete_id):
    runs = [dict(r) for r in conn.execute(
        "SELECT r.*, p.session_type AS _session_type, p.session_label AS _session_label "
        "FROM run_log r LEFT JOIN program p ON p.program_row_id = r.matched_program_row_id "
        "WHERE r.athlete_id=? ORDER BY r.recorded_at", (athlete_id,))]
    for r in runs:
        r["_splits"] = json.loads(r["splits_json"]) if r.get("splits_json") else []
    sessions = [dict(s) for s in conn.execute(
        "SELECT * FROM program WHERE athlete_id=? AND status IN ('pending','done','missed') ORDER BY session_date",
        (athlete_id,))]
    events = [dict(e) for e in conn.execute("SELECT * FROM signal_event WHERE athlete_id=? ORDER BY occurred_at",
                                            (athlete_id,))]
    return runs, sessions, events


def _other_activities(conn, athlete_id, since=None):
    q = "SELECT * FROM other_activity WHERE athlete_id=?" + (" AND start_at >= ?" if since else "") + " ORDER BY start_at"
    return [dict(o) for o in conn.execute(q, (athlete_id, since) if since else (athlete_id,))]


STRAVA_PRIVATE_NOTE = ("This athlete's activity data comes from Strava. Strava's API Agreement only allows it to "
                       "be shown to the athlete themselves, so it isn't shown here. Their plan, form answers and "
                       "check-in replies are still available.")


def _strava_private(conn, athlete_id):
    return bool(conn.execute("SELECT 1 FROM run_log WHERE athlete_id=? AND source_format='strava' UNION "
                             "SELECT 1 FROM other_activity WHERE athlete_id=? AND data_source='strava'",
                             (athlete_id, athlete_id)).fetchone())


def get_trends(athlete_id=None, chat_ref=None, include_weekly_table=True, audience="athlete"):
    """Progress trends from the athlete's whole history. Needs 14 days and 4
    logged runs before anything is reported; more metrics unlock at 4 and 8
    weeks. Relay `feedback` (already evidence-backed); never compute your own.
    audience='owner' when the owner (not the athlete) asked: Strava-sourced
    data is then withheld, per Strava's API Agreement."""
    conn = connect()
    if not athlete_id:
        r = conn.execute("SELECT athlete_id FROM athlete_profile WHERE chat_ref=?", (chat_ref or "",)).fetchone()
        if not r:
            raise ToolError("pass athlete_id, or a linked chat_ref")
        athlete_id = r["athlete_id"]
    _get_athlete(conn, athlete_id)
    if audience == "owner" and _strava_private(conn, athlete_id):
        return {"ok": True, "athlete_id": athlete_id, "private": True, "note": STRAVA_PRIVATE_NOTE}
    goal = _active_goal(conn, athlete_id) or {}
    runs, sessions, events = _trend_inputs(conn, athlete_id)
    report = build_trends(runs, sessions, events, _today(), goal.get("goal_type", "race"),
                          ladder_progress=_ladder_progress(sessions), other=_other_activities(conn, athlete_id))
    if not include_weekly_table:
        report.pop("weekly", None)
    return {"ok": True, "athlete_id": athlete_id, **report}


def get_squad_overview():
    """Owner view: one row per athlete with an active goal -- data tier,
    consistency, volume direction, aerobic trend, warnings, last run."""
    conn = connect()
    today = _today()
    rows = []
    for a in conn.execute("SELECT athlete_id, name FROM athlete_profile ORDER BY name"):
        goal = _active_goal(conn, a["athlete_id"])
        if not goal:
            continue
        if _strava_private(conn, a["athlete_id"]):
            rows.append({"athlete_id": a["athlete_id"], "name": a["name"], "goal": goal["race_name"],
                         "goal_type": goal["goal_type"], "activity_data": "private (Strava)",
                         "onboarding_status": _onboarding_status(conn, _get_athlete(conn, a["athlete_id"])),
                         "active_signals": []})
            continue
        runs, sessions, events = _trend_inputs(conn, a["athlete_id"])
        t = build_trends(runs, sessions, events, today, goal["goal_type"])
        last = max((r["recorded_at"][:10] for r in runs), default=None)
        active = [dict(s) for s in conn.execute(
            "SELECT signal_name, current_streak FROM signal_state WHERE athlete_id=? AND current_streak>0",
            (a["athlete_id"],))]
        c4 = t.get("consistency_last_4_weeks") or {}
        rows.append({"athlete_id": a["athlete_id"], "name": a["name"], "goal": goal["race_name"],
                     "goal_type": goal["goal_type"], "tier": t["tier"], "runs_logged": t["runs_logged"],
                     "consistency_4wk_pct": c4.get("pct"),
                     "volume_direction": (t.get("volume_week_on_week") or {}).get("direction"),
                     "aerobic_direction": (t.get("aerobic_fitness") or {}).get("direction"),
                     "last_run": last, "days_since_last_run": (today - date.fromisoformat(last)).days if last else None,
                     "active_signals": active})
    needs_attention = [r["name"] for r in rows if r["active_signals"] or (r.get("days_since_last_run") or 0) >= 7
                       or (r.get("consistency_4wk_pct") is not None and r["consistency_4wk_pct"] < 60)]
    return {"ok": True, "today": today.isoformat(), "athletes": rows, "needs_attention": needs_attention}


def get_run_history(athlete_id=None, chat_ref=None, start_date=None, end_date=None, limit=200, audience="athlete"):
    """Every recorded run for an athlete from the permanent history.db, newest
    first, with what each was matched to. Includes runs from old/replaced plans.
    audience='owner' withholds Strava-sourced runs (Strava's API Agreement)."""
    conn = connect()
    if not athlete_id:
        r = conn.execute("SELECT athlete_id FROM athlete_profile WHERE chat_ref=?", (chat_ref or "",)).fetchone()
        if not r:
            raise ToolError("pass athlete_id, or a linked chat_ref")
        athlete_id = r["athlete_id"]
    hconn = _hconnect()
    if audience == "owner" and _strava_private(conn, athlete_id):
        return {"ok": True, "athlete_id": athlete_id, "private": True, "note": STRAVA_PRIVATE_NOTE}
    q = ("SELECT history_id, recorded_at, logged_at, source_file_name, source_format, distance_km, "
         "elapsed_time_min, moving_time_min, avg_hr, max_hr, elevation_gain_m, avg_pace_sec_per_km, "
         "hr_drift_delta, sample_count, parser_notes FROM runs WHERE athlete_id=?")
    args = [athlete_id]
    if start_date:
        q += " AND recorded_at >= ?"; args.append(start_date)
    if end_date:
        q += " AND recorded_at < ?"; args.append((date.fromisoformat(end_date) + timedelta(days=1)).isoformat())
    q += " ORDER BY recorded_at DESC LIMIT ?"; args.append(int(limit))
    runs = []
    for r in hconn.execute(q, args):
        r = dict(r)
        ev = hconn.execute("SELECT event_type, detail_json FROM run_events WHERE history_id=? AND event_type "
                           "IN ('matched','unmatched') ORDER BY occurred_at DESC LIMIT 1", (r["history_id"],)).fetchone()
        r["matched_to"] = json.loads(ev["detail_json"]) if ev and ev["event_type"] == "matched" else None
        runs.append(r)
    totals = hconn.execute("SELECT count(*), coalesce(sum(distance_km),0), coalesce(sum(elapsed_time_min),0), "
                           "min(recorded_at) FROM runs WHERE athlete_id=?", (athlete_id,)).fetchone()
    failed = hconn.execute("SELECT count(*) FROM upload_attempts WHERE athlete_id=? AND outcome='parse_failed'",
                           (athlete_id,)).fetchone()[0]
    return {"ok": True, "athlete_id": athlete_id, "history_db": history.history_path(db_path()),
            "all_time": {"runs": totals[0], "km": round(totals[1], 1), "minutes": round(totals[2]),
                         "first_run": totals[3], "failed_uploads": failed},
            "runs": runs}


def export_run_file(history_id, out_dir=None):
    """Write the original uploaded .fit/.gpx back out from history.db (e.g. to
    send it back to the athlete or re-parse it)."""
    hconn = _hconnect()
    r = hconn.execute("SELECT source_file_name, file_blob_zlib, file_sha256 FROM runs WHERE history_id=?",
                      (history_id,)).fetchone()
    if not r:
        raise ToolError(f"no run {history_id!r} in history")
    raw = zlib.decompress(r["file_blob_zlib"])
    if hashlib.sha256(raw).hexdigest() != r["file_sha256"]:
        raise ToolError("stored file failed its checksum -- restore history.db from a backup")
    out_dir = out_dir or os.path.join(os.path.dirname(db_path()), "exports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{history_id}_{r['source_file_name']}")
    with open(path, "wb") as f:
        f.write(raw)
    return {"ok": True, "path": path, "bytes": len(raw), "checksum_ok": True}


def backfill_history():
    """One-off for installs that logged runs before history.db existed: copies
    every coach.db run not yet in history (summary only -- the original files
    weren't kept then). Safe to run any time; also reports whether the two
    databases agree."""
    conn = connect()
    hconn = _hconnect()
    added = 0
    for r in conn.execute("SELECT r.*, a.name, a.email FROM run_log r JOIN athlete_profile a USING(athlete_id)"):
        r = dict(r)
        if history.history_id_for(hconn, r["run_log_id"]) or hconn.execute(
                "SELECT 1 FROM runs WHERE athlete_id=? AND recorded_at=?", (r["athlete_id"], r["recorded_at"])).fetchone():
            continue
        hid = history._id("hist")
        hconn.execute(
            "INSERT INTO runs (history_id, athlete_id, athlete_name, athlete_email, recorded_at, logged_at, "
            "source_file_name, source_format, file_sha256, file_bytes, file_blob_zlib, distance_km, elapsed_time_min, "
            "moving_time_min, avg_hr, max_hr, elevation_gain_m, elevation_loss_m, avg_pace_sec_per_km, splits_json, "
            "first_half_avg_hr, second_half_avg_hr, hr_drift_delta, sample_count, parser_notes, athlete_notes, "
            "coach_run_log_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (hid, r["athlete_id"], r["name"], r["email"], r["recorded_at"], r["created_at"], r["source_file"],
             r["source_format"], "", 0, b"", r["distance_km"], r["elapsed_time_min"], r["moving_time_min"],
             r["avg_hr"], r["max_hr"], r["elevation_gain_m"], r["elevation_loss_m"], r["avg_pace_sec_per_km"],
             r["splits_json"], r["first_half_avg_hr"], r["second_half_avg_hr"], r["hr_drift_delta"], 0,
             ((r["parser_notes"] or "") + " [backfilled: original file not kept]").strip(), r["athlete_notes"],
             r["run_log_id"]))
        history.add_event(hconn, hid, "logged", {"coach_run_log_id": r["run_log_id"], "backfilled": True})
        history.record_attempt(hconn, r["athlete_id"], r["source_file"], None, "backfilled", hid)
        added += 1
    hconn.commit()
    coach_n = conn.execute("SELECT count(*) FROM run_log").fetchone()[0]
    hist_n = hconn.execute("SELECT count(*) FROM runs").fetchone()[0]
    return {"ok": True, "backfilled": added, "coach_db_runs": coach_n, "history_db_runs": hist_n,
            "in_sync": hist_n >= coach_n}


SAFETY_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["clear", "caution", "needs_clearance"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "quotes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["outcome", "reasons"],
}
RACE_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "race_date": {"type": "string", "description": "YYYY-MM-DD of the next edition"},
        "distance_km": {"type": "number"},
        "elevation_gain_m": {"type": ["number", "null"]},
        "terrain": {"type": ["string", "null"]},
        "source": {"type": "string", "description": "URL the details came from"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["race_date", "distance_km", "source", "confidence"],
}


def get_review_tasks(athlete_id):
    """The independent second-opinion tasks this athlete still needs, ready to
    pass to delegate_task unchanged. Only the athlete's own words / the race
    name go to the reviewer -- never their name, email or Telegram id."""
    conn = connect()
    athlete = _get_athlete(conn, athlete_id)
    goal = _active_goal(conn, athlete_id) or {}
    tasks, how = [], []
    if athlete.get("safety_screen_status") == "pending_review":
        sj = json.loads(athlete.get("safety_screen_json") or "{}")
        tasks.append({
            "goal": "Safety-screen a new runner's own words before a running plan is written for them.",
            "context": (
                "You are an independent reviewer for a running coach. Read ONLY the text below, written by the "
                "runner on a sign-up form, and decide whether a doctor should clear them before they start a "
                "running plan. You are not diagnosing anything; when in doubt, be cautious.\n\n"
                "needs_clearance: anything heart- or circulation-related (including past stents, surgery, "
                "arrhythmia, high blood pressure), chest pain, fainting or dizziness with exercise, seizures, "
                "diabetes on insulin, current pregnancy or birth in the last 6 months, surgery or a serious "
                "illness/treatment in the last 6 months, concussion in the last 3 months, a doctor/physio "
                "limiting activity, eating disorder or RED-S, unexplained breathlessness, long covid.\n"
                "caution: injuries or conditions the coach must plan around but that don't need a doctor first "
                "(old or healed injuries, a niggle, controlled asthma, joint pain).\n"
                "clear: nothing relevant, or it is explicitly negated (e.g. 'no heart problems').\n\n"
                "Keyword hints from a simple scanner (may be false alarms, may miss things): "
                + (", ".join(f"{h['label']} ('{h['excerpt']}')" for h in sj.get("keyword_hits", [])) or "none")
                + "\n\nRunner's text:\n" + sj.get("text", "")),
            "output_schema": SAFETY_REVIEW_SCHEMA})
        how.append("Safety: read the text yourself as well, then record_safety_review with reviewer_outcome (the "
                   "delegate's answer) and own_outcome (yours). The more cautious outcome is kept.")
    if goal.get("goal_type") == "race" and goal.get("verification_status") in ("unverified", None):
        tasks.append({
            "goal": f"Independently find the official details of the running race '{goal['race_name']}'.",
            "context": (f"Find the edition held on or around {goal['race_date']} (the runner's form says "
                        f"{goal['distance_km']:g} km). Use web search. Prefer the organiser's site or an official "
                        f"entry/results platform. Report the race date, exact distance in km, total elevation gain "
                        f"in metres (null if you can't find a real figure -- never guess), terrain, and the URL "
                        f"you used. If you only find last year's edition, say so in notes and set confidence low."),
            "output_schema": RACE_CHECK_SCHEMA})
        how.append("Race: look it up yourself too (source 1, starting from course_url "
                   f"{goal.get('course_url') or '(none given -- search)'}; the delegate is source 2 and must use a "
                   "different page), then verify_race_info with primary=yours, secondary=the delegate's answer.")
    return {"ok": True, "athlete_id": athlete_id, "tasks": tasks,
            "delegate_call": {"tasks": tasks} if tasks else None, "then": how,
            "note": "Nothing to review." if not tasks else
                    "Pass delegate_call to delegate_task exactly (it runs them in parallel)."}


def record_safety_review(athlete_id, reviewer_outcome, own_outcome, reasons=None, notes=None):
    """Store the free-text safety review. The more cautious of the reviewer's
    and the coach's outcome wins. needs_clearance adds a 'free text:' health
    flag, so the medical-clearance gate applies exactly as for a ticked box."""
    order = ["clear", "caution", "needs_clearance"]
    for o in (reviewer_outcome, own_outcome):
        if o not in order:
            raise ToolError(f"outcomes must be one of {order}")
    conn = connect()
    athlete = _get_athlete(conn, athlete_id)
    sj = json.loads(athlete.get("safety_screen_json") or "{}")
    final = max(reviewer_outcome, own_outcome, key=order.index)
    medical_hits = [h for h in sj.get("keyword_hits", []) if h["level"] == "medical"]
    if final == "clear" and medical_hits and not notes:
        raise ToolError("the keyword scan found medical words (" + ", ".join(h["label"] for h in medical_hits) +
                        "); clearing needs a note saying why (e.g. 'text says no heart problems')")
    sj.update(reviewer_outcome=reviewer_outcome, own_outcome=own_outcome, final=final,
              reasons=reasons or [], notes=notes, reviewed_at=_now())
    flags = json.loads(athlete.get("health_screen_flags") or "[]")
    if final == "needs_clearance":
        flag = "free text: " + ("; ".join(reasons or []) or notes or "see safety review")
        flags = [f for f in flags if not f.startswith("free text:")] + [flag]
    conn.execute("UPDATE athlete_profile SET safety_screen_status=?, safety_screen_json=?, health_screen_flags=?, "
                 "updated_at=? WHERE athlete_id=?", (final, json.dumps(sj), json.dumps(flags), _now(), athlete_id))
    conn.commit()
    return {"ok": True, "final": final, "needs_medical_clearance": final == "needs_clearance",
            "tell": {"clear": "Nothing to act on.",
                     "caution": "Plan around it; mention it to the athlete in the welcome and to the owner.",
                     "needs_clearance": "No plan until they confirm a doctor has cleared them "
                                        "(update_athlete_profile medical_clearance_at). Tell the owner."}[final]}


def verify_race_info(race_id, primary, secondary):
    """Compare two independently found descriptions of the race with each other
    and the form. Agreement -> 'verified' and the course details are recorded;
    any difference -> 'mismatch' for the owner to settle (confirm_race_info)."""
    conn = connect()
    goal = _row(conn.execute("SELECT * FROM race_target WHERE race_id=?", (race_id,)).fetchone())
    if not goal or goal["goal_type"] != "race":
        raise ToolError(f"no race goal {race_id!r}")
    res = safety_checks.compare_race_sources(goal, primary, secondary)
    detail = {"primary": primary, "secondary": secondary, "differences": res["differences"],
              "agreed": res["agreed"], "checked_at": _now()}
    if res["status"] == "verified":
        a = res["agreed"]
        notes = " ".join(x for x in (a.get("terrain_notes"), a.get("elevation_note")) if x) or None
        conn.execute("UPDATE race_target SET verification_status='verified', verification_json=?, elevation_gain_m=?, "
                     "terrain_notes=? WHERE race_id=?", (json.dumps(detail), a["elevation_gain_m"], notes, race_id))
    else:
        conn.execute("UPDATE race_target SET verification_status='mismatch', verification_json=? WHERE race_id=?",
                     (json.dumps(detail), race_id))
    conn.commit()
    return {"ok": True, "status": res["status"], "differences": res["differences"], "agreed": res["agreed"],
            "next": ("Race verified -- generate_program." if res["status"] == "verified" else
                     "Show the owner the differences with both sources; when they tell you the right details, "
                     "call confirm_race_info.")}


def confirm_race_info(race_id, note, race_date=None, distance_km=None, elevation_gain_m=None, terrain_notes=None):
    """The owner settles the race details (after a mismatch, or when no source
    has them). Only call with details the OWNER gave you."""
    conn = connect()
    goal = _row(conn.execute("SELECT * FROM race_target WHERE race_id=?", (race_id,)).fetchone())
    if not goal or goal["goal_type"] != "race":
        raise ToolError(f"no race goal {race_id!r}")
    sets = {"verification_status": "owner_confirmed"}
    if race_date:
        iso, _ = _parse_date(race_date)
        sets["race_date"] = iso
    for k, v in (("distance_km", distance_km), ("elevation_gain_m", elevation_gain_m), ("terrain_notes", terrain_notes)):
        if v is not None:
            sets[k] = v
    vj = json.loads(goal.get("verification_json") or "{}")
    vj["owner_confirmation"] = {"note": note, "values": {k: v for k, v in sets.items() if k != "verification_status"},
                                "at": _now()}
    sets["verification_json"] = json.dumps(vj)
    conn.execute(f"UPDATE race_target SET {', '.join(f'{k}=?' for k in sets)} WHERE race_id=?",
                 (*sets.values(), race_id))
    conn.commit()
    return {"ok": True, "race_target": _row(conn.execute("SELECT * FROM race_target WHERE race_id=?",
                                                         (race_id,)).fetchone())}


# ---- Strava -------------------------------------------------------

def _strava_creds():
    try:
        return strava.app_credentials(hermes_home() or os.path.expanduser("~/.hermes"))
    except strava.StravaError as e:
        raise ToolError(str(e))


def strava_connect_link(athlete_id):
    """A personal Strava authorise link for this athlete, plus the exact
    instructions to send them. Send it only in that athlete's own chat."""
    conn = connect()
    athlete = _get_athlete(conn, athlete_id)
    cid, _ = _strava_creds()
    state = uuid.uuid4().hex[:16]
    conn.execute("INSERT INTO strava_connection (athlete_id, status, pending_state) VALUES (?, 'pending', ?) "
                 "ON CONFLICT(athlete_id) DO UPDATE SET pending_state=excluded.pending_state, "
                 "status=CASE WHEN strava_connection.status='connected' THEN 'connected' ELSE 'pending' END",
                 (athlete_id, state))
    conn.commit()
    link = strava.authorize_url(cid, state)
    return {"ok": True, "link": link, "message_for_athlete": (
        f"Connect Strava so I can see your runs and workouts automatically:\n1. Open this link: {link}\n"
        f"2. Tap Authorize (leave 'View data about your activities' ticked).\n3. Your browser will then show an "
        f"error page -- that's expected. Copy the whole address from the address bar (it starts "
        f"http://localhost) and send it to me here.\nYour Strava data is used only to coach you, and only you "
        f"see it."), "athlete": athlete["name"]}


def strava_complete_connect(athlete_id, redirect_url):
    """Finish connecting: the athlete pasted the localhost address (or code).
    Stores the tokens and pulls the last 4 weeks of activities."""
    conn = connect()
    _get_athlete(conn, athlete_id)
    row = _row(conn.execute("SELECT * FROM strava_connection WHERE athlete_id=?", (athlete_id,)).fetchone())
    if not row or not row.get("pending_state"):
        raise ToolError("no connection in progress for this athlete; send a fresh link with strava_connect_link")
    try:
        code, state, scope = strava.parse_redirect(redirect_url)
        if state and state != row["pending_state"]:
            raise ToolError("that Strava address belongs to a different (or older) link; send a fresh link")
        if scope is not None and "activity:read" not in scope:
            raise ToolError("Strava wasn't given permission to read activities. Send the link again and ask them "
                            "to leave 'View data about your activities' ticked.")
        cid, secret = _strava_creds()
        tok = strava.exchange_code(cid, secret, code)
    except strava.StravaError as e:
        raise ToolError(str(e))
    conn.execute("UPDATE strava_connection SET status='connected', pending_state=NULL, strava_athlete_id=?, "
                 "access_token=?, refresh_token=?, expires_at=?, scope=?, connected_at=?, last_error=NULL "
                 "WHERE athlete_id=?",
                 (str((tok.get("athlete") or {}).get("id") or ""), tok["access_token"], tok["refresh_token"],
                  tok["expires_at"], scope or SCOPE_DEFAULT, _now(), athlete_id))
    conn.commit()
    first = strava_sync(athlete_id=athlete_id)
    res = (first.get("athletes") or [{}])[0]
    return {"ok": True, "connected": True,
            "backfilled": {"runs": len(res.get("new_runs", [])), "other": len(res.get("new_other", []))},
            "note": "Connected. The last 4 weeks were imported as history (no feedback needed on those). From now "
                    "on runs and workouts arrive automatically."}


SCOPE_DEFAULT = strava.SCOPE


def _strava_token(conn, row):
    if (row.get("expires_at") or 0) > strava.now_epoch() + 300:
        return row["access_token"]
    cid, secret = _strava_creds()
    tok = strava.refresh(cid, secret, row["refresh_token"])
    conn.execute("UPDATE strava_connection SET access_token=?, refresh_token=?, expires_at=? WHERE athlete_id=?",
                 (tok["access_token"], tok["refresh_token"], tok["expires_at"], row["athlete_id"]))
    conn.commit()
    return tok["access_token"]


def _strength_before(conn, athlete_id, run_start_iso):
    """Leg-loading strength sessions in the 30 h before a run. Sessions with a
    Hevy log count only if they trained legs (lower / full body); an upper-body
    day doesn't tire the legs. Sessions without a log are included (unknown)."""
    t = datetime.fromisoformat(run_start_iso)
    out = []
    for r in conn.execute("SELECT name, start_at, elapsed_min, strength_json FROM other_activity WHERE athlete_id=? "
                          "AND category='strength' AND start_at >= ? AND start_at < ?",
                          (athlete_id, (t - timedelta(hours=30)).isoformat(), t.isoformat())):
        r = dict(r)
        w = json.loads(r["strength_json"]) if r.get("strength_json") else None
        if w and w["focus"] not in ("lower", "full"):
            continue
        r["focus"] = w["focus"] if w else None
        r["legs"] = hevy.leg_summary(w) if w else None
        out.append(r)
    return out


def strava_sync(athlete_id=None, backfill_days=28):
    """Pull new activities from Strava for one athlete (or every connected
    athlete). Runs are logged exactly like uploaded files (history.db +
    coach.db), matched to the plan and compared; strength / mobility /
    cross-training go to other_activity. Activities from before the athlete
    connected are imported as history without needing feedback."""
    conn = connect()
    hconn = _hconnect()
    q = "SELECT * FROM strava_connection WHERE status='connected'" + (" AND athlete_id=?" if athlete_id else "")
    rows = [dict(r) for r in conn.execute(q, (athlete_id,) if athlete_id else ())]
    out = []
    for row in rows:
        aid = row["athlete_id"]
        athlete = _get_athlete(conn, aid)
        res = {"athlete_id": aid, "new_runs": [], "new_other": [], "duplicates": 0, "error": None}
        try:
            token = _strava_token(conn, row)
            first_sync = row.get("last_activity_epoch") is None
            after = row.get("last_activity_epoch") or strava.days_ago_epoch(backfill_days)
            # The first import is history (no feedback owed) -- except the last 24 h, which
            # the athlete will expect a comment on.
            history_cutoff = (datetime.fromisoformat(row.get("connected_at") or _now())
                              - timedelta(hours=24)).isoformat()
            newest = after
            for a in strava.list_activities(token, after):
                ext = f"strava:{a['id']}"
                start_iso = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).isoformat()
                newest = max(newest, strava.epoch_of(start_iso))
                backfill = first_sync and start_iso < history_cutoff
                if strava.category(a) == "run":
                    if conn.execute("SELECT 1 FROM run_log WHERE external_id=?", (ext,)).fetchone():
                        continue
                    streams = {} if a.get("manual") else strava.activity_streams(token, a["id"])
                    parsed = strava.run_summary(a, streams)
                    dup = _find_duplicate_run(conn, aid, parsed["recorded_at"], parsed.get("distance_km"))
                    if dup:
                        conn.execute("UPDATE run_log SET external_id=? WHERE run_log_id=?", (ext, dup["run_log_id"]))
                        history.record_attempt(hconn, aid, ext, None, "duplicate",
                                               history.history_id_for(hconn, dup["run_log_id"]))
                        res["duplicates"] += 1
                        continue
                    raw = json.dumps({"activity": a, "streams": streams}).encode()
                    rid, hid = _log_run(conn, hconn, athlete, parsed, "strava", ext, None, raw=raw, external_id=ext)
                    history.record_attempt(hconn, aid, ext, None, "recorded", hid)
                    hconn.commit()
                    if backfill:
                        conn.execute("UPDATE run_log SET feedback_sent_at='backfill' WHERE run_log_id=?", (rid,))
                        conn.commit()
                        res["new_runs"].append({"run_log_id": rid, "date": start_iso[:10], "backfill": True})
                        continue
                    m = match_run_to_program(aid, run_log_id=rid)["match"]
                    c = compare_run_to_program(rid, m["program_row_id"] if m else None)
                    res["new_runs"].append({
                        "run_log_id": rid, "date": start_iso[:10], "name": a.get("name"),
                        "distance_km": round(parsed["distance_km"], 2), "moving_min": round(parsed["moving_time_min"], 1),
                        "matched_session": ({k: m[k] for k in ("session_type", "session_label", "session_date")}
                                            if m else None),
                        "notes": c["comparison"]["notes"], "actions_needed": c["actions_needed"],
                        "data_quality": parsed.get("parser_notes")})
                else:
                    if conn.execute("SELECT 1 FROM other_activity WHERE external_id=?", (ext,)).fetchone():
                        continue
                    detail = strava.activity_detail(token, a["id"]) if strava.category(a) in ("strength", "mobility") \
                        else None
                    o = strava.other_summary(a, detail)
                    workout = hevy.parse_description(o["description"])     # Hevy / Hevy Coach logs
                    if workout and o["category"] != "strength":
                        o["category"] = "strength"
                    oid = _id("act")
                    conn.execute("INSERT INTO other_activity (activity_id, athlete_id, external_id, data_source, start_at, "
                                 "category, sport_type, name, description, elapsed_min, moving_min, distance_km, avg_hr, "
                                 "max_hr, perceived_exertion, strength_json, feedback_sent_at, created_at) "
                                 "VALUES (?,?,?,'strava',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (oid, aid, ext, o["start_at"], o["category"], o["sport_type"], o["name"],
                                  o["description"], o["elapsed_min"], o["moving_min"], o["distance_km"], o["avg_hr"],
                                  o["max_hr"], o["perceived_exertion"], json.dumps(workout) if workout else None,
                                  "backfill" if backfill else None, _now()))
                    conn.commit()
                    history.record_other(hconn, aid, o, raw=json.dumps({"activity": a, "detail": detail}).encode())
                    hconn.commit()
                    res["new_other"].append({"activity_id": oid, "date": o["start_at"][:10], "category": o["category"],
                                             "sport_type": o["sport_type"], "name": o["name"],
                                             "minutes": o["elapsed_min"], "backfill": backfill,
                                             "focus": workout["focus"] if workout else None})
            conn.execute("UPDATE strava_connection SET last_sync_at=?, last_activity_epoch=?, last_error=NULL "
                         "WHERE athlete_id=?", (_now(), newest, aid))
            conn.commit()
        except strava.StravaRateLimited as e:
            res["error"] = str(e)
            conn.execute("UPDATE strava_connection SET last_error=? WHERE athlete_id=?", (str(e), aid))
            conn.commit()
            out.append(res)
            break                                  # stop: every further call would fail too
        except strava.StravaAuthRevoked as e:
            res["error"] = str(e) + " -- ask the athlete to reconnect (strava_connect_link)"
            conn.execute("UPDATE strava_connection SET status='revoked', last_error=? WHERE athlete_id=?", (str(e), aid))
            conn.commit()
        except strava.StravaError as e:
            res["error"] = str(e)
            conn.execute("UPDATE strava_connection SET last_error=? WHERE athlete_id=?", (str(e), aid))
            conn.commit()
        out.append(res)
    return {"ok": True, "athletes": out}


def strava_status(athlete_id=None):
    conn = connect()
    q = "SELECT athlete_id, status, connected_at, last_sync_at, last_error FROM strava_connection" + \
        (" WHERE athlete_id=?" if athlete_id else "")
    return {"ok": True, "connections": [dict(r) for r in conn.execute(q, (athlete_id,) if athlete_id else ())]}


def strava_disconnect(athlete_id):
    """Disconnect and revoke the coach's Strava access for this athlete (on their request)."""
    conn = connect()
    row = _row(conn.execute("SELECT * FROM strava_connection WHERE athlete_id=?", (athlete_id,)).fetchone())
    if not row:
        raise ToolError("this athlete never connected Strava")
    revoked = False
    if row.get("access_token"):
        try:
            revoked = strava.deauthorize(_strava_token(conn, row))
        except strava.StravaError:
            revoked = False
    conn.execute("UPDATE strava_connection SET status='disconnected', access_token=NULL, refresh_token=NULL, "
                 "expires_at=NULL WHERE athlete_id=?", (athlete_id,))
    conn.commit()
    return {"ok": True, "revoked_on_strava": revoked,
            "note": "Disconnected. Activities already imported stay in their history; send files from now on."}


def get_pending_feedback(athlete_id):
    """Strava activities the coach hasn't commented on yet (newest last), with
    the plan comparison for runs. After messaging the athlete, call
    mark_feedback_sent."""
    conn = connect()
    runs = []
    for r in conn.execute("SELECT * FROM run_log WHERE athlete_id=? AND source_format='strava' AND "
                          "feedback_sent_at IS NULL ORDER BY recorded_at", (athlete_id,)):
        r = dict(r)
        prog = _row(conn.execute("SELECT * FROM program WHERE program_row_id=?",
                                 (r["matched_program_row_id"],)).fetchone()) if r["matched_program_row_id"] else None
        comp = analysis.compare_run_to_program(r, prog)
        notes = list(comp.notes)
        if prog and prog["session_type"] in ("long", "quality", "time_trial"):
            for st in _strength_before(conn, athlete_id, r["recorded_at"]):
                what = (f"leg work ({st['legs']})" if st.get("legs") else f"strength session '{st['name']}'")
                notes.append(f"{what} within 30 h before this {prog['session_type']} run -- if it felt heavy, that's "
                             f"a likely reason; keep hard leg work away from the day before key runs")
        runs.append({"run_log_id": r["run_log_id"], "date": r["recorded_at"][:10],
                     "distance_km": round(r["distance_km"] or 0, 2), "moving_min": round(r["moving_time_min"] or 0, 1),
                     "avg_hr": r["avg_hr"], "hr_drift_delta": r["hr_drift_delta"],
                     "planned": ({k: prog[k] for k in ("session_type", "session_label", "prescribed_distance_km",
                                                       "prescribed_duration_min")} if prog else None),
                     "notes": notes, "data_quality": r["parser_notes"]})
    other = []
    for o in conn.execute(
            "SELECT activity_id, start_at, category, sport_type, name, description, elapsed_min, avg_hr, "
            "perceived_exertion, strength_json FROM other_activity WHERE athlete_id=? AND feedback_sent_at IS NULL "
            "ORDER BY start_at", (athlete_id,)):
        o = dict(o)
        w = json.loads(o.pop("strength_json")) if o.get("strength_json") else None
        if w:
            o.pop("description", None)          # replaced by the structured version
            o["workout"] = {"focus": w["focus"], "total_sets": w["total_sets"], "volume_kg": w["volume_kg"],
                            "exercises": [{"name": e["name"], "sets": len(e["sets"]), "top_set": e["top_set"]}
                                          for e in w["exercises"]]}
        other.append(o)
    return {"ok": True, "runs": runs, "other": other, "count": len(runs) + len(other)}


def mark_feedback_sent(athlete_id, run_log_ids=None, activity_ids=None):
    conn = connect()
    now = _now()
    for rid in run_log_ids or []:
        conn.execute("UPDATE run_log SET feedback_sent_at=? WHERE run_log_id=? AND athlete_id=?", (now, rid, athlete_id))
    for oid in activity_ids or []:
        conn.execute("UPDATE other_activity SET feedback_sent_at=? WHERE activity_id=? AND athlete_id=?",
                     (now, oid, athlete_id))
    conn.commit()
    return {"ok": True, "marked": len(run_log_ids or []) + len(activity_ids or [])}


def log_other_activity(athlete_id, category, start_at, elapsed_min, name=None, description=None,
                       perceived_exertion=None):
    """Record a non-run workout the athlete told you about (no Strava): strength,
    mobility or cross_training. It's already been discussed, so no feedback is pending."""
    if category not in ("strength", "mobility", "cross_training"):
        raise ToolError("category must be strength, mobility or cross_training")
    conn = connect()
    _get_athlete(conn, athlete_id)
    start = start_at if "T" in start_at else start_at + "T12:00:00+00:00"
    workout = hevy.parse_description(description) if description else None
    oid = _id("act")
    conn.execute("INSERT INTO other_activity (activity_id, athlete_id, external_id, data_source, start_at, category, "
                 "sport_type, name, description, elapsed_min, perceived_exertion, strength_json, feedback_sent_at, "
                 "created_at) VALUES (?,?,NULL,'athlete_reported',?,?,NULL,?,?,?,?,?,?,?)",
                 (oid, athlete_id, start, category, name, description, elapsed_min, perceived_exertion,
                  json.dumps(workout) if workout else None, _now(), _now()))
    conn.commit()
    return {"ok": True, "activity_id": oid, "focus": workout["focus"] if workout else None}


def _read_xlsx(path):
    """First worksheet of an .xlsx as a 2-D list of strings (stdlib only)."""
    import zipfile
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        first = wb.find("m:sheets/m:sheet", ns)
        rid = first.get(f"{{{ns['r']}}}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        target = target.lstrip("/")
        sheet_path = target if target.startswith("xl/") else "xl/" + target
        root = ET.fromstring(z.read(sheet_path))
    rows = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", ns):
            col = 0
            for ch in re.match(r"[A-Z]+", c.get("r")).group():
                col = col * 26 + ord(ch) - 64
            t, v = c.get("t"), c.find("m:v", ns)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            cells[col - 1] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def ingest_sheet_file(file_path, start_date=None):
    """Ingest the owner's Google Sheet sent as a file (File > Download > CSV or
    Microsoft Excel). First row = the form's question headers. Same behaviour as
    ingest_sheet_rows: unchanged rows are skipped, so re-sending the whole sheet
    is safe."""
    import csv
    if not os.path.exists(file_path):
        raise ToolError(f"file not found: {file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".xlsx":
        values = _read_xlsx(file_path)
    elif ext in (".csv", ".txt"):
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            values = [row for row in csv.reader(f)]
    else:
        raise ToolError("send the Sheet as .csv or .xlsx (Google Sheets: File > Download)")
    values = [r for r in values if any(str(c).strip() for c in r)]
    return ingest_sheet_rows(values, start_date=start_date)


def sync_telegram_allowlist():
    """Add every athlete's Telegram id to this profile's TELEGRAM_ALLOWED_USERS
    (never removes anyone; backs up .env first). The owner must then restart
    the gateway -- Hermes reads the allowlist at startup."""
    home = hermes_home() or os.path.expanduser("~/.hermes")
    env = os.path.join(home, ".env")
    if not os.path.exists(env):
        raise ToolError(f"no .env at {env}; set up this profile's Telegram bot first")
    with open(env, encoding="utf-8") as f:
        lines = f.read().splitlines()
    idx = next((i for i, l in enumerate(lines) if re.match(r"\s*(export\s+)?TELEGRAM_ALLOWED_USERS\s*=", l)), None)
    if idx is None:
        raise ToolError("TELEGRAM_ALLOWED_USERS isn't set in this profile's .env. Add the owner's own Telegram id "
                        "there first (the owner must stay on the list), then run this again.")
    current = [x.strip() for x in lines[idx].split("=", 1)[1].strip().strip("\"'").split(",") if x.strip()]
    conn = connect()
    ids = [r["chat_ref"].split(":", 1)[1] for r in conn.execute(
        "SELECT chat_ref FROM athlete_profile WHERE chat_ref LIKE 'telegram:%'")]
    added = [i for i in ids if i not in current]
    if not added:
        return {"ok": True, "added": [], "restart_needed": False, "allowed_count": len(current)}
    backup = f"{env}.bak-run-coach-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    with open(backup, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    lines[idx] = "TELEGRAM_ALLOWED_USERS=" + ",".join(current + added)
    with open(env, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"ok": True, "added": added, "allowed_count": len(current) + len(added), "restart_needed": True,
            "env_backup": backup,
            "instruction": "Ask the owner to run `hermes gateway restart` (all bots blink for a few seconds). "
                           "Don't run it yourself from a gateway chat: it would end your own session."}


def get_onboarding_status():
    """Owner view after sending the Sheet: where every athlete is between form and first message."""
    conn = connect()
    rows = []
    for a in conn.execute("SELECT * FROM athlete_profile ORDER BY name"):
        a = dict(a)
        goal = _active_goal(conn, a["athlete_id"]) or {}
        rows.append({"athlete_id": a["athlete_id"], "name": a["name"], "goal": goal.get("race_name"),
                     "telegram_linked": bool(a.get("chat_ref")), "status": _onboarding_status(conn, a)})
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"ok": True, "athletes": rows, "counts": counts}


TOOLS = {f.__name__: f for f in [
    init_db, list_athletes, ingest_intake_form, update_athlete_profile, record_course_info,
    generate_program, parse_run_file, match_run_to_program, compare_run_to_program, record_missed_session,
    get_upcoming_sessions, write_program_revision, schedule_checkin, get_due_checkins,
    check_trigger_staleness, get_athlete_summary, ingest_sheet_rows, link_chat, install_checkin_gate,
    get_trends, get_squad_overview, get_run_history, export_run_file, backfill_history,
    ingest_sheet_file, sync_telegram_allowlist, get_onboarding_status, review_week,
    get_review_tasks, record_safety_review, verify_race_info, confirm_race_info,
    strava_connect_link, strava_complete_connect, strava_sync, strava_status, strava_disconnect,
    get_pending_feedback, mark_feedback_sent, log_other_activity,
]}


def call_tool(name, args=None):
    """Single entry point for function-calling platforms."""
    if name not in TOOLS:
        return {"ok": False, "error": f"unknown tool {name!r}", "tools": sorted(TOOLS)}
    _DEPTH[0] += 1
    try:
        return TOOLS[name](**(args or {}))
    except (ToolError, strava.StravaError, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}" if not isinstance(e, ToolError) else str(e)}
    finally:
        _DEPTH[0] -= 1
        if _DEPTH[0] == 0:
            while _OPEN:
                try:
                    _OPEN.pop().close()
                except sqlite3.Error:
                    pass


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[1] == "--list":
        print(json.dumps(sorted(TOOLS), indent=2))
        return 0
    args = {}
    if len(argv) > 2:
        raw = argv[2]
        if raw.startswith("@"):
            with open(raw[1:]) as f:
                raw = f.read()
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"arguments are not valid JSON: {e}"}))
            return 1
    result = call_tool(argv[1], args)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
