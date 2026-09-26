"""
analysis.py — deterministic comparison engine.

This is the layer that decides WHAT happened (numbers, deltas,
pattern streaks). Deciding what it MEANS and what to do about it is
the agent's job, working from this engine's output -- see
AGENT_INSTRUCTIONS.md's "division of labor" section for why that
split matters.

Everything here is pure computation on the schema in schema.sql.
No LLM calls, no network access, no judgment calls beyond the
thresholds a human coach set explicitly (and can change).
"""
from dataclasses import dataclass, field
from datetime import datetime


# ---- Thresholds -------------------------------------------------
# Centralized so a real coach can tune them without hunting through
# logic. These are the values used throughout this project's actual
# training block; treat them as defaults, not universal truths.

HR_DRIFT_FLAG_BPM = 15          # second-half avg HR this much above first-half -> flag the run
HR_DRIFT_STREAK_FOR_ACTION = 2   # this many flagged runs in a row -> the pattern is real, not noise
DISTANCE_SHORTFALL_PCT = 0.90    # actual/planned below this -> flag
DISTANCE_SHORTFALL_STREAK_FOR_ACTION = 2  # this many short runs in a row -> raise it
MISSED_RUN_STREAK_FOR_ACTION = 2 # consecutive missed sessions of the same type -> raise it
VOLUME_JUMP_WARN_PCT = 0.10      # week-over-week growth above this -> note it (not necessarily wrong)
GENERAL_FITNESS_VOLUME_JUMP_WARN_PCT = 0.05  # general-fitness blocks ramp at half the race-build rate


@dataclass
class RunComparison:
    run_log_id: str
    program_row_id: str | None
    distance_delta_km: float | None
    distance_pct_of_plan: float | None
    hr_drift_delta: float | None
    hr_drift_flagged: bool
    pace_vs_prescribed_sec_per_km: float | None
    duration_pct_of_plan: float | None = None
    session_type: str | None = None          # of the matched program row, if any
    notes: list[str] = field(default_factory=list)


def compare_run_to_program(run_log_row: dict, program_row: dict | None) -> RunComparison:
    """Compare one parsed run against the program row it was matched
    to (matching itself -- nearest date + session type -- is a
    separate, simpler function; do it before calling this)."""
    notes = []
    distance_delta = distance_pct = pace_delta = duration_pct = None

    if program_row and program_row.get('prescribed_distance_km'):
        planned = program_row['prescribed_distance_km']
        actual = run_log_row.get('distance_km')
        if actual is not None:
            distance_delta = actual - planned
            distance_pct = actual / planned
            if distance_pct < DISTANCE_SHORTFALL_PCT:
                notes.append(f"came up short: {actual:.2f}km vs {planned:.2f}km planned "
                             f"({distance_pct*100:.0f}%)")

    # General-fitness plans are prescribed in minutes, not km. Compare
    # elapsed time (walk breaks count -- they're part of the session).
    if (program_row and not program_row.get('prescribed_distance_km')
            and program_row.get('prescribed_duration_min')):
        planned_min = program_row['prescribed_duration_min']
        actual_min = run_log_row.get('elapsed_time_min') or run_log_row.get('moving_time_min')
        if actual_min is not None:
            duration_pct = actual_min / planned_min
            if duration_pct < DISTANCE_SHORTFALL_PCT:
                notes.append(f"came up short: {actual_min:.0f} min vs {planned_min:.0f} min "
                             f"planned ({duration_pct*100:.0f}%)")
            elif duration_pct > 1 + GENERAL_FITNESS_VOLUME_JUMP_WARN_PCT * 2:
                notes.append(f"went longer than planned: {actual_min:.0f} min vs "
                             f"{planned_min:.0f} min. On a slow-ramp plan, doing more than "
                             f"prescribed is the thing to watch, not to praise.")

    if program_row and program_row.get('prescribed_hr_high') and run_log_row.get('avg_hr'):
        cap = program_row['prescribed_hr_high']
        if run_log_row['avg_hr'] > cap:
            notes.append(f"whole-run avg HR {run_log_row['avg_hr']:.0f} exceeds the "
                         f"{cap} cap -- but check the drift split below before concluding "
                         f"anything: a fine first half can hide a bad second half.")

    hr_drift = run_log_row.get('hr_drift_delta')
    hr_drift_flagged = hr_drift is not None and hr_drift >= HR_DRIFT_FLAG_BPM
    if hr_drift_flagged:
        fh = run_log_row.get('first_half_avg_hr')
        sh = run_log_row.get('second_half_avg_hr')
        notes.append(f"HR drift: {fh:.0f} avg first half -> {sh:.0f} avg second half "
                     f"(+{hr_drift:.0f}bpm). A single instance can be heat, terrain, or "
                     f"fatigue from other sessions -- check signal_state before treating "
                     f"this as a fitness signal.")

    return RunComparison(
        run_log_id=run_log_row.get('run_log_id'),
        program_row_id=program_row.get('program_row_id') if program_row else None,
        distance_delta_km=distance_delta,
        distance_pct_of_plan=distance_pct,
        hr_drift_delta=hr_drift,
        hr_drift_flagged=hr_drift_flagged,
        pace_vs_prescribed_sec_per_km=pace_delta,
        duration_pct_of_plan=duration_pct,
        session_type=program_row.get('session_type') if program_row else None,
        notes=notes,
    )


