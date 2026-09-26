"""
End-to-end checks for the kit. Stdlib only:

    python3 -m unittest discover -s tests -v        (from run_coach_agent/)

Run this after any change to the generators, analysis thresholds or
tool layer. An agent handed this kit should run it once before first
use to confirm the environment works.
"""
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = SCRIPTS
sys.path.insert(0, KIT)

import analysis  # noqa: E402
import coach_tools as ct  # noqa: E402
from general_fitness import generate_general_fitness_plan  # noqa: E402
from race_plan import generate_race_plan  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(SCRIPTS), "examples")


def load(name):
    with open(os.path.join(EXAMPLES, name)) as f:
        return json.load(f)


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["COACH_DB"] = os.path.join(self.tmp.name, "coach.db")
        os.environ["COACH_TODAY"] = "2026-10-01"      # a Thursday -> plans start Mon 2026-10-05

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("COACH_TODAY", None)

    def call(self, name, **args):
        out = ct.call_tool(name, args)
        self.assertTrue(out.get("ok"), f"{name} failed: {out}")
        return out


class GeneralFitnessFlow(ToolTestCase):
    def test_full_flow(self):
        intake = self.call("ingest_intake_form", form_response_json=load("intake_general_fitness.json"))
        self.assertEqual(intake["goal_type"], "general_fitness")
        aid, gid = intake["athlete_id"], intake["race_id"]

        plan = self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial plan from intake")
        self.assertEqual(plan["mode"], "walk_run")
        self.assertEqual(plan["start_date"], "2026-10-05")
        # Wednesday was an avoid day; walk/run never on consecutive days.
        sessions = self.call("get_upcoming_sessions", athlete_id=aid,
                             start_date="2026-10-05", end_date="2026-12-31")["sessions"]
        self.assertTrue(sessions)
        self.assertFalse([s for s in sessions if s["day_of_week"] == "Wed"])
        self.assertTrue(all(s["prescribed_distance_km"] is None for s in sessions))

        # Log the example run (Tue 2026-10-06) and match it.
        run = self.call("parse_run_file", file_path=os.path.join(EXAMPLES, "example_run.gpx"), athlete_id=aid)
        again = self.call("parse_run_file", file_path=os.path.join(EXAMPLES, "example_run.gpx"), athlete_id=aid)
        self.assertTrue(again["duplicate"])
        match = self.call("match_run_to_program", athlete_id=aid, run_log_id=run["run_log_id"])["match"]
        self.assertIsNotNone(match)
        cmp_ = self.call("compare_run_to_program", run_log_id=run["run_log_id"],
                         program_row_id=match["program_row_id"])
        self.assertIn("missed_easy_run_streak", cmp_["signals"])
        # Comparing the same run again must not double count.
        self.call("compare_run_to_program", run_log_id=run["run_log_id"], program_row_id=match["program_row_id"])

        # Two missed walk/run sessions -> streak action threshold.
        pending = [s for s in sessions if s["session_date"] > match["session_date"]][:2]
        r1 = self.call("record_missed_session", program_row_id=pending[0]["program_row_id"])
        r2 = self.call("record_missed_session", program_row_id=pending[1]["program_row_id"])
        self.assertEqual(r1["signal"]["current_streak"], 1)
        self.assertTrue(r2["signal"]["action_threshold_met"])

        # Week-review check-in is due after week 1 and not stale.
        due = self.call("get_due_checkins", as_of_date="2026-10-12", athlete_id=aid)["due"]
        self.assertTrue(due)
        st = self.call("check_trigger_staleness", trigger_id=due[0]["trigger_id"])
        self.assertFalse(st["stale"])

        # Regenerate at the same level (week not completed comfortably):
        # old generator check-ins get cancelled, old pending rows superseded.
        week1_label = [s for s in sessions if s["week_num"] == 1][0]["session_label"]
        regen = self.call("generate_program", athlete_id=aid, race_id=gid, reason="repeat week 1",
                          start_date="2026-10-12", general_fitness_start_level={"repeat_last_week": True})
        new_first = self.call("get_upcoming_sessions", athlete_id=aid, start_date="2026-10-12",
                              end_date="2026-10-18")["sessions"][0]
        self.assertEqual(new_first["session_label"], week1_label)     # repeated, not progressed
        self.assertGreater(regen["rows_superseded"], 0)
        self.assertGreater(regen["old_generator_checkins_cancelled"], 0)

        summary = self.call("get_athlete_summary", athlete_id=aid)
        self.assertEqual(summary["active_goal"]["goal_type"], "general_fitness")
        self.assertEqual(summary["current_revision"]["revision_id"], regen["revision_id"])

    def test_medical_gate(self):
        form = load("intake_general_fitness.json")
        form["Health check — do any of these apply?"] = ["A heart condition or high blood pressure"]
        intake = self.call("ingest_intake_form", form_response_json=form)
        self.assertTrue(intake["needs_medical_clearance"])
        blocked = ct.call_tool("generate_program", {"athlete_id": intake["athlete_id"],
                                                    "race_id": intake["race_id"], "reason": "initial"})
        self.assertFalse(blocked["ok"])
        self.call("update_athlete_profile", athlete_id=intake["athlete_id"],
                  fields={"medical_clearance_at": "2026-10-02"})
        self.call("generate_program", athlete_id=intake["athlete_id"], race_id=intake["race_id"],
                  reason="initial")

    def test_missing_goal_answer_is_inferred_and_flagged(self):
        form = load("intake_general_fitness.json")
        del form["What are you training for?"]
        intake = self.call("ingest_intake_form", form_response_json=form)
        self.assertEqual(intake["goal_type"], "general_fitness")
        self.assertTrue(intake["warnings"])


