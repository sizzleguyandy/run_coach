"""
Agent Gateway — configuration.

Every value is read from the environment so the gateway can be deployed
separately from coach_core (different service, different host, different
scaling profile) without sharing any code or state with it.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── Upstream: the TR3D coaching engine (read-only consumer) ─────────────────
# The gateway ONLY issues GET requests against this base URL. It never writes
# to coach_core, and never imports coach_core modules.
CORE_API_BASE_URL: str = os.getenv(
    "CORE_API_BASE_URL",
    os.getenv("API_BASE_URL", "http://localhost:8000"),
).rstrip("/")

CORE_TIMEOUT_SECONDS: float = float(os.getenv("CORE_TIMEOUT_SECONDS", "10"))

# ── Anthropic ───────────────────────────────────────────────────────────────
# ANTHROPIC_API_KEY is read by the SDK itself; it is not referenced here.
AGENT_MODEL: str = os.getenv("AGENT_MODEL", "claude-opus-5")
AGENT_EFFORT: str = os.getenv("AGENT_EFFORT", "medium")
AGENT_MAX_TOKENS: int = int(os.getenv("AGENT_MAX_TOKENS", "8000"))
AGENT_MAX_TURNS: int = int(os.getenv("AGENT_MAX_TURNS", "12"))

# ── Gateway identity ────────────────────────────────────────────────────────
# Signing key for gateway-issued session tokens. Generated per-process when
# unset, which is fine for a demo but means tokens die on restart — set
# GATEWAY_SECRET in any real deployment.
GATEWAY_SECRET: str = os.getenv("GATEWAY_SECRET", "")
SESSION_TTL_HOURS: int = int(os.getenv("GATEWAY_SESSION_TTL_HOURS", "720"))

# ── CORS ────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS: str = os.getenv("GATEWAY_ALLOWED_ORIGINS", "").strip()
