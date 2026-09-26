"""
general_fitness.py — deterministic program generator for the
"general fitness / just get fitter" goal (no race selected on the form).

Same division of labor as the rest of the kit: the agent decides
WHEN to call this and explains the output; this code decides the
numbers. The ramp here is deliberately slower and more conservative
than the race-build rules in references/coaching-rules.md, because a
general-fitness athlete has no deadline to justify any risk:

  - Everything is prescribed in MINUTES, not km, at conversational
    effort. No paces, no time trials, no hill repeats, no quality
    sessions.
  - Athletes who can't yet run 20 minutes non-stop start on a
    walk/run ladder and move up at most one rung per week.
  - Athletes who can run 20+ minutes grow weekly time by at most 5%
    (and never more than 10 minutes) week over week.
  - Every 3rd week is an easier week (walk/run: repeat an earlier
    rung; continuous: drop to ~80%). The week after a cutback steps
    up from the last build week, never rebounds off the easy week.
  - Sessions never land on consecutive days where the athlete's
    available days allow it; beginners (walk/run) never run on
    back-to-back days at all.
  - A 4th weekly session only appears once weekly time is comfortably
    established (week 7+ and 120+ min/week).

The generated plan is the PLANNED ramp. Progression at runtime is
gated on the athlete actually coping: if a week wasn't completed
comfortably (pain, missed sessions, "that was too hard"), the agent
regenerates from the same level via write_program_revision rather
than moving on -- see `start_step` / `start_weekly_min` below.

Zero dependencies. Usage:
    from general_fitness import generate_general_fitness_plan
    plan = generate_general_fitness_plan(start_date="2026-10-05",
                                         continuous_run_min=5,
                                         current_weekly_run_min=0)

    python3 general_fitness.py            # prints a demo plan
"""
import math
from datetime import date, timedelta
from itertools import combinations

# ---- Tunable guardrails -----------------------------------------
# Centralised so a coach can tighten/loosen without touching logic.

DEFAULT_WEEKS = 12                  # one block, then reassess with the athlete
CONTINUOUS_THRESHOLD_MIN = 20       # can run this long non-stop -> skip the walk/run ladder
WEEKLY_GROWTH_PCT = 0.05            # max week-over-week growth (race builds use 10%)
WEEKLY_GROWTH_CAP_MIN = 10          # ...and never more than this many minutes in absolute terms
CUTBACK_EVERY = 3                   # every Nth week is an easier week
CUTBACK_FACTOR = 0.80               # cutback week = this fraction of the previous build week
START_FACTOR = 0.90                 # start BELOW what they're currently doing, not at it
MIN_START_WEEKLY_MIN = 45           # 3 x 15min floor for continuous runners
MAX_WEEKLY_MIN = 180                # ceiling for a general-fitness block
MIN_SESSION_MIN = 15
LONG_SESSION_SHARE = 0.35           # longest session's share of weekly time
FOURTH_SESSION_FROM_WEEK = 7
FOURTH_SESSION_MIN_WEEKLY = 120
WARMUP_WALK_MIN = 5                 # brisk walk before and after every walk/run session

EFFORT_DESC = ("Conversational effort (RPE 3-4/10): you can speak in full "
               "sentences. Slow down or walk whenever you can't.")

# Walk/run ladder: (run_min, walk_min, reps). Total running time never
# decreases rung to rung, and each rung is a small absolute step.
WALK_RUN_LADDER = [
    (1, 2, 6),      #  6 min running
    (1.5, 2, 6),    #  9
    (2, 2, 6),      # 12
    (3, 2, 5),      # 15
    (4, 2, 4),      # 16
    (5, 2, 4),      # 20
    (7, 2, 3),      # 21
    (8, 2, 3),      # 24
    (10, 2, 3),     # 30
    (15, 2, 2),     # 30, fewer breaks
    (20, 2, 1),     # 20 + 2 walk + 10 run (final rep handled below)
    (25, 0, 1),     # 25 continuous
    (30, 0, 1),     # 30 continuous -> graduate
]

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---- Day selection ----------------------------------------------

def _circular_gaps(days):
    s = sorted(days)
    return [((s[(i + 1) % len(s)] - s[i]) % 7) or 7 for i in range(len(s))]


def choose_run_days(n, long_run_day=None, avoid_days=(), allow_back_to_back=True):
    """Pick n weekday indices (0=Mon) that are as spread out as
    possible, honouring the preferred long-run day and days to avoid.
    Raises ValueError if walk/run athletes can't avoid back-to-back
    days -- the agent should then drop to fewer sessions, not squeeze."""
    avoid = {DAY_NAMES.index(d) for d in avoid_days}
    available = [d for d in range(7) if d not in avoid]
    lr = DAY_NAMES.index(long_run_day) if long_run_day else None
    if lr is not None and lr in avoid:
        lr = None

    best, best_key = None, None
    for combo in combinations(available, n):
        if lr is not None and lr not in combo:
            continue
        gaps = _circular_gaps(combo) if n > 1 else [7]
        if not allow_back_to_back and min(gaps) < 2:
            continue
        # Maximise the smallest gap, then minimise how many back-to-back pairs.
        key = (min(gaps), -sum(1 for g in gaps if g == 1))
        if best_key is None or key > best_key:
            best, best_key = combo, key
    if best is None:
        raise ValueError(f"Can't place {n} sessions on the available days "
                         f"without back-to-back running; reduce sessions per week.")
    return sorted(best)


