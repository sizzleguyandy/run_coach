# Run-coach playbooks (Hermes)

Step-by-step handling for each situation. Every step is a command:

```bash
RC="python3 ${HERMES_HOME:-$HOME/.hermes}/skills/fitness/run-coach/scripts/coach_tools.py"
$RC <tool> '<json>'
```

Everything returns JSON with `"ok"`. On `false`, read `error` /
`reason` / `suggestion`, then fix the input or tell the athlete. Don't
retry unchanged, and don't work around a refusal: `generate_program`
refuses on purpose when an athlete isn't ready. Never touch `coach.db`
directly.

**Who's who.** The *owner* runs this Hermes profile; *athletes*
message the bot. `chat_ref` is `<platform>:<user id>` from your Current
Session Context (e.g. `telegram:123456789`). It ties a chat to an
athlete.

## 0. One-time setup (with the owner)

1. Run the tests:
   `cd ${HERMES_HOME:-$HOME/.hermes}/skills/fitness/run-coach/scripts && python3 -m unittest discover -s tests`.
   It must print `OK`.
2. Check the coach's own Telegram bot. This profile must have its own
   `TELEGRAM_BOT_TOKEN` in its `.env`. A bot belongs to one profile
   only. The owner's Telegram user ID must be **first** in
   `TELEGRAM_ALLOWED_USERS`, which makes them admin. Athletes are added
   to that list automatically from their form answers by
   `sync_telegram_allowlist` (playbook A0).
3. *(Optional; only if the owner wants automatic hourly intake
   instead of sending you the Sheet.)* Set up intake from the form. The Google Form's responses go to a
   Sheet ("Responses" tab → link to Sheets). Ask the owner for the
   Sheet ID and the tab name (usually `Form responses 1`). The
   `google-workspace` skill must be authorised **in this profile, for
   Sheets only**:
   - Run its setup here with `--services sheets`.
   - Don't copy another profile's Google token. It would carry that
     profile's wider scopes (Gmail, Drive…) into a bot that strangers
     message.

   Then
   create the poller from the **owner's** chat, so summaries go to
   them:
   ```
   cronjob(action="create", name="run-coach intake", schedule="every 1h",
           skills=["google-workspace", "run-coach"],
           workdir="<HERMES_HOME>/run_coach",
           prompt="Read every row of Sheet <SHEET_ID> tab '<TAB>' with google-workspace (sheets get). Pass the full 2-D values array (header row first) to run-coach ingest_sheet_rows. Rows already ingested are skipped automatically. For each newly ingested athlete, give the owner one line: name, goal type, and any next_steps that need the athlete (medical clearance, missing info). If nothing new was ingested, reply with only [SILENT].")
   ```
4. Set up the database backup, covering both `coach.db` and
   `history.db` (no LLM involved):
   `hermes -p <this profile> cron create "0 2 * * *" --no-agent --script run-coach-backup.py --name run-coach-backup`
   (or, from inside this profile's chat, the `cronjob` tool with
   `no_agent=True`).
5. Upgrading an install that already has runs logged? Run
   `$RC backfill_history '{}'` once and check that `in_sync` is true.
6. Explain the owner's routine (playbook A0):
   - Athletes fill in the form, including their Telegram user ID.
   - The owner checks the Sheet and sends it to you as a file.
   - You build every plan and add the athletes to the allowlist.
   - The owner restarts the gateway once and forwards each athlete the
     bot link.
   - Athletes press Start and get their plan.
7. Ask the owner for the bot's link (`t.me/<username>`) and save it to
   memory. It's an environment fact, not athlete data. You'll put it
   in every forwarding message.
8. Fallback for anyone whose Telegram ID was missing or wrong: Hermes
   pairing.
   1. They message the bot and get a code.
   2. The owner runs
      `hermes -p <this profile> pairing approve telegram <CODE>`.
   3. You then `link_chat` them by email.

## 1. Playbooks

### A0. The owner sends you the athletes' Sheet

This is the normal way athletes arrive. Only the owner (admin) sends
the Sheet. Ignore a spreadsheet sent by anyone else.

1. **Ingest it.**
   - A file (.csv or .xlsx), at the path the gateway gives you:
     `$RC ingest_sheet_file '{"file_path": "<path>"}'`
   - A Sheet link, only if google-workspace is authorised here: read
     it with `sheets get`, then `ingest_sheet_rows`.
