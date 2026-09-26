"""The permanent run history (history.db): every upload recorded, original
files recoverable byte-for-byte, and nothing can be changed or deleted."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402
import history  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")
GPX = os.path.join(EXAMPLES, "example_run.gpx")


class RunHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["COACH_DB"] = os.path.join(self.tmp.name, "coach.db")
        os.environ["COACH_TODAY"] = "2026-10-01"
        with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
            intake = ct.call_tool("ingest_intake_form", {"form_response_json": json.load(f)})
        self.aid, self.gid = intake["athlete_id"], intake["race_id"]
        ct.call_tool("generate_program", {"athlete_id": self.aid, "race_id": self.gid, "reason": "t"})

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("COACH_TODAY", None)

    def call(self, name, **args):
        out = ct.call_tool(name, args)
        self.assertTrue(out.get("ok"), f"{name}: {out}")
        return out

    def hdb(self):
        return sqlite3.connect(os.path.join(self.tmp.name, "history.db"))

    def test_every_upload_recorded_and_file_recoverable(self):
        run = self.call("parse_run_file", file_path=GPX, athlete_id=self.aid, athlete_notes="felt good")
        self.assertTrue(run["history_id"])
        dup = self.call("parse_run_file", file_path=GPX, athlete_id=self.aid)
        self.assertEqual(dup["history_id"], run["history_id"])
        bad = os.path.join(self.tmp.name, "broken.gpx")
        with open(bad, "w") as f:
            f.write("<gpx><trk><trkseg><trkpt lat='x'")
        self.assertFalse(ct.call_tool("parse_run_file", {"file_path": bad, "athlete_id": self.aid})["ok"])

        m = self.call("match_run_to_program", athlete_id=self.aid, run_log_id=run["run_log_id"])["match"]
        self.call("compare_run_to_program", run_log_id=run["run_log_id"], program_row_id=m["program_row_id"])

        h = self.hdb()
        self.assertEqual(h.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
        outcomes = sorted(r[0] for r in h.execute("SELECT outcome FROM upload_attempts"))
        self.assertEqual(outcomes, ["duplicate", "parse_failed", "recorded"])
        events = [r[0] for r in h.execute("SELECT event_type FROM run_events ORDER BY occurred_at, rowid")]
        self.assertEqual(events, ["logged", "matched", "compared"])
        self.assertGreater(h.execute("SELECT sample_count FROM runs").fetchone()[0], 100)

        exp = self.call("export_run_file", history_id=run["history_id"])
        with open(exp["path"], "rb") as a, open(GPX, "rb") as b:
            self.assertEqual(a.read(), b.read())

        hist = self.call("get_run_history", athlete_id=self.aid)
        self.assertEqual(hist["all_time"]["runs"], 1)
        self.assertEqual(hist["all_time"]["failed_uploads"], 1)
        self.assertEqual(hist["runs"][0]["matched_to"]["program_row_id"], m["program_row_id"])

    def test_history_is_append_only(self):
        self.call("parse_run_file", file_path=GPX, athlete_id=self.aid)
        h = self.hdb()
        for sql in ("UPDATE runs SET distance_km=0", "DELETE FROM runs", "DELETE FROM run_events",
                    "UPDATE upload_attempts SET outcome='x'", "DELETE FROM upload_attempts"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                h.execute(sql)

    def test_history_survives_plan_rebuild(self):
        run = self.call("parse_run_file", file_path=GPX, athlete_id=self.aid)
        self.call("generate_program", athlete_id=self.aid, race_id=self.gid, reason="rebuild",
                  start_date="2026-10-12")
        self.assertEqual(self.call("get_run_history", athlete_id=self.aid)["runs"][0]["history_id"],
                         run["history_id"])

    def test_backfill_existing_runs(self):
        run = self.call("parse_run_file", file_path=GPX, athlete_id=self.aid)
        os.remove(os.path.join(self.tmp.name, "history.db"))       # simulate a pre-history install
        res = self.call("backfill_history")
        self.assertEqual((res["backfilled"], res["in_sync"]), (1, True))
        self.assertEqual(self.call("backfill_history")["backfilled"], 0)   # idempotent
        # Re-uploading that run later is logged as a duplicate; the backfilled row stays (append-only).
        dup = self.call("parse_run_file", file_path=GPX, athlete_id=self.aid)
        self.assertTrue(dup["duplicate"])
        self.assertEqual(history.history_path(os.environ["COACH_DB"]),
                         os.path.join(self.tmp.name, "history.db"))
        self.assertEqual(dup["run_log_id"], run["run_log_id"])


if __name__ == "__main__":
    unittest.main()
