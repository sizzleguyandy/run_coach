"""Phase 1.b — base build to target peak.

Easy aerobic mileage only (no quality), growing from current volume toward the
target peak. Build model: +~10% per week with a recovery week (-20%) every 4th.
Must clear the 48 km gate to unlock the race block; keeps building toward
target_peak, then holds/consolidates until the block starts.

Strides are added to two easy days from the point base exceeds ~40 km/week.
"""

from __future__ import annotations

from .models import Session, Week
from . import vdot as vdot_mod

_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _run_days(training_days: int) -> list[str]:
    # Spread runs through the week with the long run on Saturday.
    presets = {
        3: ["Tue", "Thu", "Sat"],
        4: ["Tue", "Thu", "Sat", "Sun"],
        5: ["Mon", "Tue", "Thu", "Sat", "Sun"],
        6: ["Mon", "Tue", "Wed", "Thu", "Sat", "Sun"],
        7: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }
    return presets[max(3, min(7, training_days))]


def _volume_curve(start_km: float, target_peak: float, runway_weeks: int) -> list[float]:
    """Grow from start toward peak: +10%/wk, every 4th week a -20% cutback.

    Once peak is reached, hold there for any remaining runway (consolidation).
    """
    curve: list[float] = []
    cur = max(start_km, 10.0)
    for w in range(runway_weeks):
        if (w + 1) % 4 == 0:
            curve.append(round(cur * 0.8, 1))      # recovery week
            continue
        cur = min(cur * 1.10, target_peak)
        curve.append(round(cur, 1))
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

    weeks: list[Week] = []
    for w, vol in enumerate(curve):
        idx = start_index + w
        recovery = (w + 1) % 4 == 0
        n = len(run_days)
        # Long run ~ 30% of week, rest split evenly.
        long_km = round(vol * 0.30, 1)
        other = round((vol - long_km) / max(1, n - 1), 1)
        strides = vol > 40 and not recovery

        days: list[Session] = []
        for d in _DAY_ORDER:
            if d not in run_days:
                days.append(Session(day=d, kind="rest", description="Rest or cross-train"))
                continue
            if d == run_days[-1]:
                days.append(
                    Session(
                        day=d, kind="long",
                        description=f"Long easy run {long_km:.0f} km",
                        distance_km=long_km, pace_min_per_km=round(e_pace, 2),
                    )
                )
            else:
                extra = " + 6 strides" if strides and d == run_days[0] else ""
                days.append(
                    Session(
                        day=d, kind="easy",
                        description=f"Easy run {other:.0f} km{extra}",
                        distance_km=other, pace_min_per_km=round(e_pace, 2),
                        tags=["strides"] if extra else [],
                    )
                )

        note = ""
        if vol >= 48 and (w == 0 or curve[w - 1] < 48):
            note = "48 km gate cleared — race block now unlockable."
        if recovery:
            note = "Recovery week (-20%)."
        weeks.append(
            Week(
                index=idx, phase="1b",
                phase_label="Phase 1.b — Base Build",
                weeks_to_race=None,
                target_volume_km=vol,
                days=days, note=note,
            )
        )
    return weeks
