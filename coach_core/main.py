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
    title="Run Coach API",
    description="Daniels-based deterministic running coach engine",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS — required for the mobile WebView app and any browser-based clients ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(athlete.router)
app.include_router(plan.router)
app.include_router(log.router)
app.include_router(weather.router)
app.include_router(admin.router)
app.include_router(predict.router)
app.include_router(strength.router)
app.include_router(mobile.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
