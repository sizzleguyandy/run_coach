# Intake Form — field specification

Build this as a Google Form (or Typeform/JotForm — any tool that exports
structured responses). Field names on the left map directly to
`athlete_profile` / `race_target` columns in schema.sql; wire the form's
response webhook (or a scheduled Sheet-poll) to write into those tables
via the `ingest_sheet_rows` / `ingest_intake_form` tools (see tools_schema.json).

Design rule: **never ask the athlete to compute or estimate something
the agent can derive or fetch itself.** Every field below is either raw
fact, a document upload, or a link — nothing requires the athlete to do
math or self-diagnose.

---

## Section 1 — About you

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Name | short text | `athlete_profile.name` | |
| Email | email | `athlete_profile.email` | |
| Current weight (kg) | number | `athlete_profile.weight_kg` | Set `weight_source='self_reported'` |
| Any current injuries or old injuries relevant to running | long text | `athlete_profile.injury_history` | Free text; the agent reads this before generating anything, doesn't parse it into structured fields |
| Fixed weekly commitments (gym days, work shifts, anything that can't move) | long text | `athlete_profile.fixed_commitments` | Free text in the form; the AGENT converts this to the JSON structure the schema expects during intake processing — don't make the athlete produce JSON |

## Section 2 — Your goal

First question of this section decides the branch. In Google Forms,
make it a **Multiple choice** question and turn on **"Go to section
based on answer"** (⋮ menu on the question) so the athlete only sees
the questions that apply to them.

| Field | Type | Maps to | Notes |
|---|---|---|---|
| What are you training for? | multiple choice, required: **"A specific race or event"** → go to Section 2a / **"No event — I just want to get fitter"** → go to Section 2b | `race_target.goal_type` (`'race'` / `'general_fitness'`) | Make it required so a blank can never be read as "no race" by accident. If the platform can't branch, show both 2a and 2b and treat an empty race date + distance as `general_fitness` — but still confirm with the athlete in the first reply. |

### Section 2a — Your goal race (race branch only; after this, go to Section 3)

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Race name | short text | `race_target.race_name` | |
| Race date | date | `race_target.race_date` | |
| Race distance | dropdown (5K/10K/Half/Marathon/Other+text) | `race_target.distance_km` | Convert to km |
| Official race website or entry page URL | short text (URL) | `race_target.course_url` | **The agent fetches this itself** to find elevation/terrain — never ask the athlete to describe the course from memory. See this project's own experience: asking for "how hilly is it" got a wrong guess; fetching the race's own page got the real 445m figure. |
| Race start time (if known) | short text | `race_target.start_time_local` | |

### Section 2b — General fitness (get-fitter branch only; after this, go to Section 3)

No race means no deadline — so this branch gets a **very slow, safe,
time-based ramp** (see `general_fitness.py` and the General Fitness
rules in AGENT_INSTRUCTIONS.md). These questions give the generator
its starting point in minutes, because a new or returning runner
usually doesn't know their weekly km but does know how long they can
keep going.

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Right now, how long can you run without stopping to walk? | multiple choice: *I don't run yet / under 1 min* · *1–5 min* · *5–10 min* · *10–20 min* · *20–30 min* · *30+ min* | `athlete_profile.baseline_continuous_run_min` (store the band's lower bound: 0/1/5/10/20/30) | Under 20 min puts them on the walk/run ladder; 20+ starts continuous easy running. Asking in bands, not an exact number, so nobody has to go out and test themselves before signing up. |
| Roughly how many minutes a week have you been running lately? | multiple choice: *None* · *under 30* · *30–60* · *60–90* · *90–150* · *150+* | `athlete_profile.baseline_weekly_run_min` (lower bound: 0/15/30/60/90/150) | The ramp starts *below* this, never at or above it. |
| How many days a week can you realistically fit in a session? | multiple choice: 2 / 3 | generator `sessions_per_week` | Capped at 3 on purpose — a 4th session is earned later in the block, not chosen up front. |
| What does "fitter" mean to you? (e.g. run 30 min non-stop, keep up with my kids, lose weight, feel less out of breath) | long text, optional | free text, read at intake | Used to frame check-ins and pick the end-of-block milestone; doesn't change the ramp speed. |
| Health check — do any of these apply? Chest pain or feeling faint with exercise · a heart condition or high blood pressure · a doctor has advised you to limit activity · pregnant or recently postpartum · none of these | checkboxes, required | `athlete_profile.health_screen_flags` (JSON array of ticked items, empty for "none") | Any tick other than "none" → the agent asks for medical clearance **before** generating a plan. (Worth adding to the race branch too; required here because this branch is where true beginners land.) |

