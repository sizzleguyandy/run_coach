"""
race_plan.py — deterministic program generator for goal_type='race'.

Implements the PROGRAM GENERATION RULES from references/coaching-rules.md as
code, so they are guaranteed rather than usually-followed:

  - Weekly volume grows at most 10% week over week (build weeks), with
    a cutback week (~80%) every 4th week. Any week where rounding or a
    minimum session length forces a bigger step is reported in
    `exceptions` so the agent can put it in program_revision.summary.
  - Once the base weekly structure is set, volume growth goes onto the
    long run first (capped at +2 km/week and a distance-specific peak);
    the other sessions only grow once the long run is capped.
  - Two time-trial checkpoints at ~25% and ~60% of the build, never in
    a cutback week.
  - If the course is hilly (>= 10 m of climb per km), quality sessions
    are hill-specific for the whole build, not once or twice.
  - NO race pace is prescribed. Effort is by HR zone / RPE. Race pace is
    set later, as a range, from the time trials AND long-run durability.
  - Taper: 1 week (5K/10K), 2 weeks (half), 3 weeks (marathon), the last
    of which is race week.

Zero dependencies. Usage:
    from race_plan import generate_race_plan
    plan = generate_race_plan(start_date="2026-10-05", race_date="2027-01-17",
                              distance_km=21.1, baseline_weekly_km=25,
                              longest_recent_km=10, elevation_gain_m=445)

    python3 race_plan.py            # prints a demo plan
"""
from datetime import date, timedelta

from analysis import weekly_volume_check
from general_fitness import DAY_NAMES, choose_run_days

# ---- Tunable guardrails -----------------------------------------

WEEKLY_GROWTH_PCT = 0.10
CUTBACK_EVERY = 4
CUTBACK_FACTOR = 0.80
LONG_RUN_MAX_STEP_KM = 2.0
LONG_RUN_MAX_SHARE = 0.50           # long run never more than half the week
LONG_RUN_START_SHARE = 0.45
CUTBACK_LONG_FACTOR = 0.75
MIN_BASE_WEEKLY_KM = 10             # below this, suggest a general-fitness block first
MIN_BASE_LONGEST_KM = 3
MIN_SESSION_KM = 3.0
HILLY_M_PER_KM = 10.0
PEAK_GROWTH_OVER_BASELINE = 1.8     # peak weekly volume <= 1.8x what they do now

# (max race distance, peak long run km, peak weekly km target, taper factors ending in race week)
DISTANCE_PROFILES = [
    (5.0,   10, 35, [0.60]),
    (10.0,  16, 45, [0.60]),
    (21.1,  19, 55, [0.75, 0.50]),
    (999.0, 32, 75, [0.75, 0.60, 0.40]),
]


def _profile(distance_km):
    for max_d, long_cap, weekly_target, taper in DISTANCE_PROFILES:
        if distance_km <= max_d + 0.05:
            return long_cap, weekly_target, taper
    return DISTANCE_PROFILES[-1][1:]


def _effort(kind, zones):
    z = zones or {}
    if kind in ("easy", "long"):
        text = "Easy, conversational (RPE 3-4/10)."
        if z.get("zone2_high"):
            text += f" HR cap {z['zone2_high']}."
        return text, None, z.get("zone2_high")
    if kind == "quality":
        text = "Hard efforts at RPE 7-8/10 (zone 4); recoveries fully easy."
        return text, z.get("zone4_low"), z.get("zone4_high")
    if kind == "time_trial":
        return "All-out but evenly paced effort for the timed section.", None, None
    return "Race.", None, None


def _round(km, whole):
    return float(round(km)) if whole else round(km, 1)


