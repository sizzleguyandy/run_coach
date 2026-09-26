# Run Coach Agent — starter kit

A template for building an AI running-coach agent with **no Strava/device
API access** — intake via form, training data via uploaded `.fit`/`.gpx`
files, plan tracking in a SQL database. Built and validated against a real
~7-week training block (this repo's parsers reproduce that block's actual
numbers exactly — see the test commands below).

**Handing this to an agent? Point it at `START_HERE.md`.**

## Files

| File | What it is |
|---|---|
| `START_HERE.md` | Operating manual for the agent: setup, how to call tools, step-by-step playbooks |
| `AGENT_INSTRUCTIONS.md` | The system prompt and behavioral rules for the agent, plus the reasoning for each rule |
| `coach_tools.py` | Working implementation of every tool (SQLite + parsers + generators), callable via `call_tool()` or the command line |
| `tools_schema.json` | Function-calling definitions matching `coach_tools.py` exactly |
| `schema.sql` | SQLite schema — the single source of truth for athlete profile, goal (race or general fitness), program, run log, pattern signals, check-ins, intake responses |
| `race_plan.py` | Race program generator (10% cap, cutbacks, long-run-first growth, time trials, hill specificity, taper) |
| `general_fitness.py` | Program generator for athletes with no event ("I just want to get fitter"): time-based, walk/run ladder or +5%/week easy running, cutback every 3rd week |
| `analysis.py` | Deterministic comparison engine: plan-vs-actual, HR-drift detection, streak tracking, trigger staleness checking |
| `fit_parser.py` / `gpx_parser.py` | Zero-dependency run file parsers |
| `google_form_spec.md` | Exact field list for the intake form, including the race / get-fitter branch |
| `examples/` | Sample form responses (race and general fitness) and a sample GPX |
| `tests/test_kit.py` | End-to-end tests: `python3 -m unittest discover -s tests` |

## Why it's built this way

The core design principle, learned by hitting the failure mode directly:
**the LLM plans and interprets; deterministic code parses and computes.**
Every time that line blurred in the real training block this is based on —
guessing HR zones instead of reading a watch export, trusting a whole-run
HR average instead of splitting it in half, converting a 5K time trial
straight into a half-marathon pace — the coaching went wrong or nearly did.
`AGENT_INSTRUCTIONS.md` section 1 covers this in detail.

## Quickstart

Python 3.10+, no packages to install.

```bash
cd run_coach_agent
python3 -m unittest discover -s tests          # everything works?
python3 coach_tools.py init_db                 # creates coach.db

# Walk through a general-fitness athlete end to end
python3 coach_tools.py ingest_intake_form \
  "{\"form_response_json\": $(cat examples/intake_general_fitness.json)}"
python3 coach_tools.py generate_program \
  '{"athlete_id": "<from above>", "race_id": "<from above>", "reason": "initial plan"}'
python3 coach_tools.py parse_run_file \
  '{"file_path": "examples/example_run.gpx", "athlete_id": "<from above>"}'

# Preview the generators directly
python3 general_fitness.py
python3 race_plan.py
```

## What your platform still needs to provide

- A way to receive form submissions and call `ingest_intake_form`
  (Google Forms/Sheets trigger or poller).
- A way to message the athlete and receive their run files.
- A daily scheduler that runs the check-in sweep (`get_due_checkins`
  → `check_trigger_staleness` → act).
- Web fetch (course pages) and image reading (HR zone screenshots) for
  the agent — it records what it finds via tools.
