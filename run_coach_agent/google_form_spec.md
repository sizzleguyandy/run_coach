# Intake Form — field specification

Build this as a Google Form (or Typeform/JotForm — any tool that exports
structured responses). Field names on the left map directly to
`athlete_profile` / `race_target` columns in schema.sql; wire the form's
response webhook (or a scheduled Sheet-poll) to write into those tables
via the `ingest_intake_form` tool (see tools_schema.json).

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

## Section 2 — Your goal race

| Field | Type | Maps to | Notes |
|---|---|---|---|
| Race name | short text | `race_target.race_name` | |
| Race date | date | `race_target.race_date` | |
| Race distance | dropdown (5K/10K/Half/Marathon/Other+text) | `race_target.distance_km` | Convert to km |
| Official race website or entry page URL | short text (URL) | `race_target.course_url` | **The agent fetches this itself** to find elevation/terrain — never ask the athlete to describe the course from memory. See this project's own experience: asking for "how hilly is it" got a wrong guess; fetching the race's own page got the real 445m figure. |
| Race start time (if known) | short text | `race_target.start_time_local` | |

## Section 3 — Where your fitness actually is (not where you want it to be)

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

## What the agent does with this on submission

1. Write a new `athlete_profile` row (or update if one exists for this email).
2. Write a new `race_target` row.
3. **Fetch the course URL** and extract distance/elevation/terrain — don't rely on the form answer alone if the official page has more detail.
4. If an HR zone file was uploaded, read it and populate `hr_zone_*` fields, recording `hr_zone_source` and writing a plain-language `hr_zone_basis_notes` explaining how the numbers were derived (this is what let a later check-in explain *why* a zone boundary was what it was, months later).
5. Run the program generator (see AGENT_INSTRUCTIONS.md) to create the initial `program` rows under a new `program_revision`.
6. Reply to the athlete with the generated plan and an explicit statement of what's still provisional (e.g. race pace) and what would firm it up.
