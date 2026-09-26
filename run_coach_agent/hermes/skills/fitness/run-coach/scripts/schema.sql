-- Run Coach Agent — SQLite schema
-- Design principle: the LLM plans and interprets; this schema is the deterministic
-- ground truth it plans and interprets FROM. Every number an agent reasons about
-- should be readable straight out of a table here, not recomputed from memory.

PRAGMA foreign_keys = ON;

-- ============================================================
-- ATHLETE_PROFILE — one row per athlete. Updated rarely, only on
-- explicit new information (a new HR zone export, a weight update).
-- ============================================================
CREATE TABLE athlete_profile (
    athlete_id          TEXT PRIMARY KEY,       -- form response ID or user-chosen slug
    name                TEXT NOT NULL,
    email               TEXT,
    weight_kg           REAL,
    weight_source       TEXT,                   -- 'self_reported' | 'device_sync'
    weight_updated_at    TEXT,                   -- ISO date

    -- HR zones: store the RAW basis, not just derived numbers, so the agent can
    -- explain where a zone boundary came from and re-derive it if the basis changes.
    hr_zone_source      TEXT,                   -- 'watch_export' | 'max_hr_formula' | 'lthr_test' | 'estimated_from_race_effort'
    hr_zone_basis_notes TEXT,                   -- free text: "watch zones are %LTHR not %max, LTHR backsolved to 168"
    hr_max              INTEGER,
    hr_lthr             INTEGER,
    hr_zone1_low        INTEGER, hr_zone1_high  INTEGER,
    hr_zone2_low        INTEGER, hr_zone2_high  INTEGER,
    hr_zone3_low        INTEGER, hr_zone3_high  INTEGER,
    hr_zone4_low        INTEGER, hr_zone4_high  INTEGER,
    hr_zone5_low        INTEGER,

    easy_pace_pref_sec_per_km INTEGER,          -- NULL unless athlete has explicitly overridden the derived easy pace
    easy_pace_pref_source     TEXT,             -- 'athlete_specified' | 'derived'

    -- Fixed weekly constraints — these are hard scheduling inputs, not preferences
    -- the generator is allowed to negotiate away.
    fixed_commitments   TEXT,                   -- JSON array: [{"day":"Mon","activity":"heavy legs","moveable":false}]
    injury_history       TEXT,
    health_screen_flags  TEXT,                   -- JSON array of any "yes" answers to the form's health questions; non-empty = get medical clearance before generating
    baseline_continuous_run_min INTEGER,         -- longest they can run non-stop right now (general-fitness intake)
    baseline_weekly_run_min     INTEGER,         -- roughly how many minutes of running per week lately
    medical_clearance_at TEXT,
    chat_ref             TEXT UNIQUE,            -- the athlete's chat identity (platform:user id from Hermes' session context) so a message can be tied to its athlete                   -- ISO date the athlete confirmed medical clearance (only needed if health_screen_flags is non-empty)
    distance_unit        TEXT DEFAULT 'km',      -- 'km' | 'mi' -- convert at the edges, store km internally regardless

    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- ============================================================
-- RACE_TARGET — one row per goal. An athlete could have more
-- than one over time; keep history rather than overwriting.
--
-- goal_type='general_fitness' is the "no event, just get fitter"
-- option from the intake form. It still gets a row here so program /
-- program_revision keep a single FK to hang off, but it has no race
-- date, distance or course: race_name is 'General fitness' and
-- review_date marks the end of the block, when the agent checks in
-- and either starts another block or switches to a race goal.
-- ============================================================
CREATE TABLE race_target (
    race_id             TEXT PRIMARY KEY,
    athlete_id          TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    goal_type           TEXT NOT NULL DEFAULT 'race'
                        CHECK (goal_type IN ('race', 'general_fitness')),
    race_name           TEXT NOT NULL,
    race_date           TEXT,                    -- ISO date; required for goal_type='race'
    review_date         TEXT,                    -- ISO date; end of a general-fitness block
    start_time_local    TEXT,                    -- '05:45'
    distance_km         REAL,                    -- required for goal_type='race'
    location             TEXT,
    course_url          TEXT,                    -- source for elevation/terrain -- fetch, don't ask the athlete to describe it
    elevation_gain_m    REAL,
    terrain_notes       TEXT,                    -- "firm farm paths, not trail but rougher than road"
    status              TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'dropped' | 'completed'
    superseded_by        TEXT REFERENCES race_target(race_id),  -- set when a new race target replaces this one
    created_at           TEXT NOT NULL,
    CHECK (goal_type = 'general_fitness'
           OR (race_date IS NOT NULL AND distance_km IS NOT NULL))
);