# ---- Weekly targets ---------------------------------------------

def _walk_run_rungs(weeks, start_step):
    """Rung index per week: up one rung per build week, back one rung
    on every cutback week, and the week after a cutback moves up just
    one rung from the last BUILD week (never two from the cutback)."""
    rungs, step, last_build = [], start_step, start_step
    for w in range(1, weeks + 1):
        if w % CUTBACK_EVERY == 0:
            rungs.append((max(0, last_build - 1), "consolidate"))
            continue
        if w > 1:
            step = min(last_build + 1, len(WALK_RUN_LADDER) - 1)
        rungs.append((step, "foundation"))
        last_build = step
    return rungs


def _continuous_minutes(weeks, start_weekly_min):
    mins, last_build = [], start_weekly_min
    for w in range(1, weeks + 1):
        if w % CUTBACK_EVERY == 0:
            mins.append((round(last_build * CUTBACK_FACTOR), "cutback"))
            continue
        if w > 1:
            # Growth is measured from the last BUILD week, so the week after
            # a cutback is a normal +5% step, not a rebound off the low week.
            step = min(last_build * WEEKLY_GROWTH_PCT, WEEKLY_GROWTH_CAP_MIN)
            # Floor, so rounding can never push a week past the growth cap.
            last_build = min(math.floor(last_build + step), MAX_WEEKLY_MIN)
        mins.append((round(last_build), "build"))
    return mins


