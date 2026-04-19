"""
Adaptation engine — closed-loop logic.

Runs after a week is complete. Takes actual run logs and returns:
  - adjusted volume for the next planned week
  - adjusted VO2X (if warranted)
  - list of human-readable coaching notes
"""
from coach_core.engine.training_profiles import get_profile
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class WeekSummary:
    planned_volume: float
    actual_volume: float
    avg_rpe: Optional[float]
    sessions_completed: int


def adapt_next_week(
    planned_next_volume: float,
    summary: WeekSummary,
    current_vo2x: float,
    training_profile: str = "conservative",
) -> tuple[float, float, list[str]]:
    """
    Returns (adjusted_volume, adjusted_vo2x, coaching_notes).

    Volume rules are governed by the athlete's training_profile:
      conservative: under=−15%, over=+1%, RPE9 cap=−15%
      aggressive:   under=−10%, over=+3%, RPE9 cap=−10%
    """
    profile    = get_profile(training_profile)
    compliance = summary.actual_volume / max(summary.planned_volume, 0.1)
    notes: list[str] = []
    vol_modifier = 1.0
    new_vo2x = current_vo2x

    # ── Volume adaptation ──────────────────────────────────────────────────
    under_penalty = profile["under_penalty"]     # e.g. 0.85 or 0.90
    over_boost    = profile["over_boost"]         # e.g. 1.01 or 1.03

    if compliance < 0.80:
        vol_modifier = under_penalty
        reduction_pct = round((1 - under_penalty) * 100)
        notes.append(f"⚠️ Only {compliance:.0%} of planned volume — next week reduced by {reduction_pct}%.")
    elif compliance < 0.90:
        vol_modifier = 0.95
        notes.append(f"📉 {compliance:.0%} compliance — slight volume reduction next week.")
    elif compliance > 1.05:
        vol_modifier = over_boost
        boost_pct = round((over_boost - 1) * 100)
        notes.append(f"💪 Ahead of plan ({compliance:.0%}) — {boost_pct}% progressive boost applied.")
    else:
        notes.append(f"✅ Good compliance ({compliance:.0%}) — volume on track.")

    # ── RPE adaptation ─────────────────────────────────────────────────────
    if summary.avg_rpe is not None:
        rpe9_cap  = profile["rpe9_cap"]
        rpe85_cap = profile["rpe85_cap"]
        if summary.avg_rpe >= 9.0:
            vol_modifier = min(vol_modifier, rpe9_cap)
            notes.append(f"🔴 Very high RPE ({summary.avg_rpe:.1f}) — volume and intensity reduced.")
        elif summary.avg_rpe >= 8.5:
            vol_modifier = min(vol_modifier, rpe85_cap)
            notes.append(f"🟠 High RPE ({summary.avg_rpe:.1f}) — volume trimmed, keep intensity low.")
        elif summary.avg_rpe <= 5.0 and compliance >= 0.95:
            new_vo2x = min(current_vo2x + 0.5, 85.0)
            notes.append(
                f"🟢 Low RPE + full compliance — VO2X nudged from {current_vo2x} → {new_vo2x}."
            )

    adjusted_volume = round(planned_next_volume * vol_modifier, 1)
    return adjusted_volume, new_vo2x, notes


def calculate_vo2x_from_race(race_distance_km: float, finish_time_minutes: float) -> float:
    """
    Calculate VO2X from a race performance using the Daniels formula.

    Reference: Daniels Running Formula — oxygen cost and %VO2max equations.
    Valid for finish times 3–240 minutes.
    """
    import math
    t = max(3.0, finish_time_minutes)
    v = (race_distance_km * 1000.0) / t  # velocity in m/min

    # Oxygen cost at velocity v (Daniels eq.)
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v

    # Fractional utilisation at time t
    pct_vo2max = (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t)
        + 0.2989558 * math.exp(-0.1932605 * t)
    )

    vo2x = vo2 / pct_vo2max
    return round(max(25.0, min(85.0, vo2x)), 1)


def vo2x_to_5k_minutes(vo2x: float) -> float:
    """Inverse of calculate_vo2x_from_race: given a VO2X, return the 5 km finish
    time (in minutes) that would produce that VO2X.  Uses binary search over
    the Daniels formula (valid range 3–240 min → VO2X 25–85).
    """
    target = float(vo2x)
    lo, hi = 10.0, 120.0          # 5 km in ~10 min (elite) to ~120 min (walker)
    for _ in range(60):           # 60 iterations → < 0.0001 min precision
        mid = (lo + hi) / 2.0
        if calculate_vo2x_from_race(5.0, mid) > target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 2)
