import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from coach_core.database import init_db
from coach_core.routers import athlete, plan, log, weather, admin, predict, strength
from coach_core.routers import mobile


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Tr3d Coaching Engine",
    description="Daniels-based deterministic running coach engine",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS — configurable via ALLOWED_ORIGINS env var (comma-separated) ─────────
# Default "*" is kept for local dev; set ALLOWED_ORIGINS in production.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_allowed_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── All routes are versioned under /v1 ────────────────────────────────────────
V1 = "/v1"

app.include_router(athlete.router,  prefix=V1)
app.include_router(plan.router,     prefix=V1)
app.include_router(log.router,      prefix=V1)
app.include_router(weather.router,  prefix=V1)
app.include_router(admin.router,    prefix=V1)
app.include_router(predict.router,  prefix=V1)
app.include_router(strength.router, prefix=V1)
app.include_router(mobile.router,   prefix=V1)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "1.0.0"}
