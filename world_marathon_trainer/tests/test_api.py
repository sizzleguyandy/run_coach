"""API tests using FastAPI's TestClient. Run: python -m pytest tests/test_api.py -q"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient   # noqa: E402
from api.main import app                     # noqa: E402

client = TestClient(app)

_ATHLETE = {
    "today": "2026-06-23",
    "race_id": "cape_town",
    "race_date": "2027-05-23",
    "current_weekly_km": 35.0,
    "training_days_per_week": 5,
    "can_run_10k_continuous": True,
    "recent_race_distance_km": 10.0,
    "recent_race_time_min": 52.0,
    "name": "Test Athlete",
}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["races_loaded"] >= 1


def test_list_races_includes_cape_town():
    r = client.get("/races")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()]
    assert "cape_town" in ids


def test_get_race_profile():
    r = client.get("/races/cape_town")
    assert r.status_code == 200
    assert r.json()["identity"]["id"] == "cape_town"


def test_get_unknown_race_404():
    assert client.get("/races/atlantis").status_code == 404


def test_vdot_paces():
    r = client.get("/vdot/50")
    assert r.status_code == 200
    body = r.json()
    assert set(body["paces"]) == {"E", "M", "T", "I", "R"}
    assert "Marathon" in body["race_times"]
    assert "VDOT" in body["card"]


def test_vdot_out_of_range():
    assert client.get("/vdot/200").status_code == 400


def test_vdot_from_race():
    r = client.post("/vdot/from-race", json={"distance_km": 10, "time_min": 45})
    assert r.status_code == 200
    assert 44 <= r.json()["vdot"] <= 48


def test_plan_build():
    r = client.post("/plan", json=_ATHLETE)
    assert r.status_code == 200
    body = r.json()
    assert body["assessment"]["entry_phase"] in ("1a", "1b", "2")
    # continuous, ends at race week
    weeks = body["weeks"]
    assert weeks[-1]["weeks_to_race"] == 1
    block = [w for w in weeks if w["weeks_to_race"] is not None]
    assert len(block) == 18


def test_plan_unknown_race_404():
    bad = dict(_ATHLETE, race_id="atlantis")
    assert client.post("/plan", json=bad).status_code == 404


def test_assessment_only():
    r = client.post("/plan/assessment", json=_ATHLETE)
    assert r.status_code == 200
    body = r.json()
    assert "target_peak_km" in body
    assert "weeks" not in body  # assessment endpoint returns no weeks


def test_plan_validation_rejects_bad_days():
    bad = dict(_ATHLETE, training_days_per_week=2)   # < 3
    assert client.post("/plan", json=bad).status_code == 422