## Section 3 — Where your fitness actually is (not where you want it to be)

Both branches see this section. General-fitness athletes can answer 0
to the km questions — the Section 2b minute-based answers are what
their generator uses; the km figures are just extra context.

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Longest run in the last 4 weeks (km) | number | used to seed the program generator's baseline, not stored directly | This is the single most load-bearing number in the whole intake — it's what exposes an endurance/durability gap before the plan gets built around a false assumption |
| Total running km in the last 4 weeks (roughly) | number | same | Used to compute baseline weekly volume for the 10%-rule ramp |
| Best recent timed effort — distance AND time | two fields (distance, time) | seeds pace estimates | Ask for **both**, and ask **when** it was run. A 5K time from 8 months ago is a different input than one from last week. |
| Do you use a running watch/app that shows heart rate zones? | yes/no | — | Gates the next field |
| If yes: upload a screenshot or export of your HR zones | file upload | feeds `athlete_profile.hr_zone_*` fields | **Do not ask the athlete to type out zone numbers.** Zone tables from consumer watches often express zones as %LTHR rather than %max without saying so (this project hit exactly this) — the agent should read the raw export/screenshot and work out the basis itself, not trust a transcription. |
| Preferred easy-run pace, if you have a strong opinion | short text, optional | `athlete_profile.easy_pace_pref_sec_per_km` | Leave blank by default — let the agent derive it. Only set `easy_pace_pref_source='athlete_specified'` if this is filled in, and even then the agent should sanity-check it against the athlete's own logged HR data over the first few weeks, flagging (not silently overriding) any mismatch. |

## Section 4 — Logistics

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Preferred long-run day | dropdown (Mon-Sun) | informs program generation | |
| Days you'd rather avoid running entirely | multi-select | informs program generation | |
| How should I send you your training file/spreadsheet? | short text | delivery preference, not a DB column | |
| Anything else I should know? | long text | free text, read at intake, not force-fit into a column | |

---

## Getting responses to the agent (Hermes)

In the Form's **Responses** tab, click **Link to Sheets**. The hourly
`run-coach intake` cron job (set up in `playbooks.md` section 0) reads
that Sheet with the `google-workspace` skill and passes every row to
`ingest_sheet_rows`. Rows already ingested are skipped, and
checkbox answers (the health check and days to avoid) are split
automatically.

Questions are matched by their **wording**. Keep the question text as
written above. If you reword one, run a test submission and check the
`normalized` block that `ingest_intake_form` returns.

Add this to the form's confirmation message:
*"Next, message the coach bot at <bot link>. It will reply with a
pairing code. Send that code to your coach so they can let you in,
and the bot will then send you your plan."*

Hermes only lets approved Telegram users talk to a bot; the owner
approves each athlete's pairing code. After that, the bot links their
chat to their form response by email.

## What the agent does with this on submission

1. Write a new `athlete_profile` row (or update if one exists for this email).
2. Write a new `race_target` row with `goal_type` set from the Section 2 branch question.
   - **Race:** as before.
   - **General fitness:** `race_name='General fitness'`, `race_date` and `distance_km` NULL, `review_date` = block start + 12 weeks.
3. **Race only:** fetch the course URL and extract distance/elevation/terrain — don't rely on the form answer alone if the official page has more detail. Skip for general fitness (there's no course).
4. If an HR zone file was uploaded, read it and populate `hr_zone_*` fields, recording `hr_zone_source` and writing a plain-language `hr_zone_basis_notes` explaining how the numbers were derived (this is what let a later check-in explain *why* a zone boundary was what it was, months later).
5. **General fitness only:** if `health_screen_flags` is non-empty, stop here and ask for medical clearance before generating anything.
6. Run the program generator (see AGENT_INSTRUCTIONS.md) to create the initial `program` rows under a new `program_revision` — the race generator, or `general_fitness.generate_general_fitness_plan()` for the general-fitness branch.
7. Reply to the athlete with the generated plan and an explicit statement of what's still provisional (e.g. race pace) and what would firm it up. For general fitness: explain that every session is conversational effort, that the plan only moves up after a week that felt comfortable and pain-free, and when the end-of-block review is.
