"""End-to-end audit: the plan follows the athlete's form answers, and the
weekly review readjusts it from what they actually do -- week after week."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402
from tests.helpers import complete_reviews  # noqa: E402
from tests.test_trends import make_gpx  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")
MON = date(2026, 10, 5)


class Lifecycle(unittest.TestCase):
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

    def load(self, name):
        with open(os.path.join(EXAMPLES, name)) as f:
            return json.load(f)

    def week(self, aid, ws):
        return self.call("get_upcoming_sessions", athlete_id=aid, start_date=ws.isoformat(),
                         end_date=(ws + timedelta(days=6)).isoformat())["sessions"]

    def run_week(self, aid, ws, share, minutes=False, pace=360):
        """Log the first `share` of the week's sessions, as prescribed."""
        rows = [s for s in self.week(aid, ws) if s["status"] == "pending"]
        for s in rows[:round(len(rows) * share)]:
            os.environ["COACH_TODAY"] = s["session_date"]
            if minutes:
                km, p = 3.0, s["prescribed_duration_min"] * 60 / 3.0
            else:
                km, p = s["prescribed_distance_km"], pace
            path = os.path.join(self.tmp.name, s["program_row_id"] + ".gpx")
            make_gpx(path, datetime.fromisoformat(s["session_date"] + "T06:00:00").replace(tzinfo=timezone.utc),
                     km, p, 138, 144)
            run = self.call("parse_run_file", file_path=path, athlete_id=aid)
            self.call("compare_run_to_program", run_log_id=run["run_log_id"], program_row_id=s["program_row_id"])

    def review(self, aid, ws, **fb):
        os.environ["COACH_TODAY"] = (ws + timedelta(days=7)).isoformat()      # the Monday after
        return self.call("review_week", athlete_id=aid, week_start=ws.isoformat(), feedback=fb or None)

    def test_race_plan_follows_form_and_readjusts(self):
        intake = self.call("ingest_intake_form", form_response_json=self.load("intake_race.json"))
        aid, gid = intake["athlete_id"], intake["race_id"]
        self.call("record_course_info", race_id=gid, elevation_gain_m=445, terrain_notes="farm paths")
        self.call("update_athlete_profile", athlete_id=aid, fields={
            "hr_zone2_high": 150, "hr_zone4_low": 165, "hr_zone4_high": 175,
            "hr_zone_source": "watch_export", "hr_zone_basis_notes": "test"})
        self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial",
                  constraints={"whole_number_distances": True})

        # --- plan follows the form ---------------------------------------
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date="2026-10-05",
                         end_date="2027-01-17")["sessions"]
        wk2 = [r for r in rows if r["week_num"] == 2]
        self.assertEqual(len(wk2), 5)                                         # "5 days a week"
        self.assertFalse([r for r in rows if r["day_of_week"] == "Mon"])       # avoid Monday
        self.assertTrue(all(r["day_of_week"] == "Sun" for r in rows if r["session_type"] == "long"))
        self.assertTrue(any("HILL" in (r["session_label"] or "") for r in rows))   # 445 m course
        self.assertTrue(all(r["prescribed_hr_high"] == 150 for r in rows if r["session_type"] == "easy"))
        self.assertEqual(rows[-1]["session_type"], "race")
        self.assertEqual(rows[-1]["session_date"], "2027-01-17")
        self.assertEqual(round(sum(r["prescribed_distance_km"] for r in self.week(aid, MON))), 25)  # 100 km / 4 wks

        # --- week 1 done in full -> on track, plan untouched -------------------
        self.run_week(aid, MON, 1.0)
        r1 = self.review(aid, MON, felt="comfortable", pain="none")
        self.assertEqual((r1["decision"], r1["plan_rebuilt"]), ("on_track", False))
        self.assertTrue(self.call("review_week", athlete_id=aid, week_start=MON.isoformat())["already_reviewed"])

        # --- week 2 only 60% done -> hold: week 3 repeats week 2's volume --------
        w2 = MON + timedelta(weeks=1)
        planned_w2 = sum(r["prescribed_distance_km"] for r in self.week(aid, w2))
        self.run_week(aid, w2, 0.6)
        r2 = self.review(aid, w2)
        self.assertEqual(r2["decision"], "hold")
        self.assertTrue(r2["plan_rebuilt"])
        self.assertLessEqual(r2["next_week_total_after"], planned_w2 + 0.5)
        self.assertLess(r2["next_week_total_after"], r2["next_week_total_before"])
        w3 = MON + timedelta(weeks=2)
        self.assertTrue(all(float(r["prescribed_distance_km"]).is_integer() for r in self.week(aid, w3)))  # setting kept

        # --- week 3 poor again (2 low weeks) -> step back to what they actually ran ----
        self.run_week(aid, w3, 0.2)
        r3 = self.review(aid, w3)
        self.assertEqual(r3["decision"], "step_back")
        self.assertTrue(r3["plan_rebuilt"])
        self.assertLess(r3["next_week_total_after"], planned_w2 * 0.6)          # really reduced
        self.assertTrue(r3["goal_at_risk"])                                     # ~8 km/week for a half
        self.assertTrue(any("general fitness" in x for x in r3["reasons"]))

        # --- week 4 done in full at the reduced level -> on track, building again -----
        w4 = MON + timedelta(weeks=3)
        self.run_week(aid, w4, 1.0)
        r4 = self.review(aid, w4, felt="comfortable", pain="none")
        self.assertEqual((r4["decision"], r4["plan_rebuilt"]), ("on_track", False))
        self.assertGreater(r4["next_week_total_before"], r3["next_week_total_after"])   # growth resumes

        # --- pain that changes gait -> pause, no rebuild -----------------------
        w5 = MON + timedelta(weeks=4)
        self.run_week(aid, w5, 1.0)
        r5 = self.review(aid, w5, pain="gait_changing")
        self.assertEqual((r5["decision"], r5["plan_rebuilt"]), ("pause", False))

        # --- the race date and taper never move ------------------------------
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date="2026-10-05",
                         end_date="2027-01-17")["sessions"]
        self.assertEqual([r["session_date"] for r in rows if r["session_type"] == "race"], ["2027-01-17"])
        os.environ["COACH_TODAY"] = "2027-01-04"
        late = self.call("review_week", athlete_id=aid, week_start="2026-12-28")
        self.assertEqual(late["decision"], "on_track")                         # nothing run, but taper protected

        # --- every week has a check-in AND a data-only backstop review ---------
        trig = ct.connect().execute("SELECT prompt_template FROM checkin_trigger WHERE athlete_id=? AND "
                                    "status='pending'", (aid,)).fetchall()
        self.assertTrue(any("Backstop" in t[0] for t in trig))
        self.assertTrue(any("review_week" in t[0] and "how did the week feel" in t[0] for t in trig))

    def test_general_fitness_progresses_repeats_and_steps_back(self):
        intake = self.call("ingest_intake_form", form_response_json=self.load("intake_general_fitness.json"))
        aid, gid = intake["athlete_id"], intake["race_id"]
        self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial")
        rows = self.week(aid, MON)
        self.assertEqual(len(rows), 3)                                        # "3 days a week"
        self.assertFalse([r for r in rows if r["day_of_week"] == "Wed"])      # avoid Wednesday
        label = lambda ws: self.week(aid, ws)[0]["session_label"]

        self.run_week(aid, MON, 1.0, minutes=True)
        r1 = self.review(aid, MON, felt="comfortable", pain="none")
        self.assertEqual((r1["decision"], r1["plan_rebuilt"]), ("progress", False))

        w2 = MON + timedelta(weeks=1)
        self.run_week(aid, w2, 1.0, minutes=True)
        l2 = label(w2)
        r2 = self.review(aid, w2, felt="hard")
        self.assertEqual(r2["decision"], "repeat")
        # Week 3 was already a planned easier week, so "repeat" must not make it harder.
        self.assertLessEqual(r2["next_week_total_after"], r2["next_week_total_before"])
        self.assertFalse(r2["plan_rebuilt"])

        w3, w4 = MON + timedelta(weeks=2), MON + timedelta(weeks=3)
        self.run_week(aid, w3, 0.34, minutes=True)
        self.assertEqual(self.review(aid, w3)["decision"], "repeat")
        self.run_week(aid, w4, 0.0, minutes=True)
        r4 = self.review(aid, w4)
        self.assertEqual(r4["decision"], "step_back")
        step = lambda l: next(i for i, rung in enumerate(ct.WALK_RUN_LADDER) if ct._walk_run_label(*rung) in l)
        self.assertLess(step(label(MON + timedelta(weeks=4))), step(l2))      # dropped a step


if __name__ == "__main__":
    unittest.main()
