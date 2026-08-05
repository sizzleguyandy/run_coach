"""
Agent Gateway — FastAPI app.

A separate service in front of coach_core. It:
  * authenticates a visitor once via their Telegram link code and issues its
    own signed session token (coach_core has no auth of its own, so the
    gateway does not accept a raw athlete_ref from the client);
  * runs the coaching agent, which reads coach_core over HTTP, read-only;
  * serves the chat frontend.

coach_core is not modified and not imported. Run this alongside it:

    uvicorn agent_gateway.main:app --port 8100
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent_gateway.agent import CoachAgent
from agent_gateway.config import ALLOWED_ORIGINS, CORE_API_BASE_URL
from agent_gateway.core_client import CoreUnavailable, resolve_link_code
from agent_gateway.identity import issue_token, verify_token
from agent_gateway.tools import bind_athlete

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)

app = FastAPI(
    title="TR3D Agent Gateway",
    description="Agent front-end over the TR3D coaching engine (read-only)",
    version="1.0.0",
)

# Same rule as coach_core: never wildcard origins together with credentials.
if ALLOWED_ORIGINS and ALLOWED_ORIGINS != "*":
    _origins = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    _credentials = True
else:
    _origins = ["*"]
    _credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_agent: CoachAgent | None = None


def _get_agent() -> CoachAgent:
    """Lazily construct the agent so the app can boot without a key set."""
    global _agent
    if _agent is None:
        _agent = CoachAgent()
    return _agent


# ── Auth ───────────────────────────────────────────────────────────────────

async def current_athlete(authorization: str = Header(default="")) -> str:
    """Resolve the athlete from the gateway's own bearer token.

    The athlete id comes out of a signed token, never from the request body —
    that is what stops one visitor reading another athlete's training.
    """
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    athlete_ref = verify_token(token)
    if not athlete_ref:
        raise HTTPException(status_code=401, detail="Sign in with your link code.")
    return athlete_ref


class LinkRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=64)


@app.post("/api/link")
async def link(data: LinkRequest):
    """Exchange a Telegram link code (from /mycode) for a session token."""
    try:
        athlete = await resolve_link_code(data.code)
    except CoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not athlete or not athlete.get("athlete_ref"):
        raise HTTPException(
            status_code=404,
            detail="That code didn't match an athlete. Send /mycode to the bot for a fresh one.",
        )

    return {
        "token": issue_token(str(athlete["athlete_ref"])),
        "name": athlete.get("name"),
        "race_name": athlete.get("race_name"),
    }


# ── Chat ───────────────────────────────────────────────────────────────────

class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list)


@app.post("/api/chat")
async def chat(data: ChatRequest, athlete_ref: str = Depends(current_athlete)):
    """Ask the coaching agent a question about your own training."""
    # Bind the authenticated athlete for this request. Tools read the athlete
    # from here, so nothing in the message body can redirect them.
    bind_athlete(athlete_ref)

    history = [
        {"role": t.role, "content": t.content}
        for t in data.history[-20:]
        if t.role in ("user", "assistant") and t.content.strip()
    ]

    try:
        reply = await _get_agent().reply(history, data.message)
    except Exception as e:
        _log.exception("agent failed for %s", athlete_ref)
        raise HTTPException(
            status_code=502,
            detail=f"The coach couldn't answer just now ({type(e).__name__}).",
        )

    return {"reply": reply}


# ── Meta + frontend ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "core_api": CORE_API_BASE_URL,
        "model_configured": bool(
            os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
        ),
    }


_WEB_DIR = Path(__file__).parent / "web"


@app.get("/")
async def index():
    return FileResponse(_WEB_DIR / "index.html")
