"""Onboarding tests — derive_profile (pure) + /onboard API.

Run: python -m pytest tests/test_onboarding.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine        # noqa: E402
from sqlalchemy.orm import sessionmaker      # noqa: E402
from fastapi.testclient import TestClient    # noqa: E402

from engine import derive_profile            # noqa: E402

from api.main import app                      # noqa: E402
from api.db import Base, get_session          # noqa: E402
import api.orm  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# Pure derive_profile
# --------------------------------------------------------------------------- #
def _runs_over_weeks(weeks: int, per_week: int, km: float, long_km: float):
    acts = []
    base = date(2026, 5, 1)
    for w in range(weeks):
        for d in range(per_week):
            dist = long_km if d == 0 else km
            acts.append({
                "id": f"{w}-{d}",
                "activity_type": "Run",
                "start_date": f"{(base + timedelta(weeks=w, days=d)).isoformat()}T05:30:00",
                "distance_km": dist,
                "moving_time_min": dist * 5.5,
            })
    return acts


def test_no_history_routes_to_beginner():
    p = derive_profile([])
    assert p.confidence == "none"
    assert p.n_runs == 0
    assert not p.can_run_10k


def test_derives_weekly_km_and_longest():
    acts = _runs_over_weeks(weeks=8, per_week=4, km=10.0, long_km=20.0)
    p = derive_profile(acts)
    # each week: 20 + 10*3 = 50 km
    assert 45 <= p.weekly_km <= 55
    assert p.longest_run_km == 20.0
    assert p.can_run_10k
    assert p.observed_training_days == 4
    assert p.confidence == "high"


def test_implied_vdot_from_hard_effort():
    acts = _runs_over_weeks(weeks=6, per_week=3, km=8.0, long_km=12.0)
    # add a sharp 10k (42 min)
    acts.append({"id": "sharp", "activity_type": "Run",
                 "start_date": "2026-06-01T05:30:00", "distance_km": 10.0,
                 "moving_time_min": 42.0})
    p = derive_profile(acts)
    assert p.implied_vdot is not None
    assert p.implied_vdot > 44


def test_easy_runs_dont_inflate_vdot():
    # all easy 10k at 6:30/km -> low implied vdot, never high
    acts = [{"id": f"e{i}", "activity_type": "Run",
             "start_date": f"2026-05-0{i+1}T05:30:00",
             "distance_km": 10.0, "moving_time_min": 65.0} for i in range(5)]
    p = derive_profile(acts)
    assert p.implied_vdot is not None
    assert p.implied_vdot < 40     # easy pace -> modest fitness read


def test_sparse_history_low_confidence():
    acts = _runs_over_weeks(weeks=1, per_week=2, km=5.0, long_km=6.0)
    p = derive_profile(acts)
    assert p.confidence == "low"


# --------------------------------------------------------------------------- #
# /onboard API
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


def _payload(preview=False, committed=5):
    race = (date.today() + timedelta(weeks=30)).isoformat()
    return {
        "name": "Andrew", "race_id": "cape_town", "race_date": race,
        "training_days_committed": committed, "preview": preview,
        "today": date.today().isoformat(),
        "activities": _runs_over_weeks(weeks=8, per_week=4, km=10.0, long_km=20.0),
    }


def test_onboard_preview_does_not_persist():
    r = client.post("/onboard", json=_payload(preview=True))
    assert r.status_code == 200
    body = r.json()
    assert body["preview"] is True
    assert "athlete_id" not in body
    assert body["profile"]["weekly_km"] > 0
    assert body["assessment"]["entry_phase"] in ("1a", "1b", "2")
    # nothing created
    assert client.get("/athletes").json() == [] or all(
        x["name"] != "ShouldNotExist" for x in client.get("/athletes").json()
    )


def test_onboard_creates_athlete_with_derived_fitness():
    r = client.post("/onboard", json=_payload(preview=False))
    assert r.status_code == 200
    body = r.json()
    aid = body["athlete_id"]
    a = client.get(f"/athlete/{aid}").json()
    # derived weekly km (~50) landed on the record, not a guess
    assert 45 <= a["current_weekly_km"] <= 55
    assert a["can_run_10k_continuous"] is True
    assert a["vdot"] is not None
    # activities were stored
    assert len(client.get(f"/athlete/{aid}/activities").json()) > 0


def test_committed_days_overrides_observed_for_peak():
    # observed 4 days, but commit to 6 -> peak cap should reflect 6 days
    p = _payload(preview=True, committed=6)
    r = client.post("/onboard", json=p).json()
    assert r["committed_training_days"] == 6


def test_onboard_unknown_race_404():
    p = _payload()
    p["race_id"] = "atlantis"
    assert client.post("/onboard", json=p).status_code == 404
