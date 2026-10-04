"""
history.py — the permanent run history database (history.db).

Every run file an athlete sends is recorded here, forever:
  - the ORIGINAL file (compressed), so any run can be re-exported exactly
    as uploaded and re-parsed if the parsers ever improve;
  - the full parsed summary, per-km splits and every recorded sample
    (time, HR, distance, elevation, ...);
  - every upload attempt, including duplicates and files that failed
    to parse;
  - what happened to each run afterwards (matched to a session, compared,
    warning flags) as an event log.

It is append-only: SQLite triggers reject every UPDATE and DELETE, so
history can't be rewritten by a bug or a bad instruction. It is separate
from coach.db (the live coaching state, which does change), so plan
rebuilds never touch it.

Location: $RUN_HISTORY_DB, else history.db next to coach.db.
"""
import hashlib
import json
import os
import sqlite3
import uuid
import zlib
from datetime import datetime, timezone

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    history_id          TEXT PRIMARY KEY,
    athlete_id          TEXT NOT NULL,
    athlete_name        TEXT,                 -- snapshot at upload time
    athlete_email       TEXT,
    recorded_at         TEXT NOT NULL,        -- run start, from the file
    logged_at           TEXT NOT NULL,        -- when it was uploaded
    source_file_name    TEXT NOT NULL,
    source_format       TEXT NOT NULL,        -- 'fit' | 'gpx'
    file_sha256         TEXT NOT NULL,
    file_bytes          INTEGER NOT NULL,
    file_blob_zlib      BLOB NOT NULL,        -- the original file, zlib-compressed
    distance_km         REAL,
    elapsed_time_min    REAL,
    moving_time_min     REAL,
    avg_hr              REAL,
    max_hr              INTEGER,
    elevation_gain_m    REAL,
    elevation_loss_m    REAL,
    avg_pace_sec_per_km REAL,
    splits_json         TEXT,
    first_half_avg_hr   REAL,
    second_half_avg_hr  REAL,
    hr_drift_delta      REAL,
    samples_zlib        BLOB,                 -- every recorded point as JSON, zlib-compressed
    sample_count        INTEGER,
    parser_notes        TEXT,
    athlete_notes       TEXT,
    coach_run_log_id    TEXT,                 -- the matching row in coach.db run_log
    UNIQUE (athlete_id, recorded_at)
);

CREATE TABLE IF NOT EXISTS run_events (
    event_id            TEXT PRIMARY KEY,
    history_id          TEXT NOT NULL REFERENCES runs(history_id),
    occurred_at         TEXT NOT NULL,
    event_type          TEXT NOT NULL,        -- 'logged' | 'matched' | 'unmatched' | 'compared'
    detail_json         TEXT
);

CREATE TABLE IF NOT EXISTS upload_attempts (
    attempt_id          TEXT PRIMARY KEY,
    athlete_id          TEXT NOT NULL,
    attempted_at        TEXT NOT NULL,
    source_file_name    TEXT,
    file_sha256         TEXT,
    outcome             TEXT NOT NULL,        -- 'recorded' | 'duplicate' | 'parse_failed' | 'backfilled'
    history_id          TEXT,
    detail              TEXT
);

CREATE TABLE IF NOT EXISTS other_activities (
    history_id          TEXT PRIMARY KEY,
    athlete_id          TEXT NOT NULL,
    external_id         TEXT NOT NULL UNIQUE,  -- e.g. 'strava:123456'
    start_at            TEXT NOT NULL,
    logged_at           TEXT NOT NULL,
    category            TEXT NOT NULL,         -- 'strength' | 'mobility' | 'cross_training'
    sport_type          TEXT,
    summary_json        TEXT NOT NULL,         -- name, description, durations, HR, perceived exertion
    raw_zlib            BLOB                   -- the source record (e.g. Strava activity JSON), compressed
);

