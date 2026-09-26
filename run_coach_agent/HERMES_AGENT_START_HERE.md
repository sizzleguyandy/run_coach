# Instructions for the Hermes agent: install and set up the Run Coach

Your owner has given you this folder so you can install a running-coach
system into Hermes and set it up. Work through the steps below in
order, using your terminal and file tools. Report progress to your
owner after each step. **Ask your owner before any step marked ASK.**

This folder contains:
- `hermes/`: files laid out exactly like a Hermes home (`$HERMES_HOME`).
  It holds `SOUL.md`, `memories/MEMORY.md`, `scripts/`, `run_coach/`
  and `skills/fitness/run-coach/`.
- `install.sh`: copies `hermes/` into a Hermes home safely.
- `README.md`: what each file is for.
- `INSTALL.txt`: the same install, written for a human.

What it does: athletes fill in a Google Form, choosing either a race
or "No event — I just want to get fitter". They chat with the bot,
send `.fit`/`.gpx` run files, and get plans, check-ins, and progress
trends that build up once there are 2+ weeks of data. Python
scripts build the plans and do all the maths. The agent that runs the
coach interprets the results and talks to athletes.

---

## Step 1 — Check prerequisites

Run:
```bash
python3 --version        # must be 3.10 or newer
hermes --version
hermes profile list
```
If Python is older than 3.10, stop and tell your owner.

On Windows there's often only `python`, no `python3`. Every coach
command and the installer call `python3`, so add a `python3` shim or
alias that points at the same interpreter (e.g. in a folder on PATH),
then check that `python3 --version` works.

## Step 2 — ASK: where to install

Ask your owner:

> "The coach replaces SOUL.md (the agent's identity) with a coach
> persona. I recommend installing it into a separate profile called
> `run-coach`, so your normal assistant stays as it is. Install into a
> new `run-coach` profile, or into this profile?"

- **New profile (recommended):**
  `hermes profile create run-coach`, then
  `TARGET=~/.hermes/profiles/run-coach`.
  Check the real path with `hermes profile list`, and use that path if
  it's different.
- **This profile:** `TARGET=${HERMES_HOME:-$HOME/.hermes}`. The
  installer backs up the existing SOUL.md first. If the owner wants to
  keep their SOUL.md, add `--keep-soul`.

## Step 3 — Install

From this folder, run:
```bash
bash install.sh "$TARGET"            # add --keep-soul if the owner chose that
```
It must end with the test result `OK`. If it doesn't, show your owner
the output and stop.

Confirm these files now exist under `$TARGET`:
- `SOUL.md`
- `memories/MEMORY.md`
- `skills/fitness/run-coach/SKILL.md`
- `scripts/run-coach-backup.py`
- `run_coach/AGENTS.md`

## Step 4 — Check this Hermes version matches what the coach expects

The coach was written from Hermes' published docs but hasn't been run
on your version. Check each item below. If something differs, adapt
the text files listed (edit them in `$TARGET`), then tell your owner
what you changed.

1. **Skill format.** Run `hermes -p run-coach skills list` (drop
   `-p run-coach` if you installed into this profile). `run-coach`
   should be listed. If it isn't, compare the header of
   `skills/fitness/run-coach/SKILL.md` with a working bundled skill's
   and fix it.
2. **Cron job parameters.** Look at your `cronjob` / `cronjob_manage`
   tool definition. The coach creates jobs with these fields: `action`,
   `name`, `schedule`, `script`, `skill` (or `skills`), `workdir`,
   `attach_to_session`, `prompt`.
   - If a field has a different name, fix it in the `cronjob_call`
     that `install_checkin_gate` returns
     (`skills/fitness/run-coach/scripts/coach_tools.py`, function
     `install_checkin_gate`).
   - Also fix the example in
     `skills/fitness/run-coach/references/playbooks.md` section 0.
   - If `attach_to_session` doesn't exist, just remove it; check-ins
     still work.
3. **Chat identity.** The coach identifies athletes by `chat_ref` =
   `<platform>:<user id>`, taken from your "Current Session Context".
   Look at how the user id appears there on this install. If the
   format differs, update the description of `chat_ref` in `SKILL.md`
   (Procedure step 1) and `references/playbooks.md` ("Who's who").
   Whatever format you choose, it must be the same every time for the
   same person.
