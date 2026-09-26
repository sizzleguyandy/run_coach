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
2. Set up intake from the form. The Google Form's responses go to a
   Sheet ("Responses" tab → link to Sheets). Ask the owner for the
   Sheet ID and the tab name (usually `Form responses 1`), and make
   sure the `google-workspace` skill is authorised for Sheets. Then
   create the poller from the **owner's** chat, so summaries go to
   them:
   ```
   cronjob(action="create", name="run-coach intake", schedule="every 1h",
           skills=["google-workspace", "run-coach"],
           workdir="<HERMES_HOME>/run_coach",
           prompt="Read every row of Sheet <SHEET_ID> tab '<TAB>' with google-workspace (sheets get). Pass the full 2-D values array (header row first) to run-coach ingest_sheet_rows. Rows already ingested are skipped automatically. For each newly ingested athlete, give the owner one line: name, goal type, and any next_steps that need the athlete (medical clearance, missing info). If nothing new was ingested, reply with only [SILENT].")
   ```
3. Set up the database backup (no LLM involved):
   `hermes cron create "every day at 2am" --no-agent --script run-coach-backup.py --name run-coach-backup`
4. Give the owner the bot's link to share with athletes, along with the
   form link. Athletes fill in the form first, then message the bot.

## 1. Playbooks

### A. New intake: form response → plan → check-ins

1. Ingest. The intake cron job does this with `ingest_sheet_rows`; for
   a single response pasted in chat, use:
   `$RC ingest_intake_form '{"form_response_json": {...keyed by question text...}}'`
   (see `examples/intake_*.json` for the shape).
2. Link the athlete. When they first message you, run
   `get_athlete_summary {"chat_ref": ...}`. If it's not linked, ask for
   their form email, then run
   `link_chat {"email": ..., "chat_ref": ...}`.
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
5. Check-ins, **in the athlete's chat**: run
   `install_checkin_gate {"athlete_id": ...}`, then call
   `cronjob_manage` with the returned `cronjob_call`, exactly as given.
   Delivery then defaults to this chat.
6. Reply to the athlete with:
   - the `summary`
   - the first week (`first_week`), written as a short list with days
     and dates
   - what's provisional:
     - **Race:** race pace isn't set until the first time trial.
     - **General fitness:** every session is at chatting pace, the
       plan only moves up after a comfortable, pain-free week, and
       it's reviewed after 12 weeks.

If `generate_program` returns `ok: false`, tell the athlete the
`reason` plainly and offer the `suggestion`. For example, someone with
too little running base for a race gets offered a general-fitness
block first.

### B. The athlete sends a run file (.fit / .gpx)

The gateway saves the attachment and tells you its path. If it
doesn't, save it under `$HERMES_HOME/run_coach/uploads/`.

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

`list_athletes`, then `get_athlete_summary` for any athlete they name.
The owner can see everything. Athletes only ever see their own data.

### K. An athlete leaves or pauses

Pause or remove their `run-coach checkins <athlete_id>` job with
`cronjob_manage`. Keep their data unless the owner explicitly asks for
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
