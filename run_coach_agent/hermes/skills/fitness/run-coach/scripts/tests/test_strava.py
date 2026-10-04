"""Strava integration against a fake Strava API: connect, backfill, automatic
run + strength import, plan matching, duplicates with uploaded files, token
refresh, rate limits, revoked access, quiet hours and owner privacy."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402
import strava  # noqa: E402
from tests.helpers import complete_reviews  # noqa: E402
from tests.test_trends import make_gpx  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")


class FakeStrava:
    def __init__(self):
        self.activities, self.streams, self.details = [], {}, {}
        self.access, self.refresh_token, self.expires = "tok1", "ref1", strava.now_epoch() + 21600
        self.refreshes, self.rate_limited, self.revoked, self.next_id = 0, False, False, 1000

    def add(self, start, sport="Run", km=0.0, minutes=30.0, hr=(140, 146), name=None, description=None):
        self.next_id += 1
        aid = self.next_id
        secs = int(minutes * 60)
        self.activities.append({
            "id": aid, "name": name or sport, "sport_type": sport, "type": sport,
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "distance": km * 1000,
            "moving_time": secs, "elapsed_time": secs + 30, "total_elevation_gain": 40,
            "average_heartrate": sum(hr) / 2, "max_heartrate": hr[1] + 8, "manual": False})
        n = secs // 10
        self.streams[aid] = {
            "time": {"data": [i * 10 for i in range(n + 1)]},
            "distance": {"data": [km * 1000 * i / n for i in range(n + 1)]},
            "heartrate": {"data": [int(hr[0] + (hr[1] - hr[0]) * i / n) for i in range(n + 1)]},
            "altitude": {"data": [20.0] * (n + 1)}}
        self.details[aid] = {"description": description, "perceived_exertion": 6}
        return aid

    def http(self, method, url, data=None, headers=None):
        if self.rate_limited:
            return 429, {"message": "Rate Limit Exceeded"}, {}
        if url == strava.TOKEN_URL:
            if data["grant_type"] == "authorization_code":
                if data["code"] != "goodcode":
                    return 400, {"message": "Bad Request"}, {}
            else:
                if data["refresh_token"] != self.refresh_token:
                    return 400, {"message": "bad refresh"}, {}
                self.refreshes += 1
                self.access, self.refresh_token = f"tok{self.refreshes + 1}", f"ref{self.refreshes + 1}"
                self.expires = strava.now_epoch() + 21600
            return 200, {"access_token": self.access, "refresh_token": self.refresh_token,
                         "expires_at": self.expires, "athlete": {"id": 777}}, {}
        if url == strava.DEAUTH_URL:
            return 200, {}, {}
        if self.revoked or (headers or {}).get("Authorization") != f"Bearer {self.access}":
            return 401, {"message": "Authorization Error"}, {}
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        if u.path.endswith("/athlete/activities"):
            after = int(q["after"][0])
            page = int(q.get("page", ["1"])[0])
            items = [a for a in self.activities
                     if datetime.strptime(a["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(
                         tzinfo=timezone.utc).timestamp() > after]
            return 200, items[(page - 1) * 100: page * 100], {}
        aid = int(u.path.split("/")[-2] if u.path.endswith("/streams") else u.path.split("/")[-1])
        if u.path.endswith("/streams"):
            return 200, self.streams[aid], {}
        return 200, self.details[aid], {}


class StravaCase:
    """Shared setup (fake Strava, temp home); mixed into TestCase subclasses."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        with open(os.path.join(self.home, ".env"), "w") as f:
            f.write("TELEGRAM_ALLOWED_USERS=1\nSTRAVA_CLIENT_ID=12345\nSTRAVA_CLIENT_SECRET=s3cret\n")
        os.environ.update(COACH_DB=os.path.join(self.tmp.name, "coach.db"), HERMES_HOME=self.home,
                          COACH_TODAY=date.today().isoformat())
        for k in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"):
            os.environ.pop(k, None)
        self.fake = FakeStrava()
        self._orig = strava._http
        strava._http = self.fake.http
        self.today = date.today()
        self.monday = self.today + timedelta(days=(7 - self.today.weekday()) % 7 or 7)

    def tearDown(self):
        strava._http = self._orig
        self.tmp.cleanup()
        for k in ("COACH_TODAY", "HERMES_HOME"):
            os.environ.pop(k, None)

    def call(self, _tool, **args):
        if _tool == "generate_program":
            complete_reviews(args["athlete_id"])
        out = ct.call_tool(_tool, args)
        self.assertTrue(out.get("ok"), f"{_tool}: {out}")
        return out

    def at(self, d, hour=6):
        return datetime(d.year, d.month, d.day, hour, 30, tzinfo=timezone.utc)

    def connect(self, aid):
        link = self.call("strava_connect_link", athlete_id=aid)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(link["link"]).query)
        self.assertEqual((q["client_id"][0], q["scope"][0]), ("12345", "read,activity:read_all"))
        self.assertNotIn("s3cret", link["message_for_athlete"])
        pasted = f"http://localhost/strava-connected?state={q['state'][0]}&code=goodcode&scope=read,activity:read_all"
        return self.call("strava_complete_connect", athlete_id=aid, redirect_url=pasted)



