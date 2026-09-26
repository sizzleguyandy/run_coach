"""Owner sends the athletes' Sheet to the bot (CSV or Excel): athletes are
ingested, auto-linked by the Telegram id on the form, plans are built, the
allowlist is updated, and re-sending the Sheet changes nothing."""
import csv
import json
import os
import sys
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import coach_tools as ct  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")
TG_Q = "Your Telegram user ID (message @userinfobot on Telegram to get it)"


def sheet_rows():
    with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
        gf = json.load(f)
    with open(os.path.join(EXAMPLES, "intake_race.json")) as f:
        race = json.load(f)
    gf[TG_Q], race[TG_Q] = "1111111111", "2222222222"
    race["Race date"] = "17/01/2027"                       # how a UK/ZA-locale Sheet shows it
    header = ["Timestamp"] + list(dict.fromkeys(list(gf) + list(race)))
    flat = lambda d: ["2026/09/20 10:00"] + [", ".join(d[h]) if isinstance(d.get(h), list) else d.get(h, "")
                                             for h in header[1:]]
    return [header, flat(gf), flat(race)]


def write_xlsx(path, rows):
    """Minimal real .xlsx (shared strings), like Google Sheets' Excel download."""
    strings, idx = [], {}
    def si(v):
        if v not in idx:
            idx[v] = len(strings); strings.append(v)
        return idx[v]
    col = lambda i: (chr(65 + i) if i < 26 else chr(64 + i // 26) + chr(65 + i % 26))
    sheet = "".join(f'<row r="{r + 1}">' + "".join(
        f'<c r="{col(c)}{r + 1}" t="s"><v>{si(str(v))}</v></c>' for c, v in enumerate(row) if v != "")
        + "</row>" for r, row in enumerate(rows))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
                   f'2006/relationships"><sheets><sheet name="Form responses 1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet}</sheetData></worksheet>")
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
                   + "</sst>")


class SheetOnboarding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["COACH_DB"] = os.path.join(self.tmp.name, "coach.db")
        os.environ["HERMES_HOME"] = os.path.join(self.tmp.name, "home")
        os.makedirs(os.environ["HERMES_HOME"])
        os.environ["COACH_TODAY"] = "2026-10-01"

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("COACH_TODAY", "HERMES_HOME"):
            os.environ.pop(k, None)

    def call(self, name, **args):
        out = ct.call_tool(name, args)
        self.assertTrue(out.get("ok"), f"{name}: {out}")
        return out

    def _flow(self, path):
        res = self.call("ingest_sheet_file", file_path=path)
        self.assertEqual(len(res["ingested"]), 2, res)
        by_goal = {r["goal_type"]: r for r in res["ingested"]}
        race = by_goal["race"]
        s = self.call("get_athlete_summary", chat_ref="telegram:2222222222")
        self.assertEqual(s["active_goal"]["race_date"], "2027-01-17")   # 17/01/2027 read as day/month
        self.assertEqual(s["onboarding_status"], "plan_not_generated")
        for r in res["ingested"]:
            self.call("generate_program", athlete_id=r["athlete_id"], race_id=r["race_id"], reason="initial")
        self.assertEqual(self.call("get_athlete_summary", chat_ref="telegram:1111111111")["onboarding_status"],
                         "welcome_pending")
        # Re-sending the same Sheet: nothing new, plans untouched.
        again = self.call("ingest_sheet_file", file_path=path)
        self.assertEqual((len(again["ingested"]), again["duplicates"]), (0, 2))
        return race

    def test_csv_sheet(self):
        path = os.path.join(self.tmp.name, "responses.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(sheet_rows())
        self._flow(path)

    def test_xlsx_sheet_allowlist_and_welcome(self):
        path = os.path.join(self.tmp.name, "responses.xlsx")
        write_xlsx(path, sheet_rows())
        race = self._flow(path)

        env = os.path.join(os.environ["HERMES_HOME"], ".env")
        with open(env, "w") as f:
            f.write("TELEGRAM_BOT_TOKEN=abc\nTELEGRAM_ALLOWED_USERS=1491755393\n")
        res = self.call("sync_telegram_allowlist")
        self.assertEqual(sorted(res["added"]), ["1111111111", "2222222222"])
        self.assertTrue(res["restart_needed"])
        with open(env) as f:
            text = f.read()
        self.assertIn("TELEGRAM_ALLOWED_USERS=1491755393,", text)       # owner kept, first
        self.assertIn("TELEGRAM_BOT_TOKEN=abc", text)
        self.assertFalse(self.call("sync_telegram_allowlist")["restart_needed"])   # idempotent

        self.call("update_athlete_profile", athlete_id=race["athlete_id"],
                  fields={"welcomed_at": "2026-10-02"})
        st = self.call("get_onboarding_status")
        self.assertEqual(st["counts"], {"active": 1, "welcome_pending": 1})

    def test_edited_row_keeps_goal_and_plan(self):
        rows = sheet_rows()
        path = os.path.join(self.tmp.name, "r.csv")
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        first = self.call("ingest_sheet_file", file_path=path)["ingested"]
        for r in first:
            self.call("generate_program", athlete_id=r["athlete_id"], race_id=r["race_id"], reason="initial")
        rows[1][rows[0].index("Current weight (kg)")] = "80"             # owner corrects a detail
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        upd = self.call("ingest_sheet_file", file_path=path)["ingested"]
        self.assertEqual(len(upd), 1)
        gf_first = next(r for r in first if r["goal_type"] == "general_fitness")
        self.assertEqual(upd[0]["race_id"], gf_first["race_id"])           # same goal, plan kept
        steps = " ".join(upd[0]["next_steps"])
        self.assertNotIn("Call generate_program", steps)
        self.assertIn("already has a plan", steps)
        self.assertNotIn("Telegram id linked", steps)                   # not repeated on updates

    def test_bad_telegram_value_warns(self):
        with open(os.path.join(EXAMPLES, "intake_general_fitness.json")) as f:
            form = json.load(f)
        form[TG_Q] = "@sam_runs"
        out = self.call("ingest_intake_form", form_response_json=form)
        self.assertFalse(out["telegram_linked"])
        self.assertTrue(any("@userinfobot" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
