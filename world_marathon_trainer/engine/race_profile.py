"""Race profile loader.

Reads the per-race JSON files from the races/ folder. Adding a race is a data
operation, never a code change. The engine only ever touches the fields it needs;
the rest are carried for the conversational agent (RAG).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

_RACES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "races")


class RaceProfile:
    """Thin typed accessor over a race JSON document."""

    def __init__(self, doc: dict):
        self.doc = doc

    # -- identity -------------------------------------------------------- #
    @property
    def id(self) -> str:
        return self.doc["identity"]["id"]

    @property
    def name(self) -> str:
        return self.doc["identity"]["name"]

    @property
    def distance_km(self) -> float:
        return self.doc["identity"]["distance_km"]

    # -- training drivers ------------------------------------------------ #
    @property
    def overlay(self) -> dict:
        return self.doc.get("training_overlay", {})

    @property
    def hill_loading_weeks(self) -> int:
        return int(self.overlay.get("hill_loading_weeks", 0))

    @property
    def downhill_loading(self) -> bool:
        return bool(self.overlay.get("downhill_loading", False))

    @property
    def heat_prep_required(self) -> bool:
        return bool(self.overlay.get("heat_prep_required", False))

    @property
    def back_to_backs(self) -> bool:
        return bool(self.overlay.get("back_to_backs", False))

    @property
    def race_profile_long_run(self) -> str:
        return self.overlay.get("race_profile_long_run", "")

    @property
    def phase3_modifiers(self) -> list[str]:
        return list(self.overlay.get("phase3_session_modifiers", []))

    @property
    def phase4_modifiers(self) -> list[str]:
        return list(self.overlay.get("phase4_session_modifiers", []))

    @property
    def key_segments(self) -> list[dict]:
        return self.doc.get("course_profile", {}).get("key_segments", [])

    def late_climb(self) -> dict | None:
        """The most demanding climb that falls after 25 km (the differentiator)."""
        late = [
            s for s in self.key_segments
            if s.get("start_km", 0) >= 25 and s.get("elevation_change_m", 0) > 0
        ]
        if not late:
            return None
        return max(late, key=lambda s: s.get("elevation_change_m", 0))


def _path_for(race_id: str) -> str:
    return os.path.join(_RACES_DIR, f"{race_id}.json")


@lru_cache(maxsize=None)
def load_race(race_id: str) -> RaceProfile:
    path = _path_for(race_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"no race profile for id '{race_id}' at {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return RaceProfile(json.load(fh))


def available_races() -> list[str]:
    if not os.path.isdir(_RACES_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(_RACES_DIR)
        if f.endswith(".json") and not f.startswith("_")
    )