-- ============================================================
-- PROGRAM — the generated plan. One row per PLANNED session.
-- Never delete rows here to "fix" the plan -- write a new revision
-- (see program_revision) and mark old rows superseded. This is what
-- lets a stale scheduled check-in detect that the plan moved on
-- without it, instead of confidently answering an outdated question.
-- ============================================================
CREATE TABLE program (
    program_row_id      TEXT PRIMARY KEY,
    athlete_id          TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    race_id             TEXT NOT NULL REFERENCES race_target(race_id),
    revision_id         TEXT NOT NULL,           -- FK to program_revision -- which plan version wrote this row
    week_num            INTEGER NOT NULL,
    session_date        TEXT NOT NULL,           -- ISO date, not just day-of-week -- always resolve to a real calendar date
    day_of_week         TEXT NOT NULL,
    session_type        TEXT NOT NULL,           -- 'easy' | 'quality' | 'long' | 'time_trial' | 'race' | 'rest' | 'walk_run'
    session_label       TEXT,                    -- 'HILL REPEATS' etc, human-readable
    prescribed_distance_km   REAL,
    prescribed_duration_min  REAL,               -- general-fitness plans are time-based: distance is NULL, this is set
    prescribed_effort_desc   TEXT,               -- 'HR 150-160' or '6:30/km' -- keep as text, both units appear
    prescribed_hr_low   INTEGER,
    prescribed_hr_high  INTEGER,
    elevation_target_m  REAL,
    week_phase          TEXT,                    -- 'rebuild' | 'build' | 'cutback' | 'peak' | 'taper' | 'race_week' | 'foundation' | 'consolidate' (last two: general-fitness walk/run)
    status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'done' | 'missed' | 'moved' | 'superseded'
    moved_to_date        TEXT,                    -- if status='moved', where it went
    matched_run_log_id  TEXT REFERENCES run_log(run_log_id),
    created_at           TEXT NOT NULL
);

-- ============================================================
-- PROGRAM_REVISION — audit trail of every time the plan changed.
-- This is the fix for the exact failure mode we hit: a scheduled
-- check-in fired referencing session structure that had since been
-- rebuilt, and nothing flagged the mismatch automatically.
-- ============================================================
CREATE TABLE program_revision (
    revision_id          TEXT PRIMARY KEY,
    athlete_id           TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    race_id              TEXT NOT NULL REFERENCES race_target(race_id),
    created_at            TEXT NOT NULL,
    reason               TEXT NOT NULL,          -- "time trial result", "athlete requested whole-km distances", "race date changed"
    changed_by           TEXT NOT NULL,          -- 'agent' | 'athlete_request'
    summary              TEXT                    -- short human-readable diff summary
);

-- ============================================================
-- RUN_LOG — one row per ACTUAL run, populated by parsing an
-- uploaded .fit or .gpx file. Never hand-computed by the agent --
-- always written by the deterministic parser.
-- ============================================================
CREATE TABLE run_log (
    run_log_id           TEXT PRIMARY KEY,
    athlete_id           TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    source_file          TEXT NOT NULL,
    source_format        TEXT NOT NULL,          -- 'fit' | 'gpx'
    recorded_at          TEXT NOT NULL,           -- start timestamp, ISO
    distance_km          REAL,
    elapsed_time_min     REAL,
    moving_time_min      REAL,
    avg_hr               REAL,
    max_hr               INTEGER,
    elevation_gain_m     REAL,
    elevation_loss_m     REAL,
    avg_pace_sec_per_km  REAL,                    -- moving-time based
    splits_json          TEXT,                    -- per-km: [{"km":1,"pace_sec":..,"avg_hr":..}, ...]
    first_half_avg_hr    REAL,                    -- drift-detection inputs, computed once at parse time
    second_half_avg_hr   REAL,
    hr_drift_delta       REAL,                    -- second_half_avg_hr - first_half_avg_hr
    matched_program_row_id TEXT REFERENCES program(program_row_id),
    parser_notes         TEXT,                    -- anything odd the parser flagged (short recording, no HR data, GPS gaps)
    athlete_notes        TEXT,                    -- free text the athlete or intake message included
    created_at            TEXT NOT NULL
);