CREATE INDEX IF NOT EXISTS idx_runs_athlete ON runs(athlete_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(history_id, occurred_at);

CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'history.db is append-only: runs cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'history.db is append-only: runs cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON run_events
BEGIN SELECT RAISE(ABORT, 'history.db is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON run_events
BEGIN SELECT RAISE(ABORT, 'history.db is append-only'); END;
CREATE TRIGGER IF NOT EXISTS other_no_update BEFORE UPDATE ON other_activities
BEGIN SELECT RAISE(ABORT, 'history.db is append-only'); END;
CREATE TRIGGER IF NOT EXISTS other_no_delete BEFORE DELETE ON other_activities
BEGIN SELECT RAISE(ABORT, 'history.db is append-only'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON upload_attempts
BEGIN SELECT RAISE(ABORT, 'history.db is append-only'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON upload_attempts
BEGIN SELECT RAISE(ABORT, 'history.db is append-only'); END;
"""


def history_path(coach_db_path):
    return os.environ.get("RUN_HISTORY_DB") or os.path.join(os.path.dirname(os.path.abspath(coach_db_path)),
                                                            "history.db")


def connect(coach_db_path):
    conn = sqlite3.connect(history_path(coach_db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record_attempt(hconn, athlete_id, file_name, sha, outcome, history_id=None, detail=None):
    hconn.execute("INSERT INTO upload_attempts VALUES (?,?,?,?,?,?,?,?)",
                  (_id("upl"), athlete_id, _now(), file_name, sha, outcome, history_id, detail))


def record_run(hconn, athlete, path, fmt, parsed, coach_run_log_id, athlete_notes=None, raw=None,
               source_name=None):
    """Insert the run (with original file + all samples). Returns history_id,
    or the existing history_id if this athlete already has a run at that start time.
    For sources without a file (Strava), pass raw=<bytes of the source record>."""
    existing = hconn.execute("SELECT history_id FROM runs WHERE athlete_id=? AND recorded_at=?",
                             (athlete["athlete_id"], parsed["recorded_at"])).fetchone()
    if existing:
        return existing["history_id"], False
    if raw is None:
        with open(path, "rb") as f:
            raw = f.read()
    points = parsed.get("points") or []
    hid = _id("hist")
    hconn.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (hid, athlete["athlete_id"], athlete.get("name"), athlete.get("email"), parsed["recorded_at"], _now(),
         source_name or os.path.basename(path), fmt, hashlib.sha256(raw).hexdigest(), len(raw), zlib.compress(raw, 9),
         parsed.get("distance_km"), parsed.get("elapsed_time_min"), parsed.get("moving_time_min"),
         parsed.get("avg_hr"), parsed.get("max_hr"), parsed.get("elevation_gain_m"), parsed.get("elevation_loss_m"),
         parsed.get("avg_pace_sec_per_km"), json.dumps(parsed.get("splits"), default=str),
         parsed.get("first_half_avg_hr"), parsed.get("second_half_avg_hr"), parsed.get("hr_drift_delta"),
         zlib.compress(json.dumps(points, default=str).encode(), 9), len(points),
         parsed.get("parser_notes"), athlete_notes, coach_run_log_id))
    add_event(hconn, hid, "logged", {"coach_run_log_id": coach_run_log_id})
    return hid, True


def record_other(hconn, athlete_id, summary, raw=None):
    """Append a non-run workout (strength, mobility, cross-training). Idempotent by external_id."""
    if hconn.execute("SELECT 1 FROM other_activities WHERE external_id=?", (summary["external_id"],)).fetchone():
        return False
    hconn.execute("INSERT INTO other_activities VALUES (?,?,?,?,?,?,?,?,?)",
                  (_id("hoth"), athlete_id, summary["external_id"], summary["start_at"], _now(), summary["category"],
                   summary.get("sport_type"), json.dumps(summary, default=str),
                   zlib.compress(raw, 9) if raw else None))
    return True


def add_event(hconn, history_id, event_type, detail=None):
    hconn.execute("INSERT INTO run_events VALUES (?,?,?,?,?)",
                  (_id("evt"), history_id, _now(), event_type, json.dumps(detail, default=str)))


def history_id_for(hconn, coach_run_log_id):
    r = hconn.execute("SELECT history_id FROM runs WHERE coach_run_log_id=?", (coach_run_log_id,)).fetchone()
    return r["history_id"] if r else None
