"""Simulates ~9 weeks of an athlete's training and checks trends unlock
and build as history grows (2 weeks minimum, more at 4 and 8 weeks)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402
from tests.helpers import complete_reviews  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")


def make_gpx(path, start, km, pace_sec, hr_start, hr_end):
    """Straight-line run: km at pace_sec/km, HR ramping hr_start -> hr_end."""
    total = km * pace_sec
    n = int(total // 5)
    pts = []
    for i in range(n + 1):
        f = i / n
        lat = 51.5 + (km * 1000 * f) / 111320.0
        t = (start + timedelta(seconds=total * f)).strftime("%Y-%m-%dT%H:%M:%SZ")
        hr = int(hr_start + (hr_end - hr_start) * f)
        pts.append(f'<trkpt lat="{lat:.7f}" lon="-0.1200000"><ele>20</ele><time>{t}</time><extensions>'
                   f'<gpxtpx:TrackPointExtension><gpxtpx:hr>{hr}</gpxtpx:hr></gpxtpx:TrackPointExtension>'
                   f'</extensions></trkpt>')
    with open(path, "w") as fh:
        fh.write('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
                 'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
                 + "".join(pts) + "</trkseg></trk></gpx>")


class TrendsBuildOverTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["COACH_DB"] = os.path.join(self.tmp.name, "coach.db")
        os.environ["COACH_TODAY"] = "2026-10-01"

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("COACH_TODAY", None)

    def call(self, name, **args):
        if name == "generate_program":
            complete_reviews(args["athlete_id"])
        out = ct.call_tool(name, args)
        self.assertTrue(out.get("ok"), f"{name}: {out}")
        return out

    def test_trends_unlock_and_grow(self):
        with open(os.path.join(EXAMPLES, "intake_race.json")) as f:
            intake = self.call("ingest_intake_form", form_response_json=json.load(f))
        aid, gid = intake["athlete_id"], intake["race_id"]
        self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial")
        start = date(2026, 10, 5)
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date="2026-10-05",
                         end_date="2026-12-31")["sessions"]
        tiers, reports = {}, {}
        for week in range(9):
            wk_start = start + timedelta(weeks=week)
            os.environ["COACH_TODAY"] = (wk_start + timedelta(days=7)).isoformat()
            for s in [r for r in rows if wk_start.isoformat() <= r["session_date"] < (wk_start + timedelta(days=7)).isoformat()]:
                if s["day_of_week"] == "Fri" and week in (2, 4, 5):
                    self.call("record_missed_session", program_row_id=s["program_row_id"])
                    continue
                km = s["prescribed_distance_km"]
                easy_pace = 390 - 3.5 * week                      # 6:30/km -> ~6:02/km, same HR
                pace = easy_pace - 60 if s["session_type"] in ("quality", "time_trial") else easy_pace
                drift = (18 - 1.5 * week) if s["session_type"] == "long" else 4
                path = os.path.join(self.tmp.name, f"{s['program_row_id']}.gpx")
                make_gpx(path, datetime.fromisoformat(s["session_date"] + "T06:30:00").replace(tzinfo=timezone.utc),
                         km, pace, 140, 140 + drift * 2)
                run = self.call("parse_run_file", file_path=path, athlete_id=aid)
                self.call("compare_run_to_program", run_log_id=run["run_log_id"],
                          program_row_id=s["program_row_id"])
            t = self.call("get_trends", athlete_id=aid)
            tiers[week + 1], reports[week + 1] = t["tier"], t

        # 1 week of data: still building a baseline, and says what's needed.
        self.assertEqual(tiers[1], "building_baseline")
        self.assertIn("more day", reports[1]["next_unlock"])
        # 2-3 weeks: early tier -- consistency + volume, no aerobic trend yet.
        self.assertEqual(tiers[2], "early")
        self.assertNotIn("aerobic_fitness", reports[3])
        self.assertTrue(reports[2]["feedback"])
        # 4+ weeks: developing -- aerobic fitness trend appears and is improving.
        self.assertEqual(tiers[4], "developing")
        ef = reports[6]["aerobic_fitness"]
        self.assertTrue(ef["available"], ef)
        self.assertEqual(ef["direction"], "improving")
        self.assertLess(ef["pace_change_sec_per_km"], 0)
        self.assertEqual(reports[6]["long_run_drift"]["direction"], "improving")
        self.assertIn("Fri", reports[6]["missed_by_weekday"])
        # 8+ weeks: established -- block comparison appears; feedback keeps growing.
        self.assertEqual(tiers[9], "established")
        self.assertIn("block_4_vs_4", reports[9])
        self.assertGreater(len(reports[9]["feedback"]), len(reports[2]["feedback"]))
        self.assertIsNotNone(reports[9]["time_trials"]["change_sec_first_to_latest"])
        # Signal history is kept even after streaks reset.
        self.assertIn("missed_easy_run_streak", reports[9]["signals"])

        vol = next(f["text"] for f in reports[9]["feedback"] if f["topic"] == "volume")
        self.assertIn("after a planned easier week", vol)
        last_flag = reports[9]["signals"]["long_run_hr_drift"]["last_flagged"]
        self.assertGreaterEqual(last_flag, "2026-10-05")          # dated by the run, not the log time

        squad = self.call("get_squad_overview")
        self.assertEqual(squad["athletes"][0]["tier"], "established")
        self.assertEqual(squad["athletes"][0]["aerobic_direction"], "improving")


    def test_general_fitness_walk_run_progress(self):
        with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
            intake = self.call("ingest_intake_form", form_response_json=json.load(f))
        aid, gid = intake["athlete_id"], intake["race_id"]
        self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial")
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date="2026-10-05",
                         end_date="2026-11-01")["sessions"]
        for s in rows[:9]:                                         # ~3 weeks of walk/run
            os.environ["COACH_TODAY"] = s["session_date"]
            path = os.path.join(self.tmp.name, f"{s['program_row_id']}.gpx")
            make_gpx(path, datetime.fromisoformat(s["session_date"] + "T07:00:00").replace(tzinfo=timezone.utc),
                     3.0, s["prescribed_duration_min"] * 60 / 3.0, 125, 135)
            run = self.call("parse_run_file", file_path=path, athlete_id=aid)
            self.call("compare_run_to_program", run_log_id=run["run_log_id"], program_row_id=s["program_row_id"])
        os.environ["COACH_TODAY"] = "2026-10-26"
        t = self.call("get_trends", athlete_id=aid)
        self.assertEqual(t["tier"], "early")
        self.assertEqual(t["unit"], "minutes")
        self.assertIn("walk_run", [f["topic"] for f in t["feedback"]])
        self.assertGreater(t["walk_run_progress"]["highest_step"], t["walk_run_progress"]["first_step"])


if __name__ == "__main__":
    unittest.main()
