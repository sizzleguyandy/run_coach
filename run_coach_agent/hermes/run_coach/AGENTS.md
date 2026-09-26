# Run coach workspace

This folder (`$HERMES_HOME/run_coach/`) is the run-coach's working
directory. Cron jobs created by the `run-coach` skill run here, so this
file is loaded into their context.

## Layout
- `coach.db`: the SQLite database. Created on first use. **Back it up;**
  it is every athlete's history.
- `uploads/`: save athletes' run files (.fit/.gpx) here before
  parsing, if the gateway hasn't already saved them somewhere.
- `backups/`: daily copies of the database, written by the
  `run-coach-backup` cron job (`$HERMES_HOME/scripts/run-coach-backup.py`).
- Code and instructions: `$HERMES_HOME/skills/fitness/run-coach/`
  (load the skill; its `references/` hold the full rules and playbooks).

## Commands
```bash
RC="python3 ${HERMES_HOME:-$HOME/.hermes}/skills/fitness/run-coach/scripts/coach_tools.py"
$RC --list                                  # every tool
$RC get_athlete_summary '{"chat_ref": "telegram:123456789"}'
$RC get_due_checkins '{}'
$RC get_trends '{"chat_ref": "telegram:123456789"}'
$RC get_squad_overview '{}'                   # owner only
```

## Rules for work in this folder
- Only change `coach.db` through `coach_tools.py`. No raw SQL, no
  editing rows by hand, no deleting the file.
- Plans come from `generate_program` and are never written by hand.
  Every change to future sessions goes through `write_program_revision`
  or a regeneration.
- In a cron run your final response is delivered to the athlete. Write
  only the message to them, or `[SILENT]` if there's nothing to send.
  Put `[CRON_FAILURE]` on the first line if a tool failed and the
  check-in couldn't be done.