class StravaFlow(StravaCase, unittest.TestCase):
    def test_full_strava_flow(self):
        with open(os.path.join(EXAMPLES, "intake_race.json")) as f:
            intake = self.call("ingest_intake_form", form_response_json=json.load(f))
        aid, gid = intake["athlete_id"], intake["race_id"]

        # --- last 4 weeks already on Strava: imported as history, no feedback owed ---
        for days, km in ((20, 8), (13, 10), (8, 12), (3, 14)):
            self.fake.add(self.at(self.today - timedelta(days=days)), km=km, minutes=km * 6)
        self.fake.add(self.at(self.today - timedelta(days=4), 17), sport="WeightTraining", minutes=45, name="Legs")
        res = self.connect(aid)
        self.assertEqual(res["backfilled"], {"runs": 4, "other": 1})
        self.assertEqual(self.call("get_pending_feedback", athlete_id=aid)["count"], 0)

        # --- the plan starts from what they really run (11 km/week), not the form (25) ---
        plan = self.call("generate_program", athlete_id=aid, race_id=gid, reason="initial")
        self.assertIn("4 logged runs", plan["summary"])
        wk1 = sum(w.get("total_km", 0) for w in plan["weeks"][:1])
        self.assertLess(wk1, 15)

        # --- new week: strength the evening before the long run, then the runs ---
        rows = self.call("get_upcoming_sessions", athlete_id=aid, start_date=self.monday.isoformat(),
                         end_date=(self.monday + timedelta(days=6)).isoformat())["sessions"]
        long_run = next(r for r in rows if r["session_type"] == "long")
        long_day = date.fromisoformat(long_run["session_date"])
        self.fake.add(self.at(long_day - timedelta(days=1), 18), sport="WeightTraining", minutes=50,
                      name="Heavy legs", description="squats 5x5")
        for r in rows:
            self.fake.add(self.at(date.fromisoformat(r["session_date"])), km=r["prescribed_distance_km"],
                          minutes=r["prescribed_distance_km"] * 6)
        sync = self.call("strava_sync", athlete_id=aid)["athletes"][0]
        self.assertEqual(len(sync["new_runs"]), len(rows))
        self.assertTrue(all(r["matched_session"] for r in sync["new_runs"]))
        pend = self.call("get_pending_feedback", athlete_id=aid)
        self.assertEqual((len(pend["runs"]), len(pend["other"])), (len(rows), 1))
        self.assertEqual(pend["other"][0]["description"], "squats 5x5")
        long_fb = next(r for r in pend["runs"] if r["date"] == long_run["session_date"])
        self.assertTrue(any("strength session 'Heavy legs'" in n for n in long_fb["notes"]))
        self.assertEqual(self.call("strava_sync", athlete_id=aid)["athletes"][0]["new_runs"], [])  # idempotent

        # --- same run also sent as a file: recognised, not double-counted ---
        gpx = os.path.join(self.tmp.name, "dup.gpx")
        r0 = rows[0]
        make_gpx(gpx, self.at(date.fromisoformat(r0["session_date"])) + timedelta(seconds=40),
                 r0["prescribed_distance_km"], 360, 140, 146)
        self.assertTrue(self.call("parse_run_file", file_path=gpx, athlete_id=aid)["duplicate"])

        self.call("mark_feedback_sent", athlete_id=aid, run_log_ids=[r["run_log_id"] for r in pend["runs"]],
                  activity_ids=[o["activity_id"] for o in pend["other"]])
        self.assertEqual(self.call("get_pending_feedback", athlete_id=aid)["count"], 0)

        # --- strength shows up in trends and the athlete summary ---
        s = self.call("get_athlete_summary", athlete_id=aid)
        self.assertTrue(any(o["category"] == "strength" for o in s["recent_other_activities"]))
        self.assertEqual(s["strava"]["status"], "connected")

        # --- owner never sees Strava data; the athlete does ---
        self.assertTrue(self.call("get_trends", athlete_id=aid, audience="owner")["private"])
        self.assertTrue(self.call("get_run_history", athlete_id=aid, audience="owner")["private"])
        self.assertNotIn("private", self.call("get_trends", athlete_id=aid))
        row = self.call("get_squad_overview")["athletes"][0]
        self.assertEqual(row["activity_data"], "private (Strava)")
        self.assertNotIn("consistency_4wk_pct", row)

        # --- token refresh, rate limit, revoked access ---
        ct.connect().execute("UPDATE strava_connection SET expires_at=0").connection.commit()
        self.call("strava_sync", athlete_id=aid)
        self.assertEqual(self.fake.refreshes, 1)
        self.fake.rate_limited = True
        rl = self.call("strava_sync", athlete_id=aid)["athletes"][0]
        self.assertIn("rate limit", rl["error"])
        self.fake.rate_limited, self.fake.revoked = False, True
        rv = self.call("strava_sync", athlete_id=aid)["athletes"][0]
        self.assertIn("reconnect", rv["error"])
        self.assertEqual(self.call("strava_status", athlete_id=aid)["connections"][0]["status"], "revoked")

    def test_connect_errors(self):
        with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
            aid = self.call("ingest_intake_form", form_response_json=json.load(f))["athlete_id"]
        self.call("strava_connect_link", athlete_id=aid)
        wrong = ct.call_tool("strava_complete_connect", {"athlete_id": aid,
                             "redirect_url": "http://localhost/strava-connected?state=nope&code=goodcode&scope=read"})
        self.assertFalse(wrong["ok"])
        link = self.call("strava_connect_link", athlete_id=aid)
        st = urllib.parse.parse_qs(urllib.parse.urlparse(link["link"]).query)["state"][0]
        noscope = ct.call_tool("strava_complete_connect", {"athlete_id": aid,
                               "redirect_url": f"http://localhost/strava-connected?state={st}&code=goodcode&scope=read"})
        self.assertFalse(noscope["ok"])
        self.assertIn("View data about your activities", noscope["error"])
        denied = ct.call_tool("strava_complete_connect", {"athlete_id": aid,
                              "redirect_url": "http://localhost/strava-connected?error=access_denied"})
        self.assertIn("Cancel", denied["error"])
        os.remove(os.path.join(self.home, ".env"))
        missing = ct.call_tool("strava_connect_link", {"athlete_id": aid})
        self.assertIn("STRAVA_CLIENT_ID", missing["error"])

    def test_gate_syncs_and_respects_quiet_hours(self):
        with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
            intake = self.call("ingest_intake_form", form_response_json=json.load(f))
        aid = intake["athlete_id"]
        self.connect(aid)
        gate = self.call("install_checkin_gate", athlete_id=aid)
        self.assertEqual(gate["cronjob_call"]["schedule"], "0 * * * *")
        with open(gate["gate_script"]) as f:
            code = f.read()

        def run_gate(hour):
            os.environ["COACH_NOW_HOUR"] = str(hour)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    exec(compile(code, gate["gate_script"], "exec"), {"__name__": "__main__"})
                except SystemExit:
                    pass
            os.environ.pop("COACH_NOW_HOUR")
            return json.loads(buf.getvalue().strip().splitlines()[-1])

        self.assertFalse(run_gate(10)["wakeAgent"])                            # nothing new
        # A workout done this morning, before connecting, still gets feedback (not filed as history).
        self.fake.add(datetime.now(timezone.utc) - timedelta(minutes=5), sport="WeightTraining", minutes=40)
        self.assertFalse(run_gate(23)["wakeAgent"])                            # quiet hours: synced, held
        self.assertEqual(self.call("get_pending_feedback", athlete_id=aid)["count"], 1)
        woke = run_gate(8)
        self.assertTrue(woke["wakeAgent"])
        self.assertEqual(woke["context"]["new_activities"]["other"][0]["category"], "strength")

    def test_manual_strength_log(self):
        with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
            aid = self.call("ingest_intake_form", form_response_json=json.load(f))["athlete_id"]
        self.call("log_other_activity", athlete_id=aid, category="strength",
                  start_at=self.today.isoformat(), elapsed_min=40, name="Gym: upper body")
        s = self.call("get_athlete_summary", athlete_id=aid)
        self.assertEqual(s["recent_other_activities"][0]["name"], "Gym: upper body")
        self.assertEqual(self.call("get_pending_feedback", athlete_id=aid)["count"], 0)   # already discussed


if __name__ == "__main__":
    unittest.main()
