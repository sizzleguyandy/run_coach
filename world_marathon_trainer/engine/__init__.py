"""World Marathon Trainer — pure training-logic engine.

No I/O, no database, no network. Deterministic functions that turn an athlete's
current state + a target race profile into a full periodised training plan.

Pipeline:
    assessment  -> where does the athlete slot in + what peak can they reach
    phase1a     -> 0->10K plan (true beginners)
    phase1b     -> base build to target peak (gate >= 48 km/week)
    phase2to5   -> Daniels-informed 18-week race block, modulated by race overlay
    builder     -> orchestrates the above into one continuous plan
"""

from .models import (
    AthleteInput,
    Session,
    Week,
    Plan,
    Assessment,
)
from .builder import build_plan

__all__ = [
    "AthleteInput",
    "Session",
    "Week",
    "Plan",
    "Assessment",
    "build_plan",
]
