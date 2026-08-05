"""
Read-only client for the TR3D coaching engine (coach_core).

This is the ONLY module that talks to coach_core, and it is deliberately
read-only: `_get` is the single transport function and it issues GET requests
exclusively. The gateway therefore cannot mutate an athlete's plan, logs, or
profile no matter what the agent decides to do — the capability simply is not
present in the code.

coach_core is consumed over HTTP rather than by importing its modules, matching
the pattern the Telegram bot already uses ("the bot only calls the API via
httpx — it never imports engine modules directly"). That keeps the gateway
deployable as its own service and keeps coach_core untouched.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from agent_gateway.config import CORE_API_BASE_URL, CORE_TIMEOUT_SECONDS

_log = logging.getLogger(__name__)


class CoreUnavailable(RuntimeError):
    """coach_core could not be reached or returned an unusable response."""


class CoreClient:
    """Read-only view of one athlete's data in coach_core."""

    def __init__(self, athlete_ref: str, base_url: str = CORE_API_BASE_URL):
        self.athlete_ref = athlete_ref
        self._base = base_url.rstrip("/")

    async def _get(self, path: str) -> Optional[Any]:
        """Issue a GET against coach_core. Returns None on 404.

        This is the only outbound call in the gateway's upstream path — there is
        deliberately no _post/_patch/_delete counterpart.
        """
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=CORE_TIMEOUT_SECONDS) as client:
                r = await client.get(url)
        except Exception as e:  # network, DNS, timeout
            _log.warning("core GET %s failed: %s", path, e)
            raise CoreUnavailable(f"Could not reach the coaching engine: {e}") from e

        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            _log.warning("core GET %s -> %s", path, r.status_code)
            raise CoreUnavailable(
                f"The coaching engine returned {r.status_code} for {path}"
            )
        try:
            return r.json()
        except Exception as e:
            raise CoreUnavailable(f"Malformed response from {path}") from e

    # ── Athlete ────────────────────────────────────────────────────────────
    async def athlete(self) -> Optional[dict]:
        return await self._get(f"/athlete/{self.athlete_ref}")

    async def paces(self) -> Optional[dict]:
        return await self._get(f"/athlete/{self.athlete_ref}/paces")

    # ── Plan ───────────────────────────────────────────────────────────────
    async def current_week(self) -> Optional[dict]:
        return await self._get(f"/plan/{self.athlete_ref}/current")

    async def week(self, week_number: int) -> Optional[dict]:
        return await self._get(f"/plan/{self.athlete_ref}/week/{week_number}")

    async def full_plan(self) -> Optional[dict]:
        return await self._get(f"/plan/{self.athlete_ref}")

    # ── Logs ───────────────────────────────────────────────────────────────
    async def week_log_summary(self, week_number: int) -> Optional[dict]:
        return await self._get(
            f"/log/{self.athlete_ref}/week/{week_number}/summary"
        )

    async def month_log_summary(self, year: int, month: int) -> Optional[dict]:
        return await self._get(
            f"/log/{self.athlete_ref}/month/{year}/{month}/summary"
        )

    # ── Weather ────────────────────────────────────────────────────────────
    async def weather(self) -> Optional[dict]:
        return await self._get(f"/weather/{self.athlete_ref}/conditions")


async def resolve_link_code(code: str, base_url: str = CORE_API_BASE_URL) -> Optional[dict]:
    """Exchange a Telegram link code (/mycode) for the athlete it identifies.

    Read-only: this is coach_core's own lookup endpoint, which the gateway uses
    once at sign-in to learn which athlete a visitor is. coach_core rate-limits
    it per IP, so brute-force enumeration is handled upstream.
    """
    url = f"{base_url.rstrip('/')}/mobile/athlete/by-code/{code.strip().upper()}"
    try:
        async with httpx.AsyncClient(timeout=CORE_TIMEOUT_SECONDS) as client:
            r = await client.get(url)
    except Exception as e:
        raise CoreUnavailable(f"Could not reach the coaching engine: {e}") from e

    if r.status_code in (404, 429):
        return None
    if r.status_code >= 400:
        raise CoreUnavailable(f"Link lookup failed ({r.status_code})")
    return r.json()
