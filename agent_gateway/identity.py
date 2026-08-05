"""
Gateway-side identity.

coach_core currently treats a raw `telegram_id` as identity with no
authentication, so the gateway does NOT accept one from the client. Instead a
visitor proves who they are once with a Telegram link code, and the gateway
issues its own signed bearer token. Every later request carries that token, and
the athlete id is read out of the verified token — never from user input.

Tokens are HMAC-signed and stateless: no new table, no shared session store, and
nothing to migrate in coach_core.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from agent_gateway.config import GATEWAY_SECRET, SESSION_TTL_HOURS

# A per-process key when none is configured. Fine for a demo; tokens are
# invalidated by a restart, which is why GATEWAY_SECRET should be set anywhere
# real. Never falls back to a hardcoded constant.
_SECRET: bytes = (GATEWAY_SECRET or secrets.token_urlsafe(48)).encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(telegram_id: str, ttl_hours: int = SESSION_TTL_HOURS) -> str:
    """Mint a signed session token binding this browser to one athlete."""
    payload = {
        "tid": str(telegram_id),
        "exp": int(time.time()) + ttl_hours * 3600,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64e(hmac.new(_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> Optional[str]:
    """Return the athlete's telegram_id, or None if the token is bad/expired."""
    if not token or token.count(".") != 1:
        return None
    body, sig = token.split(".", 1)
    expected = _b64e(hmac.new(_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    # Constant-time compare — a fast-fail loop here would leak the signature.
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    tid = payload.get("tid")
    return str(tid) if tid else None
