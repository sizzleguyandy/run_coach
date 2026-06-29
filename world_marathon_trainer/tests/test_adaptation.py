"""Adaptation loop tests — pure engine logic + API wiring.

Run: python -m pytest tests/test_adaptation.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine        # noqa: E402
from sqlalchemy.orm import sessionmaker      # noqa: E402
from fastapi.testclient import TestClient    # noqa: E402

from engine.adaptation import evaluate_week  # noqa: E402
from engine.models import Session, Week      # noqa: E402

from api.main import app                      # noqa: E402
from api.db import Base, get_session          # noqa: E402
import api.orm  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #
def _week(volume=60.0, long_km=18.0, n_easy=3):
    days = [Session(day="Sat", kind="long", description="Long run",
                    distance_km=long_km)]
    for d in ("Mon", "Tue", "Thu")[:n_easy]:
        days.append(Session(day=d, kind="easy", description="Easy",
                            distance_km=10.0))
    return Week(index=10, phase="3", phase_label="P3", weeks_to_race=8,
                target_volume_km=volume, days=days)


def _run(distance_km, moving_time_min=None, hr=None, suffer=None):
    return SimpleNamespace(
        distance_km=distance_km,
        moving_time_min=moving_time_min or distance_km * 5.5,
        average_heartrate=hr, suffer_score=suffer,
    )


def test_struggling_lowers_vdot():
    week = _week(volume=60.0)
    # only 20km of 60 planned -> 33% compliance
    runs = [_run(20.0)]
    d = evaluate_week(week, runs, runs, current_vdot=45.0, in_taper=False)
    assert d.vdot_after < 45.0
    assert d.volume_compliance < 0.65
    assert any("Eas" in n or "eas" in n for n in d.notes)


def test_hard_effort_raises_vdot():
    week = _week(volume=60.0)
    runs = [_run(20.0), _run(20.0), _run(20.0)]   # full volume
    # a sharp recent 10k (40 min -> ~VDOT 48) should pull a VDOT-45 athlete up
    recent = runs + [_run(10.0, moving_time_min=40.0)]
    d = evaluate_week(week, runs, recent, current_vdot=45.0, in_taper=False)
    assert d.vdot_after > 45.0
    assert d.best_recent_vdot > 45.0


def test_taper_never_raises():
    week = _week(volume=30.0)
    week.phase = "5"
    runs = [_run(10.0), _run(10.0), _run(10.0)]
    recent = runs + [_run(10.0, moving_time_min=38.0)]   # very sharp
    d = evaluate_week(week, runs, recent, current_vdot=45.0, in_taper=True)
    assert d.vdot_after == 45.0          # held despite the fast effort


def test_missed_long_run_flagged():
    week = _week(volume=60.0, long_km=20.0)
    # full-ish volume but no long run (all short)
    runs = [_run(12.0), _run(12.0), _run(12.0), _run(12.0), _run(12.0)]
    d = evaluate_week(week, runs, runs, current_vdot=45.0, in_taper=False)
    assert not d.long_run_done
    assert any("Long run" in f for f in d.flags)


def test_big_drop_flagged():
    week = _week(volume=60.0)
    runs = [_run(5.0)]   # ~8% of plan
    d = evaluate_week(week, runs, runs, current_vdot=45.0, in_taper=False)
    assert any("volume drop" in f for f in d.flags)


def test_on_track_holds():
    week = _week(volume=60.0)
    runs = [_run(20.0), _run(20.0), _run(20.0)]   # full, no sharp effort
    d = evaluate_week(week, runs, runs, current_vdot=45.0, in_taper=False)
    assert d.vdot_after == 45.0
    assert d.best_recent_vdot <= 45.0 or d.vdot_after == 45.0


# --------------------------------------------------------------------------- #
# API wiring
# --------------------------------------------------------------------------- #
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
_engine = create_engine(
    f"sqlite:///{_TMP.name}", connect_args={"check_same_thread": False}
)
_TestSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(_engine)


def _override():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_session] = _override
client = TestClient(app)


def _make_athlete(race_in_weeks=20):
    race_date = (date.today() + timedelta(weeks=race_in_weeks)).isoformat()
    r = client.post("/athlete", json={
        "name": "Andrew", "race_id": "cape_town", "race_date": race_date,
        "current_weekly_km": 55.0, "training_days_per_week": 5,
        "can_run_10k_continuous": True,
        "recent_race_distance_km": 10.0, "recent_race_time_min": 45.0,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_adapt_no_completed_week():
    # race far out, plan starts in the future -> nothing completed yet
    a = _make_athlete(race_in_weeks=30)
    r = client.post(f"/athlete/{a['id']}/adapt")
    assert r.status_code == 200
    assert r.json()["adapted"] is False


def test_adapt_applies_and_logs():
    a = _make_athlete(race_in_weeks=18)   # block starts ~now
    # Build the plan to learn the first completed week's dates.
    plan = client.post(f"/athlete/{a['id']}/plan").json()
    # find a week that has already ended
    today = date.today()
    completed = [w for w in plan["weeks"] if w["end_date"] and w["end_date"] <= today.isoformat()]
    if not completed:
        # nudge as_of to just after week 1 ends
        wk1_end = plan["weeks"][0]["end_date"]
        as_of = wk1_end
    else:
        as_of = max(w["end_date"] for w in completed)

    week = next(w for w in plan["weeks"] if w["end_date"] == as_of)
    # sync runs covering ~full planned volume inside that week, plus a sharp 10k
    start = date.fromisoformat(week["start_date"])
    acts = []
    for i in range(3):
        acts.append({
            "id": f"w-{i}",
            "start_date": f"{(start + timedelta(days=i)).isoformat()}T05:30:00",
            "distance_km": week["target_volume_km"] / 3,
            "moving_time_min": (week["target_volume_km"] / 3) * 5.5,
        })
    acts.append({
        "id": "sharp", "start_date": f"{(start + timedelta(days=4)).isoformat()}T05:30:00",
        "distance_km": 10.0, "moving_time_min": 42.0,
    })
    client.post(f"/athlete/{a['id']}/sync", json={"activities": acts,
                                                  "recompute_weekly_km": False})

    r = client.post(f"/athlete/{a['id']}/adapt", params={"as_of": as_of})
    assert r.status_code == 200
    body = r.json()
    assert body["adapted"] is True
    assert "vdot_after" in body

    # logged
    logs = client.get(f"/athlete/{a['id']}/adaptations").json()
    assert len(logs) >= 1
    assert logs[0]["week_index"] == body["week_index"]
