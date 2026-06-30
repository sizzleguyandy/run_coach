"""Tests for the Strava mapping (the bug-prone unit conversions).

Run: python -m pytest tests/test_strava_sync.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.strava_sync import map_strava_activity, map_activities, is_run  # noqa: E402


_RAW = {
    "id": 13371337,
    "name": "Morning Run",
    "sport_type": "Run",
    "start_date": "2026-06-22T05:30:00Z",
    "distance": 12400.0,          # metres
    "moving_time": 3852,          # seconds (64.2 min)
    "elapsed_time": 3960,
    "total_elevation_gain": 88,
    "average_speed": 3.2189,      # m/s
    "average_heartrate": 148,
    "max_heartrate": 167,
    "suffer_score": 73,
}


def test_units_converted():
    m = map_strava_activity(_RAW)
    assert m["id"] == "13371337"                  # stringified
    assert abs(m["distance_km"] - 12.4) < 0.01    # metres -> km
    assert abs(m["moving_time_min"] - 64.2) < 0.1  # seconds -> minutes
    assert abs(m["elapsed_time_min"] - 66.0) < 0.1


def test_pace_derived_from_speed():
    m = map_strava_activity(_RAW)
    # 3.2189 m/s -> ~5.18 min/km
    assert abs(m["average_pace_min_per_km"] - 5.18) < 0.05


def test_passthrough_fields():
    m = map_strava_activity(_RAW)
    assert m["total_elevation_gain_m"] == 88
    assert m["average_heartrate"] == 148
    assert m["suffer_score"] == 73
    assert m["start_date"] == "2026-06-22T05:30:00Z"


def test_type_fallback():
    m = map_strava_activity({"id": 1, "type": "TrailRun", "distance": 5000,
                             "moving_time": 1800, "start_date": "x"})
    assert m["activity_type"] == "TrailRun"
    assert m["average_pace_min_per_km"] is None   # no speed -> server derives


def test_is_run_filter():
    assert is_run({"sport_type": "Run"})
    assert is_run({"type": "TrailRun"})
    assert is_run({"sport_type": "VirtualRun"})
    assert not is_run({"sport_type": "Ride"})
    assert not is_run({"type": "Swim"})


def test_map_activities_filters_non_runs():
    raw = [
        {"id": 1, "sport_type": "Run", "distance": 10000, "moving_time": 3000,
         "start_date": "a"},
        {"id": 2, "sport_type": "Ride", "distance": 40000, "moving_time": 4000,
         "start_date": "b"},
    ]
    out = map_activities(raw)
    assert len(out) == 1 and out[0]["id"] == "1"
