"""
Hill work engine.

Determines whether a quality session should be replaced with hill work,
and prescribes the appropriate hill workout based on:
    - race_hilliness: "low" | "medium" | "high"
    - phase:          1 | 2 | 3 | 4

Replacement matrix (from spec):
    Phase I   → never replaced (strides already hill-like)
    Phase II  → medium: 50% sessions; high: 100%
    Phase III → medium: 50% sessions; high: 100%
    Phase IV  → no replacement, but high adds downhill/terrain to long run

"50% replacement" is implemented by alternating weeks:
    odd week numbers within the phase → hill workout
    even week numbers within the phase → standard flat workout
"""

from dataclasses import dataclass
from typing import Optional


# ── Replacement decision ──────────────────────────────────────────────────

def should_replace_with_hills(
    phase: int,
    hilliness: str,
    week_number_in_phase: int,  # 1-based within the current phase
) -> bool:
    """
    Return True if the Tuesday quality session should be a hill workout.
    """
    if hilliness == "low":
        return False
    if phase == 1 or phase == 4:
        return False
    if hilliness == "high":
        # Phase II and III: 100% replacement
        return phase in (2, 3)
    if hilliness == "medium":
        # Phase II and III: 50% replacement — alternate weeks
        if phase in (2, 3):
            return week_number_in_phase % 2 == 1
    return False


def week_number_in_phase(week_number: int, phases) -> int:
    """
    Return the 1-based week index within the athlete's current phase.
    Requires a PhaseAllocation object.
    """
    from coach_core.engine.phases import get_phase_for_week
    phase = get_phase_for_week(week_number, phases)
    if phase == 1:
        return week_number
    if phase == 2:
        return week_number - phases.phase_I
    if phase == 3:
        return week_number - phases.phase_I - phases.phase_II
    return week_number - phases.phase_I - phases.phase_II - phases.phase_III


# ── Hill workout prescriptions ────────────────────────────────────────────

def get_hill_quality_session(
    phase: int,
    hilliness: str,
    weekly_volume: float,
) -> dict:
    """
    Return a hill quality session dict matching the structure returned by
    get_quality_session() in workouts.py, so it can be used as a drop-in.
    """
    if phase == 2:
        return _short_hill_sprints(weekly_volume)
    if phase == 3:
        return _long_hill_repeats(weekly_volume)
    # Phase 4 quality stays flat — hills go into long run (handled separately)
    return _short_hill_sprints(weekly_volume)


def _short_hill_sprints(weekly_volume: float) -> dict:
    """
    Phase II replacement: short, steep (6–10% grade) all-out sprints.
    8–15 sec each. 8–12 repeats.
    """
    # Scale repeats loosely to volume (more volume = slightly more neuromuscular stimulus)
    reps = max(8, min(12, int(weekly_volume / 10)))
    return {
        "type": "Short Hill Sprints",
        "detail": (
            f"{reps} × 10–15 sec all-out uphill (6–10% grade) — "
            "walk/jog back down as full recovery"
        ),
        "warmup_km": 2.0,
        "cooldown_km": 1.5,
        "quality_km": round(reps * 0.05, 1),   # ~50 m per sprint
        "total_km": round(reps * 0.05 + 3.5, 1),
        "notes": (
            "Focus on power and form, not pace. Drive knees high, pump arms. "
            "Full recovery between each rep — these are neuromuscular, not aerobic."
        ),
    }


def _long_hill_repeats(weekly_volume: float) -> dict:
    """
    Phase III replacement: moderate hill (4–6% grade), 2–5 min at I-pace effort.
    4–8 repeats. Jog down recovery.
    """
    reps = max(4, min(8, int(weekly_volume / 15)))
    rep_duration_min = 3  # ~3 min uphill effort
    quality_km = round(reps * (rep_duration_min / 5.5), 1)   # approx at I-pace equiv.
    return {
        "type": "Long Hill Repeats",
        "detail": (
            f"{reps} × ~{rep_duration_min} min uphill (4–6% grade) @ I-pace effort — "
            "jog down as recovery (equal to rep time)"
        ),
        "warmup_km": 2.0,
        "cooldown_km": 1.0,
        "quality_km": quality_km,
        "total_km": round(quality_km + 3.0, 1),
        "notes": (
            "Effort matches interval pace — controlled aggression. "
            "Maintain form on the way down; downhill running is eccentric loading."
        ),
    }


# ── Downhill-specific additions (Phase III/IV, high hilliness only) ───────

def get_downhill_session(weekly_volume: float, is_taper: bool = False) -> dict:
    """
    Friday bonus session for high-hilliness races in Phase III and IV.
    Replaces the standard recovery run on one Friday per fortnight.
    Gentle downhill (2–4% grade), 400–800m at T-pace effort.
    """
    reps = 4 if is_taper else max(6, min(10, int(weekly_volume / 12)))
    rep_dist_m = 600
    km = round(reps * (rep_dist_m / 1000), 1)
    return {
        "session": "Downhill Repeats",
        "km": round(km + 2.0, 1),
        "notes": (
            f"WU 1 km easy | "
            f"{reps} × {rep_dist_m}m downhill (2–4% grade) @ T-pace effort — "
            "easy jog back up | CD 1 km easy. "
            "Purpose: eccentric quad strength + downhill mechanics. "
            "Keep effort controlled — this is resilience work, not a workout."
        ),
    }


def get_hilly_long_run_note(long_run_km: float, marathon_pace_str: str) -> str:
    """
    Annotated long run description for high-hilliness Phase III/IV.
    Replaces the flat long run note with terrain-specific guidance.
    """
    return (
        f"{long_run_km} km on hilly terrain — "
        "simulate race-day elevation profile. "
        "Run uphills at steady effort (not pace), "
        "run downhills controlled (protect quads). "
        f"Target avg pace around marathon pace ({marathon_pace_str}) on flat sections."
    )
