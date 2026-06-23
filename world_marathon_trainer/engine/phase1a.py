"""Phase 1.a — the 0->10K plan (true beginners).

Walk/run progression -> continuous time -> continuous distance, graduating on a
steady 10K. Built on Daniels' novice walk/run structure. 3 sessions per week,
at least one easy day between. Graduation gate = run a steady 10K.

The plan is a fixed 14-week ladder; slot-in lets fitter beginners start partway
down it (handled by the builder trimming completed rungs).
"""

from __future__ import annotations

from .models import Session, Week

# Each rung: (label, the three session descriptions for the week, approx minutes)
_LADDER = [
    # Stage 1 — walk/run foundation
    ("0:60 run / 1:30 walk x8", "8 x (1:00 run / 1:30 walk)", 20),
    ("1:30 run / 1:30 walk x8", "8 x (1:30 run / 1:30 walk)", 24),
    ("2:00 run / 1:30 walk x6", "6 x (2:00 run / 1:30 walk)", 21),
    # Stage 2 — run dominant
    ("3:00 run / 1:30 walk x5", "5 x (3:00 run / 1:30 walk)", 22),
    ("5:00 run / 1:30 walk x4", "4 x (5:00 run / 1:30 walk)", 26),
    ("8:00 run / 2:00 walk x3", "3 x (8:00 run / 2:00 walk)", 30),
    # Stage 3 — continuous time
    ("20 min continuous", "20:00 continuous easy", 20),
    ("25 min continuous", "25:00 continuous easy", 25),
    ("30 min continuous", "30:00 continuous easy (5K-ready)", 30),
    # Stage 4 — continuous distance, 5K -> 10K
    ("steady 5K", "Steady 5K easy", 33),
    ("6K", "Steady 6K easy", 40),
    ("7K", "Steady 7K easy", 47),
    ("8.5K", "Steady 8.5K easy", 57),
    ("steady 10K (GRADUATION)", "Steady 10K — graduation run", 67),
]

_DAYS = ["Mon", "Wed", "Sat"]   # 3 sessions, easy day between


def total_rungs() -> int:
    return len(_LADDER)


def build_phase1a(start_index: int, from_rung: int = 0) -> list[Week]:
    """Return the 0->10K weeks, starting at absolute week `start_index`.

    `from_rung` lets a fitter beginner skip the earliest rungs.
    """
    weeks: list[Week] = []
    idx = start_index
    for rung in range(from_rung, len(_LADDER)):
        label, desc, minutes = _LADDER[rung]
        days: list[Session] = []
        for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
            if d in _DAYS:
                days.append(
                    Session(
                        day=d,
                        kind="walk_run" if rung < 6 else "easy",
                        description=desc,
                        duration_min=float(minutes),
                    )
                )
            else:
                days.append(Session(day=d, kind="rest", description="Rest"))
        graduates = rung == len(_LADDER) - 1
        weeks.append(
            Week(
                index=idx,
                phase="1a",
                phase_label="Phase 1.a — Couch to 10K",
                weeks_to_race=None,
                target_volume_km=round(minutes * 3 / 6.5, 1),  # rough easy km
                days=days,
                note="Graduation: complete a steady 10K." if graduates else "",
            )
        )
        idx += 1
    return weeks
