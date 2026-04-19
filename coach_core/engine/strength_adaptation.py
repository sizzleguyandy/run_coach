"""
Strength adaptation engine — v1.6

Two responsibilities:

1. Running intensity block
   After a strength session is logged, decide how long to block non-Easy running:
     - First 30 days of plan  → 36 hours always
     - Weights unchanged       → 24 hours
     - Weights increased       → 48 hours
   Written to athletes.strength_load_expires_at (DateTime).

2. VO2X pace-gap check
   Daily: for each full-plan athlete, check whether 70%+ of runs in the last
   14 days were slower than prescribed pace. If so, apply −0.5 VO2X.
   Cooldown: 14 days after any adjustment.
"""

from datetime import datetime, date, timedelta
from typing import Optional
import logging

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

FIRST_PLAN_DAYS       = 30      # days from start_date that use the 36h window
BLOCK_FIRST_MONTH_H   = 36      # recovery hours during first 30 days
BLOCK_SAME_WEIGHT_H   = 24      # recovery hours when volume unchanged
BLOCK_WEIGHT_UP_H     = 48      # recovery hours when volume increased

PACE_GAP_WINDOW_DAYS  = 14      # rolling window for pace-gap check
PACE_GAP_THRESHOLD    = 0.70    # fraction of sessions below pace to trigger
PACE_GAP_TOLERANCE    = 0.05    # 5% slower than prescribed = "below pace"
PACE_GAP_MIN_SESSIONS = 5       # minimum sessions in window to run check
PACE_GAP_VO2X_DROP    = 0.5     # VO2X reduction per adjustment
PACE_GAP_COOLDOWN_DAYS = 14     # days before re-evaluating same athlete


# ── 1. Running intensity block ─────────────────────────────────────────────

def compute_strength_block_hours(
    new_volume: float,
    last_volume: Optional[float],
    plan_start_date: date,
    log_date: date,
) -> int:
    """
    Return the number of hours to block non-Easy running after a strength session.

    Args:
        new_volume:       total_volume_load of the session just logged
        last_volume:      athletes.strength_last_volume (None = first ever session)
        plan_start_date:  athletes.start_date
        log_date:         date of the strength session

    Returns:
        Block duration in hours (24, 36, or 48)
    """
    days_into_plan = (log_date - plan_start_date).days

    # First 30 days → always 36h regardless of weight change
    if days_into_plan < FIRST_PLAN_DAYS:
        return BLOCK_FIRST_MONTH_H

    # No previous volume → treat as same weight (first session after month 1)
    if last_volume is None or last_volume <= 0:
        return BLOCK_SAME_WEIGHT_H

    # Weights increased → 48h
    if new_volume > last_volume:
        return BLOCK_WEIGHT_UP_H

    # Weights unchanged or decreased → 24h
    return BLOCK_SAME_WEIGHT_H


def is_running_blocked(strength_load_expires_at: Optional[datetime]) -> bool:
    """
    Returns True if the running intensity block is currently active.
    Thread-safe — compares against UTC now.
    """
    if not strength_load_expires_at:
        return False
    return datetime.utcnow() < strength_load_expires_at


def block_expires_in_hours(strength_load_expires_at: Optional[datetime]) -> float:
    """
    Returns hours remaining on the block, or 0 if not active.
    """
    if not is_running_blocked(strength_load_expires_at):
        return 0.0
    delta = strength_load_expires_at - datetime.utcnow()
    return round(delta.total_seconds() / 3600, 1)


# ── 2. VO2X pace-gap check ────────────────────────────────────────────────

def _pace_is_below(actual_pace: float, prescribed_pace: float, tolerance: float = PACE_GAP_TOLERANCE) -> bool:
    """
    Returns True if actual_pace is slower than prescribed by more than tolerance.
    Pace is in min/km — higher = slower.
    """
    return actual_pace > prescribed_pace * (1 + tolerance)


def check_pace_gap(runs: list[dict]) -> dict:
    """
    Analyse a list of run log dicts for the pace-gap pattern.

    Each dict must have:
        actual_distance_km: float
        duration_minutes:   float
        prescribed_pace_min_per_km: float | None

    Returns:
        {
          "eligible":         bool,   # enough data to evaluate
          "sessions_checked": int,
          "below_pace_count": int,
          "below_pace_pct":   float,
          "trigger":          bool,   # True → apply VO2X drop
        }
    """
    # Filter to runs with both pace fields populated
    eligible = [
        r for r in runs
        if r.get("prescribed_pace_min_per_km")
        and r.get("duration_minutes", 0) > 0
        and r.get("actual_distance_km", 0) > 0
    ]

    if len(eligible) < PACE_GAP_MIN_SESSIONS:
        return {
            "eligible": False,
            "sessions_checked": len(eligible),
            "below_pace_count": 0,
            "below_pace_pct": 0.0,
            "trigger": False,
        }

    below = 0
    for r in eligible:
        actual_pace = r["duration_minutes"] / r["actual_distance_km"]
        if _pace_is_below(actual_pace, r["prescribed_pace_min_per_km"]):
            below += 1

    pct = below / len(eligible)
    return {
        "eligible": True,
        "sessions_checked": len(eligible),
        "below_pace_count": below,
        "below_pace_pct": round(pct, 3),
        "trigger": pct >= PACE_GAP_THRESHOLD,
    }


def pace_gap_cooldown_active(cooldown_until: Optional[date], check_date: date) -> bool:
    """Returns True if the athlete is within the cooldown window."""
    if not cooldown_until:
        return False
    return check_date <= cooldown_until


def pace_gap_bot_message(old_vo2x: float, new_vo2x: float) -> str:
    """Human-readable message sent to athlete after a pace-adjusted VO2X drop."""
    return (
        f"Your recent runs suggest your current paces may be a little high. "
        f"I've adjusted your VO2X from {old_vo2x} → {new_vo2x} — "
        f"your new paces will feel more manageable. "
        f"Keep showing up consistently and it'll come back up."
    )
