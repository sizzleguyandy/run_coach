"""Onboarding smoke test — proves the adapter <-> FastAPI link WITHOUT the agent.

Runs the exact code path the agent uses (the MCP tool functions in
coach_mcp_server) against the running API — minus the LLM. If this passes, the
backend + MCP adapter are proven end to end, and any failure in the live agent is
purely the LLM not calling tools (a persona/config issue, not connectivity).

Layers:
  1. FastAPI health      (is the engine up?)
  2. MCP tools -> API     (do the agent's tools actually reach the engine?)
        list_races -> onboard (preview) -> onboard (commit) -> get_today -> get_paces

Run (API must be running):
    WMT_API_BASE=http://localhost:8000 python scripts/smoke_onboarding.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import httpx                       # noqa: E402
import coach_mcp_server as mcp     # noqa: E402  (the agent's actual tools)

API = os.environ.get("WMT_API_BASE", "http://localhost:8000")
PASS, FAIL = "PASS", "FAIL"
_fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _fails
    mark = PASS if ok else FAIL
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _fails += 1


def _synthetic_activities(weeks: int = 8) -> list[dict]:
    today = date.today()
    base = today - timedelta(weeks=weeks)
    acts = []
    for w in range(weeks):
        for d, dist in enumerate([19, 11, 11, 11]):
            day = base + timedelta(weeks=w, days=d)
            acts.append({
                "id": f"smoke-{w}-{d}",
                "activity_type": "Run",
                "start_date": f"{day.isoformat()}T05:30:00",
                "distance_km": dist,
                "moving_time_min": dist * 5.6,
            })
    acts.append({"id": "smoke-pr", "activity_type": "Run",
                 "start_date": f"{(today - timedelta(days=6)).isoformat()}T08:00:00",
                 "distance_km": 5.0, "moving_time_min": 21.5})
    return acts


def main() -> int:
    print(f"Onboarding smoke test against {API}\n")

    # ---- Layer 1: FastAPI directly ----------------------------------- #
    print("Layer 1 — FastAPI health (no agent, no MCP):")
    try:
        h = httpx.get(f"{API}/health", timeout=10).json()
        check("GET /health", h.get("status") == "ok", str(h))
    except Exception as e:
        check("GET /health", False, f"cannot reach API: {e}")
        print("\nAPI isn't reachable — start it: uvicorn api.main:app --port 8000")
        return 1

    # ---- Layer 2: the agent's MCP tools -> FastAPI ------------------- #
    print("\nLayer 2 — MCP tools reach the engine (the agent's exact path, no LLM):")

    races = mcp.list_races()
    check("list_races()", isinstance(races, list) and len(races) >= 1,
          f"{len(races) if isinstance(races, list) else '?'} race(s)")

    race_id = races[0]["id"] if isinstance(races, list) and races else "cape_town"
    race_date = (date.today() + timedelta(weeks=30)).isoformat()
    activities = _synthetic_activities()

    preview = mcp.onboard_athlete(
        name="Smoke Test", race_id=race_id, race_date=race_date,
        training_days_committed=5, activities=activities, preview=True,
    )
    ok_preview = isinstance(preview, dict) and "assessment" in preview \
        and not preview.get("error")
    check("onboard_athlete(preview=True)", ok_preview,
          f"entry {preview.get('assessment', {}).get('entry_phase')}, "
          f"peak {preview.get('assessment', {}).get('target_peak_km')} km"
          if ok_preview else str(preview))

    committed = mcp.onboard_athlete(
        name="Smoke Test", race_id=race_id, race_date=race_date,
        training_days_committed=5, activities=activities, preview=False,
    )
    aid = committed.get("athlete_id") if isinstance(committed, dict) else None
    check("onboard_athlete(commit)", bool(aid), f"athlete_id={aid}")

    if aid:
        prof = mcp.get_athlete(aid)
        check("get_athlete()", isinstance(prof, dict) and prof.get("id") == aid,
              f"vdot {prof.get('vdot')}" if isinstance(prof, dict) else str(prof))

        today = mcp.get_today(aid)
        check("get_today()", isinstance(today, dict) and not today.get("error"),
              today.get("today_session", {}).get("description", today.get("message", ""))
              if isinstance(today, dict) else str(today))

        paces = mcp.get_paces(aid)
        check("get_paces()", isinstance(paces, dict) and "paces" in paces,
              f"E {paces['paces']['E']} M {paces['paces']['M']}"
              if isinstance(paces, dict) and "paces" in paces else str(paces))

    # ---- Verdict ----------------------------------------------------- #
    print()
    if _fails == 0:
        print("ALL PASS — the backend + MCP adapter work end to end.")
        print("If the live AGENT still won't onboard, it's the LLM not calling")
        print("tools (check SOUL.md is loaded), NOT connectivity. The plumbing is proven.")
        return 0
    print(f"{_fails} check(s) FAILED — fix the backend/adapter before involving the agent.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
