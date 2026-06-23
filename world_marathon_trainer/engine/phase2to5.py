"""Phase 2-5 — the 18-week race block (Daniels-informed, race-modulated).

The invariant skeleton (from Daniels' Final-18 2Q program):
  * fraction-of-peak volume curve by weeks-to-race
  * two quality sessions per week (Q1 long/M-focused, Q2 T/I-focused)
  * VDOT progression: wks 18-13 use goal-2, 12-7 use goal-1, 6-1 use goal
  * E days fill the remaining weekly volume

The race overlay (from the race JSON) modulates session *content* in Phases 3 & 4:
  hills, downhill loading, late-climb rehearsal, heat prep. Two athletes on the
  same category run the same structure but different sessions.

Phase split inside the block:
  Phase 2: weeks-to-race 18-13   (threshold base, VDOT goal-2)
  Phase 3: weeks-to-race 12-7    (marathon-specific build, course work begins)
  Phase 4: weeks-to-race  6-3    (peak + full course rehearsal)
  Phase 5: weeks-to-race  2-1    (taper)
"""

from __future__ import annotations

from .models import Session, Week
from .race_profile import RaceProfile
from . import vdot as vdot_mod

# Fraction of peak by weeks-to-race (Daniels Final-18, representative curve).
_PEAK_FRACTION = {
    18: 0.80, 17: 0.80, 16: 0.90, 15: 0.90, 14: 0.90, 13: 0.90,
    12: 1.00, 11: 0.90, 10: 1.00, 9: 1.00, 8: 0.90, 7: 0.90,
    6: 1.00, 5: 1.00, 4: 0.90, 3: 0.90, 2: 0.85, 1: 0.45,
}


def phase_for(wtr: int) -> tuple[str, str]:
    if wtr >= 13:
        return "2", "Phase 2 — Threshold Base"
    if wtr >= 7:
        return "3", "Phase 3 — Marathon-Specific Build"
    if wtr >= 3:
        return "4", "Phase 4 — Peak & Race Rehearsal"
    return "5", "Phase 5 — Taper"


def _vdot_for(wtr: int, goal_vdot: float) -> float:
    if wtr >= 13:
        return goal_vdot - 2
    if wtr >= 7:
        return goal_vdot - 1
    return goal_vdot


def _run_days(training_days: int) -> list[str]:
    presets = {
        4: ["Tue", "Thu", "Sat", "Sun"],
        5: ["Mon", "Tue", "Thu", "Sat", "Sun"],
        6: ["Mon", "Tue", "Wed", "Thu", "Sat", "Sun"],
        7: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }
    return presets[max(4, min(7, training_days))]


# --------------------------------------------------------------------------- #
# Quality-session archetypes per phase. Distances scale with weekly volume.
# Returns (description, tags) given paces and the week's quality budget in km.
# --------------------------------------------------------------------------- #
def _q1(phase: str, wtr: int, vol: float, paces, race: RaceProfile) -> Session:
    """Q1 = the long / marathon-pace session."""
    m = vdot_mod.fmt_pace(paces["M"])
    tags: list[str] = []
    if phase == "2":
        dur = min(110, 80 + (18 - wtr) * 6)
        desc = f"Long aerobic run {int(dur)} min easy"
        kind = "long"
    elif phase == "3":
        m_km = round(vol * 0.30, 0)
        desc = f"Marathon-pace block: {int(m_km)} km @ {m} inside a long run"
        kind = "quality"
        for mod in race.phase3_modifiers:
            tags.append(f"course:{mod}")
        climb = race.late_climb()
        if climb:
            tags.append(f"course:late_climb@{int(climb['start_km'])}km")
    elif phase == "4":
        if wtr in (6, 4):
            m_km = round(vol * 0.40, 0)
            desc = f"Race rehearsal: {int(m_km)} km @ marathon pace {m}"
        else:
            desc = "Longest run of the block (150 min steady)"
        kind = "quality"
        for mod in race.phase4_modifiers:
            tags.append(f"course:{mod}")
        if race.race_profile_long_run:
            tags.append("course:profile_long_run")
        if race.downhill_loading:
            tags.append("course:downhill_reps")
    else:  # phase 5 taper
        desc = "Easy taper long run 60-90 min"
        kind = "long"
    return Session(day="", kind=kind, description=desc,
                   pace_min_per_km=round(paces["M"], 2), tags=tags)


