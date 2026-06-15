import math
from dataclasses import dataclass


# ── Template base-mileage gate ─────────────────────────────────────────────
# Race distances 21 km and up require a minimum weekly base before the Phase 2
# quality templates begin. Anyone starting below this base gets an extended
# Phase 1 (base building) until they reach it. 5k/10k are exempt.
TEMPLATE_BASE_KM: float = 48.0
TEMPLATE_BASE_DISTANCES: set[str] = {"half", "marathon", "ultra", "ultra_56", "ultra_90"}


@dataclass
class PhaseAllocation:
    phase_I:    int
    phase_II:   int
    phase_III:  int
    phase_IV:   int
    total_weeks: int


def _split_quality_weeks(remaining: int) -> tuple[int, int]:
    """Split the weeks left for quality work into (Phase II, Phase III).

    Mirrors the standard allocation: Phase II is guaranteed a minimum of 2
    weeks (up to 4) when there is room; Phase III takes the rest.
    """
    if remaining >= 2:
        phase_II  = min(4, max(2, remaining // 3))
        phase_III = max(0, remaining - phase_II)
    else:
        phase_II  = 0
        phase_III = max(0, remaining)
    return phase_II, phase_III


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
            phase_II, phase_III = _split_quality_weeks(remaining)
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


def get_phases_with_base(
    weeks: int,
    base_phase2_start: "int | None",
    reachable: bool,
) -> tuple[PhaseAllocation, "str | None"]:
    """
    Phase allocation for races that require the 48 km template base (21 km+).

    Extends Phase I so that the Phase 2 quality templates only begin once the
    athlete has built up to the template base mileage.

    Args:
      weeks:             total plan length (derived from the race date)
      base_phase2_start: the earliest week at which weekly volume reaches the
                         template base (from volume.base_phase_for_distance);
                         None when the base is never reached.
      reachable:         whether the template base is achievable given the
                         athlete's starting mileage and profile peak cap.

    Returns (PhaseAllocation, status) where status is one of:
      None          — standard 6-week base is enough; athlete reaches the base
                       within Phase 1 with no changes.
      "extended"    — Phase 1 was lengthened (Phase II/III shrink accordingly).
      "no_time"     — not enough weeks before the taper to reach the base; the
                       plan is base-building only (Phase II = III = 0).
      "unreachable" — the peak cap prevents ever reaching the base; base-only.
    """
    weeks = max(6, min(24, weeks))
    std = get_phases(weeks)

    # Short plans (6–11 weeks) are already base + taper only — no template
    # phases exist to gate. Flag if the base still won't be reached.
    if weeks < 12:
        if not reachable or base_phase2_start is None or base_phase2_start > std.phase_I:
            return std, "no_time"
        return std, None

    phase_IV    = std.phase_IV          # 6 for long plans
    max_phase_I = weeks - phase_IV       # leave the taper intact; II/III may be 0

    # Peak cap makes the base unreachable → base-building only.
    if not reachable:
        alloc = PhaseAllocation(
            phase_I=max_phase_I, phase_II=0, phase_III=0,
            phase_IV=phase_IV, total_weeks=weeks,
        )
        return alloc, "unreachable"

    # Required Phase I = enough base weeks so Phase 2 starts at/after the base.
    required_phase_I = std.phase_I
    if base_phase2_start is not None:
        required_phase_I = max(std.phase_I, base_phase2_start - 1)

    # Standard 6-week base already gets them there — no change needed.
    if required_phase_I <= std.phase_I:
        return std, None

    # Not enough room for any template phases before the taper → base-only.
    if required_phase_I >= max_phase_I:
        alloc = PhaseAllocation(
            phase_I=max_phase_I, phase_II=0, phase_III=0,
            phase_IV=phase_IV, total_weeks=weeks,
        )
        return alloc, "no_time"

    # Extend Phase I; split whatever remains into Phase II / III.
    remaining = weeks - required_phase_I - phase_IV
    phase_II, phase_III = _split_quality_weeks(remaining)
    alloc = PhaseAllocation(
        phase_I=required_phase_I,
        phase_II=phase_II,
        phase_III=phase_III,
        phase_IV=phase_IV,
        total_weeks=weeks,
    )
    return alloc, "extended"


def get_phase_for_week(week_number: int, phases: PhaseAllocation) -> int:
    """Return phase number (1–4) for a given 1-based week number."""
    if week_number <= phases.phase_I:
        return 1
    if week_number <= phases.phase_I + phases.phase_II:
        return 2
    if week_number <= phases.phase_I + phases.phase_II + phases.phase_III:
        return 3
    return 4
