"""
Athlete link codes — the shareable identifier front-ends connect with.

Every athlete gets a code the moment their profile is created, so any client
(mobile app, web gateway, partner front-end) can identify an athlete without
that athlete first having to run /mycode in Telegram.

This module is the single source of truth for code generation. The Telegram
bot imports from here rather than carrying its own copy, so the two can never
drift apart into two different code formats.

Format: NAME-XXXXXXXX  e.g. ANDY-7K2M9QX4
  * 4-character name prefix so a code is recognisably the athlete's own
  * 8 random characters from an unambiguous alphabet (~8.5 x 10^11 codes)

The alphabet omits 0/O and 1/I/L so a code survives being read aloud, typed
off a screenshot, or written down.
"""
from __future__ import annotations

import logging
import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_BODY_LENGTH = 8

# The superseded weak format (e.g. ANDY-4821): 4 digits is only 10,000
# combinations, which is trivially enumerable. Kept so existing codes can be
# recognised and upgraded rather than silently trusted.
LEGACY_CODE_RE = re.compile(r"^[A-Z]{4}-\d{4}$")


def generate_link_code(name: str | None, body_length: int = CODE_BODY_LENGTH) -> str:
    """Build one candidate code. Uniqueness is the caller's job."""
    letters = "".join(c for c in (name or "").upper() if c.isalpha())
    prefix = letters[:4].ljust(4, "X") if letters else "TR3D"
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(body_length))
    return f"{prefix}-{body}"


def is_legacy_code(code: str | None) -> bool:
    """True if this is an old 4-digit code that should be upgraded."""
    return bool(code and LEGACY_CODE_RE.match(code))


async def assign_link_code(db: AsyncSession, athlete, attempts: int = 8) -> str:
    """Give `athlete` a unique link code and return it.

    Sets the attribute but does not commit — the caller owns the transaction.

    Collisions are checked against the table before assigning. If every attempt
    somehow collides we widen the random body rather than giving up, so this
    function always returns a usable code and never leaves link_code as None.

    The database's UNIQUE constraint remains the final guard: two concurrent
    inserts could in principle pick the same code between the check and the
    commit, but at ~8.5e11 candidates that is vanishingly unlikely, and the
    constraint turns it into a loud error rather than a silent duplicate.
    """
    from coach_core.models import Athlete  # local import avoids a cycle

    for _ in range(attempts):
        candidate = generate_link_code(athlete.name)
        existing = await db.execute(
            select(Athlete.id).where(Athlete.link_code == candidate)
        )
        if existing.scalar_one_or_none() is None:
            athlete.link_code = candidate
            return candidate

    # Astronomically unlikely. Widen the body instead of returning None.
    fallback = generate_link_code(athlete.name, body_length=14)
    _log.warning(
        "link code: %d collisions for %r, falling back to a wider code",
        attempts, athlete.name,
    )
    athlete.link_code = fallback
    return fallback


async def backfill_link_codes(session_factory) -> int:
    """Give a code to every athlete created before codes were universal.

    Idempotent: once no athlete has a NULL code this is a no-op, so it is safe
    to run on every startup. Existing codes — including legacy ones — are left
    alone here; the bot upgrades those lazily so a code an athlete has already
    saved or shared keeps working.

    Returns the number of athletes backfilled.
    """
    from coach_core.models import Athlete

    async with session_factory() as db:
        result = await db.execute(
            select(Athlete).where(Athlete.link_code.is_(None))
        )
        athletes = result.scalars().all()
        if not athletes:
            return 0

        for athlete in athletes:
            await assign_link_code(db, athlete)
        await db.commit()

    _log.info("link codes: backfilled %d athlete(s)", len(athletes))
    return len(athletes)
