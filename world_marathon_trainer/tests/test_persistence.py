"""Persistence + Strava sync tests. Uses an isolated temp SQLite DB.

Run: python -m pytest tests/test_persistence.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine        # noqa: E402
from sqlalchemy.orm import sessionmaker      # noqa: E402
from fastapi.testclient import TestClient    # noqa: E402

from api.main import app                      # noqa: E402
from api.db import Base, get_session          # noqa: E402
import api.orm  # noqa: E402,F401  (register models on Base)

# Isolated throwaway DB for this test module, wired in via dependency override.
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
_engine = create_engine(
    f"sqlite:///{_TMP.name}", connect_args={"check_same_thread": False}
)
_TestSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(_engine)


def _override_session():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_session] = _override_session

client = TestClient(app)

_NEW = {
    "name": "Andrew",
    "race_id": "cape_town",
    "race_date": "2027-05-23",
    "current_weekly_km": 35.0,
    "training_days_per_week": 5,
    "can_run_10k_continuous": True,
    "recent_race_distance_km": 10.0,
    "recent_race_time_min": 52.0,
}


def _create():
    r = client.post("/athlete", json=_NEW)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_returns_id_and_vdot():
    a = _create()
    assert a["id"]
    assert a["vdot"] is not None        # cached on create
    assert a["name"] == "Andrew"


def test_create_unknown_race_404():
    bad = dict(_NEW, race_id="atlantis")
    assert client.post("/athlete", json=bad).status_code == 404


def test_get_and_list():
    a = _create()
    assert client.get(f"/athlete/{a['id']}").status_code == 200
    assert any(x["id"] == a["id"] for x in client.get("/athletes").json())


def test_get_missing_404():
    assert client.get("/athlete/nope").status_code == 404


def test_update():
    a = _create()
    r = client.patch(f"/athlete/{a['id']}", json={"training_days_per_week": 6})
    assert r.status_code == 200
    assert r.json()["training_days_per_week"] == 6


def test_plan_from_stored_athlete():
    a = _create()
    r = client.post(f"/athlete/{a['id']}/plan")
    assert r.status_code == 200
    weeks = r.json()["weeks"]
    assert weeks[-1]["weeks_to_race"] == 1


def test_sync_inserts_and_dedups():
    a = _create()
    payload = {
        "activities": [
            {
                "id": "act-1",
                "start_date": "2026-06-22T05:30:00Z",
                "activity_type": "Run",
                "distance_km": 12.0,
                "moving_time_min": 60.0,
            },
            {
                "id": "act-2",
                "start_date": "2026-06-24T05:30:00Z",
                "activity_type": "Run",
                "distance_km": 8.0,
                "moving_time_min": 40.0,
            },
        ]
    }
    r = client.post(f"/athlete/{a['id']}/sync", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2 and body["updated"] == 0

    # Re-sync the same ids -> updates, no duplicates.
    r2 = client.post(f"/athlete/{a['id']}/sync", json=payload)
    assert r2.json()["created"] == 0 and r2.json()["updated"] == 2

    acts = client.get(f"/athlete/{a['id']}/activities").json()
    assert len(acts) == 2


def test_sync_derives_pace_and_recomputes_volume():
    a = _create()
    payload = {
        "activities": [
            {"id": "p-1", "start_date": "2026-06-22T05:30:00Z",
             "distance_km": 10.0, "moving_time_min": 50.0},
        ],
        "recompute_weekly_km": True,
    }
    r = client.post(f"/athlete/{a['id']}/sync", json=payload)
    assert r.status_code == 200
    assert "current_weekly_km" in r.json()

    act = client.get(f"/athlete/{a['id']}/activities").json()[0]
    assert act["average_pace_min_per_km"] == 5.0     # 50 min / 10 km, derived


def test_sync_missing_athlete_404():
    payload = {"activities": []}
    assert client.post("/athlete/ghost/sync", json=payload).status_code == 404


def test_delete_cascades():
    a = _create()
    client.post(f"/athlete/{a['id']}/sync", json={
        "activities": [{"id": "d-1", "start_date": "2026-06-22T05:30:00Z",
                        "distance_km": 5.0, "moving_time_min": 25.0}]
    })
    assert client.delete(f"/athlete/{a['id']}").status_code == 204
    assert client.get(f"/athlete/{a['id']}").status_code == 404