def _split_week(total_min, n_sessions):
    """Longest session gets LONG_SESSION_SHARE, the rest split evenly,
    in whole minutes floored at MIN_SESSION_MIN. Whole minutes (not
    5-minute blocks) so rounding can't sneak a bigger jump past the
    weekly growth cap."""
    if n_sessions == 1:
        return [max(MIN_SESSION_MIN, round(total_min))]
    other = max(MIN_SESSION_MIN, int(total_min * (1 - LONG_SESSION_SHARE) / (n_sessions - 1)))
    other = min(other, total_min // n_sessions) if total_min >= MIN_SESSION_MIN * n_sessions else other
    long = max(other, total_min - other * (n_sessions - 1))
    return [long] + [other] * (n_sessions - 1)


def _walk_run_label(run, walk, reps):
    if walk == 0:
        return f"Run {run:g} min continuously"
    if reps == 1 and run == 20:
        return "Run 20 min, walk 2 min, run 10 min"
    return f"{reps} x (run {run:g} min / walk {walk:g} min)"


def _walk_run_session_min(run, walk, reps):
    if reps == 1 and run == 20 and walk:
        running, walking = 30, 2
    else:
        running, walking = run * reps, walk * max(reps - 1, 0)
    total = running + walking + 2 * WARMUP_WALK_MIN
    return running, int(total) if total == int(total) else total


# ---- Public entry point -----------------------------------------

def generate_general_fitness_plan(start_date, continuous_run_min, current_weekly_run_min,
                                  weeks=DEFAULT_WEEKS, sessions_per_week=3,
                                  long_run_day=None, avoid_days=(),
                                  hr_zone2_low=None, hr_zone2_high=None,
                                  start_step=None, start_weekly_min=None):
    """Build a general-fitness block.

    start_date            ISO date of the Monday (or any day) the block starts;
                          week 1 is the 7 days from this date.
    continuous_run_min    longest the athlete can currently run without stopping.
    current_weekly_run_min roughly how many minutes they run per week now (0 is fine).
    sessions_per_week     3 by default; clamped to 2-3 until a 4th is earned.
    start_step / start_weekly_min
                          override the starting level when regenerating after a
                          week that wasn't completed comfortably -- pass the
                          CURRENT level so the athlete repeats it, never a higher one.

    Returns {"mode", "summary", "rows"} where rows map onto the
    program table (session_type, prescribed_duration_min, week_phase...).
    """
    start = date.fromisoformat(start_date)
    sessions_per_week = max(2, min(3, sessions_per_week))
    walk_run = continuous_run_min < CONTINUOUS_THRESHOLD_MIN and start_weekly_min is None
    rows, notes = [], []

    if walk_run:
        if start_step is None:
            # Start on the rung whose single running block they can already do,
            # then drop one rung for safety.
            fit = [i for i, (run, _, _) in enumerate(WALK_RUN_LADDER) if run <= continuous_run_min]
            start_step = max(0, (fit[-1] - 1) if fit else 0)
        schedule = _walk_run_rungs(weeks, start_step)
        run_days = choose_run_days(sessions_per_week, long_run_day, avoid_days,
                                   allow_back_to_back=False)
        for w, (rung, phase) in enumerate(schedule, start=1):
            run, walk, reps = WALK_RUN_LADDER[rung]
            running, total = _walk_run_session_min(run, walk, reps)
            for d in run_days:
                session_date = start + timedelta(days=7 * (w - 1) + (d - start.weekday()) % 7)
                rows.append(_row(w, session_date, "walk_run",
                                 f"WALK/RUN: {WARMUP_WALK_MIN} min brisk walk, "
                                 f"{_walk_run_label(run, walk, reps)}, "
                                 f"{WARMUP_WALK_MIN} min walk", total, phase,
                                 hr_zone2_low, hr_zone2_high))
        final_rung = schedule[-1][0]
        notes.append(f"Walk/run ladder, {sessions_per_week} sessions/week on non-consecutive "
                     f"days. Starts at rung {start_step + 1}/{len(WALK_RUN_LADDER)}, "
                     f"at most one rung per week, every {CUTBACK_EVERY}rd week repeats an "
                     f"easier rung.")
        if final_rung == len(WALK_RUN_LADDER) - 1:
            notes.append("Reaches 30 min continuous running: next block can switch to "
                         "continuous mode.")
        mode = "walk_run"
    else:
        if start_weekly_min is None:
            base = max(current_weekly_run_min * START_FACTOR, MIN_START_WEEKLY_MIN)
            # Never prescribe a session longer than they've shown they can run.
            start_weekly_min = min(base, MAX_WEEKLY_MIN,
                                   continuous_run_min * sessions_per_week * 1.2)
            start_weekly_min = max(start_weekly_min, MIN_START_WEEKLY_MIN)
        schedule = _continuous_minutes(weeks, start_weekly_min)
        for w, (total, phase) in enumerate(schedule, start=1):
            n = sessions_per_week
            if w >= FOURTH_SESSION_FROM_WEEK and total >= FOURTH_SESSION_MIN_WEEKLY:
                n = sessions_per_week + 1
            run_days = choose_run_days(n, long_run_day, avoid_days)
            lr_idx = DAY_NAMES.index(long_run_day) if long_run_day in DAY_NAMES \
                and DAY_NAMES.index(long_run_day) in run_days else run_days[-1]
            split = _split_week(total, n)
            others = iter(split[1:])
            for d in run_days:
                is_long = d == lr_idx
                minutes = split[0] if is_long else next(others)
                session_date = start + timedelta(days=7 * (w - 1) + (d - start.weekday()) % 7)
                rows.append(_row(w, session_date, "long" if is_long else "easy",
                                 f"{'LONGER EASY RUN' if is_long else 'EASY RUN'}: {minutes} min",
                                 minutes, phase, hr_zone2_low, hr_zone2_high))
        notes.append(f"Continuous easy running, {start_weekly_min:.0f} -> "
                     f"{max(m for m, _ in schedule)} min/week over {weeks} weeks "
                     f"(max +{WEEKLY_GROWTH_PCT:.0%}/week, capped at +{WEEKLY_GROWTH_CAP_MIN} min), "
                     f"cutback to {CUTBACK_FACTOR:.0%} every {CUTBACK_EVERY}rd week.")
        mode = "continuous"

    notes.append("All sessions conversational effort; no time trials, intervals or hill "
                 "repeats in a general-fitness block. Progress only after a week completed "
                 "comfortably and pain-free; otherwise repeat the week.")
    return {"mode": mode, "summary": " ".join(notes), "rows": rows}


def _row(week_num, session_date, session_type, label, minutes, phase, hr_low, hr_high):
    effort = EFFORT_DESC
    if hr_high:
        effort += f" HR ceiling {hr_high}."
    return {
        "week_num": week_num,
        "session_date": session_date.isoformat(),
        "day_of_week": DAY_NAMES[session_date.weekday()],
        "session_type": session_type,
        "session_label": label,
        "prescribed_distance_km": None,        # time-based on purpose
        "prescribed_duration_min": minutes,
        "prescribed_effort_desc": effort,
        "prescribed_hr_low": hr_low,
        "prescribed_hr_high": hr_high,
        "elevation_target_m": None,
        "week_phase": phase,
    }


if __name__ == "__main__":
    for label, kwargs in [
        ("Beginner (can run ~2 min)", dict(continuous_run_min=2, current_weekly_run_min=0)),
        ("Returning runner (30 min, ~90 min/week)",
         dict(continuous_run_min=30, current_weekly_run_min=90)),
    ]:
        plan = generate_general_fitness_plan("2026-10-05", long_run_day="Sun", **kwargs)
        print(f"\n=== {label}: {plan['mode']} ===\n{plan['summary']}")
        by_week = {}
        for r in plan["rows"]:
            by_week.setdefault(r["week_num"], []).append(r)
        for w, rs in by_week.items():
            total = sum(r["prescribed_duration_min"] for r in rs)
            print(f"  wk{w:>2} {rs[0]['week_phase']:<11} {total:>4.0f} min  "
                  + " | ".join(f"{r['day_of_week']} {r['session_label']}" for r in rs[:1])
                  + (f"  (+{len(rs) - 1} more)" if len(rs) > 1 else ""))
