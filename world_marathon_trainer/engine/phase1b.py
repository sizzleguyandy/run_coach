"""Phase 1.b — base build to target peak.

Easy aerobic mileage only (no quality), growing from current volume toward the
target peak. Build model: +10%/wk with a -20% cutback every 4th week.

BUILD-LATER design:
    If the athlete has more runway than they need to reach their peak, the ramp
    does NOT start immediately. Instead:
      1. Aerobic maintenance — hold at a gentle maintenance level (current km or
         a modest bump) for the 'early_weeks' at the start of the runway.
      2. Build ramp — the last 'build_weeks' before the race block, grow from
         current → target_peak.
      3. Consolidation — hold at peak for ~3 weeks just before the block starts,
         so the athlete arrives at the block fresh and at their target volume.

    This avoids holding peak mileage for 10-16 weeks (no coach does that) and
    keeps load appropriate across the full base period.

Gate: must clear >= 48 km/week to unlock Phase 2. Flagged at assessment level.
Strides added to the first easy day once volume exceeds 40 km/week.
"""

from __future__ import annotations

import math

from .models import Session, Week
from . import vdot as vdot_mod

_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CONSOLIDATION_WEEKS = 3   # weeks at peak before the block starts


def _run_days(training_days: int) -> list[str]:
    presets = {
        3: ["Tue", "Thu", "Sat"],
        4: ["Tue", "Thu", "Sat", "Sun"],
        5: ["Mon", "Tue", "Thu", "Sat", "Sun"],
        6: ["Mon", "Tue", "Wed", "Thu", "Sat", "Sun"],
        7: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }
    return presets[max(3, min(7, training_days))]


def _weeks_to_reach_peak(start_km: float, target_km: float) -> int:
    """How many weeks does a +10%/wk build (with every-4th cutback) need?"""
    cur = max(start_km, 10.0)
    if cur >= target_km:
        return 0
    weeks = 0
    while cur < target_km and weeks < 200:
        weeks += 1
        if weeks % 4 == 0:
            # cutback week — volume drops but base doesn't change for the ramp
            continue
        cur = min(cur * 1.10, target_km)
    return weeks


def _build_ramp(start_km: float, target_km: float, n_weeks: int) -> list[float]:
    """n_weeks of +10%/wk build from start_km toward target_km.

    Every 4th week is a cutback (-20%). Remaining weeks after peak is reached
    consolidate at peak.
    """
    curve: list[float] = []
    cur = max(start_km, 10.0)
    for w in range(n_weeks):
        if (w + 1) % 4 == 0:
            curve.append(round(cur * 0.80, 1))
            continue
        cur = min(cur * 1.10, target_km)
        curve.append(round(cur, 1))
    return curve


def _volume_curve(
    start_km: float, target_peak: float, runway_weeks: int
) -> list[tuple[float, str]]:
    """Return [(weekly_km, sub_phase_label)] for all runway weeks.

    Sub-phases:
        'maintenance' — early easy weeks (holding, not building)
        'build'       — active ramp toward peak
        'consolidation' — holding at peak before the block
    """
    build_needed = _weeks_to_reach_peak(start_km, target_peak)
    consol = min(CONSOLIDATION_WEEKS, runway_weeks)
    build_weeks = min(build_needed, max(0, runway_weeks - consol))
    early_weeks = max(0, runway_weeks - build_weeks - consol)

    curve: list[tuple[float, str]] = []

    # --- 1. Aerobic maintenance ----------------------------------------- #
    if early_weeks > 0:
        # Sit at a small step above current volume — enough to keep fitness,
        # not so much that they peak too early.
        maint_km = round(min(start_km * 1.05, target_peak * 0.70), 1)
        maint_km = max(maint_km, start_km)
        for w in range(early_weeks):
            # Gentle 4-week rhythm in maintenance too (cutback every 4th).
            km = maint_km * 0.85 if (w + 1) % 4 == 0 else maint_km
            curve.append((round(km, 1), "maintenance"))

    # --- 2. Build ramp -------------------------------------------------- #
    ramp = _build_ramp(start_km, target_peak, build_weeks)
    for km in ramp:
        curve.append((km, "build"))

    # --- 3. Consolidation ----------------------------------------------- #
    for _ in range(consol):
        curve.append((round(target_peak, 1), "consolidation"))

    # Safety: trim or pad to exact runway_weeks.
    if len(curve) > runway_weeks:
        curve = curve[:runway_weeks]
    while len(curve) < runway_weeks:
        curve.append((round(target_peak, 1), "consolidation"))

    return curve


def build_phase1b(
    start_index: int,
    start_km: float,
    target_peak: float,
    runway_weeks: int,
    training_days: int,
    vdot: float,
) -> list[Week]:
    if runway_weeks <= 0:
        return []

    paces = vdot_mod.paces_from_vdot(vdot)
    e_pace = paces["E"]
    curve = _volume_curve(start_km, target_peak, runway_weeks)
    run_days = _run_days(training_days)
    prev_km = 0.0

    weeks: list[Week] = []
    for w, (vol, sub) in enumerate(curve):
        idx = start_index + w
        n = len(run_days)
        long_km = round(vol * 0.30, 1)
        other = round((vol - long_km) / max(1, n - 1), 1)
        strides = vol > 40 and sub != "maintenance"
        cutback = sub == "build" and (w + 1) % 4 == 0

        days: list[Session] = []
        for d in _DAY_ORDER:
            if d not in run_days:
                days.append(Session(day=d, kind="rest",
                                    description="Rest or light cross-train"))
                continue
            if d == run_days[-1]:
                days.append(Session(
                    day=d, kind="long",
                    description=f"Long easy run {long_km:.0f} km",
                    distance_km=long_km, pace_min_per_km=round(e_pace, 2),
                ))
            else:
                stride_note = " + 6 strides" if strides and d == run_days[0] else ""
                days.append(Session(
                    day=d, kind="easy",
                    description=f"Easy run {other:.0f} km{stride_note}",
                    distance_km=other, pace_min_per_km=round(e_pace, 2),
                    tags=["strides"] if stride_note else [],
                ))

        note = ""
        if sub == "maintenance":
            note = "Aerobic maintenance — hold volume, no intensity."
        elif cutback:
            note = "Cutback week (−20%) — recovery."
        elif sub == "consolidation":
            note = f"Consolidation at {target_peak:.0f} km peak before the race block."
        if vol >= 48 and prev_km < 48:
            note = ("48 km gate cleared. " + note).strip()

        weeks.append(Week(
            index=idx, phase="1b",
            phase_label="Phase 1.b — Base Build",
            weeks_to_race=None,
            target_volume_km=vol,
            days=days, note=note,
        ))
        prev_km = vol

    return weeks