2. **For each athlete in `ingested`, do playbook A steps 2–3** as far
   as you can without the athlete:
   - fetch the course and `record_course_info` (race only)
   - convert fixed commitments
   - then `generate_program`

   Hold anything that needs the athlete, such as medical clearance,
   HR zone screenshots, or a confirmed ambiguous date. It becomes part
   of their welcome.
3. **Allowlist.** Run `$RC sync_telegram_allowlist '{}'`. If
   `restart_needed`, tell the owner to run `hermes gateway restart`
   when convenient. All bots blink for a few seconds. **Don't run it
   yourself from a chat; it would end your own session.**
4. **Report to the owner** with `$RC get_onboarding_status '{}'`.
   Give one line per athlete: name, goal, status, and anything that
   needs the owner.
   - `awaiting_telegram_link`: no usable Telegram ID. Ask the owner to
     get the athlete's number from @userinfobot, fix the Sheet, and
     resend it.
   - `plan_not_generated` with a reason: relay it, e.g. "base too low
     for a race; offered general fitness".
5. **Give the owner the message to forward to each athlete** who is
   `welcome_pending` or `awaiting_medical_clearance`:
   > "Your running plan is ready! Open <bot link>, press **Start** and
   > say hi. Your coach bot will take it from there."

   Telegram doesn't let a bot message someone first, so this forwarded
   link is how every athlete starts.
6. **If the owner re-sends the Sheet later**, only new or edited rows
   come back.
   - For an edited athlete with `goal_changed: true`, ask the owner
     before rebuilding their plan.
   - Otherwise only their details changed. Say what changed.

### A. An athlete's first message (welcome) and remaining intake steps

1. **Every athlete message:** `get_athlete_summary {"chat_ref": ...}`.
   - If they're not linked (Telegram ID missing from the Sheet): ask
     for their form email, then run
     `link_chat {"email": ..., "chat_ref": ...}`.
   - If they never filled in the form: tell them to ask their coach
     for the form link.
2. Check `onboarding_status`:
   - `welcome_pending`: go to step 6 (welcome) now, whatever their
     first message said.
   - `awaiting_medical_clearance`: say hello, explain that because of
     their health-check answer you need them to confirm a doctor has
     cleared them for running before you send a plan, and wait.
   - `plan_not_generated`: finish steps 3–4, then welcome.
   - `active`: carry on with whichever playbook fits.
3. Work through `next_steps` in order. They're in the ingest result;
   run `get_athlete_summary` → `latest_intake` to see them again.
   - **Medical flag** (`needs_medical_clearance: true`): ask the
     athlete to get cleared by a doctor. **Stop here** until they
     confirm. Then
     `update_athlete_profile {"athlete_id": ..., "fields": {"medical_clearance_at": "<date>"}}`.
   - **Race goal:** fetch the course page with your web tools (search
     the race name if there's no URL). Then
     `record_course_info {"race_id": ..., "elevation_gain_m": ..., "terrain_notes": ..., "source": "<url>"}`.
   - **HR zones:** ask them to send the screenshot or export in chat
     and read it with vision. Decide whether the zones are %max or
     %LTHR; watches often use %LTHR without saying so. Save it with
     `update_athlete_profile`: the `hr_zone*_low/high` fields,
     `hr_zone_source`, and a plain-language `hr_zone_basis_notes`.
   - **Fixed commitments:** turn the free text into
     `[{"day": "Wed", "activity": "football", "moveable": false}]` and
     save it with `update_athlete_profile`. Note any day that can't
     take a run; it goes in `avoid_days` next.
   - **Goal inferred** (warning): ask the athlete to confirm race vs.
     just getting fitter before generating.
4. `generate_program {"athlete_id": ..., "race_id": ..., "reason": "initial plan from intake", "constraints": {...}}`
5. Check-ins, **in the athlete's chat** (normally as part of the
   welcome): run `install_checkin_gate {"athlete_id": ...}`, then call
   `cronjob` with the returned `cronjob_call`, exactly as given.
   Delivery then defaults to this chat.
