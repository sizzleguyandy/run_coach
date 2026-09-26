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
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import analysis
from fit_parser import parse_fit
from general_fitness import (DEFAULT_WEEKS as GF_WEEKS, WALK_RUN_LADDER, _walk_run_label,
                             generate_general_fitness_plan)
from gpx_parser import parse_gpx
from race_plan import generate_race_plan

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


def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT name FROM sqlite_master WHERE name='athlete_profile'").fetchone():
        with open(os.path.join(KIT_DIR, "schema.sql")) as f:
            conn.executescript(f.read())
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
                out.setdefault(canon, value)
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
    return {"ok": True, "db_path": db_path(),
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

    health = norm.get("health_screen") or []
    if isinstance(health, str):
        health = [h.strip() for h in re.split(r"[,;]", health) if h.strip()]
    health = [h for h in health if "none" not in h.lower()]

    existing = None
    if norm.get("email"):
        existing = _row(conn.execute("SELECT * FROM athlete_profile WHERE lower(email)=lower(?)",
                                     (norm["email"],)).fetchone())
    athlete_id = existing["athlete_id"] if existing else (
        re.sub(r"[^a-z0-9]+", "-", str(norm.get("name") or norm["email"]).lower()).strip("-")[:24]
        + "-" + uuid.uuid4().hex[:6])

    profile = {
        "name": norm.get("name") or (existing or {}).get("name") or norm["email"],
        "email": norm.get("email"),
        "weight_kg": _num(norm.get("weight_kg")),
        "weight_source": "self_reported" if norm.get("weight_kg") else None,
        "weight_updated_at": now[:10] if norm.get("weight_kg") else None,
        "injury_history": norm.get("injury_history"),
        "health_screen_flags": json.dumps(health),
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
        if not norm.get("race_date") or not dist:
            raise ToolError("goal is 'race' but race date or distance is missing/unreadable: "
                            f"date={norm.get('race_date')!r} distance={norm.get('race_distance')!r}. "
                            "Ask the athlete, then resubmit with canonical keys race_date / race_distance.")
        goal = dict(race_id=race_id, athlete_id=athlete_id, goal_type="race",
                    race_name=norm.get("race_name") or f"{dist:g} km race",
                    race_date=str(norm["race_date"])[:10], start_time_local=norm.get("race_start_time"),
                    distance_km=dist, course_url=norm.get("course_url"), created_at=now)
    else:
        goal = dict(race_id=race_id, athlete_id=athlete_id, goal_type="general_fitness",
                    race_name="General fitness",
                    review_date=(start + timedelta(weeks=GF_WEEKS)).isoformat(), created_at=now)
    old = _active_goal(conn, athlete_id)
    conn.execute(f"INSERT INTO race_target ({', '.join(goal)}) VALUES ({', '.join('?' * len(goal))})",
                 tuple(goal.values()))
    if old:
        conn.execute("UPDATE race_target SET status='dropped', superseded_by=? WHERE race_id=?",
                     (race_id, old["race_id"]))
        warnings.append(f"Replaced previous active goal {old['race_name']!r} ({old['race_id']}).")
    conn.commit()

    # Tell the agent exactly what's left to do.
    if health:
        next_steps.append("MEDICAL: health check flagged " + ", ".join(health) + ". Ask the athlete to get "
                          "medical clearance; once they confirm, call update_athlete_profile with "
                          "medical_clearance_at. generate_program will refuse until then.")
    if goal_type == "race":
        next_steps.append("Fetch the course page yourself (web tool) -- "
                          + (goal["course_url"] or f"search for {goal['race_name']!r}")
                          + " -- then call record_course_info with elevation gain and terrain.")
    if hr_zone_file_url or norm.get("hr_zone_upload"):
        next_steps.append(f"Read the HR zone upload ({hr_zone_file_url or norm.get('hr_zone_upload')}) "
                          f"yourself, work out whether it's %max or %LTHR, and call "
                          f"update_athlete_profile with the hr_zone_* fields, hr_zone_source and "
                          f"hr_zone_basis_notes.")
    if norm.get("fixed_commitments"):
        next_steps.append("Convert the fixed-commitments free text to a JSON list "
                          "[{day, activity, moveable}] and call update_athlete_profile(fixed_commitments=...). "
                          "Pass any day that can't hold a run as constraints.avoid_days to generate_program.")
    if goal_type == "race" and (norm.get("longest_run_km") is None or norm.get("total_km_4wk") is None):
        next_steps.append("Longest run / 4-week km missing: ask the athlete before generating a race plan.")
    next_steps.append(f"Call generate_program(athlete_id={athlete_id!r}, race_id={race_id!r}, "
                      f"reason='initial plan from intake').")
    if not (existing or {}).get("chat_ref"):
        next_steps.append("When the athlete messages you, link their chat: link_chat(email=..., chat_ref=...). "
                          "Then set up their daily check-ins with install_checkin_gate FROM THEIR CHAT.")
    return {"ok": True, "duplicate": False, "athlete_id": athlete_id, "race_id": race_id, "goal_type": goal_type,
            "is_update_of_existing_athlete": bool(existing), "health_screen_flags": health,
            "needs_medical_clearance": bool(health), "normalized": norm,
            "warnings": warnings, "next_steps": next_steps}


def update_athlete_profile(athlete_id, fields):
    allowed = {"name", "email", "weight_kg", "weight_source", "injury_history", "fixed_commitments",
               "distance_unit", "easy_pace_pref_sec_per_km", "easy_pace_pref_source",
               "hr_zone_source", "hr_zone_basis_notes", "hr_max", "hr_lthr",
               "hr_zone1_low", "hr_zone1_high", "hr_zone2_low", "hr_zone2_high", "hr_zone3_low",
               "hr_zone3_high", "hr_zone4_low", "hr_zone4_high", "hr_zone5_low",
               "health_screen_flags", "medical_clearance_at",
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
    constraints = constraints or {}
    conn = connect()
    athlete = _get_athlete(conn, athlete_id)
    goal = _row(conn.execute("SELECT * FROM race_target WHERE race_id=? AND athlete_id=?",
                             (race_id, athlete_id)).fetchone())
    if not goal:
        raise ToolError(f"no goal {race_id!r} for athlete {athlete_id!r}")
    if goal["status"] != "active":
        raise ToolError(f"goal {race_id!r} is {goal['status']!r}; generate for the active goal")
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
        if intake.get("total_km_4wk") is None or intake.get("longest_run_km") is None:
            raise ToolError("race plan needs the athlete's longest run and total km over the last "
                            "4 weeks; ask them, then resubmit the intake with longest_run_km / total_km_4wk")
        plan = generate_race_plan(
            start.isoformat(), goal["race_date"], goal["distance_km"],
            baseline_weekly_km=_num(intake["total_km_4wk"]) / 4,
            longest_recent_km=_num(intake["longest_run_km"]),
            elevation_gain_m=goal.get("elevation_gain_m"),
            days_per_week=constraints.get("max_days_per_week") or constraints.get("min_days_per_week") or 4,
            long_run_day=long_run_day or "Sun", avoid_days=avoid_days,
            whole_number_distances=bool(constraints.get("whole_number_distances")),
            min_session_km=constraints.get("min_session_km") or 3.0,
            hr_zones={"zone2_high": athlete.get("hr_zone2_high"),
                      "zone4_low": athlete.get("hr_zone4_low"),
                      "zone4_high": athlete.get("hr_zone4_high")})
        if not plan["ok"]:
            return plan
        if goal.get("course_url") and goal.get("elevation_gain_m") is None:
            plan["summary"] += (" WARNING: course elevation not recorded yet -- generated as a flat "
                                "course; call record_course_info and regenerate if it's hilly.")

    now = _now()
    revision_id = _id("rev")
    conn.execute("INSERT INTO program_revision VALUES (?,?,?,?,?,?,?)",
                 (revision_id, athlete_id, race_id, now, reason, changed_by, plan["summary"]))
    superseded = conn.execute(
        "UPDATE program SET status='superseded' WHERE athlete_id=? AND status='pending' AND session_date>=?",
        (athlete_id, start.isoformat())).rowcount
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
        prompt = (f"Week {wk} review: ask how the week went. Mark any unlogged session with "
                  f"record_missed_session (or ask for the file). ")
        prompt += ("Only let next week progress if this one felt comfortable and pain-free; otherwise "
                   "regenerate at the same level (general_fitness_start_level)." if gf else
                   "Check signals from get_athlete_summary before confirming next week.")
        triggers.append(("scheduled_date", (date.fromisoformat(last) + timedelta(days=1)).isoformat(), None, prompt))
    for i in plan.get("time_trial_row_indexes", []):
        triggers.append(("after_program_row", None, row_ids[i],
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
    conn = connect()
    _get_athlete(conn, athlete_id)
    if not os.path.exists(file_path):
        raise ToolError(f"file not found: {file_path}")
    fmt = file_format
    if fmt == "auto":
        with open(file_path, "rb") as f:
            head = f.read(64)
        fmt = "fit" if b".FIT" in head[:14] else "gpx" if b"<" in head else os.path.splitext(file_path)[1][1:].lower()
    if fmt not in ("fit", "gpx"):
        raise ToolError(f"can't tell the format of {file_path}; pass file_format 'fit' or 'gpx'")
    parsed = parse_fit(file_path) if fmt == "fit" else parse_gpx(file_path)
    summary = {k: v for k, v in parsed.items() if k != "points"}
    if not summary.get("recorded_at"):
        return {"ok": False, "parser_notes": summary.get("parser_notes"),
                "error": "file has no usable timestamps/records; ask the athlete for a different export"}
    dup = conn.execute("SELECT run_log_id FROM run_log WHERE athlete_id=? AND recorded_at=?",
                       (athlete_id, summary["recorded_at"])).fetchone()
    if dup:
        return {"ok": True, "duplicate": True, "run_log_id": dup["run_log_id"], "summary": summary,
                "note": "this run was already logged; not inserted again"}
    rid = _id("run")
    conn.execute(
        "INSERT INTO run_log (run_log_id, athlete_id, source_file, source_format, recorded_at, distance_km, "
        "elapsed_time_min, moving_time_min, avg_hr, max_hr, elevation_gain_m, elevation_loss_m, "
        "avg_pace_sec_per_km, splits_json, first_half_avg_hr, second_half_avg_hr, hr_drift_delta, "
        "parser_notes, athlete_notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, athlete_id, os.path.basename(file_path), fmt, summary["recorded_at"], summary.get("distance_km"),
         summary.get("elapsed_time_min"), summary.get("moving_time_min"), summary.get("avg_hr"),
         summary.get("max_hr"), summary.get("elevation_gain_m"), summary.get("elevation_loss_m"),
         summary.get("avg_pace_sec_per_km"), json.dumps(summary.get("splits"), default=str),
         summary.get("first_half_avg_hr"), summary.get("second_half_avg_hr"), summary.get("hr_drift_delta"),
         summary.get("parser_notes"), athlete_notes, _now()))
    conn.commit()
    return {"ok": True, "duplicate": False, "run_log_id": rid, "summary": summary,
            "data_quality_warning": summary.get("parser_notes")}


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
        signals[name] = new
    conn.commit()
    return {"ok": True, "comparison": comp.__dict__, "session": prog, "signals": signals,
            "actions_needed": [n for n, s in signals.items() if s.get("action_threshold_met")]}


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
    conn.execute("INSERT INTO program_revision VALUES (?,?,?,?,?,?,?)",
                 (revision_id, athlete_id, race_id, now, reason, changed_by, summary))
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
            "latest_intake": _latest_intake(conn, athlete_id)}


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
# Generated by run-coach install_checkin_gate. Cron pre-run gate: wakes the
# agent only when {athlete_id} has a check-in due. Safe to delete (then remove
# the matching cron job).
import json, os, sys
sys.path.insert(0, {scripts_dir!r})
os.environ.setdefault("COACH_DB", {db!r})
import coach_tools
due = coach_tools.get_due_checkins(athlete_id={athlete_id!r})["due"]
if not due:
    print(json.dumps({{"wakeAgent": False}}))
else:
    print(json.dumps({{"wakeAgent": True, "context": {{"athlete_id": {athlete_id!r},
        "due_checkins": [{{"trigger_id": t["trigger_id"], "prompt": t["prompt_template"]}} for t in due]}}}}))
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
    prompt = (f"Run-coach check-ins for athlete {athlete_id} ({athlete['name']}). The pre-run context lists "
              f"the due check-ins. Load the run-coach skill and follow playbook F: for EACH trigger_id call "
              f"check_trigger_staleness first, then get_athlete_summary, then act. Your final response is sent "
              f"straight to the athlete: write only the message to them, friendly and short. If every due "
              f"check-in turned out stale or needs no message, reply with only [SILENT].")
    return {"ok": True, "gate_script": os.path.join(scripts, name),
            "cronjob_call": {"action": "create", "name": f"run-coach checkins {athlete_id}",
                             "schedule": "every day at 7am", "script": name, "skill": "run-coach",
                             "workdir": workdir, "attach_to_session": True, "prompt": prompt},
            "instruction": "Create the job with your cronjob_manage tool using cronjob_call exactly, from this "
                           "athlete's chat (delivery defaults to origin). Adjust the time to suit the athlete."}


TOOLS = {f.__name__: f for f in [
    init_db, list_athletes, ingest_intake_form, update_athlete_profile, record_course_info,
    generate_program, parse_run_file, match_run_to_program, compare_run_to_program, record_missed_session,
    get_upcoming_sessions, write_program_revision, schedule_checkin, get_due_checkins,
    check_trigger_staleness, get_athlete_summary, ingest_sheet_rows, link_chat, install_checkin_gate,
]}


def call_tool(name, args=None):
    """Single entry point for function-calling platforms."""
    if name not in TOOLS:
        return {"ok": False, "error": f"unknown tool {name!r}", "tools": sorted(TOOLS)}
    try:
        return TOOLS[name](**(args or {}))
    except (ToolError, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}" if not isinstance(e, ToolError) else str(e)}


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
