"""
Domain events — channel-agnostic notifications out of the engine.

The engine knows *that* something notable happened to an athlete (their VO2X
went up, they earned a badge). It deliberately does not know *how* an athlete
is reached — Telegram, mobile push, email, or nothing at all. That decision
belongs to whatever delivery layer is listening.

Previously `coach_core` imported `telegram_bot` directly to send a level-up
message. That was a backwards dependency: the engine reached into a UI layer,
which meant coach_core could not run without the bot's source code and a
Telegram token present, and tied the engine to one channel forever.

Now the engine POSTs a generic JSON event to `NOTIFY_WEBHOOK_URL` and forgets
about it. Any delivery layer can subscribe. If the variable is unset, emitting
is a silent no-op, so the engine runs perfectly well with no notifier at all.

Event shape:

    {
      "event":       "vo2x_levelup",
      "athlete_ref": "12345",
      "occurred_at": "2026-06-16T10:00:00+00:00",
      "data":        { ... event-specific fields ... }
    }
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

_log = logging.getLogger(__name__)

NOTIFY_WEBHOOK_URL: str = os.getenv("NOTIFY_WEBHOOK_URL", "").strip()
NOTIFY_TIMEOUT_SECONDS: float = float(os.getenv("NOTIFY_TIMEOUT_SECONDS", "8"))

# Strong references to in-flight tasks. Without this, asyncio only holds a weak
# reference and a fire-and-forget task can be garbage-collected mid-flight.
_in_flight: set[asyncio.Task] = set()


def build_event(event: str, athlete_ref: str, **data: Any) -> dict:
    """Build the event envelope. Separated out so it is easy to assert on."""
    return {
        "event": event,
        "athlete_ref": str(athlete_ref),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


async def _post(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=NOTIFY_TIMEOUT_SECONDS) as client:
            r = await client.post(NOTIFY_WEBHOOK_URL, json=payload)
            if r.status_code >= 400:
                _log.warning(
                    "notify: %s returned %s", payload.get("event"), r.status_code
                )
    except Exception as e:
        # A delivery failure must never affect the engine's own work.
        _log.warning("notify: %s failed — %s", payload.get("event"), e)


def emit(event: str, athlete_ref: str, **data: Any) -> bool:
    """Fire-and-forget a domain event. Returns True if it was dispatched.

    Never raises and never blocks the caller: notification is a side channel,
    so a broken or slow notifier must not fail an athlete's adaptation run.
    """
    if not NOTIFY_WEBHOOK_URL:
        return False

    payload = build_event(event, athlete_ref, **data)
    try:
        task = asyncio.create_task(_post(payload))
    except RuntimeError:
        # No running loop (e.g. called from sync context) — drop it rather than
        # blowing up the request.
        _log.debug("notify: no running loop, dropped %s", event)
        return False

    _in_flight.add(task)
    task.add_done_callback(_in_flight.discard)
    return True


# ── Event constructors ─────────────────────────────────────────────────────
# Named helpers keep event names and field shapes consistent across call sites.

def vo2x_levelup(
    athlete_ref: str,
    name: str,
    vo2x_before: float,
    vo2x_after: float,
    source: str,
) -> bool:
    """The athlete's VO2X crossed a whole point — worth celebrating."""
    return emit(
        "vo2x_levelup",
        athlete_ref,
        name=name,
        vo2x_before=vo2x_before,
        vo2x_after=vo2x_after,
        source=source,
    )
