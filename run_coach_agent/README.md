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

## Safety checks before any plan

- **Number checks** run the moment a form comes in: swapped answers,
  pounds instead of kg, implausible distances, a race too soon or too
  far off. A race date in the past rejects the row.
- **Free-text health review.** If an athlete writes something like
  "had a stent last year" but ticks "None of these", a keyword net
  flags it. An **independent reviewer agent** (Hermes `delegate_task`)
  reads everything they wrote, and the coach does too; the more
  cautious answer wins. "Needs clearance" blocks the plan exactly like
  a ticked health box.
- **Race check.** The coach and a second agent each look the race up
  from different sources. Date, distance and elevation must agree with
  each other and the form, otherwise you decide.

The plan builder refuses until these are done. They run once per new
athlete, not on every message.

## Plans that readjust every week

The first plan is built from the form:
- goal, race date and distance
- current running
- days per week and days to avoid
- preferred long-run day
- course hills
- heart-rate zones

After that, **every finished week is reviewed** and the plan is
readjusted from what the athlete actually did (`review_week`):
- **progress / on track:** the plan continues as scheduled.
- **repeat / hold:** next week stays at this level, never harder.
- **step back:** after two poor weeks, rebuild from what they're
  really running. A race goal that's slipping is flagged.
- **pause:** pain that changes how they walk or run; they get it
  checked first.

It runs when the athlete answers the weekly check-in, and as a
data-only backstop 3 days later if they don't. The taper and race date
never move, and plan settings (days per week, whole-km distances…)
survive every rebuild.

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

## Telegram

The coach profile gets its **own** Telegram bot; a bot token can belong
to one profile only. It's served by the same (default, multiplexing)
gateway and uses the default profile's model. `BOB_TELEGRAM_SETUP.md`
walks your Hermes agent through it.

**Owner routine for new athletes:**
1. Athletes fill in the Google Form, which includes their Telegram user
   ID from @userinfobot.
2. You check the responses Sheet and send it to the bot as a .csv or
   .xlsx file.
3. The bot builds every plan and adds the athletes to its allowlist.
   It reports who's ready and gives you a message to forward to each
   athlete.
4. You run `hermes gateway restart` once.
5. You forward the messages. Each athlete opens the bot, presses
   Start, and gets their welcome and plan.

Telegram doesn't let bots message people first, so the forwarded link
and the Start press are the one step that can't be automated. If an
athlete's ID was missing, Hermes pairing is the fallback.

Sending the Sheet as a file needs no Google login. If you'd rather send
links, give the coach profile Google access for **Sheets only**, and
don't copy another profile's token: the coach talks to people you don't
know.

## Scheduled jobs

Created by the agent during setup and onboarding, following
`references/playbooks.md`:

- **`run-coach intake`** (optional, hourly): only if you want
  automatic intake from a Sheet link instead of sending the file
  yourself.
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
python3 -m unittest discover -s tests        # 29 tests (incl. safety checks, full athlete lifecycles, Sheet onboarding, trends, history)
python3 general_fitness.py                   # preview a general-fitness block
python3 race_plan.py                         # preview a race build
```
