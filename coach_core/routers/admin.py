"""
Admin broadcast endpoint.

POST /admin/broadcast
  - Requires X-Admin-Key header matching ADMIN_SECRET in .env
  - Sends a message to every athlete's Telegram account
  - Respects Telegram rate limit (30 msg/sec) via asyncio.sleep

No core logic changes. No model changes.
Uses existing athletes table (telegram_id column only).
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coach_core.database import get_db
from coach_core.models import Athlete

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
