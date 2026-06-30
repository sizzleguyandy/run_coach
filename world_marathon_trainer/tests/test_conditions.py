"""Condition handling — bounded, deterministic, safe. Pure logic + API.

Run: python -m pytest tests/test_conditions.py -q
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.conditions import evaluate_condition, CONDITIONS   # noqa: E402

_QUALITY = {"day": "Tue", "kind": "quality", "description": "5x1km threshold"}
_EASY = {"day": "Mon", "kind": "easy", "description": "Easy 10 km"}


# --------------------------------------------------------------------------- #
# Red flags — engine forbids training, escalates
# --------------------------------------------------------------------------- #
def test_pain_is_red_flag_no_workout():
    r = evaluate_condition("pain", body_area="left knee", today_session=_QUALITY)
    assert r.escalate_to_professional is True
    assert r.action == "stop"
    assert r.modified_session["kind"] == "rest"


def test_systemic_illness_red_flag():
    r = evaluate_condition("illness_systemic", today_session=_EASY)
    assert r.escalate_to_professional is True
    assert r.modified_session["kind"] == "rest"


def test_severe_niggle_escalates_to_flag():
    r = evaluate_condition("niggle", severity="severe", body_area="achilles",
                           today_session=_EASY)
    assert r.escalate_to_professional is True
    assert r.action == "stop"


# --------------------------------------------------------------------------- #
# Non-red-flag adjustments
# --------------------------------------------------------------------------- #
def test_mild_niggle_swaps_to_crosstrain_not_escalated():
    r = evaluate_condition("niggle", severity="mild", body_area="left knee",
                           today_session=_QUALITY)
    assert r.escalate_to_professional is False
    assert r.modified_session["kind"] == "cross_train"
    assert "left knee" in r.message


def test_tired_on_quality_day_becomes_recovery():
    r = evaluate_condition("tired", today_session=_QUALITY)
    assert r.escalate_to_professional is False
    assert r.action == "reduce"
    assert r.modified_session["kind"] in ("easy", "rest")


def test_tired_on_easy_day_becomes_rest():
    r = evaluate_condition("tired", today_session=_EASY)
    assert r.modified_session["kind"] == "rest"


def test_illness_mild_above_neck_easy():
    r = evaluate_condition("illness_mild", today_session=_QUALITY)
    assert r.escalate_to_professional is False
    assert "above the neck" in r.message.lower() or "head cold" in r.message.lower()


def test_missed_prioritises_no_escalation():
    r = evaluate_condition("missed")
    assert r.action == "prioritise"
    assert r.escalate_to_professional is False


def test_great_holds_the_plan():
    r = evaluate_condition("great")
    assert r.action == "hold"
    assert r.modified_session is None


def test_stress_reduces_intensity():
    r = evaluate_condition("stress", today_session=_QUALITY)
    assert r.action == "reduce"
    assert r.escalate_to_professional is False


def test_unknown_condition_asks_to_clarify():
    r = evaluate_condition("hangry")
    assert r.recognised is False
    assert r.action == "clarify"


def test_taxonomy_red_flags_marked():
    assert CONDITIONS["pain"]["red_flag"] is True
    assert CONDITIONS["illness_systemic"]["red_flag"] is True
    assert CONDITIONS["tired"]["red_flag"] is False


# --------------------------------------------------------------------------- #
# API + MCP wiring
# --------------------------------------------------------------------------- #
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ.setdefault("WMT_DB_URL", f"sqlite:///{_TMP.name}")

from sqlalchemy import create_engine          # noqa: E402
from sqlalchemy.orm import sessionmaker        # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402
from api.main import app                        # noqa: E402
from api.db import Base, get_session            # noqa: E402
import api.orm  # noqa: E402,F401

_engine = create_engine(f"sqlite:///{_TMP.name}",
                        connect_args={"check_same_thread": False})
_TestSession = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(_engine)


def _ov():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_session] = _ov
client = TestClient(app)


def _make_athlete():
    race = (date.today() + timedelta(weeks=20)).isoformat()
    return client.post("/athlete", json={
        "name": "A", "race_id": "cape_town", "race_date": race,
        "current_weekly_km": 55, "training_days_per_week": 5,
        "can_run_10k_continuous": True,
        "recent_race_distance_km": 10, "recent_race_time_min": 45,
    }).json()


def test_condition_endpoint_red_flag():
    a = _make_athlete()
    r = client.post(f"/athlete/{a['id']}/condition",
                    json={"condition": "pain", "body_area": "knee"})
    assert r.status_code == 200
    body = r.json()
    assert body["escalate_to_professional"] is True
    assert body["modified_session"]["kind"] == "rest"
    assert "valid_conditions" in body


def test_condition_endpoint_tired():
    a = _make_athlete()
    r = client.post(f"/athlete/{a['id']}/condition", json={"condition": "tired"})
    assert r.status_code == 200
    assert r.json()["escalate_to_professional"] is False


def test_condition_unknown_athlete_404():
    r = client.post("/athlete/ghost/condition", json={"condition": "tired"})
    assert r.status_code == 404


def test_list_conditions_endpoint():
    r = client.get("/conditions")
    assert r.status_code == 200
    assert "pain" in r.json() and "tired" in r.json()
