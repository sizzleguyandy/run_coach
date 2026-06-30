"""MCP adapter tests — tool registration, error forwarding, today-selection.

Run: python -m pytest tests/test_mcp.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point at an unused base so unreachable-API behaviour is deterministic.
os.environ.setdefault("WMT_API_BASE", "http://localhost:59999")

import coach_mcp_server as s   # noqa: E402

EXPECTED_TOOLS = {
    "list_races", "race_knowledge", "onboard_athlete", "get_athlete",
    "get_plan", "get_today", "get_paces", "adapt_week",
    "get_adaptation_history",
}


def test_all_tools_registered():
    tools = asyncio.run(s.mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names


def test_tool_descriptions_present():
    tools = asyncio.run(s.mcp.list_tools())
    for t in tools:
        assert t.description and len(t.description) > 20


def test_unreachable_api_returns_structured_error():
    out = s.get_athlete("x")
    assert isinstance(out, dict) and "error" in out
    assert "cannot reach" in out["error"]


def test_get_today_selects_correct_day(monkeypatch):
    # Fake a plan whose current week contains 'today'; assert the right session.
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=today.weekday())     # Monday this week
    end = start + timedelta(days=7)
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today.weekday()]

    fake_plan = {
        "race_date": (today + timedelta(days=120)).isoformat(),
        "weeks": [{
            "phase": "3", "phase_label": "Phase 3", "weeks_to_race": 8,
            "target_volume_km": 70.0, "note": "",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "days": [
                {"day": d, "kind": "easy", "description": f"{d} run",
                 "distance_km": 10.0, "duration_min": None,
                 "pace_min_per_km": 5.5, "tags": []}
                for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            ],
        }],
    }
    monkeypatch.setattr(s, "_request", lambda *a, **k: fake_plan)
    out = s.get_today("athlete-1")
    assert out["weekday"] == weekday
    assert out["today_session"]["day"] == weekday
    assert out["weeks_to_race"] == 8


def test_get_today_no_active_week(monkeypatch):
    fake_plan = {"race_date": "2099-01-01", "weeks": [{
        "phase": "2", "phase_label": "P2", "weeks_to_race": 18,
        "target_volume_km": 50.0, "note": "",
        "start_date": "2099-01-01", "end_date": "2099-01-08", "days": [],
    }]}
    monkeypatch.setattr(s, "_request", lambda *a, **k: fake_plan)
    out = s.get_today("athlete-1")
    assert "message" in out