class RaceFlow(ToolTestCase):
    def test_full_flow(self):
        intake = self.call("ingest_intake_form", form_response_json=load("intake_race.json"))
        self.assertEqual(intake["goal_type"], "race")
        aid, gid = intake["athlete_id"], intake["race_id"]
        self.call("record_course_info", race_id=gid, elevation_gain_m=445,
                  terrain_notes="farm paths", source="https://example.com/valley-half")
        plan = self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial plan from intake",
                         constraints={"whole_number_distances": True})
        self.assertIn("hill repeats", plan["summary"].lower())
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date="2026-10-05",
                         end_date="2027-01-31")["sessions"]
        self.assertEqual([r for r in rows if r["session_type"] == "race"][0]["session_date"], "2027-01-17")
        self.assertEqual(len([r for r in rows if r["session_type"] == "time_trial"]), 2)
        self.assertFalse([r for r in rows if r["day_of_week"] == "Mon"])

        # Moving a quality session next to the long run is warned about in a dry run.
        q = [r for r in rows if r["session_type"] == "quality"][0]
        lr = [r for r in rows if r["session_type"] == "long" and r["week_num"] == q["week_num"]][0]
        d = date.fromisoformat(lr["session_date"]).toordinal() - 1
        dry = self.call("write_program_revision", athlete_id=aid, race_id=gid, reason="athlete request",
                        changed_by="athlete_request", summary="move quality", dry_run=True,
                        affected_program_row_ids=[q["program_row_id"]],
                        new_sessions=[{**{k: q[k] for k in ("session_type", "session_label",
                                                            "prescribed_distance_km", "week_phase")},
                                       "session_date": date.fromordinal(d).isoformat()}])
        self.assertTrue(dry["warnings"])

        # Moving the first time trial by a day: its check-in follows it, and
        # the weekly reviews survive the revision (fired, flagged stale).
        tt = [r for r in rows if r["session_type"] == "time_trial"][0]
        new_d = date.fromordinal(date.fromisoformat(tt["session_date"]).toordinal() + 1).isoformat()
        mv = self.call("write_program_revision", athlete_id=aid, race_id=gid, reason="athlete request",
                       changed_by="athlete_request", summary="TT moved a day",
                       affected_program_row_ids=[tt["program_row_id"]],
                       new_sessions=[{**{k: tt[k] for k in ("session_type", "session_label",
                                                            "prescribed_distance_km", "week_phase")},
                                      "session_date": new_d}])
        self.assertEqual(mv["checkins_repointed"], 1)
        due = self.call("get_due_checkins", as_of_date="2026-10-12", athlete_id=aid)["due"]
        weekly = [t for t in due if t["trigger_type"] == "scheduled_date"][0]
        st = self.call("check_trigger_staleness", trigger_id=weekly["trigger_id"])
        self.assertTrue(st["stale"])
        self.assertEqual(st["status"], "fired")

    def test_race_rejects_low_base(self):
        form = load("intake_race.json")
        form["Total running km in the last 4 weeks (roughly)"] = "8"
        intake = self.call("ingest_intake_form", form_response_json=form)
        out = ct.call_tool("generate_program", {"athlete_id": intake["athlete_id"],
                                                "race_id": intake["race_id"], "reason": "initial"})
        self.assertFalse(out["ok"])
        self.assertIn("general-fitness", out["suggestion"])


