# Run Coach Agent — starter kit

A template for building an AI running-coach agent with **no Strava/device
API access** — intake via form, training data via uploaded `.fit`/`.gpx`
files, plan tracking in a SQL database. Built and validated against a real
~7-week training block (this repo's parsers reproduce that block's actual
numbers exactly — see the test commands below).

## Files

| File | What it is |
|---|---|
| `schema.sql` | SQLite schema — the single source of truth for athlete profile, race target, program (planned sessions), run log (actual sessions), pattern-tracking, and check-in triggers |
| `fit_parser.py` | Zero-dependency FIT file parser |
| `gpx_parser.py` | Zero-dependency GPX file parser |
| `analysis.py` | Deterministic comparison engine: plan-vs-actual, HR-drift detection, streak tracking, trigger staleness checking |
| `general_fitness.py` | Program generator for athletes with no event ("I just want to get fitter"): time-based, walk/run ladder or +5%/week easy running, cutback every 3rd week |
| `google_form_spec.md` | Exact field list for the intake form, mapped to schema columns |
| `AGENT_INSTRUCTIONS.md` | The system prompt and behavioral rules for the agent, plus the reasoning for each rule |
| `tools_schema.json` | Function-calling tool definitions wiring the agent to the code above |

## Why it's built this way

The core design principle, learned by hitting the failure mode directly:
**the LLM plans and interprets; deterministic code parses and computes.**
Every time that line blurred in the real training block this is based on —
guessing HR zones instead of reading a watch export, trusting a whole-run
HR average instead of splitting it in half, converting a 5K time trial
straight into a half-marathon pace — the coaching went wrong or nearly did.
`AGENT_INSTRUCTIONS.md` section 1 covers this in detail.

## Quickstart

```bash
# 1. Create the database
python3 -c "
import sqlite3
conn = sqlite3.connect('coach.db')
conn.executescript(open('schema.sql').read())
conn.commit()
"

# 2. Parse a run file directly (no DB needed to test this part)
python3 fit_parser.py /path/to/run.fit
python3 gpx_parser.py /path/to/run.gpx

# 3. Preview a general-fitness (no race) plan
python3 general_fitness.py

# 4. Wire tools_schema.json's functions to thin wrappers around
#    fit_parser.parse_fit() / gpx_parser.parse_gpx() / analysis.py's
#    functions plus your platform's DB access and scheduling primitives.
```

## What you still need to build

- The race program generator itself (`generate_program` for
  `goal_type='race'`) — the rules it must
  follow are in `AGENT_INSTRUCTIONS.md`, but the actual week-by-week
  session construction is specific to your training philosophy.
  The no-race "general fitness" option is already built in
  `general_fitness.py`; route `goal_type='general_fitness'` to it. This
  kit gives you the guardrails (10% rule, cutback cadence, hill
  specificity, time-trial checkpoints), not a canned plan.
- The DB access layer connecting `tools_schema.json`'s functions to
  `schema.sql` — SQLite via Python's stdlib `sqlite3` is enough to start.
- The intake form itself (Google Forms or equivalent) per
  `google_form_spec.md`, and a webhook or poller feeding submissions to
  `ingest_intake_form`.
- Your platform's scheduling primitive for `schedule_checkin` —
  `checkin_trigger` rows describe *when* and *why* a check-in should
  fire; actually firing it is platform-specific.
