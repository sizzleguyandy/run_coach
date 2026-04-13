"""
C25K Engine — Couch to 5K program.

Completely separate from the phase-based system. No VDOT, no volume curves,
no phases. Rejoins the main system only at the transition point (week 12 → full plan).

Schedule: 12 weeks × 3 sessions/week (Mon/Wed/Fri).
Pacing: conversational effort only — no pace zones prescribed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# ── Static C25K schedule ──────────────────────────────────────────────────
# Each entry represents one week. Sessions are identical across Mon/Wed/Fri.
# Keys:
#   runs:       list of run segment durations in minutes
#   walks:      list of walk segment durations in minutes (len = len(runs) or len(runs)-1)
#   continuous: single continuous run duration in minutes (replaces runs/walks)
#   strides:    number of 20-sec strides to add after the run (weeks 11–12)
#   time_trial: True if a 5k time trial is offered this week

C25K_SCHEDULE: list[dict] = [
    # Week 1: 6 × (1 min run / 2 min walk)
    {"runs": [1]*6,   "walks": [2]*6,  "total_minutes": 20},
    # Week 2: 6 × (1.5 min run / 2 min walk)
    {"runs": [1.5]*6, "walks": [2]*6,  "total_minutes": 20},
    # Week 3: 6 × (2 min run / 2 min walk)
    {"runs": [2]*6,   "walks": [2]*6,  "total_minutes": 25},
    # Week 4: 5 × (3 min run / 2 min walk)
    {"runs": [3]*5,   "walks": [2]*5,  "total_minutes": 25},
    # Week 5: 4 × (4 min run / 2 min walk)
    {"runs": [4]*4,   "walks": [2]*4,  "total_minutes": 25},
    # Week 6: 4 × (5 min run / 2 min walk)
    {"runs": [5]*4,   "walks": [2]*4,  "total_minutes": 25},
    # Week 7: 3 × (8 min run / 2 min walk)
    {"runs": [8]*3,   "walks": [2]*3,  "total_minutes": 30},
    # Week 8: 10 / 2 / 10 / 2 / 5
    {"runs": [10, 10, 5], "walks": [2, 2], "total_minutes": 30},
    # Week 9: 30 min continuous
    {"continuous": 30, "total_minutes": 30},
    # Week 10: 30 min continuous
    {"continuous": 30, "total_minutes": 30},
    # Week 11: 30 min continuous + 4 strides
    {"continuous": 30, "strides": 4, "total_minutes": 30},
    # Week 12: 30 min continuous + 6 strides + optional 5k time trial
    {"continuous": 30, "strides": 6, "time_trial": True, "total_minutes": 30},
]

TOTAL_WEEKS = len(C25K_SCHEDULE)   # 12

# Estimated speed for a beginner (used only for estimated distance)
BEGINNER_PACE_MIN_PER_KM = 8.0   # ~8 min/km conversational jog


def get_week_schedule(week_number: int) -> dict:
    """Return the schedule dict for a 1-based week number."""
    idx = max(0, min(week_number - 1, TOTAL_WEEKS - 1))
    return C25K_SCHEDULE[idx]


def estimate_distance_km(total_run_minutes: float) -> float:
    """Estimate run distance from total running time at beginner pace."""
    return round(total_run_minutes / BEGINNER_PACE_MIN_PER_KM, 1)


def build_c25k_week(week_number: int, weather_factor: float = 1.0) -> dict:
    """
    Build the full 7-day session dict for a C25K week.
    Sessions on Mon / Wed / Fri.
    Tue / Thu / Sat = rest or cross-train. Sun = rest.

    weather_factor: TRUEPACE adjustment (1.0 = no change).
    When factor > 1.05, a heat/humidity note is appended to each run session.
    C25K runs are time-based so pace is not adjusted — effort guidance is given instead.
    """
    sched = get_week_schedule(week_number)
    session_desc = _format_session(sched, week_number)
    total_run_min = _total_run_minutes(sched)
    est_km = estimate_distance_km(total_run_min)

    # Append weather note to run sessions if conditions are challenging
    if weather_factor > 1.05:
        adj_pct = round((weather_factor - 1.0) * 100, 1)
        if weather_factor > 1.10:
            heat_note = (
                f"🔴 High heat/humidity (+{adj_pct}% effort) — run at an effort that feels "
                "very easy. Shorten the session if needed and hydrate well."
            )
        else:
            heat_note = (
                f"🌡️ Warm conditions (+{adj_pct}% effort) — keep effort conversational. "
                "Slow down more than usual; focus on time on feet, not pace."
            )
        session_desc = session_desc + " | " + heat_note

    return {
        "week_number": week_number,
        "plan_type": "c25k",
        "total_minutes": sched.get("total_minutes", 30),
        "total_run_minutes_per_session": round(total_run_min, 1),
        "estimated_km_per_session": est_km,
        "weather_factor": weather_factor,
        "days": {
            "Mon": {"session": "Run/Walk",    "km": est_km, "notes": session_desc},
            "Tue": {"session": "Rest / Walk", "km": 0,      "notes": "Active recovery — gentle walk or full rest"},
            "Wed": {"session": "Run/Walk",    "km": est_km, "notes": session_desc},
            "Thu": {"session": "Rest / Walk", "km": 0,      "notes": "Active recovery — gentle walk or full rest"},
            "Fri": {"session": "Run/Walk",    "km": est_km, "notes": session_desc},
            "Sat": {"session": "Cross-Train", "km": 0,      "notes": "Optional: 20–30 min walk, bike, or swim"},
            "Sun": {"session": "Rest",        "km": 0,      "notes": "Full rest"},
        },
    }


def _total_run_minutes(sched: dict) -> float:
    if "continuous" in sched:
        return float(sched["continuous"])
    return sum(sched.get("runs", []))


def _format_session(sched: dict, week_number: int) -> str:
    """Generate human-readable session description."""
    lines = []

    if "continuous" in sched:
        lines.append(f"Run {sched['continuous']} minutes continuously at conversational pace")
    else:
        runs = sched["runs"]
        walks = sched["walks"]
        if len(set(runs)) == 1 and len(set(walks)) == 1:
            # All intervals identical — compact format
            reps = len(runs)
            r, w = runs[0], walks[0]
            lines.append(
                f"{reps} × ({_fmt(r)} run / {_fmt(w)} walk) — conversational pace, walk briskly"
            )
        else:
            # Mixed intervals
            segments = []
            for i, r in enumerate(runs):
                segments.append(f"{_fmt(r)} run")
                if i < len(walks):
                    segments.append(f"{_fmt(walks[i])} walk")
            lines.append(" → ".join(segments) + " — conversational pace")

    if sched.get("strides"):
        n = sched["strides"]
        lines.append(f"After run: {n} × 20-sec strides (faster but relaxed effort, full recovery between)")

    if sched.get("time_trial"):
        lines.append(
            "💡 Optional: run a 5K time trial today to unlock your full training plan — "
            "or sign up for a parkrun this Saturday and race it for real!"
        )

    return " | ".join(lines)


def _fmt(minutes: float) -> str:
    """Format a minute value: 1.5 → '1m 30s', 2 → '2 min'"""
    if minutes == int(minutes):
        return f"{int(minutes)} min"
    m = int(minutes)
    s = round((minutes - m) * 60)
    return f"{m}m {s}s"


# ── C25K adaptation ───────────────────────────────────────────────────────

def adapt_c25k_week(
    current_week: int,
    planned_run_minutes: float,
    actual_run_minutes: float,
) -> tuple[int, list[str]]:
    """
    Returns (next_week_number, coaching_notes).

    If compliance >= 80%: progress to next week.
    If compliance < 80%: repeat the same week (do not advance).
    If compliance < 60%: drop back one week.

    Week is capped at TOTAL_WEEKS (12).
    """
    compliance = actual_run_minutes / max(planned_run_minutes, 0.1)
    notes: list[str] = []

    if compliance >= 0.80:
        next_week = min(current_week + 1, TOTAL_WEEKS)
        notes.append(f"✅ Great work — advancing to Week {next_week}.")
    elif compliance >= 0.60:
        next_week = current_week
        notes.append(
            f"⚠️ {compliance:.0%} of sessions completed — repeating Week {current_week}. "
            "That's completely fine; consistency beats speed."
        )
    else:
        next_week = max(1, current_week - 1)
        notes.append(
            f"🔄 Tough week ({compliance:.0%} complete) — stepping back to Week {next_week} "
            "to rebuild confidence. No shame in this."
        )

    return next_week, notes


# ── C25K → Full plan transition ───────────────────────────────────────────

def compute_transition(
    time_trial_5k_minutes: Optional[float],
    week11_avg_km: Optional[float],
) -> dict:
    """
    Compute VDOT and estimated_weekly_mileage for transition to full plan.

    If a 5k time trial was logged: use the Daniels formula.
    If no time trial: estimate from 30-min continuous run distance in week 11/12.
    """
    from coach_core.engine.adaptation import calculate_vdot_from_race

    if time_trial_5k_minutes:
        vdot = calculate_vdot_from_race(5.0, time_trial_5k_minutes)
        source = "5k time trial"
    elif week11_avg_km:
        # Extrapolate: if athlete ran week11_avg_km in 30 min, how long for 5k?
        pace_min_per_km = 30.0 / week11_avg_km
        estimated_5k_time = pace_min_per_km * 5.0
        vdot = calculate_vdot_from_race(5.0, estimated_5k_time)
        source = f"estimated from {week11_avg_km}km/30min pace"
    else:
        # Fallback: assign a conservative beginner VDOT
        vdot = 32.0
        source = "beginner default"

    # Estimated weekly mileage: 3 sessions × est_km + some walking
    est_session_km = estimate_distance_km(30.0)
    estimated_weekly_km = round(est_session_km * 3 * 0.85, 1)  # conservative

    return {
        "vdot": round(vdot, 1),
        "estimated_weekly_km": estimated_weekly_km,
        "source": source,
        "message": (
            f"🎉 C25K complete! Your VDOT is estimated at {round(vdot, 1)} "
            f"(based on {source}).\n\n"
            "You can run a 5K — now let's race one. 🏃\n"
            "Thousands of South Africans do parkrun every Saturday morning — "
            "free, timed, and friendly. Find your nearest one at parkrun.co.za "
            "and use it as your first official 5K result.\n\n"
            "Ready to start your first real training plan?"
        ),
    }
