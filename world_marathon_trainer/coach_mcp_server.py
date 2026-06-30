"""World Marathon Trainer — MCP server (adapter over the FastAPI engine).

A thin Model Context Protocol server that exposes the World Marathon Trainer
API as a fixed set of agent tools. An MCP client (e.g. Hermes Agent) connects to
this over stdio; the agent can ONLY do what these tools allow — it cannot invent
training prescriptions, only call the engine.

Layering (unchanged from the rest of the project):
    Hermes (mouth/ears/memory/WhatsApp)
        -> this MCP adapter (thin, no logic)
            -> FastAPI (thin)
                -> engine (the brain: deterministic, tested)

Run standalone (for a quick check):
    WMT_API_BASE=http://localhost:8000 python coach_mcp_server.py

Wire into Hermes (~/.hermes/config.yaml):
    mcp_servers:
      world_marathon_trainer:
        command: python
        args: ["/abs/path/to/coach_mcp_server.py"]
        env:
          WMT_API_BASE: "http://localhost:8000"

Requires: pip install "mcp[cli]" httpx
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = os.environ.get("WMT_API_BASE", "http://localhost:8000")
TIMEOUT = float(os.environ.get("WMT_API_TIMEOUT", "30"))

mcp = FastMCP("world_marathon_trainer")
_client = httpx.Client(base_url=API_BASE, timeout=TIMEOUT)

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# --------------------------------------------------------------------------- #
# Internal helpers (forwarding only — no training logic)
# --------------------------------------------------------------------------- #
def _request(method: str, path: str, **kw) -> Any:
    """Call the FastAPI engine and return JSON, or a structured error dict."""
    try:
        r = _client.request(method, path, **kw)
    except httpx.RequestError as e:
        return {"error": f"cannot reach coach API at {API_BASE}: {e}"}
    if r.status_code >= 400:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:200]
        return {"error": f"{r.status_code}: {detail}"}
    if r.status_code == 204:
        return {"ok": True}
    return r.json()


# --------------------------------------------------------------------------- #
# Race knowledge (RAG source)
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_races() -> Any:
    """List the marathons an athlete can train for (id, name, distance, month).

    Call this when the athlete is choosing a race or asks what's available.
    """
    return _request("GET", "/races")


@mcp.tool()
def race_knowledge(race_id: str) -> Any:
    """Get the full course + strategy profile for a race.

    Returns elevation profile, key climbs (with where in the race they fall),
    weather norms, recommended pacing, danger zones, common mistakes, gear and
    fuelling notes. Use this to answer ANY race-specific question (e.g. "how
    hilly is the back half?", "how should I pace Cape Town?", "what catches
    people out?"). This is the source of truth for race knowledge — do not
    answer race questions from memory.
    """
    return _request("GET", f"/races/{race_id}")


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #
@mcp.tool()
def onboard_athlete(
    name: str,
    race_id: str,
    race_date: str,
    training_days_committed: int,
    activities: Optional[list[dict]] = None,
    preview: bool = True,
) -> Any:
    """Slot a new athlete into the programme from their Strava history.

    Args:
        name: athlete's name.
        race_id: target race id (see list_races).
        race_date: race day, YYYY-MM-DD.
        training_days_committed: days/week the athlete commits to going forward
            (NOT their past habit — this drives the safe peak mileage).
        activities: recent runs (normally supplied by the Strava sync pipeline,
            not by you). Each: {id, start_date, distance_km, moving_time_min,...}.
        preview: if true (default), returns where they'd slot in WITHOUT saving —
            use this to show the athlete their starting point and ask them to
            confirm. Set false only when they've agreed to commit.

    Returns the derived fitness profile + the slot-in assessment (entry phase,
    target peak, the reason it's capped, feasibility).
    """
    body = {
        "name": name,
        "race_id": race_id,
        "race_date": race_date,
        "training_days_committed": training_days_committed,
        "preview": preview,
        "activities": activities or [],
    }
    return _request("POST", "/onboard", json=body)


# --------------------------------------------------------------------------- #
# Athlete + plan
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_athlete(athlete_id: str) -> Any:
    """Fetch an athlete's stored profile (race, fitness, VDOT, training days)."""
    return _request("GET", f"/athlete/{athlete_id}")


@mcp.tool()
def get_plan(athlete_id: str) -> Any:
    """Build the athlete's full training plan (live-computed from their record).

    Returns the assessment plus every week (phase, weeks-to-race, volume, the
    daily sessions, calendar dates). Prefer get_today for "what's my run today";
    use this for the bigger picture ("show me the next few weeks").
    """
    return _request("POST", f"/athlete/{athlete_id}/plan")


@mcp.tool()
def get_today(athlete_id: str, as_of: Optional[str] = None) -> Any:
    """Get the athlete's session for today (or `as_of`, YYYY-MM-DD).

    Selects the current week and today's session from the live plan. Use this
    for "what should I run today / this week?". This is a selection over the
    plan — the training content always comes from the engine, never invented.
    """
    plan = _request("POST", f"/athlete/{athlete_id}/plan")
    if isinstance(plan, dict) and plan.get("error"):
        return plan

    today = date.fromisoformat(as_of) if as_of else date.today()
    weeks = plan.get("weeks", [])
    current = None
    for w in weeks:
        s, e = w.get("start_date"), w.get("end_date")
        if s and e and s <= today.isoformat() < e:
            current = w
            break
    if current is None:
        return {
            "message": "No active training week for that date — the plan either "
                       "hasn't started or has finished.",
            "race_date": plan.get("race_date"),
        }

    weekday = _WEEKDAYS[today.weekday()]
    session = next((d for d in current["days"] if d["day"] == weekday), None)
    return {
        "date": today.isoformat(),
        "weekday": weekday,
        "phase": current["phase_label"],
        "weeks_to_race": current["weeks_to_race"],
        "week_target_volume_km": current["target_volume_km"],
        "week_note": current.get("note", ""),
        "today_session": session,
    }


@mcp.tool()
def get_paces(athlete_id: str) -> Any:
    """Get the athlete's current training paces + equivalent race times.

    Uses the athlete's current VDOT to return E/M/T/I/R paces and a readable
    pace card. Use this for "what pace should I run easy / threshold?".
    """
    athlete = _request("GET", f"/athlete/{athlete_id}")
    if isinstance(athlete, dict) and athlete.get("error"):
        return athlete
    vdot = athlete.get("vdot")
    if not vdot:
        return {"error": "athlete has no VDOT yet — onboard or sync first"}
    return _request("GET", f"/vdot/{vdot}")


# --------------------------------------------------------------------------- #
# Adaptation
# --------------------------------------------------------------------------- #
@mcp.tool()
def adapt_week(athlete_id: str, as_of: Optional[str] = None) -> Any:
    """Run the weekly adaptation: compare the last completed week's plan to what
    the athlete actually did (Strava) and apply a bounded fitness nudge.

    Returns compliance metrics, the VDOT change, and coaching notes/flags you
    should relay to the athlete. Normally triggered by the weekly sync pipeline;
    call it directly if the athlete asks to "review my week". `as_of` (YYYY-MM-DD)
    overrides today for backfill.
    """
    params = {"as_of": as_of} if as_of else None
    return _request("POST", f"/athlete/{athlete_id}/adapt", params=params)


@mcp.tool()
def get_adaptation_history(athlete_id: str, limit: int = 10) -> Any:
    """Get the athlete's recent adaptation decisions (audit trail)."""
    return _request("GET", f"/athlete/{athlete_id}/adaptations",
                    params={"limit": limit})


if __name__ == "__main__":
    mcp.run()
