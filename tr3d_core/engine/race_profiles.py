"""
Race training profiles — the data side of RACE_PROFILE_SPEC.md.

Each preset race is described by a small set of demand tags drawn from a
fixed vocabulary (VALID_TAGS), plus two optional note strings. Each tag maps
to exactly one deterministic session/note modification implemented once in
workouts.py. Tags never change weekly volume, phase allocation, or paces —
the Daniels math remains the single source of truth for load.

Adding a race = adding a data entry here. Zero engine changes.
Athletes without a preset (or with an unknown preset_race_id) get the empty
default profile, which produces exactly the generic plan.

Pure module: no I/O, no database — engine rules apply.
"""
from __future__ import annotations

from typing import Optional

# ── Fixed tag vocabulary (see RACE_PROFILE_SPEC.md §2) ──────────────────────
VALID_TAGS: frozenset[str] = frozenset({
    "hilly_long_runs",       # hilly-terrain long-run notes from Phase II onward
    "hill_quality",          # full hill replacement of quality sessions (Phase II/III)
    "downhill_resilience",   # fortnightly downhill repeats in Phase III/IV
    "trail_terrain",         # trail long-run notes + fortnightly Phase III trail session
    "altitude",              # altitude arrival/effort guidance in Phase III/IV
    "heat_humidity",         # heat-adaptation + hydration notes in final 4 weeks
    "power_hike",            # structured walk-break practice in Phase II/III long runs
    "wind_exposure",         # wind-pacing practice notes on Phase III/IV long runs
})

# Max demand tags per race — a race is its 1–3 signature demands.
MAX_TAGS = 3


# ── Per-race training profiles ───────────────────────────────────────────────
# Keyed by preset_race_id (matches race_presets_sa.py / race_presets_uk.py).
# Schema per entry:
#   "tags":           list[str]      0–3 entries from VALID_TAGS
#   "long_run_note":  Optional[str]  appended to long runs from Phase III onward
#   "race_week_note": Optional[str]  appended to race-day notes in race week

RACE_TRAINING_PROFILES: dict[str, dict] = {
    # ── South Africa ────────────────────────────────────────────────────────
    "comrades_marathon": {
        "tags": ["hilly_long_runs", "downhill_resilience", "power_hike"],
        "long_run_note": (
            "Long runs on sustained hills; practice power-hiking climbs at ≤T effort."
        ),
        "race_week_note": (
            "Walk the big climbs from the gun — trust your checkpoint plan."
        ),
    },
    "two_oceans_marathon": {
        "tags": ["hilly_long_runs", "power_hike"],
        "long_run_note": (
            "Include one long sustained climb mid-run (Chapman's Peak simulation)."
        ),
        "race_week_note": None,
    },
    "cape_town_marathon": {
        "tags": ["wind_exposure"],
        "long_run_note": (
            "Practice goal-pace segments into wind on exposed routes."
        ),
        "race_week_note": (
            "Check the SE wind forecast — adjust early pacing if it's blowing."
        ),
    },
    "soweto_marathon": {
        "tags": ["altitude", "hilly_long_runs"],
        "long_run_note": (
            "If not Joburg-based, expect ~4% slower paces at altitude — run by effort."
        ),
        "race_week_note": None,
    },
    "durban_international_marathon": {
        "tags": ["heat_humidity"],
        "long_run_note": (
            "Flat course: run long runs on flat routes at metronomic even pace."
        ),
        "race_week_note": (
            "If race-day temperature exceeds 22°C, add 15–30 s/km to goal pace."
        ),
    },
    "knysna_forest_marathon": {
        "tags": ["trail_terrain", "hilly_long_runs"],
        "long_run_note": (
            "Long runs on forest trail; practice descending with short controlled stride."
        ),
        "race_week_note": None,
    },

    # ── United Kingdom ──────────────────────────────────────────────────────
    "london_marathon": {
        "tags": [],
        "long_run_note": (
            "Practice patient starts: first 3 km deliberately slower than goal pace."
        ),
        "race_week_note": None,
    },
    "manchester_marathon": {
        "tags": ["wind_exposure"],
        "long_run_note": None,
        "race_week_note": (
            "Expect the A56 headwind around km 28–34 — tuck in, don't force pace."
        ),
    },
    "brighton_marathon": {
        "tags": ["wind_exposure"],
        "long_run_note": (
            "Seafront miles: practice pacing in wind both directions."
        ),
        "race_week_note": None,
    },
    "edinburgh_marathon": {
        "tags": [],
        "long_run_note": (
            "Net-downhill course: practice controlled downhill running at goal pace."
        ),
        "race_week_note": None,
    },
    "yorkshire_marathon": {
        "tags": ["wind_exposure"],
        "long_run_note": None,
        "race_week_note": None,
    },
    "loch_ness_marathon": {
        "tags": ["downhill_resilience", "hilly_long_runs"],
        "long_run_note": (
            "Rolling terrain throughout — never bank time on descents."
        ),
        "race_week_note": (
            "Do NOT race the descents after km 20 — save your quads for Inverness."
        ),
    },
}

_EMPTY_PROFILE: dict = {"tags": frozenset(), "long_run_note": None, "race_week_note": None}


def get_training_profile(preset_race_id: Optional[str]) -> dict:
    """
    Return the normalized training profile for a preset race.

    Always safe to call: unknown/None preset ids return the empty profile,
    which leaves the generated plan exactly as the generic engine builds it.
    Unknown tags are silently dropped; tags beyond MAX_TAGS are truncated.

    Returns: {"tags": frozenset[str], "long_run_note": str|None, "race_week_note": str|None}
    """
    if not preset_race_id:
        return dict(_EMPTY_PROFILE)
    raw = RACE_TRAINING_PROFILES.get(preset_race_id)
    if not raw:
        return dict(_EMPTY_PROFILE)
    tags = [t for t in raw.get("tags", []) if t in VALID_TAGS][:MAX_TAGS]
    return {
        "tags": frozenset(tags),
        "long_run_note": raw.get("long_run_note"),
        "race_week_note": raw.get("race_week_note"),
    }