class HermesIntegration(ToolTestCase):
    def setUp(self):
        super().setUp()
        os.environ["HERMES_HOME"] = os.path.join(self.tmp.name, "hermes_home")

    def tearDown(self):
        os.environ.pop("HERMES_HOME", None)
        super().tearDown()

    def test_sheet_ingest_link_and_checkin_gate(self):
        form = load("intake_general_fitness.json")
        header = list(form)
        row = [", ".join(v) if isinstance(v, list) else v for v in form.values()]
        first = self.call("ingest_sheet_rows", values=[header, row])
        self.assertEqual(len(first["ingested"]), 1, first)
        again = self.call("ingest_sheet_rows", values=[header, row])
        self.assertEqual((len(again["ingested"]), again["duplicates"]), (0, 1))
        aid, gid = first["ingested"][0]["athlete_id"], first["ingested"][0]["race_id"]
        latest = self.call("get_athlete_summary", athlete_id=aid)["latest_intake"]
        self.assertEqual(latest["avoid_days"], ["Wednesday"])            # checkbox split

        unlinked = ct.call_tool("get_athlete_summary", {"chat_ref": "telegram:42"})
        self.assertFalse(unlinked["ok"])
        self.call("link_chat", email="SAM@example.com", chat_ref="telegram:42")
        self.assertEqual(self.call("get_athlete_summary", chat_ref="telegram:42")["athlete"]["athlete_id"], aid)
        other = self.call("ingest_intake_form", form_response_json=load("intake_race.json"))
        clash = ct.call_tool("link_chat", {"athlete_id": other["athlete_id"], "chat_ref": "telegram:42"})
        self.assertFalse(clash["ok"])

        self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial")
        gate = self.call("install_checkin_gate", athlete_id=aid)
        self.assertTrue(gate["gate_script"].startswith(os.environ["HERMES_HOME"]))
        self.assertEqual(gate["cronjob_call"]["skill"], "run-coach")

        def run_gate(today):
            env = {**os.environ, "COACH_TODAY": today}
            out = subprocess.run([sys.executable, gate["gate_script"]], env=env, capture_output=True,
                                 text=True, check=True).stdout.strip().splitlines()[-1]
            return json.loads(out)
        self.assertFalse(run_gate("2026-10-05")["wakeAgent"])            # nothing due on day 1
        woke = run_gate("2026-10-12")
        self.assertTrue(woke["wakeAgent"])
        self.assertTrue(woke["context"]["due_checkins"])

    def test_backup_script(self):
        self.call("init_db")
        home = os.environ["HERMES_HOME"]
        os.makedirs(os.path.join(home, "scripts"))
        src = os.path.join(KIT, "..", "..", "..", "..", "scripts", "run-coach-backup.py")
        if not os.path.exists(src):
            self.skipTest("backup script not installed alongside the skill")
        dst = os.path.join(home, "scripts", "run-coach-backup.py")
        with open(src) as f, open(dst, "w") as g:
            g.write(f.read())
        res = subprocess.run([sys.executable, dst], env=os.environ.copy(), capture_output=True, text=True)
        self.assertEqual((res.returncode, res.stdout), (0, ""))
        backups = sorted(os.listdir(os.path.join(os.path.dirname(os.environ["COACH_DB"]), "backups")))
        self.assertEqual([b.split("-")[0] for b in backups], ["coach", "history"])


