"""Engine tests. Run: python -m pytest world_marathon_trainer/tests -q
   (or from this folder: python -m pytest -q)
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import AthleteInput, build_plan          # noqa: E402
from engine import vdot as vdot_mod                   # noqa: E402
from engine import assessment as asmt                 # noqa: E402


# --------------------------------------------------------------------------- #
# VDOT / paces
# --------------------------------------------------------------------------- #
def test_paces_are_ordered():
    paces = vdot_mod.paces_from_vdot(50)
    # easier zones are slower (bigger min/km) than faster zones
    assert paces["E"] > paces["M"] > paces["T"] > paces["I"] > paces["R"]


def test_vdot_from_race_sane():
    # ~50 min 10K is roughly VDOT 40
    v = vdot_mod.vdot_from_race(10.0, 50.0)
    assert 38 <= v <= 43


def test_faster_race_gives_higher_vdot():
    assert vdot_mod.vdot_from_race(10.0, 40.0) > vdot_mod.vdot_from_race(10.0, 50.0)


# --------------------------------------------------------------------------- #
# Assessment — peak selection
# --------------------------------------------------------------------------- #
def _athlete(**kw):
    base = dict(
        today=date(2026, 1, 1),
        race_id="cape_town",
        race_date=date(2026, 12, 1),
        current_weekly_km=50.0,
        training_days_per_week=6,
        can_run_10k_continuous=True,
        recent_race_distance_km=10.0,
        recent_race_time_min=45.0,
    )
    base.update(kw)
    return AthleteInput(**base)


def test_days_cap_binds_when_time_rich():
    # tons of runway but only 4 days -> days cap at 64
    a = asmt.assess(_athlete(training_days_per_week=4, race_date=date(2027, 6, 1)))
    assert a.target_peak_km == 64.0
    assert a.peak_binding_constraint == "days"


def test_runway_cap_binds_when_time_poor():
    # 6 days available but short runway -> runway caps
    a = asmt.assess(_athlete(training_days_per_week=6, race_date=date(2026, 7, 1),
                             current_weekly_km=50.0))
    assert a.peak_binding_constraint in ("runway", "gate")
    assert a.target_peak_km <= 113.0


def test_gate_floor_enforced():
    a = asmt.assess(_athlete(training_days_per_week=3, race_date=date(2026, 6, 1)))
    assert a.target_peak_km >= asmt.GATE_KM


def test_category_mapping():
    assert asmt.category_for(48) == "up_to_64"
    assert asmt.category_for(80) == "km_66_89"
    assert asmt.category_for(100) == "km_90_113"
    assert asmt.category_for(130) == "km_114_137"


def test_infeasible_when_no_time():
    # brand-new runner, race in 6 weeks -> cannot reach gate
    a = asmt.assess(_athlete(
        current_weekly_km=10.0, can_run_10k_continuous=False,
        longest_continuous_run_min=5.0, race_date=date(2026, 2, 12),
    ))
    assert a.feasible is False
    assert a.flags


# --------------------------------------------------------------------------- #
# Plan assembly
# --------------------------------------------------------------------------- #
def test_plan_weeks_are_consecutive_and_end_on_race_week():
    plan = build_plan(_athlete(race_date=date(2027, 5, 23), current_weekly_km=35.0))
    indices = [w.index for w in plan.weeks]
    assert indices == list(range(1, len(indices) + 1))     # no gaps
    assert plan.weeks[-1].weeks_to_race == 1               # ends at race week


def test_block_is_exactly_18_weeks():
    plan = build_plan(_athlete(race_date=date(2027, 5, 23), current_weekly_km=35.0))
    block = [w for w in plan.weeks if w.weeks_to_race is not None]
    assert len(block) == 18
    assert {w.phase for w in block} == {"2", "3", "4", "5"}


def test_beginner_gets_phase_1a():
    plan = build_plan(_athlete(
        current_weekly_km=8.0, can_run_10k_continuous=False,
        longest_continuous_run_min=5.0, race_date=date(2028, 5, 1),
    ))
    assert any(w.phase == "1a" for w in plan.weeks)
    # graduation note present on last 1a week
    grad = [w for w in plan.weeks if w.phase == "1a"][-1]
    assert "10K" in grad.note


def test_race_overlay_reaches_sessions():
    # Cape Town has phase3/phase4 modifiers + a late climb -> tags must appear
    plan = build_plan(_athlete(race_date=date(2027, 5, 23), current_weekly_km=35.0))
    p3 = [w for w in plan.weeks if w.phase == "3"]
    tags = [t for w in p3 for s in w.days for t in s.tags]
    assert any("course:" in t for t in tags)


# --------------------------------------------------------------------------- #
# VDOT table + pace card
# --------------------------------------------------------------------------- #
def test_lookup_table_covers_30_to_85():
    tbl = vdot_mod.lookup_table()
    vdots = [r["vdot"] for r in tbl]
    assert vdots[0] == 30 and vdots[-1] == 85 and len(tbl) == 56


def test_lookup_table_paces_have_all_zones():
    tbl = vdot_mod.lookup_table()
    for row in tbl[::10]:
        assert set(row["paces"].keys()) == {"E", "M", "T", "I", "R"}
        assert set(row["race_times"].keys()) == {"5K", "10K", "Half", "Marathon"}


def test_race_times_are_sane():
    # VDOT 50 marathon should be roughly 3:10 (between 2:50 and 3:30)
    times = vdot_mod.race_times_from_vdot(50.0)
    assert 170 < times["Marathon"] < 210   # 2:50 < t < 3:30 (minutes)
    # 5K should be well under half the 10K time (they scale sub-linearly)
    assert times["5K"] < times["10K"] / 1.9


def test_pace_card_contains_all_zones():
    card = vdot_mod.pace_card(45.0)
    for zone in ("Easy", "Marathon", "Threshold", "Interval", "Repetition"):
        assert zone in card
    for race in ("5K", "10K", "Half", "Marathon"):
        assert race in card


def test_build_later_no_long_peak_hold():
    # Long runway (36 weeks). Peak should NOT be held for more than ~6 weeks.
    from engine import phase1b
    curve = phase1b._volume_curve(35.0, 89.0, 36)
    peak_weeks = sum(1 for km, _ in curve if km >= 88.0)
    assert peak_weeks <= 6, f"peak held {peak_weeks} weeks — too long"


def test_build_later_has_maintenance_phase():
    # Long runway: first weeks should be maintenance, not building
    from engine import phase1b
    curve = phase1b._volume_curve(35.0, 89.0, 36)
    first_labels = [label for _, label in curve[:5]]
    assert "maintenance" in first_labels


def test_race_profile_expandable():
    from engine.race_profile import available_races, load_race
    races = available_races()
    assert "cape_town" in races
    # Loading a valid race returns a RaceProfile with the right id
    race = load_race("cape_town")
    assert race.id == "cape_town"
    assert race.distance_km == 42.195


def test_vdot_progression_in_block():
    # paces must get faster (smaller min/km) from phase 2 to phase 4
    plan = build_plan(_athlete(race_date=date(2027, 5, 23), current_weekly_km=35.0))
    def m_pace(phase):
        wk = [w for w in plan.weeks if w.phase == phase][0]
        q = [s for s in wk.days if s.kind in ("quality", "long") and s.pace_min_per_km][0]
        return q.pace_min_per_km
    assert m_pace("2") > m_pace("4")   # phase 2 slower than phase 4
