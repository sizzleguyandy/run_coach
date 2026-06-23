"""Slot-in and peak selection.

Decides:
  1. Where the athlete enters (Phase 1.a / 1.b / 2) from current fitness + time.
  2. Their target peak weekly mileage = min(runway ceiling, days ceiling),
     floored at the 48 km gate.

Anchor: Phase 2 always begins at race_date - 18 weeks ("the block").
Everything before the block is Phase 1 base building.
"""

from __future__ import annotations

from datetime import timedelta

from .models import AthleteInput, Assessment
from . import vdot as vdot_mod

GATE_KM = 48.0            # minimum weekly base to enter the race block
BLOCK_WEEKS = 18         # length of the Phase 2-5 race block

# Runway (weeks of base-building available) -> achievable peak ceiling (km).
# More calendar time before the block = higher peak you can safely build to.
_RUNWAY_PEAK = [
    (8, 48.0),    # < 8 weeks  -> only reach the gate
    (16, 80.0),   # 8-16       -> Cat 2 territory
    (28, 113.0),  # 16-28      -> Cat 3
    (999, 137.0),  # 28+        -> Cat 4
]

# Training days/week -> safe weekly ceiling (km). Life caps mileage regardless
# of how much calendar time exists.
_DAYS_PEAK = {
    3: 48.0,
    4: 64.0,
    5: 89.0,
    6: 113.0,
    7: 137.0,
}

# Peak km -> Daniels mileage category (selects the Phase 2-5 table flavour).
_CATEGORIES = [
    (64.0, "up_to_64"),
    (89.0, "km_66_89"),
    (113.0, "km_90_113"),
    (999.0, "km_114_137"),
]


def _runway_peak(runway_weeks: int) -> float:
    for limit, peak in _RUNWAY_PEAK:
        if runway_weeks < limit:
            return peak
    return _RUNWAY_PEAK[-1][1]


def _days_peak(days: int) -> float:
    days = max(3, min(7, days))
    return _DAYS_PEAK[days]


def category_for(peak_km: float) -> str:
    for limit, name in _CATEGORIES:
        if peak_km <= limit:
            return name
    return _CATEGORIES[-1][1]


def weeks_between(d_from, d_to) -> int:
    return (d_to - d_from).days // 7


def assess(athlete: AthleteInput) -> Assessment:
    block_start = athlete.race_date - timedelta(weeks=BLOCK_WEEKS)
    runway_weeks = max(0, weeks_between(athlete.today, block_start))

    vdot = vdot_mod.estimate_vdot(athlete)
    flags: list[str] = []
    rationale: list[str] = []

    # ---- entry phase -------------------------------------------------- #
    can_10k = athlete.can_run_10k_continuous or athlete.current_weekly_km >= 40
    if not can_10k and athlete.longest_continuous_run_min < 50:
        entry_phase = "1a"
        rationale.append(
            "Cannot yet run a steady 10K -> start Phase 1.a (0->10K)."
        )
    elif athlete.current_weekly_km < GATE_KM and runway_weeks > 0:
        entry_phase = "1b"
        rationale.append(
            f"Runs 10K but only {athlete.current_weekly_km:.0f} km/week "
            f"(< {GATE_KM:.0f} gate) -> Phase 1.b base build."
        )
    elif runway_weeks <= 0:
        entry_phase = "2"
        rationale.append(
            "Inside the 18-week window already -> straight into the race block."
        )
    else:
        entry_phase = "1b"
        rationale.append(
            "At/above the 48 km gate with runway to spare -> Phase 1.b "
            "consolidation until the block starts."
        )

    # ---- peak selection ----------------------------------------------- #
    rp = _runway_peak(runway_weeks)
    dp = _days_peak(athlete.training_days_per_week)
    target_peak = min(rp, dp)
    if rp <= dp:
        binding = "runway"
        rationale.append(
            f"Runway {runway_weeks} wk caps peak at {rp:.0f} km "
            f"(days allow {dp:.0f})."
        )
    else:
        binding = "days"
        rationale.append(
            f"{athlete.training_days_per_week} days/week caps peak at {dp:.0f} km "
            f"(runway allows {rp:.0f})."
        )

    # gate floor
    if target_peak < GATE_KM:
        target_peak = GATE_KM
        binding = "gate"

    # ---- feasibility of reaching the gate ----------------------------- #
    feasible = True
    if entry_phase in ("1a", "1b"):
        # rough: need ~ (gate - current)/current growth at ~10%/wk effective 7%.
        gap = max(0.0, GATE_KM - max(athlete.current_weekly_km, 10.0))
        weeks_needed = 0
        cur = max(athlete.current_weekly_km, 10.0)
        while cur < GATE_KM and weeks_needed < 200:
            cur *= 1.07
            weeks_needed += 1
        if entry_phase == "1a":
            weeks_needed += 14  # 0->10K runway before base build can start
        if weeks_needed > runway_weeks:
            feasible = False
            flags.append(
                f"Cannot safely reach the {GATE_KM:.0f} km gate in time "
                f"(needs ~{weeks_needed} wk, has {runway_weeks}). "
                f"Pick a later race or accept a reduced-goal plan."
            )

    category = category_for(target_peak)

    return Assessment(
        entry_phase=entry_phase,
        target_peak_km=round(target_peak, 1),
        peak_binding_constraint=binding,
        daniels_category=category,
        vdot=round(vdot, 1),
        runway_weeks=runway_weeks,
        feasible=feasible,
        flags=flags,
        rationale=rationale,
    )
