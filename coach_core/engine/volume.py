from typing import List
from coach_core.engine.phases import PhaseAllocation, TEMPLATE_BASE_KM
from coach_core.engine.training_profiles import get_profile

# Midpoint of Daniels target peak ranges per race distance
# ultra_56 = Two Oceans (56km), ultra_90 = Comrades (90km)
# 'ultra' kept as alias for ultra_56 for backward compatibility
PEAK_VOLUME_KM: dict[str, float] = {
    "5k":       70.0,
    "10k":      80.0,
    "half":     90.0,
    "marathon": 120.0,
    "ultra":    100.0,   # legacy alias → Two Oceans range
    "ultra_56": 100.0,   # Two Oceans — 80–110km range, midpoint 100
    "ultra_90": 160.0,   # Comrades    — 120–160km range, midpoint 160
}

# Distance-specific taper lengths (Daniels-inspired)
TAPER_WEEKS: dict[str, int] = {
    "5k":       1,
    "10k":      1,
    "half":     2,
    "marathon": 3,
    "ultra":    3,
    "ultra_56": 2,   # Two Oceans — 2 weeks sufficient for 56km
    "ultra_90": 3,   # Comrades   — full 3-week taper
}


def get_taper_weeks(race_distance: str) -> int:
    return TAPER_WEEKS.get(race_distance, 2)


def _target_peak(current_weekly_mileage: float, race_distance: str, profile: dict) -> float:
    """Peak volume target for the plan, capped by the profile's peak factor and
    floored at the starting mileage. Single source of truth used by both the
    volume curve and the base-building gate."""
    target_peak = PEAK_VOLUME_KM.get(race_distance, 90.0)
    target_peak = min(target_peak, current_weekly_mileage * profile["peak_cap_factor"])
    target_peak = max(target_peak, current_weekly_mileage)
    return target_peak


def _simulate_build(
    current_weekly_mileage: float,
    target_peak: float,
    build_rate: float,
    cutback_factor: float,
    max_weeks: int = 60,
) -> List[float]:
    """Simulate the weekly build curve (same rules as build_volume_curve's
    build phase): +build_rate each week, ×cutback every 4th week, capped at
    target_peak. Returns the per-week volumes."""
    vols: List[float] = []
    for w in range(1, max_weeks + 1):
        if w == 1:
            vol = current_weekly_mileage
        elif w % 4 == 0:
            vol = vols[-1] * cutback_factor
        else:
            vol = min(vols[-1] * build_rate, target_peak)
        vols.append(round(vol, 1))
    return vols


def base_phase_for_distance(
    current_weekly_mileage: float,
    race_distance: str,
    training_profile: str = "conservative",
    target_km: float = TEMPLATE_BASE_KM,
) -> tuple["int | None", bool, float]:
    """
    Work out when an athlete reaches the template base mileage.

    Returns (phase2_start_week, reachable, target_peak):
      phase2_start_week: earliest 1-based week at which weekly volume reaches
                         `target_km` on a build (non-cutback) week — the soonest
                         Phase 2 quality templates may begin. None if never reached.
      reachable:         False when the profile's peak cap keeps the build below
                         `target_km` no matter how many weeks are available.
      target_peak:       the peak the build is capped to (for messaging).
    """
    profile     = get_profile(training_profile)
    target_peak = _target_peak(current_weekly_mileage, race_distance, profile)

    # Already at or above the base.
    if current_weekly_mileage >= target_km:
        return 1, True, target_peak

    # Peak cap will never let the build reach the base.
    if target_peak < target_km:
        return None, False, target_peak

    vols = _simulate_build(
        current_weekly_mileage,
        target_peak,
        profile["build_rate"],
        profile["cutback_factor"],
    )
    # First build (non-cutback) week at/above the base — gives a stable base
    # rather than a transient cutback-week reading.
    for w, v in enumerate(vols, start=1):
        if v >= target_km and w % 4 != 0:
            return w, True, target_peak

    return None, False, target_peak


def build_volume_curve(
    current_weekly_mileage: float,
    race_distance: str,
    phases: PhaseAllocation,
    training_profile: str = "conservative",
) -> List[float]:
    """
    Generate weekly volume (km) for every week of the plan.

    Build rate and cutback depth are governed by the athlete's training_profile:
      conservative: +8%/week, cutback ×0.82, peak cap ×2.0
      aggressive:   +12%/week, cutback ×0.88, peak cap ×3.0

    Peak volume targets by distance:
      ultra_56 (Two Oceans): 100km midpoint — conservative gets 80km, aggressive 120km
      ultra_90 (Comrades):   160km midpoint — conservative gets 120km, aggressive 160km
    """
    profile     = get_profile(training_profile)
    target_peak = _target_peak(current_weekly_mileage, race_distance, profile)

    taper_weeks = get_taper_weeks(race_distance)
    desired_final_volume = target_peak * 0.35

    weeks_to_peak = phases.phase_I + phases.phase_II + phases.phase_III
    if weeks_to_peak == 0:
        weeks_to_peak = phases.phase_I

    phase_iv_length = phases.phase_IV
    total_weeks     = phases.total_weeks

    build_rate   = profile["build_rate"]
    cutback_rate = profile["cutback_factor"]

    build_volumes: List[float] = []
    for i in range(1, weeks_to_peak + 1):
        if i == 1:
            vol = current_weekly_mileage
        else:
            prev = build_volumes[-1]
            if i % 4 == 0:
                vol = prev * cutback_rate
            else:
                vol = min(prev * build_rate, target_peak)
        build_volumes.append(round(vol, 1))

    actual_peak = max(build_volumes)

    taper_start_week = weeks_to_peak + phase_iv_length - taper_weeks

    taper_volumes: List[float] = []
    for abs_week in range(weeks_to_peak + 1, total_weeks + 1):
        is_race_week = abs_week == total_weeks

        if is_race_week:
            vol = desired_final_volume

        elif abs_week >= taper_start_week and taper_weeks > 1:
            taper_position = abs_week - taper_start_week
            step = taper_position / taper_weeks
            vol = actual_peak - step * (actual_peak - desired_final_volume)

        else:
            vol = actual_peak

        taper_volumes.append(round(vol, 1))

    return build_volumes + taper_volumes
