"""
Admin endpoints.

All endpoints require:  X-Admin-Key: <ADMIN_SECRET from .env>

POST   /admin/broadcast           — send message to all athletes on Telegram
GET    /admin/stats               — platform stats
GET    /admin/athletes            — list all athletes with key fields
DELETE /admin/athletes/{id}       — delete athlete + all their logs/history
PATCH  /admin/athletes/{id}/vo2x  — update VO2X (also writes VO2XHistory record)
"""
from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from coach_core.database import get_db
from coach_core.models import Athlete, RunLog, VO2XHistory

router = APIRouter(prefix="/admin", tags=["admin"])

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured.")
    return token


def _check_admin_key(x_admin_key: str = Header(...)) -> None:
    secret = os.getenv("ADMIN_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET not set in .env")
    if x_admin_key != secret:
        raise HTTPException(status_code=401, detail="Invalid admin key.")


class BroadcastRequest(BaseModel):
    message: str                    # HTML-formatted text to send
    image_url: Optional[str] = None # optional photo URL (will send as photo + caption)
    parse_mode: str = "HTML"        # always HTML — consistent with system


class BroadcastResponse(BaseModel):
    total_athletes: int
    sent: int
    failed: int
    failed_ids: list[str]


@router.post("/broadcast", response_model=BroadcastResponse)
async def broadcast(
    body: BroadcastRequest,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """
    Send a message to every athlete on the platform.

    Usage:
      POST /admin/broadcast
      Headers: X-Admin-Key: your_secret
      Body: { "message": "<b>Big news!</b>\n\nCheck this out...", "image_url": null }

    Rate limiting: 0.04s delay between sends (25 msg/sec, safely under Telegram's 30/sec limit).
    Large platforms: for > 5000 users consider running this as a background task.
    """
    # Fetch all telegram_ids
    result = await db.execute(select(Athlete.telegram_id))
    telegram_ids = [row[0] for row in result.fetchall()]

    if not telegram_ids:
        return BroadcastResponse(total_athletes=0, sent=0, failed=0, failed_ids=[])

    token = _get_bot_token()
    sent = 0
    failed = 0
    failed_ids: list[str] = []

    async with httpx.AsyncClient(timeout=10) as client:
        for tid in telegram_ids:
            try:
                if body.image_url:
                    # Send photo with caption
                    url = TELEGRAM_API.format(token=token, method="sendPhoto")
                    payload = {
                        "chat_id": tid,
                        "photo": body.image_url,
                        "caption": body.message,
                        "parse_mode": body.parse_mode,
                    }
                else:
                    # Text only
                    url = TELEGRAM_API.format(token=token, method="sendMessage")
                    payload = {
                        "chat_id": tid,
                        "text": body.message,
                        "parse_mode": body.parse_mode,
                    }

                r = await client.post(url, json=payload)

                if r.status_code == 200 and r.json().get("ok"):
                    sent += 1
                else:
                    failed += 1
                    failed_ids.append(tid)

            except Exception:
                failed += 1
                failed_ids.append(tid)

            # Respect Telegram rate limit — 25 msg/sec
            await asyncio.sleep(0.04)

    return BroadcastResponse(
        total_athletes=len(telegram_ids),
        sent=sent,
        failed=failed,
        failed_ids=failed_ids,
    )


@router.get("/stats")
async def stats(
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """Quick platform stats — total athletes, plan type breakdown."""
    from sqlalchemy import func
    from coach_core.models import Athlete

    result = await db.execute(select(Athlete.plan_type, func.count()).group_by(Athlete.plan_type))
    rows = result.fetchall()
    breakdown = {row[0]: row[1] for row in rows}
    total = sum(breakdown.values())

    return {
        "total_athletes": total,
        "full_plan": breakdown.get("full", 0),
        "c25k": breakdown.get("c25k", 0),
    }


# ── LIST ALL ATHLETES ─────────────────────────────────────────────────────────

@router.get("/athletes")
async def list_athletes(
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """Return all athletes with key fields for the admin dashboard."""
    result = await db.execute(
        select(Athlete).order_by(Athlete.created_at.desc())
    )
    athletes = result.scalars().all()

    rows = []
    for a in athletes:
        # Count run logs
        log_count_result = await db.execute(
            select(RunLog).where(RunLog.athlete_id == a.id)
        )
        log_count = len(log_count_result.scalars().all())

        rows.append({
            "id":                 a.id,
            "name":               a.name,
            "telegram_id":        a.telegram_id,
            "plan_type":          a.plan_type,
            "vo2x":               a.vo2x,
            "race_name":          a.race_name,
            "race_distance":      a.race_distance,
            "race_date":          a.race_date.isoformat() if a.race_date else None,
            "preset_race_id":     a.preset_race_id,
            "long_run_day":       a.long_run_day,
            "quality_day":        a.quality_day,
            "training_profile":   a.training_profile,
            "c25k_week":          a.c25k_week,
            "c25k_completed":     a.c25k_completed,
            "streak_weeks":       a.streak_weeks,
            "total_badges":       a.total_badges,
            "link_code":          a.link_code,
            "run_log_count":      log_count,
            "created_at":         a.created_at.isoformat() if a.created_at else None,
        })

    return {"athletes": rows, "total": len(rows)}


# ── DELETE ATHLETE ────────────────────────────────────────────────────────────

@router.delete("/athletes/{athlete_id}")
async def delete_athlete(
    athlete_id: int,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """
    Permanently delete an athlete and all their associated data.
    This resets their onboarding — they will be prompted to /start again in Telegram.
    """
    # Verify athlete exists
    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found.")

    name = athlete.name
    telegram_id = athlete.telegram_id

    # Delete cascade: run logs → VO2X history → athlete
    await db.execute(delete(RunLog).where(RunLog.athlete_id == athlete_id))
    await db.execute(delete(VO2XHistory).where(VO2XHistory.athlete_id == athlete_id))
    await db.execute(delete(Athlete).where(Athlete.id == athlete_id))
    await db.commit()

    return {
        "deleted": True,
        "athlete_id": athlete_id,
        "name": name,
        "telegram_id": telegram_id,
    }


# ── UPDATE VO2X ───────────────────────────────────────────────────────────────

class VO2XUpdateRequest(BaseModel):
    vo2x: float
    note: Optional[str] = None   # optional reason / note stored in VO2XHistory


@router.patch("/athletes/{athlete_id}/vo2x")
async def update_athlete_vo2x(
    athlete_id: int,
    body: VO2XUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: None = Depends(_check_admin_key),
):
    """
    Override an athlete's VO2X and record the change in VO2XHistory.
    The new VO2X takes effect immediately — their next /plan or /today
    will use updated paces.
    """
    if body.vo2x < 20 or body.vo2x > 85:
        raise HTTPException(status_code=422, detail="VO2X must be between 20 and 85.")

    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail=f"Athlete {athlete_id} not found.")

    old_vo2x = athlete.vo2x
    athlete.vo2x = body.vo2x

    # Record in history
    history_entry = VO2XHistory(
        athlete_id=athlete_id,
        vo2x=body.vo2x,
        source="admin_adjusted",
        effective_date=date.today(),
    )
    db.add(history_entry)
    await db.commit()

    return {
        "athlete_id":  athlete_id,
        "name":        athlete.name,
        "old_vo2x":    old_vo2x,
        "new_vo2x":    body.vo2x,
        "note":        body.note,
    }