def _q2(phase: str, wtr: int, vol: float, paces, race: RaceProfile) -> Session:
    """Q2 = the threshold / interval sharpening session."""
    t = vdot_mod.fmt_pace(paces["T"])
    i = vdot_mod.fmt_pace(paces["I"])
    tags: list[str] = []
    if phase == "2":
        desc = f"Threshold: 4-5 x (5 min T @ {t}) w/1 min jog"
    elif phase == "3":
        desc = f"Intervals: 5 x (3 min I @ {i}) + 2 x (5 min T @ {t})"
        if race.hill_loading_weeks and wtr in (12, 10, 8):
            desc = f"Hill repeats: 6-8 x 90 s uphill hard + threshold @ {t}"
            tags.append("course:hill_reps")
    elif phase == "4":
        desc = f"Sharpening: 3 x (2 min T @ {t}) + strides"
        if race.heat_prep_required:
            tags.append("course:heat_prep")
    else:  # taper
        desc = f"Race sharpener: 4 x (90 s T @ {t}) w/2 min jog"
    return Session(day="", kind="quality", description=desc,
                   pace_min_per_km=round(paces["T"], 2), tags=tags)


def build_phase2to5(
    start_index: int,
    peak_km: float,
    goal_vdot: float,
    training_days: int,
    race: RaceProfile,
) -> list[Week]:
    run_days = _run_days(training_days)
    weeks: list[Week] = []

    for offset, wtr in enumerate(range(18, 0, -1)):
        idx = start_index + offset
        phase, label = phase_for(wtr)
        vol = round(peak_km * _PEAK_FRACTION[wtr], 1)
        wk_vdot = _vdot_for(wtr, goal_vdot)
        paces = vdot_mod.paces_from_vdot(wk_vdot)
        e_pace = round(paces["E"], 2)

        # Quality placement: Q1 on the long-run day (Sat), Q2 mid-week (Tue/Wed).
        q1 = _q1(phase, wtr, vol, paces, race)
        q2 = _q2(phase, wtr, vol, paces, race)
        q1_day = run_days[-1]                       # weekend long
        q2_day = "Wed" if "Wed" in run_days else "Tue"

        # Easy volume = remaining after quality (approximate quality as ~35% vol).
        quality_km = round(vol * 0.35, 1)
        easy_days = [d for d in run_days if d not in (q1_day, q2_day)]
        easy_each = round(max(0.0, vol - quality_km) / max(1, len(easy_days)), 1)

        days: list[Session] = []
        for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            if d == q1_day:
                s = Session(day=d, kind=q1.kind, description=q1.description,
                            pace_min_per_km=q1.pace_min_per_km, tags=q1.tags)
            elif d == q2_day:
                s = Session(day=d, kind=q2.kind, description=q2.description,
                            pace_min_per_km=q2.pace_min_per_km, tags=q2.tags)
            elif d in easy_days:
                s = Session(day=d, kind="easy",
                            description=f"Easy run {easy_each:.0f} km",
                            distance_km=easy_each, pace_min_per_km=e_pace)
            else:
                s = Session(day=d, kind="rest", description="Rest or easy 30 min")
            days.append(s)

        note = ""
        if wtr == 1:
            note = "RACE WEEK — race is at the end of this week. Trust the taper."
        weeks.append(
            Week(
                index=idx, phase=phase, phase_label=label,
                weeks_to_race=wtr, target_volume_km=vol,
                days=days, note=note,
            )
        )
    return weeks
