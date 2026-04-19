"""
Mobile app API — endpoints used exclusively by the Virgin Race white-label app.

Exposes:
  GET  /mobile/vo2x          — compute VO2X from a race result
  POST /mobile/coach         — enriched coach-chat proxy to n8n (keeps webhook URL server-side)
"""
from __future__ import annotations

import logging
import os
from datetime import date

import httpx
import random
import string

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/mobile", tags=["mobile"])
_log = logging.getLogger(__name__)

N8N_CHAT_WEBHOOK = os.getenv("N8N_CHAT_WEBHOOK", "")
API_BASE_URL     = os.getenv("API_BASE_URL", "http://localhost:8000")


# ── Link code helpers ─────────────────────────────────────────────────────────

def _generate_link_code(name: str) -> str:
    """Generate a short human-friendly link code e.g. ANDY-4821."""
    prefix = ''.join(c for c in name.upper() if c.isalpha())[:4].ljust(4, 'X')
    digits = ''.join(random.choices(string.digits, k=4))
    return f"{prefix}-{digits}"


# ── VO2X from race result ─────────────────────────────────────────────────────

@router.get("/vo2x")
async def compute_vo2x_from_race(
    distance_km:  float = Query(..., description="Race distance in km"),
    time_minutes: float = Query(..., description="Finish time in minutes"),
):
    """Convert a race result to a Daniels VO2X score."""
    from coach_core.engine.adaptation import calculate_vo2x_from_race
    vo2x = calculate_vo2x_from_race(distance_km, time_minutes)
    return {"vo2x": round(vo2x, 1)}


# ── Link code: look up athlete by code ───────────────────────────────────────

@router.get("/athlete/by-code/{code}")
async def get_athlete_by_link_code(code: str):
    """
    Look up an athlete by their link code (generated in the Telegram bot via /mycode).
    Returns the athlete's telegram_id and name so the mobile app can adopt it as its
    own athlete identifier — no new record is created.
    """
    from sqlalchemy import select
    from coach_core.database import get_db
    from coach_core.models import Athlete

    code = code.strip().upper()

    async for db in get_db():
        result = await db.execute(select(Athlete).where(Athlete.link_code == code))
        athlete = result.scalar_one_or_none()

        if not athlete:
            raise HTTPException(status_code=404, detail="Code not found — check the code and try again")

        return {
            "telegram_id":    athlete.telegram_id,
            "name":           athlete.name,
            "race_name":      athlete.race_name,
            "race_date":      str(athlete.race_date) if athlete.race_date else None,
            "race_distance":  athlete.race_distance,
            "vo2x":           athlete.vo2x,
            "training_profile": athlete.training_profile,
        }


# ── Mobile coach chat proxy ───────────────────────────────────────────────────

class MobileCoachRequest(BaseModel):
    athlete_id: str   # The athlete's telegram_id equivalent (UUID stored on device)
    question:   str


@router.post("/coach")
async def mobile_coach_chat(data: MobileCoachRequest):
    """
    Enriches the user's question with full athlete + plan context,
    forwards to n8n, and returns the AI reply.

    The n8n webhook URL is kept server-side — never exposed to the mobile client.
    """
    payload = await _build_payload(data.athlete_id, data.question)
    reply   = await _call_n8n(payload)
    return {"reply": reply}


# ── Payload builder (mirrors coach_chat.py logic) ─────────────────────────────