4. **Incoming files.** Find where the gateway saves attachments that
   users send (for example `~/.hermes/cache/documents/`) and how you
   receive the path. If it isn't handed to you automatically, note in
   `references/playbooks.md` playbook B where to find it.
5. **Second-opinion agent.** Check that the coach profile has the
   `delegate_task` tool (Hermes' delegation toolset) enabled. Before
   any plan is built, the coach uses it for an independent safety
   review of what the athlete wrote, and for an independent race
   check. If it's missing, enable delegation for the coach profile,
   or tell your owner.
6. **Google Sheets access (optional).** The owner normally sends the
   athletes' Sheet as a downloaded .csv/.xlsx file, which needs no
   Google access. Only if they want to send Sheet links: set up
   `google-workspace` in the coach profile for Sheets only
   (`BOB_TELEGRAM_SETUP.md` step 4).

## Step 5 — Smoke test (no real athletes)

Use throwaway databases in a temporary folder, so the real ones stay
empty:
```bash
SMOKE=$(mktemp -d)
export COACH_DB=$SMOKE/coach.db          # history.db is created next to it
RC="python3 $TARGET/skills/fitness/run-coach/scripts/coach_tools.py"
EX="$TARGET/skills/fitness/run-coach/examples"
$RC ingest_intake_form "{\"form_response_json\": $(cat $EX/intake_general_fitness.json)}"
#   note athlete_id and race_id from the output, then:
$RC generate_program '{"athlete_id": "<id>", "race_id": "<id>", "reason": "smoke test"}'
#   ^ should REFUSE: the athlete wrote about an old knee injury, so the safety review comes first
$RC record_safety_review '{"athlete_id": "<id>", "reviewer_outcome": "caution", "own_outcome": "caution", "reasons": ["old knee injury"], "notes": "smoke test"}'
$RC generate_program '{"athlete_id": "<id>", "race_id": "<id>", "reason": "smoke test"}'
$RC parse_run_file "{\"file_path\": \"$EX/example_run.gpx\", \"athlete_id\": \"<id>\"}"
$RC get_run_history '{"athlete_id": "<id>"}'     # the run is in the permanent history
unset COACH_DB; rm -rf "$SMOKE"
```
- The first `generate_program` must return `"ok": false` (safety review
  pending). After `record_safety_review`, the second must come back with
  `"mode": "walk_run"`.
- The run file should parse at about 5 km in 30 min.
- `get_run_history` should show 1 run under `all_time`.

Tell your owner the results.

## Step 5b — Upgrading an existing install?

If `$TARGET/run_coach/coach.db` already has runs from an earlier
version, run this once and check `in_sync` is `true`:
```bash
python3 $TARGET/skills/fitness/run-coach/scripts/coach_tools.py backfill_history '{}'
```

## Step 6 — Hand over to the coach

SOUL.md and memory load when a session starts, so the coach persona
only takes effect in a **new** session in the target profile. Tell your
owner:

> "Installed and tested. Start the coach with `hermes -p run-coach chat`
> (or connect that profile to your Telegram gateway), then type:
> `/run-coach set up the coaching system`. The coach will then:
> - create the daily database backup
> - explain your routine: download the form's responses Sheet as CSV
>   or Excel and send it to the bot; it builds every plan and gives
>   you a link to forward to each athlete."

If you *are* running in the target profile already, start a new
session (`/new`) and carry on from `playbooks.md` section 0 yourself.

## Next: Telegram

To give the coach its own Telegram bot on the same gateway, using the
same model as the default profile, follow `BOB_TELEGRAM_SETUP.md` in
this folder.

## Rules while doing this

- Don't edit or delete `run_coach/coach.db` or `run_coach/history.db`
  by hand, ever. Only the scripts touch them. history.db is the
  permanent record of every run and refuses changes by design.
- Don't copy athlete details into your memory. Memory is shared across
  all chats.
- If a step fails and the fix isn't obvious, stop and show your owner
  the exact error rather than guessing.
