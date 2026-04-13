import math
from dataclasses import dataclass


@dataclass
class PhaseAllocation:
    phase_I:    int
    phase_II:   int
    phase_III:  int
    phase_IV:   int
    total_weeks: int


def get_phases(weeks: int) -> PhaseAllocation:
    """
    Allocate training weeks across 4 Daniels phases.

    Weeks are capped to 6–24.

    Phase structure (Daniels-compliant):
      Phase I   — Base building (always 6 weeks minimum)
      Phase II  — Quality introduction (minimum 2 weeks when plan >= 14 weeks)
      Phase III — Peak quality (fills remaining build weeks)
      Phase IV  — Taper (always 6 weeks)

    Fix applied: 16 and 18-week plans previously had zero Phase II.
    Now Phase II gets a minimum of 2 weeks for plans >= 14 weeks,
    taken from Phase III if needed.
    """
    weeks = max(6, min(24, weeks))

    if weeks >= 12:
        phase_I  = 6
        phase_IV = 6
        remaining = weeks - 12   # weeks for Phase II + III

        if weeks < 14:
            # 12-week plan — not enough room for full Phase II
            phase_II  = 0
            phase_III = remaining
        else:
            # Guarantee at least 2 weeks of Phase II
            # Daniels recommends ~4 weeks for Phase II
            phase_II  = min(4, max(2, remaining // 3))
            phase_III = max(0, remaining - phase_II)
    else:
        # Short plan (6–11 weeks) — base + taper only
        phase_I   = math.floor(weeks / 2)
        phase_IV  = math.ceil(weeks / 2)
        phase_II  = 0
        phase_III = 0

    # Enforce minimums
    phase_I  = max(phase_I, 3)
    phase_IV = max(phase_IV, 3)

    return PhaseAllocation(
        phase_I=phase_I,
        phase_II=phase_II,
        phase_III=phase_III,
        phase_IV=phase_IV,
        total_weeks=weeks,
    )


def get_phase_for_week(week_number: int, phases: PhaseAllocation) -> int:
    """Return phase number (1–4) for a given 1-based week number."""
    if week_number <= phases.phase_I:
        return 1
    if week_number <= phases.phase_I + phases.phase_II:
        return 2
    if week_number <= phases.phase_I + phases.phase_II + phases.phase_III:
        return 3
    return 4
