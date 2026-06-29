"""Onboarding — derive an athlete's starting profile from their Strava history.

'Strava is truth' applies to onboarding too: rather than self-reported (and
usually optimistic) numbers, read the last ~8 weeks of runs and MEASURE the
athlete's current volume, longest run, 10K-readiness and implied fitness.

Pure logic — accepts a list of activity dicts (the sync payload shape) and
returns a DerivedProfile. The API maps this into an athlete record + assessment.

What this derives:   current_weekly_km, longest run, can_run_10k, observed
                     training days, implied VDOT, confidence.
What it can't:        the race/date (future intent) and the days the athlete can
                     COMMIT to (history shows habit, not future availability).
"""

from __future__ import annotations

from datetime import datetime, date
from math import ceil

from .models import DerivedProfile
from . import vdot as vdot_mod

MIN_EFFORT_KM = 5.0     # min distance for a run to imply a VDOT


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def derive_profile(activities: list[dict]) -> DerivedProfile:
    """Derive a starting profile from recent activities (runs only)."""
    runs = [
        a for a in activities
        if (a.get("activity_type", "Run") or "Run").lower().endswith("run")
        and (a.get("distance_km") or 0) > 0
    ]

    if not runs:
        return DerivedProfile(
            weeks_of_data=0, n_runs=0, weekly_km=0.0,
            longest_run_km=0.0, longest_run_min=0.0,
            can_run_10k=False, observed_training_days=0,
            implied_vdot=None, confidence="none",
            notes=["No running history found — routing to the beginner "
                   "(couch-to-10K) path. We'll confirm a few things instead."],
        )

    # --- window span (first → last run), clamped to >= 1 week --------- #
    dates = [d for d in (_as_date(a.get("start_date")) for a in runs) if d]
    if dates:
        span_days = (max(dates) - min(dates)).days
        weeks_of_data = max(1, ceil((span_days + 1) / 7))
    else:
        weeks_of_data = 1

    total_km = sum(a.get("distance_km", 0.0) for a in runs)
    weekly_km = round(total_km / weeks_of_data, 1)

    longest_run_km = round(max(a.get("distance_km", 0.0) for a in runs), 1)
    longest_run_min = round(max(a.get("moving_time_min", 0.0) or 0.0 for a in runs), 1)
    can_run_10k = longest_run_km >= 10.0

    observed_training_days = max(1, min(7, round(len(runs) / weeks_of_data)))

    # --- implied fitness: best recent sustained effort (Daniels rule) --- #
    implied = []
    for a in runs:
        dist = a.get("distance_km", 0.0)
        t = a.get("moving_time_min", 0.0) or 0.0
        if dist >= MIN_EFFORT_KM and t > 0:
            try:
                implied.append(vdot_mod.vdot_from_race(dist, t))
            except ValueError:
                continue
    implied_vdot = round(max(implied), 1) if implied else None

    # --- confidence ---------------------------------------------------- #
    n = len(runs)
    if weeks_of_data >= 6 and n >= 12:
        confidence = "high"
    elif weeks_of_data >= 3 and n >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    notes: list[str] = []
    notes.append(
        f"Read {n} runs over ~{weeks_of_data} weeks: {weekly_km} km/week average, "
        f"longest {longest_run_km} km, ~{observed_training_days} days/week."
    )
    if implied_vdot is None:
        notes.append(
            "No sustained hard effort in the window — starting fitness is a "
            "conservative estimate; it'll sharpen as soon as you race a session."
        )
    if confidence == "low":
        notes.append(
            "Limited history — treat these numbers as provisional and confirm "
            "with the athlete."
        )

    return DerivedProfile(
        weeks_of_data=weeks_of_data, n_runs=n, weekly_km=weekly_km,
        longest_run_km=longest_run_km, longest_run_min=longest_run_min,
        can_run_10k=can_run_10k, observed_training_days=observed_training_days,
        implied_vdot=implied_vdot, confidence=confidence, notes=notes,
    )