# Each signal: which session types it tracks, and how many flagged
# events in a row count as a real pattern. A session of a type the
# signal doesn't track leaves its streak untouched -- an easy run says
# nothing about long-run HR drift, so it must not reset that streak.
SIGNALS = {
    'long_run_hr_drift':      {'session_types': {'long'},
                               'threshold': HR_DRIFT_STREAK_FOR_ACTION},
    'distance_shortfall':     {'session_types': None,   # any matched session
                               'threshold': DISTANCE_SHORTFALL_STREAK_FOR_ACTION},
    'missed_easy_run_streak': {'session_types': {'easy', 'walk_run'},
                               'threshold': MISSED_RUN_STREAK_FOR_ACTION},
}


def update_signal_state(current_state: dict | None, signal_name: str, now_iso: str,
                        comparison: RunComparison | None = None,
                        run_log_id: str | None = None,
                        missed_program_row: dict | None = None) -> dict:
    """Update a streak counter for one signal. This is what turns 'one
    bad run' into 'a pattern worth acting on' -- the agent should only
    escalate when action_threshold_met is True, and say so explicitly
    rather than reacting to single events.

    Two kinds of event feed it:
      - a completed run: pass `comparison` (+ run_log_id). A run that
        matched a tracked session type either extends the streak
        (flagged) or resets it (clean).
      - a missed session: pass `missed_program_row`. Only
        missed_easy_run_streak counts these; a completed easy/walk_run
        session resets it.

    current_state: the existing signal_state row for
    (athlete_id, signal_name), or None if this is the first time.
    Returns the new row plus 'action_threshold_met' and 'updated'
    (False when the event wasn't relevant to this signal).
    """
    if signal_name not in SIGNALS:
        raise ValueError(f"unknown signal {signal_name!r}; expected one of {sorted(SIGNALS)}")
    spec = SIGNALS[signal_name]
    prev = current_state['current_streak'] if current_state else 0

    if missed_program_row is not None:
        session_type = missed_program_row.get('session_type')
        relevant = signal_name == 'missed_easy_run_streak' and session_type in spec['session_types']
        is_flagged = True
    elif comparison is not None:
        session_type = comparison.session_type
        relevant = spec['session_types'] is None or session_type in spec['session_types']
        if signal_name == 'distance_shortfall' and session_type is None:
            relevant = False                   # unmatched run: nothing to fall short of
        is_flagged = {
            'long_run_hr_drift': comparison.hr_drift_flagged,
            'distance_shortfall': any(pct is not None and pct < DISTANCE_SHORTFALL_PCT
                                      for pct in (comparison.distance_pct_of_plan,
                                                  comparison.duration_pct_of_plan)),
            'missed_easy_run_streak': False,   # it happened, so it wasn't missed
        }[signal_name]
    else:
        raise ValueError("pass either comparison (a completed run) or missed_program_row")

    if not relevant:
        streak = prev
    else:
        streak = prev + 1 if is_flagged else 0

    return {
        'signal_name': signal_name,
        'current_streak': streak,
        'last_run_log_id': run_log_id if (relevant and comparison is not None)
                           else (current_state or {}).get('last_run_log_id'),
        'last_updated_at': now_iso if relevant else (current_state or {}).get('last_updated_at', now_iso),
        'action_threshold': spec['threshold'],
        'action_threshold_met': streak >= spec['threshold'],
        'updated': relevant,
    }


def check_program_revision_staleness(trigger_revision_id: str, current_revision_id: str) -> bool:
    """A checkin_trigger was written against a specific program
    revision. If the plan has since been revised, the trigger's
    prompt may reference sessions or structure that no longer exist
    -- this is the exact failure mode this project hit with a real
    scheduled check-in. Call this before acting on any fired trigger;
    if it returns True, surface the mismatch to the athlete instead
    of answering the stale prompt at face value."""
    return trigger_revision_id != current_revision_id


def weekly_volume_check(this_week_km: float, last_week_km: float,
                        goal_type: str = 'race') -> dict:
    """Flag (not block) week-over-week jumps above the guideline.
    A jump above threshold isn't automatically wrong -- e.g. a
    fourth weekly run returning after a minimum-session-length floor
    forces a step -- but the agent should know about it and be able
    to explain why, rather than silently generating an aggressive
    ramp.

    For goal_type='general_fitness', pass weekly MINUTES instead of km
    (those plans are time-based) -- the tighter 5% threshold applies."""
    if last_week_km <= 0:
        return {'pct_change': None, 'flagged': False}
    threshold = (GENERAL_FITNESS_VOLUME_JUMP_WARN_PCT if goal_type == 'general_fitness'
                 else VOLUME_JUMP_WARN_PCT)
    pct_change = (this_week_km - last_week_km) / last_week_km
    return {'pct_change': pct_change, 'flagged': pct_change > threshold}


def match_run_to_program_row(run_recorded_at: str, run_distance_km: float,
                               candidate_program_rows: list[dict]) -> dict | None:
    """Match an uploaded run to the nearest pending program row by
    date (same day, else nearest within +/-1 day), preferring rows
    whose session_type isn't 'rest'. Returns None if nothing is
    within 1 day -- the agent should then ask the athlete what this
    run was, rather than guessing."""
    run_date = datetime.fromisoformat(run_recorded_at).date()
    best = None
    best_delta = None
    for row in candidate_program_rows:
        if row.get('status') not in ('pending',):
            continue
        if row.get('session_type') == 'rest':
            continue
        row_date = datetime.fromisoformat(row['session_date']).date()
        delta = abs((row_date - run_date).days)
        if delta <= 1 and (best_delta is None or delta < best_delta):
            best, best_delta = row, delta
    return best