class SignalFixes(unittest.TestCase):
    def test_thresholds_are_per_signal_and_irrelevant_runs_dont_reset(self):
        drift = analysis.compare_run_to_program(
            {"run_log_id": "1", "hr_drift_delta": 20, "first_half_avg_hr": 140, "second_half_avg_hr": 160},
            {"program_row_id": "p", "session_type": "long"})
        easy = analysis.compare_run_to_program({"run_log_id": "2"}, {"program_row_id": "q", "session_type": "easy"})
        st = analysis.update_signal_state(None, "long_run_hr_drift", "t", drift, "1")
        st = analysis.update_signal_state(st, "long_run_hr_drift", "t", easy, "2")
        self.assertEqual(st["current_streak"], 1)          # easy run didn't reset it
        st = analysis.update_signal_state(st, "long_run_hr_drift", "t", drift, "3")
        self.assertTrue(st["action_threshold_met"])

        m = analysis.update_signal_state(None, "missed_easy_run_streak", "t", missed_program_row={"session_type": "easy"})
        self.assertEqual(m["current_streak"], 1)            # used to be always 0
        m = analysis.update_signal_state(m, "missed_easy_run_streak", "t", easy, "2")
        self.assertEqual(m["current_streak"], 0)
        self.assertEqual(analysis.SIGNALS["missed_easy_run_streak"]["threshold"],
                         analysis.MISSED_RUN_STREAK_FOR_ACTION)


class GeneratorGuardrails(unittest.TestCase):
    def test_general_fitness_never_ramps_fast(self):
        for c, wk, spw in itertools.product([0, 3, 10, 19, 20, 30, 45, 60], [0, 30, 90, 150, 300], [2, 3]):
            p = generate_general_fitness_plan("2026-10-05", c, wk, sessions_per_week=spw, long_run_day="Sun")
            tot, ph = {}, {}
            for r in p["rows"]:
                tot[r["week_num"]] = tot.get(r["week_num"], 0) + r["prescribed_duration_min"]
                ph[r["week_num"]] = r["week_phase"]
            builds = [tot[w] for w in sorted(tot) if ph[w] == "build"]
            for a, b in zip(builds, builds[1:]):
                self.assertLessEqual(b, a * 1.05 + 0.01, (c, wk, spw))
                self.assertLessEqual(b - a, 10, (c, wk, spw))

    def test_race_plan_reports_every_jump(self):
        for d, base, days, whole in itertools.product([5, 10, 21.1, 42.2], [10, 25, 50], [3, 4, 5], [True, False]):
            p = generate_race_plan("2026-10-05", "2027-03-14", d, base, 8, elevation_gain_m=100,
                                   days_per_week=days, whole_number_distances=whole)
            self.assertTrue(p["ok"])
            tot, ph = {}, {}
            for r in p["rows"]:
                tot[r["week_num"]] = tot.get(r["week_num"], 0) + r["prescribed_distance_km"]
                ph[r["week_num"]] = r["week_phase"]
                if r["session_type"] != "race":
                    self.assertGreaterEqual(r["prescribed_distance_km"], 3)
            builds = [(w, tot[w]) for w in sorted(tot) if ph[w] in ("build", "rebuild")]
            for (_, a), (w, b) in zip(builds, builds[1:]):
                if b > a * 1.10 + 1e-6:
                    self.assertTrue(any(e.startswith(f"wk{w}:") for e in p["exceptions"]), (d, base, days, w))


if __name__ == "__main__":
    unittest.main()
