"""Closed-loop weekly adaptation (pure logic).

Compares one completed plan week against what the athlete actually did (Strava
truth) and decides a bounded VDOT nudge plus coaching notes / flags. Applies
nothing itself — the caller (store) persists the result. Plans are live-computed,
so the next build picks up the new VDOT automatically.

Adaptation philosophy:
  * Raise VDOT only on EVIDENCE (a hard recent effort implies more fitness).
  * Lower VDOT only when STRUGGLING (compliance poor).
  * Never raise during taper.
  * Small bounded steps, floored — no runaway.
"""

from __future__ import annotations

from statistics import mean
from typing import Optional

from .models import AdaptationDecision
from . import vdot as vdot_mod

VDOT_FLOOR = 30.0
VDOT_UP_STEP_CAP = 1.0       # max upward nudge per evaluation
VDOT_DOWN_STEP = 0.3
MIN_EFFORT_KM = 5.0          # min distance for an activity to imply a VDOT


def _planned_long_km(planned_week) -> float:
    """Best estimate of the week's long-run distance."""
    longs = [s.distance_km for s in planned_week.days
             if s.kind == "long" and s.distance_km]
    if longs:
        return max(longs)
    # Block long runs are duration-based; approximate from weekly volume.
    return round(planned_week.target_volume_km * 0.30, 1)


def _best_recent_vdot(recent_runs) -> float:
    """Highest VDOT implied by any sustained recent run (Daniels' rule)."""
    candidates = []
    for a in recent_runs:
        if a.distance_km and a.distance_km >= MIN_EFFORT_KM and a.moving_time_min:
            try:
                candidates.append(
                    vdot_mod.vdot_from_race(a.distance_km, a.moving_time_min)
                )
            except ValueError:
                continue
    return round(max(candidates), 1) if candidates else 0.0


def evaluate_week(
    planned_week,
    week_runs: list,
    recent_runs: list,
    current_vdot: float,
    in_taper: bool,
) -> AdaptationDecision:
    """Evaluate a completed week. `*_runs` are Activity-like objects.

    week_runs   — activities inside the completed week
    recent_runs — activities in the last ~4 weeks (for the fitness signal)
    """
    planned_km = planned_week.target_volume_km
    actual_km = round(sum(a.distance_km for a in week_runs), 1)
    volume_compliance = round(min(actual_km / planned_km, 2.0), 3) if planned_km else 0.0

    planned_sessions = sum(
        1 for s in planned_week.days if s.kind not in ("rest",)
    )
    completed_sessions = len(week_runs)
    session_compliance = (
        round(min(completed_sessions / planned_sessions, 2.0), 3)
        if planned_sessions else 0.0
    )

    planned_long = _planned_long_km(planned_week)
    actual_long = round(max((a.distance_km for a in week_runs), default=0.0), 1)
    long_run_done = actual_long >= 0.85 * planned_long if planned_long else True

    hrs = [a.average_heartrate for a in week_runs if a.average_heartrate]
    suffers = [a.suffer_score for a in week_runs if a.suffer_score]
    avg_hr = round(mean(hrs), 1) if hrs else None
    avg_suffer = round(mean(suffers), 1) if suffers else None

    best_vdot = _best_recent_vdot(recent_runs)

    # ---- decide the VDOT nudge ---------------------------------------- #
    new_vdot = current_vdot
    notes: list[str] = []
    flags: list[str] = []

    if not in_taper and best_vdot > current_vdot + 0.3:
        step = min(VDOT_UP_STEP_CAP, round((best_vdot - current_vdot) * 0.5, 1))
        new_vdot = round(current_vdot + step, 1)
        notes.append(
            f"A recent effort implies VDOT ~{best_vdot}. Raising your fitness "
            f"index {current_vdot} -> {new_vdot} (paces get a touch quicker)."
        )
    elif volume_compliance < 0.65 or session_compliance < 0.60:
        new_vdot = round(max(current_vdot - VDOT_DOWN_STEP, VDOT_FLOOR), 1)
        notes.append(
            f"You completed {volume_compliance * 100:.0f}% of planned volume. "
            f"Easing the load and paces (VDOT {current_vdot} -> {new_vdot})."
        )
    else:
        if in_taper:
            notes.append("Taper week — holding fitness steady, staying fresh.")
        else:
            notes.append(
                f"On track ({volume_compliance * 100:.0f}% of volume). "
                f"Holding paces at VDOT {current_vdot}."
            )

    # ---- flags (coaching signals, no parameter change) ---------------- #
    if not long_run_done and planned_long > 0:
        flags.append(
            f"Long run short ({actual_long:.0f} vs {planned_long:.0f} km planned) "
            f"- it's the key marathon session; prioritise it next week."
        )
    if volume_compliance < 0.40:
        flags.append(
            "Large volume drop - possible illness, injury, or travel. "
            "Consider a deliberate recovery reset rather than chasing the miss."
        )
    if volume_compliance > 1.35:
        flags.append(
            "You ran well above plan. Guard against too much, too soon - "
            "the plan's progression is deliberate."
        )

    return AdaptationDecision(
        week_index=planned_week.index,
        weeks_to_race=planned_week.weeks_to_race,
        phase=planned_week.phase,
        planned_km=planned_km,
        actual_km=actual_km,
        volume_compliance=volume_compliance,
        planned_sessions=planned_sessions,
        completed_sessions=completed_sessions,
        session_compliance=session_compliance,
        planned_long_km=planned_long,
        actual_long_km=actual_long,
        long_run_done=long_run_done,
        avg_heartrate=avg_hr,
        avg_suffer=avg_suffer,
        best_recent_vdot=best_vdot,
        vdot_before=current_vdot,
        vdot_after=new_vdot,
        notes=notes,
        flags=flags,
    )