-- ============================================================
-- SIGNAL_STATE — running counters for patterns that should only
-- trigger a plan change after repeating, not on a single run.
-- This is what stopped us overreacting to one hard long run and
-- only acting once the SAME pattern showed up twice.
-- ============================================================
CREATE TABLE signal_state (
    athlete_id            TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    signal_name           TEXT NOT NULL,          -- 'long_run_hr_drift' | 'missed_easy_run_streak' | 'distance_shortfall'
    current_streak        INTEGER NOT NULL DEFAULT 0,
    last_run_log_id       TEXT REFERENCES run_log(run_log_id),
    last_updated_at        TEXT NOT NULL,
    notes                  TEXT,
    PRIMARY KEY (athlete_id, signal_name)
);

-- ============================================================
-- SIGNAL_EVENT — append-only history behind signal_state. signal_state
-- only holds the CURRENT streak; this keeps every flagged/clean event so
-- trends can show how often a pattern has appeared over months.
-- ============================================================
CREATE TABLE signal_event (
    event_id              TEXT PRIMARY KEY,
    athlete_id            TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    signal_name           TEXT NOT NULL,
    occurred_at           TEXT NOT NULL,
    run_log_id            TEXT REFERENCES run_log(run_log_id),
    program_row_id        TEXT REFERENCES program(program_row_id),
    flagged               INTEGER NOT NULL,        -- 1 = this event extended the streak
    streak_after          INTEGER NOT NULL,
    threshold_met         INTEGER NOT NULL
);

-- ============================================================
-- CHECKIN_TRIGGER — scheduled or conditional check-ins bound to
-- specific plan events (day after a time trial, Nth consecutive
-- flagged pattern), not arbitrary calendar cadence.
-- ============================================================
CREATE TABLE checkin_trigger (
    trigger_id             TEXT PRIMARY KEY,
    athlete_id             TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    trigger_type           TEXT NOT NULL,         -- 'scheduled_date' | 'after_program_row' | 'after_signal_streak'
    fires_at_date           TEXT,                  -- for 'scheduled_date'
    depends_on_program_row  TEXT REFERENCES program(program_row_id),  -- for 'after_program_row'
    depends_on_signal        TEXT,                  -- for 'after_signal_streak', matches signal_state.signal_name
    depends_on_streak_count  INTEGER,
    revision_id_at_creation  TEXT NOT NULL REFERENCES program_revision(revision_id),  -- staleness check
    prompt_template          TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'fired' | 'stale_cancelled'
    origin                    TEXT NOT NULL DEFAULT 'agent',    -- 'generator' (auto-created with a plan; cancelled when the plan is regenerated) | 'agent'
    created_at                TEXT NOT NULL
);

-- ============================================================
-- INTAKE_RESPONSE — every form submission, raw and normalised.
-- Fields the program generators need that aren't athlete facts
-- (longest recent run, 4-week km, best effort, preferred long-run
-- day, days to avoid, delivery preference, free-text notes) live
-- here rather than being force-fit into athlete_profile columns.
-- ============================================================
CREATE TABLE intake_response (
    intake_id             TEXT PRIMARY KEY,
    athlete_id            TEXT NOT NULL REFERENCES athlete_profile(athlete_id),
    received_at            TEXT NOT NULL,
    raw_json               TEXT NOT NULL,          -- exactly what the form delivered
    normalized_json        TEXT NOT NULL           -- canonical keys, see coach_tools.INTAKE_FIELDS
);

CREATE INDEX idx_program_athlete_date ON program(athlete_id, session_date);
CREATE INDEX idx_runlog_athlete_date ON run_log(athlete_id, recorded_at);
CREATE INDEX idx_program_status ON program(status);
CREATE INDEX idx_signal_event_athlete ON signal_event(athlete_id, occurred_at);
CREATE INDEX idx_intake_athlete ON intake_response(athlete_id, received_at);
