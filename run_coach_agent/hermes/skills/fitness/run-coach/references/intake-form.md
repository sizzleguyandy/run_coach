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
| Your Telegram user ID (message @userinfobot on Telegram to get it) | short text, required | `athlete_profile.chat_ref` = `telegram:<id>` | A **number** such as `1491755393`, not an @username. When the owner sends the Sheet, the bot links this athlete to that Telegram account and adds them to the bot's allowlist, so they're recognised the moment they press Start. Suggested validation in Google Forms: Response validation → Regular expression → Matches → `^[0-9]{5,15}$`. |
| Health check — do any of these apply? | checkboxes, required, for EVERYONE: Chest pain or feeling faint with exercise · A heart condition or high blood pressure · A doctor has advised you to limit activity · Pregnant or recently postpartum · None of these | `athlete_profile.health_screen_flags` | Any tick other than "None of these" → no plan until the athlete confirms medical clearance. In Section 1 so race runners answer it too. |
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

## Section 3 — Where your fitness actually is (not where you want it to be)

Both branches see this section. General-fitness athletes can answer 0
to the km questions — the Section 2b minute-based answers are what
their generator uses; the km figures are just extra context.

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Longest run in the last 4 weeks (km) | number | used to seed the program generator's baseline, not stored directly | This is the single most load-bearing number in the whole intake — it's what exposes an endurance/durability gap before the plan gets built around a false assumption |
| Total running km in the last 4 weeks (roughly) | number | same | Used to compute baseline weekly volume for the 10%-rule ramp |
| Best recent timed effort — distance AND time | two fields (distance, time) | seeds pace estimates | Ask for **both**, and ask **when** it was run. A 5K time from 8 months ago is a different input than one from last week. |
| Do you use a running watch/app that shows heart rate zones? | yes/no | — | If yes, the bot asks for a screenshot of the zones in Telegram at the welcome (it reads it itself; don't ask athletes to type zone numbers, and don't use a Forms file upload — those land in Drive where the bot can't see them). |

## Section 4 — Logistics

| Field | Type | Maps to | Notes |
|---|---|---|---|
| How many days a week can you run? (3-6) | dropdown 3/4/5/6 | race plans: sessions per week | Race branch athletes. (General-fitness athletes answer their own 2/3 question in Section 2b; both map to the same field.) If blank, race plans use 4. |
| Preferred long-run day | dropdown (Mon-Sun) | informs program generation | |
| Days you'd rather avoid running entirely | multi-select | informs program generation | |
| Anything else I should know? | long text | free text, read at intake, not force-fit into a column | |

---

## Getting responses to the agent (Hermes)

The owner checks the responses Sheet (fixing typos, removing test
rows), then **sends it to the coach bot**:

- **As a file (recommended, no Google login needed):** in Google
  Sheets, File → Download → **Comma-separated values (.csv)** or
  **Microsoft Excel (.xlsx)**, then send the file to the bot in
  Telegram. The bot runs `ingest_sheet_file`.
- **As a link:** only if `google-workspace` is authorised for Sheets
  in the coach profile. The bot reads it and runs `ingest_sheet_rows`.

Re-sending the whole Sheet is safe:
- Unchanged rows are skipped.
- An edited row updates that athlete's details but keeps their goal
  and plan, unless the goal itself changed; in that case the bot asks
  the owner before rebuilding.
- Checkbox answers (the health check and days to avoid) are split
  automatically.
- Dates like `17/01/2027` are read as day/month. Ambiguous ones such
  as `05/06/2027` are flagged for confirmation.

Questions are matched by their **wording**. Keep the question text as
written above. If you reword one, send a test row and check the
`normalized` block in the result.

**Telegram rule:** a bot can't message someone first. The athlete
must open the bot and press **Start** before the coach can talk to
them. So the form's confirmation message should say:
*"Thanks! Once your coach has set up your plan you'll get a link to
the coach bot. Open it, press Start and say hi, and your plan will be
waiting."*

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
