"""Hevy / Hevy Coach workouts posted to Strava: the description is read into
exercises and sets, leg days are told apart from upper-body days, and
strength progress shows in trends."""
import json
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402
import hevy  # noqa: E402
from tests.test_strava import EXAMPLES, StravaCase  # noqa: E402

# Exactly what Hevy posted to Strava (from the owner's real account).
UPPER = """Logged with hevyapp.com

Chest Press (Machine)
"Alternating"
Set 1: 32.5 kg x 5 
Set 2: 32.5 kg x 5 
Set 3: 32.5 kg x 5 
Set 4: 32.5 kg x 7 
Set 5: 32.5 kg x 7 

Lat Pulldown (Cable)
Set 1: 70 kg x 5 
Set 2: 70 kg x 5 
Set 3: 70 kg x 5 
Set 4: 70 kg x 7 

Seated Dip Machine
Set 1: 50 kg x 8 
Set 2: 50 kg x 8 
Set 3: 50 kg x 8 
Set 4: 50 kg x 8 

Preacher Curl (Machine)
Set 1: 32.5 kg x 8 
Set 2: 32.5 kg x 8 
Set 3: 32.5 kg x 8 
Set 4: 32.5 kg x 8"""

LEGS = """Logged with hevyapp.com

Squat (Barbell)
Set 1 (Warm-up): 40 kg x 10
Set 2: 80 kg x 5
Set 3: 80 kg x 5
Set 4: 80 kg x 5

Romanian Deadlift (Barbell)
Set 1: 60 kg x 8
Set 2: 60 kg x 8

Plank
Set 1: 1:00"""


class HevyParser(unittest.TestCase):
    def test_real_upper_body_log(self):
        w = hevy.parse_description(UPPER)
        self.assertEqual((w["focus"], w["total_sets"], w["volume_kg"]), ("upper", 17, 5122.5))
        names = [e["name"] for e in w["exercises"]]
        self.assertEqual(names, ["Chest Press (Machine)", "Lat Pulldown (Cable)", "Seated Dip Machine",
                                 "Preacher Curl (Machine)"])
        self.assertEqual(w["exercises"][0]["note"], "Alternating")
        self.assertEqual(w["exercises"][1]["top_set"], {"weight_kg": 70.0, "reps": 7})

    def test_leg_day(self):
        w = hevy.parse_description(LEGS)
        self.assertEqual(w["focus"], "lower")
        self.assertEqual(hevy.leg_summary(w), "Squat (Barbell) 3x5 @ 80 kg, Romanian Deadlift (Barbell) 2x8 @ 60 kg")
        self.assertEqual(w["exercises"][2]["sets"][0]["duration_sec"], 60)

    def test_not_hevy(self):
        self.assertIsNone(hevy.parse_description("Easy spin, felt good"))
        self.assertEqual(hevy.region("Hanging Leg Raise"), "core")               # not a leg exercise
        self.assertEqual(hevy.parse_description("Bench\nSet 1: 135 lbs x 5")["exercises"][0]["sets"][0]["weight_kg"],
                         61.2)


class HevyThroughStrava(StravaCase, unittest.TestCase):
    def test_leg_day_flagged_upper_day_not_and_progress(self):
        with open(os.path.join(EXAMPLES, "intake_race.json")) as f:
            intake = self.call("ingest_intake_form", form_response_json=json.load(f))
        aid, gid = intake["athlete_id"], intake["race_id"]
        # Three weeks ago (history): an upper-body day at a lighter lat pulldown.
        self.fake.add(self.at(self.today - timedelta(days=21), 17), sport="WeightTraining", minutes=50,
                      name="Upper A", description=UPPER.replace("70 kg", "62.5 kg"))
        self.connect(aid)
        self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial")
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date=self.monday.isoformat(),
                         end_date=(self.monday + timedelta(days=6)).isoformat())["sessions"]
        long_run = next(r for r in rows if r["session_type"] == "long")
        key = next(r for r in rows if r["session_type"] in ("quality", "easy") and r["session_date"] < long_run["session_date"])
        ld, kd = date.fromisoformat(long_run["session_date"]), date.fromisoformat(key["session_date"])
        self.fake.add(self.at(ld - timedelta(days=1), 18), sport="WeightTraining", minutes=55, name="Lower A",
                      description=LEGS)
        self.fake.add(self.at(kd - timedelta(days=1), 18), sport="WeightTraining", minutes=50, name="Upper A",
                      description=UPPER)
        for r in rows:
            self.fake.add(self.at(date.fromisoformat(r["session_date"])), km=r["prescribed_distance_km"],
                          minutes=r["prescribed_distance_km"] * 6)
        self.call("strava_sync", athlete_id=aid)
        pend = self.call("get_pending_feedback", athlete_id=aid)
        long_fb = next(r for r in pend["runs"] if r["date"] == long_run["session_date"])
        self.assertTrue(any("leg work (Squat (Barbell) 3x5 @ 80 kg" in n for n in long_fb["notes"]), long_fb)
        key_fb = next(r for r in pend["runs"] if r["date"] == key["session_date"])
        self.assertFalse(any("leg work" in n or "strength session" in n for n in key_fb["notes"]))   # upper day
        focuses = sorted(o["workout"]["focus"] for o in pend["other"])
        self.assertEqual(focuses, ["lower", "upper"])
        t = self.call("get_trends", athlete_id=aid)
        prog = {p["exercise"]: p for p in t.get("strength_progress", [])}
        self.assertEqual(prog["Lat Pulldown (Cable)"]["first"], "62.5 kg x 7")
        self.assertEqual(prog["Lat Pulldown (Cable)"]["latest"], "70 kg x 7")


if __name__ == "__main__":
    unittest.main()