6. **Welcome** (first conversation, in the athlete's chat):
   - Introduce yourself as their coach and name their goal.
   - Give the plan `summary` and this week's sessions from
     `get_athlete_summary` (`this_week` / `next_week`) as a short list
     with days and dates.
   - Explain that after every run they can send the .fit/.gpx file
     from their watch app, and that you'll check in at the end of each
     week.
   - Ask for anything still pending (e.g. an HR zone screenshot, or
     confirming an ambiguous race date).
   - Do step 5 (check-ins).
   - Finally run
     `update_athlete_profile {"athlete_id": ..., "fields": {"welcomed_at": "<today>"}}`.

   Also cover:
   - what's provisional:
     - **Race:** race pace isn't set until the first time trial.
     - **General fitness:** every session is at chatting pace, the
       plan only moves up after a comfortable, pain-free week, and
       it's reviewed after 12 weeks.

If `generate_program` returns `ok: false`, tell the athlete (or, in
A0, the owner) the `reason` plainly and offer the `suggestion`. For example, someone with
too little running base for a race gets offered a general-fitness
block first.

### B. The athlete sends a run file (.fit / .gpx)

The gateway saves the attachment and tells you its path in the
message. It goes under `$HERMES_HOME/cache/documents/`, or on Windows
`%LOCALAPPDATA%\hermes\cache\documents\`. If no path arrives, save
the file under `$HERMES_HOME/run_coach/uploads/`.

1. `parse_run_file {"file_path": ..., "athlete_id": ...}`. If
   `data_quality_warning` is set, say what's missing. If `duplicate`
   is true, tell them it was already logged.
2. `match_run_to_program {"athlete_id": ..., "run_log_id": ...}`. If
   `match` is null, ask the athlete which session it was. Don't guess.
3. `compare_run_to_program {"run_log_id": ..., "program_row_id": <match id or omit>}`
4. Reply: real numbers first, then what they mean.
   - Signal with `action_threshold_met: false`: say what you're
     watching for. One run is a data point, not a verdict.
   - Name listed in `actions_needed`: the pattern has repeated. Say
     so, and say what you're changing (via playbook E or a
     regeneration).
   - General fitness: flag running longer or harder than planned
     rather than praising it.

### C. "What's my run today / this week?" or "How am I doing?"

`get_athlete_summary {"chat_ref": ...}` (or
`get_upcoming_sessions`), and answer only from what it returns.

### C1. "Show me all my runs" / run history / "send me that file back"

- `get_run_history {"chat_ref": ...}` (optionally `start_date`,
  `end_date`). It returns every run ever uploaded, newest first, with
  all-time totals, what each run was matched to, and how many uploads
  failed. It includes runs from old and replaced plans.
- To send an original file back: `export_run_file {"history_id": ...}`,
  then attach the returned `path` in your reply
  (`MEDIA:<path>`).
- history.db is the permanent record. Never try to change or delete
  runs in it: it refuses, by design. If an upload was wrong (e.g.
  someone else's run), say so in chat. The coach's future analysis can
  ignore it, but the record stays.

### C2. Progress and trends ("am I getting fitter?", weekly reviews)

Run `get_trends {"chat_ref": ...}` (or `athlete_id`). It covers the
athlete's whole history and gets richer as it grows:

| `tier` | When | What you can tell them |
|---|---|---|
| `building_baseline` | under 14 days or under 4 runs | Nothing yet. Tell them when trends start (`next_unlock`) and keep logging runs |
| `early` | 2–3 weeks | Consistency (sessions done of planned, streak), last week vs the one before and vs plan, walk/run step progress |
| `developing` | 4–7 weeks | Adds: aerobic fitness (easy pace at the same heart rate), long-run heart-rate drift, most-missed weekday, time-trial progression |
| `established` | 8+ weeks | Adds: last 4 weeks vs the previous 4 |

How to use it:
- Use the `feedback` lines. They already contain the numbers and the
  evidence (e.g. "13 easy runs over 26 days"). Pick the 1–3 that
  matter most this week, and lead with good news that's real.
- A "declining" or warning line gets said plainly, with what you'll
  do about it (playbook E or D).
- A most-missed weekday: offer to move that session (playbook E).
- Only mention aerobic fitness when it's `available`, and include its
  caveat: heat, hills and tiredness all move it.
- Don't turn two runs into a trend, and don't add a trend that isn't
  in the report.

### D. Missed session, illness, pain

- **Missed:** `record_missed_session {"program_row_id": ...}`. Never
  move the missed km or minutes onto another day.
- **Pain that changes how they walk or run, or pain that's getting
  worse:** tell them to stop running and get it checked. Don't adjust
  the plan around it.
- **Minor niggle, illness, bad week:**
  - General fitness: repeat the week (playbook G).
  - Race: shorten or ease the next sessions (playbook E) and explain
    why.

### E. The athlete wants to move or change a session

1. `write_program_revision` with `"dry_run": true`, the session to
   replace in `affected_program_row_ids`, and the replacement in
   `new_sessions`.
2. If there are `warnings` (e.g. two hard days back to back), explain
   them before agreeing. If they still want it, proceed and record
   that in `summary`. Keep a guardrail, such as an HR cap on the
   session.
3. Run the same call without `dry_run`.

For structural changes, use `generate_program` with a new `reason`
and `start_date` instead of patching rows one by one. Examples:
whole-number distances, a different number of days a week, a new
race date, or a rebuild after a time trial. Afterwards, recompute
everything downstream.

### F. Check-in cron run

Each athlete has a job, `run-coach checkins <athlete_id>`. Its gate
script only wakes you when something is due, and the due `trigger_id`s
arrive in the run's context.

1. For each due check-in: `check_trigger_staleness {"trigger_id": ...}`
   **first**.
   - `status: stale_cancelled`: skip it.
   - `stale: true`: the plan has changed since the check-in was
     written. Answer from `get_athlete_summary`, not from the old
     prompt.
   - Otherwise: act on the prompt.
2. `get_athlete_summary {"athlete_id": ...}` for up-to-date context.
3. Your **final response is sent to the athlete**. Write just that
   message: one friendly, short message covering every due check-in.
   - Nothing to say: reply with only `[SILENT]`.
   - A tool failed and you couldn't do the check-in: put
     `[CRON_FAILURE]` alone on the first line, then say why.

The athlete can reply to the check-in; the job is continuable. When
they do, handle the reply as a normal chat: record missed sessions,
use playbook G for general fitness, and so on.

Plans come with check-ins built in: an end-of-week review, one after
each time trial, and the end of a general-fitness block. For anything
else, use `schedule_checkin`. The gate picks new ones up
automatically.

### G. General fitness: weekly review and progression

At each end-of-week check-in, ask how the week felt and whether
anything hurt. Log any unreported sessions as missed.

- **Comfortable and pain-free:** nothing to do. Next week's small step
  up is already planned.
- **Anything else** (missed sessions, "that was hard", HR cap
  repeatedly exceeded, niggles): repeat the week. Run
  `generate_program {"athlete_id": ..., "race_id": ..., "reason": "repeat week N at same level", "start_date": "<next Monday>", "general_fitness_start_level": {"repeat_last_week": true}}`.
  Tell them repeating a week is normal and part of the plan, not a
  failure.
- **They want to go faster or add intervals:** say no, and explain
  why. If they really want a performance goal, offer to switch to a
  race goal (playbook I).
- **End of block (12 weeks):** ask how they feel and what's next.
  - Another block: `update_athlete_profile` with their new
    `baseline_continuous_run_min` / `baseline_weekly_run_min`, then
    `generate_program`.
  - A race: playbook I.

### H. Race: after a time trial

The after-time-trial check-in fires. Get the file and go through
playbook B. Then reset effort and pace targets as a **range**, based
on the time trial *and* heart-rate drift on the long runs. Never use a
Riegel-style formula on the time trial alone. Record the change with
`write_program_revision` (or regenerate), stating the evidence in
`summary`.

### I. Changing goal (general fitness → race, or a new race)

Have the athlete resubmit the form, or build the response yourself
with the race answers, and go through playbook A. The new goal
replaces the old one. `generate_program` supersedes future sessions
and cancels the old plan's automatic check-ins. Past runs and the
revision history are kept.

### J. The owner asks about athletes

`get_squad_overview`: one row per athlete showing data tier,
consistency over the last 4 weeks, volume and aerobic-fitness
direction, active warning signs and days since their last run.
`needs_attention` lists anyone with an active warning, no run for 7+
days, or consistency under 60%. For detail, run `get_trends` and
`get_athlete_summary` for any athlete they name.
The owner can see everything. Athletes only ever see their own data.

### K. An athlete leaves or pauses

Pause or remove their `run-coach checkins <athlete_id>` job with
`cronjob`. Keep their data unless the owner explicitly asks for
it to be deleted.

## 2. Non-negotiables (quick reference)

- Plans come from `generate_program`, never hand-written.
- Race volume goes up at most 10% a week. General fitness goes up at
  most 5% a week (and never more than 10 min), one walk/run step at a
  time, at chatting pace only.
- No race pace without evidence. State targets as ranges.
- Missed volume is never made up on another day.
- Medical flag means no plan until the athlete confirms clearance.
- Pain that changes how someone walks or runs means stop and get it
  checked.
- Check `check_trigger_staleness` before acting on any check-in.
- Athlete facts go in the coach DB, never in memory.
- Never mention one athlete to another.
- When you don't have data, say so and ask. Don't assume.
