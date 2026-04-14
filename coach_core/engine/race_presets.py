"""
Race presets facade.

Reads RACE_COUNTRY from the environment (.env) and exposes the correct
country's presets through the same public interface that all callers expect:

    get_preset(preset_id)
    get_next_race_date(preset_id)
    preset_keyboard_rows()
    find_preset_by_label(text)
    RACE_PRESETS
    RACE_COORDS

To switch countries: set RACE_COUNTRY=uk (or sa) in .env and restart.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

_COUNTRY = os.getenv("RACE_COUNTRY", "sa").lower().strip()

if _COUNTRY == "uk":
    from .race_presets_uk import RACE_PRESETS_UK  as RACE_PRESETS  # noqa: F401
    from .race_presets_uk import RACE_COORDS_UK   as RACE_COORDS   # noqa: F401
    from .race_presets_uk import get_next_race_date_uk as _get_next
else:
    from .race_presets_sa import RACE_PRESETS_SA  as RACE_PRESETS  # noqa: F401
    from .race_presets_sa import RACE_COORDS_SA   as RACE_COORDS   # noqa: F401
    from .race_presets_sa import get_next_race_date_sa as _get_next


def get_preset(preset_id: str) -> dict | None:
    return RACE_PRESETS.get(preset_id)


def get_next_race_date(preset_id: str) -> str:
    return _get_next(preset_id)


def preset_keyboard_rows() -> list[list[str]]:
    """
    Returns keyboard rows for Telegram.
    Two presets per row, 'Other' on its own final row.
    No emoji in button text.
    """
    labels = [p["display_name"] for p in RACE_PRESETS.values()]
    rows = [labels[i:i+2] for i in range(0, len(labels), 2)]
    rows.append(["Other race \u2014 I will enter details"])
    return rows


def find_preset_by_label(text: str) -> str | None:
    """
    Match user button text back to a preset_id.
    Returns preset_id or None.
    """
    text_lower = text.lower().strip()
    for pid, p in RACE_PRESETS.items():
        if text_lower == p["display_name"].lower() or p["display_name"].lower() in text_lower:
            return pid
    return None
