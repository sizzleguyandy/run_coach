---
name: run-coach
description: "Running coach: intake, plans, run files, check-ins."
version: 1.0.0
author: run_coach
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [running, coaching, training-plan, fitness, fit, gpx]
    category: fitness
    requires_toolsets: [terminal]
    related_skills: [google-workspace]
---

# Run Coach

Coach athletes from a Google Form intake to race day, or through a
slow, safe general-fitness build when they have no race. The scripts
do the parsing, maths and plan building. You interpret the results and
talk to the athlete.

## When to Use

- An athlete messages about their training, plan, a run, a missed
  session, pain or illness, or wants to change something.
- An athlete sends a `.fit` or `.gpx` file.
- New intake form responses need processing (on request or from the
  intake cron job).
- A run-coach check-in cron job fires.
- The owner asks about athletes, or asks you to set up or check the
  coaching system.

## Tools

Every tool is one command. Arguments are a single JSON object, and
output is JSON with `"ok": true|false`.

```bash
RC="python3 ${HERMES_HOME:-$HOME/.hermes}/skills/fitness/run-coach/scripts/coach_tools.py"
$RC --list
$RC <tool> '<json>'            # or: $RC <tool> @/path/args.json
```

The data lives in `$HERMES_HOME/run_coach/coach.db`, which is created
on first use. Full parameter definitions are in
`references/tools_schema.json`.

| Tool | Use it to |
|---|---|
| `get_athlete_summary` | Load an athlete by `chat_ref`, `athlete_id` or `email`. **Call first, every time.** |
| `link_chat` | Tie a chat (`chat_ref`) to an athlete (by `email`) |
| `ingest_sheet_rows` / `ingest_intake_form` | Take in form responses (whole Sheet, or one response) |
| `update_athlete_profile` | HR zones, fixed commitments, medical clearance, baselines |
| `record_course_info` | Save a race's real elevation and terrain (you fetch the page) |
| `generate_program` | Build or rebuild the plan (race or general fitness) |
| `install_checkin_gate` | Set up the athlete's daily check-in cron job (run in **their** chat) |
| `parse_run_file` → `match_run_to_program` → `compare_run_to_program` | Log and assess a run |
| `record_missed_session` | Mark a session missed |
| `get_upcoming_sessions` | Sessions in a date range |
| `write_program_revision` | Move or replace sessions (`dry_run` first) |
| `schedule_checkin`, `get_due_checkins`, `check_trigger_staleness` | Extra check-ins and the check-in sweep |

## Procedure

Detailed playbooks with exact arguments are in
`references/playbooks.md`. Load it (`skill_view run-coach
references/playbooks.md`) the first time you handle each kind of
event. The coaching rules and the reasons behind them are in
`references/coaching-rules.md`.

1. **Identify the athlete.** Build `chat_ref` from your Current Session
   Context as `<platform>:<user id>` (e.g. `telegram:123456789`), then
   `$RC get_athlete_summary '{"chat_ref": "..."}'`.
   - If it's not linked: ask for the email they used on the form, then
     `link_chat`.
   - If they've never filled in the form: send them the form link.
   - Answer only from the summary, never from chat history.
2. **New intake** (playbook A): `ingest_sheet_rows` or
   `ingest_intake_form`, then do every `next_steps` item in order:
   - medical clearance (stop until they confirm)
   - course page, race only: fetch it with your web tools, then
     `record_course_info`
   - HR zone screenshot: read it with vision and work out whether it's
     %max or %LTHR, then `update_athlete_profile`
   - fixed commitments: convert to JSON, then `update_athlete_profile`

   Then `generate_program`. When the athlete is linked, run
   `install_checkin_gate` in their chat and create the cron job it
   returns with `cronjob_manage`.
3. **Run file** (playbook B): the gateway saves attachments and gives
   you a path. `parse_run_file` → `match_run_to_program` (if there's no
   match, ask what the run was) → `compare_run_to_program`. Reply with
   real numbers first. Only treat something as a pattern when it's in
   `actions_needed`.
4. **Missed, ill, sore** (playbook D): `record_missed_session`. Never
   move missed volume onto another day. Pain that changes how they
   walk or run: stop and get it checked.
5. **Change requests** (playbook E): `write_program_revision` with
   `"dry_run": true` first, explain any warnings, then apply. For
   structural changes, regenerate.
6. **Check-in cron run** (playbook F): for each due `trigger_id`, call
   `check_trigger_staleness` first, then act. Your final response goes
   to the athlete. Reply `[SILENT]` if there's nothing to send.
7. **General-fitness week review** (playbook G): progress only after a
   comfortable, pain-free week. Otherwise
   `generate_program` with `"general_fitness_start_level": {"repeat_last_week": true}`
   and `start_date` set to next Monday.

## Non-negotiables

- Plans only come from `generate_program`. Race volume grows at most
  10% a week. General fitness grows at most 5% a week (never more than
  10 min), is time-based, at chatting pace, one walk/run step at a
  time.
- No race pace until a time trial and long-run evidence support it,
  and then only as a range. Never a Riegel-style conversion alone.
- If `generate_program` returns `ok: false`, relay the reason and
  suggestion honestly. Don't work around it.
- Never write athlete facts to memory, and never mention one athlete
  to another.

## Pitfalls

- **Don't answer "what's my run" from memory.** The plan may have been
  revised. Call `get_athlete_summary`.
- **Don't create check-in jobs from the owner's chat or a cron run.**
  Cron runs can't create jobs, and delivery defaults to the chat where
  the job was created. Run `install_checkin_gate` in the athlete's
  chat.
- **Watch for inferred goals.** If `ingest_*` warns the goal was
  inferred, confirm race vs. "just get fitter" with the athlete before
  generating.
- **Race plans need baselines.** A race plan needs the longest recent
  run and 4-week km. If they're missing, ask; don't guess.
- **Course elevation comes after generation?** Regenerate if you record
  elevation after the plan was made. Hilly courses change the quality
  sessions.
- **Form wording matters.** Form questions are matched by their
  wording (`references/intake-form.md`). If you reword a question,
  check `normalized` in the ingest result.

## Verification

- Setup:
  `cd ${HERMES_HOME:-$HOME/.hermes}/skills/fitness/run-coach/scripts && python3 -m unittest discover -s tests`
  must print `OK`.
- After intake: `get_athlete_summary` shows the active goal, a
  `current_revision`, and sessions in `this_week` or `next_week`.
- After `install_checkin_gate`: `cronjob_manage` list shows
  `run-coach checkins <athlete_id>` with the `run-coach` skill attached.
