# Run Coach for Hermes Agent

An AI running coach packaged for [Hermes Agent](https://hermes-agent.nousresearch.com/)'s
file layout. Athletes fill in a Google Form, choosing either a race or
*"No event — I just want to get fitter"*. They then chat with the bot,
send `.fit`/`.gpx` files after runs, and get check-ins. Deterministic
code builds the plans and does the maths. The agent interprets and
talks.

## Install

```bash
hermes profile create run-coach          # recommended: its own SOUL, memory, cron, skills
./install.sh ~/.hermes/profiles/run-coach
# or into your default home:  ./install.sh   (backs up your SOUL.md first)
```

Then start a **new** session in that profile (`hermes -p run-coach chat`,
or its gateway) and say `/run-coach set up the coaching system`.

Requires Python 3.10+ (standard library only), the `terminal` toolset,
and, for form intake, the bundled `google-workspace` skill authorised
for Sheets. To receive athlete messages and files, connect a messaging
gateway such as Telegram.

## What goes where

`hermes/` mirrors `$HERMES_HOME`. `install.sh` copies it in.

| Path in `$HERMES_HOME` | Hermes role | Contents |
|---|---|---|
| `SOUL.md` | Slot #1 of the system prompt: identity | Coach's voice, how it decides, safety and privacy lines. Kept short because it loads every turn |
| `memories/MEMORY.md` | Agent-maintained notes | Seeded with 4 entries: where the DB is, athlete data never goes in memory, how to identify athletes, how check-ins work. The agent maintains it from there |
| `memories/USER.md` | Agent-maintained profile of **you** | Not shipped. The agent fills it in as it learns about the owner |
| `skills/fitness/run-coach/SKILL.md` | On-demand skill (`/run-coach`) | When to use it, every tool, the core procedure, pitfalls, verification |
| `skills/fitness/run-coach/references/` | Loaded on demand | `playbooks.md` (step by step), `coaching-rules.md` (rules and reasons), `intake-form.md` (form spec), `tools_schema.json` |
| `skills/fitness/run-coach/scripts/` | Skill scripts | `coach_tools.py` (all tools, command line), race and general-fitness generators, `trends.py`, `history.py`, FIT/GPX parsers, analysis, `schema.sql`, `tests/` |
| `skills/fitness/run-coach/examples/` | Examples | Sample race and general-fitness form responses, a sample GPX |
| `run_coach/AGENTS.md` | Project rules for the workspace | Loaded into cron jobs, which run with `workdir` set here |
| `run_coach/coach.db` | Data: live coaching state | Plans, sessions, signals. Created on first use, outside the skill so updates never touch it |
| `run_coach/history.db` | Data: permanent run history | Every run ever uploaded: the original file, every data point, every upload attempt (including failed and duplicate ones), and what each run was matched to. Append-only: the database rejects changes and deletions |
| `scripts/run-coach-backup.py` | Cron script (no-agent) | Daily backup of both databases, keeps 30 of each |
| `scripts/run-coach-due-<id>.py` | Cron pre-run gate | Generated per athlete; only wakes the agent when a check-in is due |

## Run history

Every run file an athlete sends is recorded permanently in `history.db`:
- the original `.fit`/`.gpx` file, recoverable byte for byte with
  `export_run_file`
- every recorded data point
- every upload attempt, including duplicates and unreadable files
- an event log of what the run was matched and compared to

Nothing in it can be edited or deleted; database triggers refuse.
Rebuilding a plan never touches it. `get_run_history` lists an
athlete's full history with all-time totals.

## Progress trends

Every run, session (done or missed), plan change and warning sign is
kept. `get_trends` turns that history into feedback that gets richer
as it grows:

- **Under 2 weeks or 4 runs:** no trends yet; it tells the athlete when
  they start.
- **2–3 weeks:** consistency and streaks, weekly volume against the
  plan, walk/run step progress.
- **4–7 weeks:** adds aerobic fitness (easy pace at the same heart
  rate), long-run heart-rate drift, the most-missed weekday, and
  time-trial progression.
- **8+ weeks:** adds a comparison of the last 4 weeks with the 4
  before.

Every line comes with its evidence (e.g. "13 easy runs over 26 days").
Weekly check-ins include the 1–3 most useful lines automatically.
`get_squad_overview` gives you, the owner, one table across all
athletes, with a "needs attention" list.

## Scheduled jobs

Created by the agent during setup and onboarding, following
`references/playbooks.md`:

- **`run-coach intake`** (hourly, from the owner's chat): reads the
  form's response Sheet and ingests new rows. Duplicates are skipped.
  Sends you a one-line summary per new athlete, and stays silent
  otherwise.
- **`run-coach checkins <athlete>`** (daily, created in that athlete's
  chat so it delivers to them): its gate script costs nothing unless
  something is due. It covers the weekly review, after each time
  trial, and the end of a general-fitness block. Athletes can reply to
  it.
- **`run-coach-backup`** (daily, no LLM): backs up `coach.db` and
  `history.db`.

## Notes on Hermes behaviour this relies on

- **Session start:** SOUL.md and memory are read at session start.
  Start a new session after installing or editing them.
- **Shared memory:** USER.md and MEMORY.md are shared across every
  chat on a profile. That's why athlete data lives only in `coach.db`,
  and SOUL.md tells the agent never to put it in memory. Using a
  dedicated profile keeps your own memory separate from the coach's.
- **No auto-compaction:** memory is capped at 2,200 chars for
  MEMORY.md and 1,375 for USER.md. The seed uses about 770.
- **Cron limits:** cron runs can't create cron jobs, and a job
  delivers to the chat it was created in. That's why check-in jobs are
  created from each athlete's own chat.

## Developing

```bash
cd hermes/skills/fitness/run-coach/scripts
python3 -m unittest discover -s tests        # 16 tests (incl. a 9-week trend simulation and history-is-permanent checks)
python3 general_fitness.py                   # preview a general-fitness block
python3 race_plan.py                         # preview a race build
```
