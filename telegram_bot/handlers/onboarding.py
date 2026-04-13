"""
onboarding.py — legacy file, retained only for _parse_race_time utility.

The full onboarding ConversationHandler that previously lived here has been
replaced by onboarding_v2.py and is no longer registered in bot.py.

Do NOT re-register the old ConversationHandler — its state constants overlap
with those defined in onboarding_v2.py and would cause silent routing conflicts.

_parse_race_time is still imported by log_handler.py.
"""
from __future__ import annotations

from typing import Optional


def _parse_race_time(text: str) -> Optional[float]:
    """
    Parse a race time string into decimal minutes.

    Accepts:
      HH:MM:SS  → e.g. "3:45:00" → 225.0
      MM:SS     → e.g. "45:30"   → 45.5
      float     → e.g. "225"     → 225.0

    Returns None if the input cannot be parsed.
    """
    text = text.strip()
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
        return float(text)
    except (ValueError, IndexError):
        return None