async def _build_payload(athlete_id: str, question: str) -> dict:
    payload: dict = {
        "user_question": question,
        "grounding_instruction": (
            "IMPORTANT: Answer using ONLY the athlete data fields provided in this payload. "
            "Do NOT fall back to generic training plan descriptions. "
            "Use week_num, phase_name, taper_start_week, taper_starts_in_weeks, "
            "taper_start_date and today_date to give precise, personalised answers. "
            "If a field is missing, say so rather than guessing."
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            athlete_r, week_r = await _gather_safe(
                client.get(f"{API_BASE_URL}/athlete/{athlete_id}"),
                client.get(f"{API_BASE_URL}/plan/{athlete_id}/current"),
            )

        athlete = None
        if athlete_r and athlete_r.status_code == 200:
            athlete = athlete_r.json()
            payload["athlete_name"]     = athlete.get("name", "Runner")
            payload["race_name"]        = athlete.get("race_name") or "your race"
            payload["training_profile"] = athlete.get("training_profile", "conservative")
            payload["race_date"]        = str(athlete.get("race_date") or "")

            vo2x = athlete.get("vo2x")
            if vo2x:
                try:
                    from coach_core.engine.paces import calculate_paces, format_pace
                    p = calculate_paces(vo2x)
                    payload["vo2x"]           = vo2x
                    payload["easy_pace"]      = format_pace(p.easy_min_per_km)
                    payload["marathon_pace"]  = format_pace(p.marathon_min_per_km)
                    payload["threshold_pace"] = format_pace(p.threshold_min_per_km)
                    payload["interval_pace"]  = format_pace(p.interval_min_per_km)
                except Exception:
                    pass

            preset_race_id = athlete.get("preset_race_id")
            if preset_race_id or vo2x:
                try:
                    from coach_core.engine.race_knowledge import get_race_context
                    race_ctx = get_race_context(
                        preset_race_id=preset_race_id,
                        vo2x=vo2x,
                        race_date_str=str(athlete.get("race_date") or ""),
                    )
                    payload["race_knowledge_text"] = race_ctx["knowledge_text"]
                    payload["checkpoint_summary"]  = race_ctx["checkpoint_summary"]
                    payload["race_display_name"]   = race_ctx["race_display_name"]
                    if race_ctx["race_display_name"] != "your race":
                        payload["race_name"] = race_ctx["race_display_name"]
                except Exception as e:
                    _log.warning(f"mobile coach: RAG context failed — {e}")
                    payload["race_knowledge_text"] = ""
                    payload["checkpoint_summary"]  = ""

        if week_r and week_r.status_code == 200:
            week    = week_r.json()
            total   = week.get("total_weeks")
            current = week.get("week_number", 1)
            payload["phase_num"]     = week.get("phase", 1)
            payload["phase_name"]    = _phase_label(week.get("phase", 1))
            payload["week_num"]      = current
            payload["total_weeks"]   = total
            payload["weeks_to_race"] = max(0, total - current) if total else None
            payload["total_vol_km"]  = week.get("planned_volume_km", 0)

            try:
                from datetime import timedelta
                from coach_core.engine.phases import get_phases
                from coach_core.engine.volume import get_taper_weeks

                if total:
                    race_dist         = athlete.get("race_distance", "marathon") if athlete else "marathon"
                    phases            = get_phases(total)
                    taper_len         = get_taper_weeks(race_dist)
                    weeks_to_peak     = phases.phase_I + phases.phase_II + phases.phase_III
                    taper_start_week      = weeks_to_peak + phases.phase_IV - taper_len + 1
                    taper_starts_in_weeks = max(0, taper_start_week - current)
                    today             = date.today()
                    taper_start_date  = today + timedelta(weeks=taper_starts_in_weeks)

                    payload["taper_length_weeks"]    = taper_len
                    payload["taper_start_week"]      = taper_start_week
                    payload["taper_starts_in_weeks"] = taper_starts_in_weeks
                    payload["taper_start_date"]      = taper_start_date.strftime("%d %b %Y")
                    payload["today_date"]            = today.strftime("%d %b %Y")
                    payload["in_taper"]              = current >= taper_start_week
            except Exception as e:
                _log.warning(f"mobile coach: taper computation failed — {e}")

    except Exception as e:
        _log.warning(f"mobile coach: context fetch failed — {e}")

    return payload


async def _gather_safe(*coros):
    import asyncio
    results = await asyncio.gather(*coros, return_exceptions=True)
    return [None if isinstance(r, Exception) else r for r in results]


async def _call_n8n(payload: dict) -> str:
    if not N8N_CHAT_WEBHOOK:
        return "Coach chat is not configured on this server."

    timeout = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(N8N_CHAT_WEBHOOK, json=payload)
            if r.status_code == 200:
                data  = r.json()
                reply = data.get("output") or data.get("coached_message") or data.get("text", "")
                if reply:
                    return reply
    except httpx.TimeoutException:
        _log.warning("mobile coach: n8n timed out after 90 s")
    except Exception as e:
        _log.warning(f"mobile coach: n8n call failed — {e}")

    return "I couldn't reach the coaching service right now. Please try again in a moment."


def _phase_label(phase: int) -> str:
    return {1: "Base", 2: "Repetitions", 3: "Intervals", 4: "Threshold"}.get(phase, "Training")
