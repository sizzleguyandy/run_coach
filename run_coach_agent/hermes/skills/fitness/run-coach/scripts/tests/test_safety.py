"""The three safety nets: number sanity checks, the free-text health review
(keyword net + independent reviewer), and two-source race verification."""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")


class Safety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["COACH_DB"] = os.path.join(self.tmp.name, "coach.db")
        os.environ["COACH_TODAY"] = "2026-10-01"

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("COACH_TODAY", None)

    def call(self, name, **args):
        out = ct.call_tool(name, args)
        self.assertTrue(out.get("ok"), f"{name}: {out}")
        return out

    def form(self, name):
        with open(os.path.join(EXAMPLES, name)) as f:
            return json.load(f)

    def gen(self, aid, gid):
        return ct.call_tool("generate_program", {"athlete_id": aid, "race_id": gid, "reason": "t"})

    # --- 1. number sanity checks ---------------------------------------------
    def test_number_checks(self):
        f = self.form("intake_race.json")
        f["Longest run in the last 4 weeks (km)"], f["Total running km in the last 4 weeks (roughly)"] = "40", "25"
        f["Current weight (kg)"] = "165"
        out = self.call("ingest_intake_form", form_response_json=f)
        codes = {c["code"] for c in out["normalized"]["checks"]}
        self.assertTrue({"longest_gt_total"} <= codes)
        self.assertTrue(any("swapped" in w for w in out["warnings"]))
        f2 = self.form("intake_race.json")
        f2["Email"], f2["Race date"] = "past@example.com", "2025-01-17"
        bad = ct.call_tool("ingest_intake_form", {"form_response_json": f2})
        self.assertFalse(bad["ok"])
        self.assertIn("in the past", bad["error"])
        self.assertFalse(self.call("list_athletes")["athletes"][-1]["email"] == "past@example.com")  # nothing written

    def test_blocked_row_reported_in_sheet(self):
        f = self.form("intake_race.json")
        f["Race date"] = "2020-05-01"
        path = os.path.join(self.tmp.name, "s.csv")
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerows([list(f), [", ".join(v) if isinstance(v, list) else v for v in f.values()]])
        res = self.call("ingest_sheet_file", file_path=path)
        self.assertEqual((len(res["ingested"]), len(res["errors"])), (0, 1))

    # --- 2. free-text health review ------------------------------------------
    def test_hidden_heart_condition_in_free_text(self):
        f = self.form("intake_general_fitness.json")
        f["Anything else I should know?"] = "Had a stent fitted last year but feel great"
        out = self.call("ingest_intake_form", form_response_json=f)
        aid, gid = out["athlete_id"], out["race_id"]
        self.assertFalse(out["needs_medical_clearance"])                     # the box said "None of these"
        self.assertFalse(self.gen(aid, gid)["ok"])                            # ...but the plan waits for review
        self.assertEqual(self.call("get_athlete_summary", athlete_id=aid)["onboarding_status"],
                         "awaiting_safety_review")
        tasks = self.call("get_review_tasks", athlete_id=aid)
        t = tasks["delegate_call"]["tasks"][0]
        self.assertIn("stent", t["context"])
        self.assertIn("heart", t["context"])                                  # keyword hint passed on
        for private in ("Sam", "sam@example.com", "1111111111"):
            self.assertNotIn(private, t["context"])                           # reviewer gets no identity
        self.assertEqual(t["output_schema"]["properties"]["outcome"]["enum"], ["clear", "caution", "needs_clearance"])

        # Reviewer says needs_clearance, coach said caution -> the cautious one wins.
        r = self.call("record_safety_review", athlete_id=aid, reviewer_outcome="needs_clearance",
                      own_outcome="caution", reasons=["heart stent"])
        self.assertEqual(r["final"], "needs_clearance")
        blocked = self.gen(aid, gid)
        self.assertFalse(blocked["ok"])
        self.assertIn("medical clearance", blocked["reason"])
        self.call("update_athlete_profile", athlete_id=aid, fields={"medical_clearance_at": "2026-10-02"})
        self.assertTrue(self.gen(aid, gid)["ok"])

        # Re-sending the same row keeps the review; changing the text re-opens it.
        self.call("ingest_intake_form", form_response_json={**f, "Current weight (kg)": "81"})
        self.assertEqual(self.call("get_athlete_summary", athlete_id=aid)["athlete"]["safety_screen_status"],
                         "needs_clearance")
        self.call("ingest_intake_form", form_response_json={**f, "Anything else I should know?": "Also asthma"})
        self.assertEqual(self.call("get_athlete_summary", athlete_id=aid)["athlete"]["safety_screen_status"],
                         "pending_review")

    def test_clearing_medical_words_needs_a_reason(self):
        f = self.form("intake_general_fitness.json")
        f["Any current injuries or old injuries relevant to running"] = "No heart problems, nothing else"
        aid = self.call("ingest_intake_form", form_response_json=f)["athlete_id"]
        refused = ct.call_tool("record_safety_review", {"athlete_id": aid, "reviewer_outcome": "clear",
                                                        "own_outcome": "clear"})
        self.assertFalse(refused["ok"])
        self.call("record_safety_review", athlete_id=aid, reviewer_outcome="clear", own_outcome="clear",
                  notes="text explicitly says no heart problems")

    def test_trivial_free_text_needs_no_review(self):
        f = self.form("intake_general_fitness.json")
        f["Any current injuries or old injuries relevant to running"] = "None"
        f["Fixed weekly commitments (gym days, work shifts, anything that can't move)"] = "n/a"
        f["What does \"fitter\" mean to you? (e.g. run 30 min non-stop, keep up with my kids, lose weight, "
          "feel less out of breath)"] = ""
        out = self.call("ingest_intake_form", form_response_json=f)
        self.assertEqual(self.call("get_review_tasks", athlete_id=out["athlete_id"])["tasks"], [])
        self.assertTrue(self.gen(out["athlete_id"], out["race_id"])["ok"])

    # --- 3. race verification -------------------------------------------------
    def _race(self):
        f = self.form("intake_race.json")
        f["Fixed weekly commitments (gym days, work shifts, anything that can't move)"] = "none"
        out = self.call("ingest_intake_form", form_response_json=f)
        return out["athlete_id"], out["race_id"]

    def test_race_sources_agree(self):
        aid, gid = self._race()
        self.assertEqual(self.call("get_athlete_summary", athlete_id=aid)["onboarding_status"], "awaiting_race_check")
        self.assertFalse(self.gen(aid, gid)["ok"])
        task = self.call("get_review_tasks", athlete_id=aid)["tasks"][0]
        self.assertIn("Example Valley Half Marathon", task["goal"])
        v = self.call("verify_race_info", race_id=gid,
                      primary={"race_date": "2027-01-17", "distance_km": 21.1, "elevation_gain_m": 445,
                               "terrain": "farm paths", "source": "https://example.com/valley-half"},
                      secondary={"race_date": "2027-01-17", "distance_km": 21.0975, "elevation_gain_m": 470,
                                 "source": "https://results.example.org/valley"})
        self.assertEqual(v["status"], "verified")
        plan = self.gen(aid, gid)
        self.assertTrue(plan["ok"])
        self.assertIn("hill", plan["summary"].lower())                        # verified elevation used

    def test_race_sources_disagree_then_owner_confirms(self):
        aid, gid = self._race()
        same = self.call("verify_race_info", race_id=gid,
                         primary={"race_date": "2027-01-17", "distance_km": 21.1, "source": "https://a.example/x"},
                         secondary={"race_date": "2027-01-17", "distance_km": 21.1, "source": "https://a.example/x/"})
        self.assertEqual(same["status"], "mismatch")                          # not independent
        v = self.call("verify_race_info", race_id=gid,
                      primary={"race_date": "2027-01-24", "distance_km": 21.1, "source": "https://a.example"},
                      secondary={"race_date": "2027-01-24", "distance_km": 21.1, "source": "https://b.example"})
        self.assertEqual(v["status"], "mismatch")                             # both say 24th, form said 17th
        self.assertTrue(any("form says 2027-01-17" in d for d in v["differences"]))
        refused = self.gen(aid, gid)
        self.assertFalse(refused["ok"])
        self.assertIn("disagree", refused["reason"])
        self.assertEqual(self.call("get_athlete_summary", athlete_id=aid)["onboarding_status"],
                         "race_details_disputed")
        self.call("confirm_race_info", race_id=gid, race_date="2027-01-24", note="owner checked the organiser site")
        self.assertTrue(self.gen(aid, gid)["ok"])
        s = self.call("get_athlete_summary", athlete_id=aid)
        self.assertEqual(s["active_goal"]["race_date"], "2027-01-24")


if __name__ == "__main__":
    unittest.main()