def generate_race_plan(start_date, race_date, distance_km, baseline_weekly_km,
                       longest_recent_km, elevation_gain_m=None, days_per_week=4,
                       long_run_day="Sun", avoid_days=(), whole_number_distances=False,
                       min_session_km=MIN_SESSION_KM, hr_zones=None):
    """Build a race program.

    hr_zones: optional {"zone2_high", "zone4_low", "zone4_high"} from athlete_profile.
    Returns {"ok", "summary", "rows", "exceptions", "time_trial_row_indexes"} or
    {"ok": False, "reason", "suggestion"} when the athlete isn't ready for a race
    build or there isn't enough time.
    """
    start = date.fromisoformat(start_date)
    race = date.fromisoformat(race_date)
    long_cap, weekly_target, taper = _profile(distance_km)
    days_per_week = max(3, min(6, days_per_week))

    if baseline_weekly_km < MIN_BASE_WEEKLY_KM or longest_recent_km < MIN_BASE_LONGEST_KM:
        return {"ok": False,
                "reason": (f"Current running ({baseline_weekly_km} km/week, longest "
                           f"{longest_recent_km} km) is below the minimum base for a race "
                           f"build ({MIN_BASE_WEEKLY_KM} km/week, {MIN_BASE_LONGEST_KM} km longest)."),
                "suggestion": ("Offer a general-fitness block first (goal_type='general_fitness'), "
                               "then a race build once they're running 10+ km/week. If the race "
                               "date doesn't allow that, tell the athlete plainly and let them "
                               "decide whether to walk/run the race or pick a later one.")}

    total_weeks = (race - start).days // 7 + 1
    build_weeks = total_weeks - len(taper)
    if race <= start or build_weeks < 2:
        return {"ok": False,
                "reason": f"Only {max(total_weeks, 0)} week(s) until race day; a {distance_km:g} km "
                          f"plan needs at least {len(taper) + 2} (build + {len(taper)}-week taper).",
                "suggestion": "Tell the athlete plainly; offer a short maintain-and-taper week or a later race."}

    hilly = bool(elevation_gain_m) and elevation_gain_m / distance_km >= HILLY_M_PER_KM
    peak_weekly = max(baseline_weekly_km,
                      min(weekly_target, baseline_weekly_km * PEAK_GROWTH_OVER_BASELINE))
    long_cap = min(long_cap, peak_weekly * LONG_RUN_MAX_SHARE)
    unit = 1.0 if whole_number_distances else 0.1

    def floor_u(km):
        return round(int(km / unit + 1e-9) * unit, 1)

    # ---- time trials: ~25% and ~60% of the build, never a cutback ----
    def _tt_week(frac):
        target = max(2, round(build_weeks * frac))
        for delta in (0, 1, -1, 2, -2):
            w = target + delta
            if 2 <= w <= build_weeks and w % CUTBACK_EVERY != 0:
                return w
        return None
    tt_weeks = sorted({w for w in (_tt_week(0.25), _tt_week(0.60)) if w})
    tt_km = 3.0 if distance_km <= 5.05 else 5.0
    tt_wu = 1.5

    rows, exceptions, tt_indexes = [], [], []
    # Growth is measured from the previous build week's ACTUAL (rounded)
    # total, so rounding can never sneak a bigger jump past the 10% cap.
    last_build_total = float(baseline_weekly_km)
    first_long = floor_u(min(max(longest_recent_km, min_session_km),
                             baseline_weekly_km * LONG_RUN_START_SHARE, long_cap))
    last_long = first_long
    peak_total, peak_long = last_build_total, last_long

    for w in range(1, total_weeks + 1):
        week_start = start + timedelta(days=7 * (w - 1))
        if w <= build_weeks:
            if w % CUTBACK_EVERY == 0:
                phase = "cutback"
                target = last_build_total * CUTBACK_FACTOR
                long_km = floor_u(last_long * CUTBACK_LONG_FACTOR)
            elif w == 1:
                phase, target, long_km = "rebuild", float(baseline_weekly_km), first_long
            else:
                phase = "build"
                target = min(last_build_total * (1 + WEEKLY_GROWTH_PCT), peak_weekly)
                long_km = floor_u(min(last_long + LONG_RUN_MAX_STEP_KM, long_cap,
                                      target * LONG_RUN_MAX_SHARE))
            long_km = max(long_km, min_session_km)
        else:
            i = w - build_weeks - 1
            phase = "race_week" if w == total_weeks else "taper"
            target = peak_total * taper[i]
            long_km = floor_u(peak_long * (0.7 if i == 0 else 0.5))

        week_rows = []
        if phase == "race_week":
            # Two short easy runs early in the week, then the race.
            days = [d for d in choose_run_days(min(days_per_week, 3), long_run_day, avoid_days)
                    if week_start + timedelta(days=(d - start.weekday()) % 7) < race - timedelta(days=1)]
            for k, d in enumerate(days[:2]):
                label = "EASY RUN + STRIDES (4 x 20 s relaxed)" if k == 0 else "EASY SHAKEOUT"
                week_rows.append(_row(w, week_start + timedelta(days=(d - start.weekday()) % 7),
                                      "easy", label, min_session_km, _effort("easy", hr_zones), phase))
            week_rows.append(_row(w, race, "race", "RACE DAY", distance_km,
                                  _effort("race", hr_zones), phase))
            rows.extend(sorted(week_rows, key=lambda r: r["session_date"]))
            continue

        # Decide the sessions, dropping easy days rather than prescribing
        # runs shorter than min_session_km.
        is_tt = w in tt_weeks
        fixed_tt = tt_km + 2 * tt_wu if is_tt else 0.0
        n = days_per_week
        while n > 2 and (target - long_km - fixed_tt) / (n - 1 - (1 if is_tt else 0) or 1) < min_session_km:
            n -= 1
        if n < days_per_week:
            exceptions.append(f"wk{w}: {n} sessions instead of {days_per_week} to keep every run "
                              f">= {min_session_km:g} km")
        run_days = choose_run_days(n, long_run_day, avoid_days)
        lr_idx = DAY_NAMES.index(long_run_day) if (long_run_day in DAY_NAMES and
                                                   DAY_NAMES.index(long_run_day) in run_days) else run_days[-1]
        q_idx = max((d for d in run_days if d != lr_idx),
                    key=lambda d: min((d - lr_idx) % 7, (lr_idx - d) % 7), default=None)
        has_quality = n >= 3 or is_tt

        flex_days = [d for d in run_days if d != lr_idx and not (is_tt and d == q_idx)]
        remainder = max(0.0, target - long_km - fixed_tt)
        per = floor_u(remainder / max(len(flex_days), 1))
        extra_units = int(round((floor_u(remainder) - per * len(flex_days)) / unit))
        if per < min_session_km:
            per, extra_units = min_session_km, 0
        flex_km = {}
        for k, d in enumerate(flex_days):
            flex_km[d] = round(per + (unit if k < extra_units else 0), 1)

        for d in run_days:
            session_date = week_start + timedelta(days=(d - start.weekday()) % 7)
            if d == lr_idx:
                week_rows.append(_row(w, session_date, "long", "LONG RUN", long_km,
                                      _effort("long", hr_zones), phase))
            elif is_tt and d == q_idx:
                tt_indexes.append(len(rows) + len(week_rows))
                week_rows.append(_row(w, session_date, "time_trial",
                                      f"TIME TRIAL: {tt_wu:g} km easy, {tt_km:g} km timed, {tt_wu:g} km easy",
                                      fixed_tt, _effort("time_trial", hr_zones), phase))
            elif d == q_idx and has_quality and phase in ("build", "taper"):
                if phase == "taper":
                    label = "SHARPENER: easy running with 4-6 x 1 min at goal effort, 2 min easy between"
                elif hilly:
                    label = ("HILL REPEATS: easy warm-up, 6-10 x 60-90 s uphill hard, "
                             "jog back down, easy cool-down")
                elif w % 2:
                    label = "TEMPO: easy warm-up, 15-25 min comfortably hard (zone 3-4), easy cool-down"
                else:
                    label = "INTERVALS: easy warm-up, 5-6 x 3 min hard (zone 4) / 2 min jog, easy cool-down"
                week_rows.append(_row(w, session_date, "quality", label, flex_km[d],
                                      _effort("quality", hr_zones), phase))
            else:
                label = ("EASY RUN + STRIDES (6 x 20 s relaxed)"
                         if d == q_idx and phase in ("rebuild", "cutback") else "EASY RUN")
                week_rows.append(_row(w, session_date, "easy", label, flex_km[d],
                                      _effort("easy", hr_zones), phase))
        rows.extend(sorted(week_rows, key=lambda r: r["session_date"]))

        total = round(sum(r["prescribed_distance_km"] for r in week_rows), 1)
        if phase == "build":
            chk = weekly_volume_check(total, last_build_total)
            if chk["flagged"]:
                exceptions.append(f"wk{w}: {last_build_total:g} -> {total:g} km "
                                  f"(+{chk['pct_change']:.0%}) forced by the minimum session length")
        if phase in ("build", "rebuild"):
            last_build_total, last_long = total, long_km
            peak_total, peak_long = max(peak_total, total), max(peak_long, long_km)

    summary = (f"{distance_km:g} km race on {race_date}: {build_weeks} build weeks + "
               f"{len(taper)}-week taper. Weekly volume {baseline_weekly_km:g} -> "
               f"{peak_total:g} km (max +10%/week, cutback every {CUTBACK_EVERY}th week); long run "
               f"{first_long:g} -> {peak_long:g} km. Time trials in week(s) "
               f"{', '.join(map(str, tt_weeks)) or 'none'}. "
               f"{'Hilly course: quality sessions are hill repeats. ' if hilly else ''}"
               f"No race pace set yet -- set it as a range after the first time trial, "
               f"checked against long-run HR drift.")
    if peak_long < distance_km * 0.75 and distance_km > 10:
        summary += (f" Note: peak long run {peak_long:g} km is well short of race distance "
                    f"because of the time/base available; frame the goal as finishing strong, "
                    f"not a time.")
    if exceptions:
        summary += " Exceptions: " + "; ".join(exceptions) + "."
    return {"ok": True, "summary": summary, "rows": rows, "exceptions": exceptions,
            "time_trial_row_indexes": tt_indexes}


def _row(week_num, session_date, session_type, label, km, effort, phase):
    text, hr_low, hr_high = effort
    return {
        "week_num": week_num,
        "session_date": session_date.isoformat(),
        "day_of_week": DAY_NAMES[session_date.weekday()],
        "session_type": session_type,
        "session_label": label,
        "prescribed_distance_km": km,
        "prescribed_duration_min": None,
        "prescribed_effort_desc": text,
        "prescribed_hr_low": hr_low,
        "prescribed_hr_high": hr_high,
        "elevation_target_m": None,
        "week_phase": phase,
    }


if __name__ == "__main__":
    plan = generate_race_plan("2026-10-05", "2027-01-17", 21.1, 25, 10,
                              elevation_gain_m=445, whole_number_distances=True)
    print(plan["summary"])
    by_week = {}
    for r in plan["rows"]:
        by_week.setdefault(r["week_num"], []).append(r)
    for w, rs in by_week.items():
        print(f"  wk{w:>2} {rs[0]['week_phase']:<9} {sum(r['prescribed_distance_km'] for r in rs):>5.1f} km  "
              + " | ".join(f"{r['day_of_week']} {r['session_type']} {r['prescribed_distance_km']:g}" for r in rs))
