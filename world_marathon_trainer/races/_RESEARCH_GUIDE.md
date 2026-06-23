# Race Profile — Research Guide

One JSON file per race in this folder. Copy `_TEMPLATE.json`, rename to the race id
(e.g. `boston.json`), and fill every field. The training engine reads these at startup —
**adding a race = adding a file, no code changes.**

Fields marked **[DRIVES TRAINING]** directly change the program. Fields marked
**[RAG]** feed the conversational agent's race knowledge. Fill both — but the
training-drivers are the ones that must be accurate.

---

## identity

| Field | Meaning | Where to research |
|---|---|---|
| `id` | lowercase slug, matches filename (`boston`) | you choose |
| `name` | official race name | race website |
| `city`, `country` | location | race website |
| `distance_km` | exact distance (42.195 for marathon, 56 for Two Oceans, 89.x for Comrades) | race website |
| `race_month` | 1–12, the month it's run | race website |
| `typical_date` | rule for the date ("3rd Monday of April", "first Sunday of October") | Wikipedia / race history |
| `loop_or_point_to_point` | `loop` / `point_to_point` / `out_and_back` — affects wind & logistics | course map |
| `world_major` | true for the 6 Abbott Majors | known |
| `edition_notes` | anything notable (course changed recently, etc.) | race news |

## course_profile **[DRIVES TRAINING]**

This is the heart of the differentiation. Get the elevation right.

| Field | Meaning | Where to research |
|---|---|---|
| `surface` | `asphalt` / `cobble` / `mixed` / `trail` | course description |
| `net_elevation_m` | finish elevation − start elevation (Boston ≈ −139, net **downhill**) | elevation chart |
| `total_ascent_m` | sum of all climbs | Strava route / TopoGraphic / race GPX |
| `total_descent_m` | sum of all descents | same |
| `elevation_profile` | array of `{km, elevation_m}` — one point per km is ideal, every 2–5km acceptable | **best source: race GPX file → import to Strava/Veloviewer**, or official elevation chart digitised |
| `key_segments` | the climbs/descents that matter (see below) | elevation chart + race reports |
| `technical_notes` | cobbles, sharp turns, tunnels (GPS loss), narrow sections | course preview videos, race reports |

### key_segments — the make-or-break parts of the course
For each notable climb or descent:
- `name` — "Heartbreak Hill", "Queensboro Bridge", "Newton Hills"
- `start_km` / `end_km` — where in the race it falls (**timing matters**: a hill at km 32 on dead legs is brutal; same hill at km 5 is nothing)
- `elevation_change_m` — gain (+) or loss (−)
- `avg_grade_pct` — steepness
- `training_note` — what this demands in training ("simulate on a long run at the 32km point, on tired legs")

**Where to find segments:** race course guides, "[race] course strategy" articles,
YouTube course previews, Strava segment data for the actual route.

## environment **[DRIVES TRAINING]** for heat/altitude

| Field | Meaning | Where to research |
|---|---|---|
| `avg_temp_c` | typical race-day temp at start | historical weather (timeanddate.com, weatherspark for the city + month) |
| `temp_range_c` | `[low, high]` you might face | weather history |
| `avg_humidity_pct` | typical humidity | weatherspark |
| `wind_exposure` | `sheltered` / `variable` / `exposed` (Chicago = exposed, windy) | course + local knowledge |
| `rain_probability_pct` | chance of rain that month | weather history |
| `altitude_m` | course elevation above sea level (most majors ≈ 0; some ultras/Mexico City high) | course data |
| `sun_exposure` | `shaded` / `mixed` / `full_sun` | course description |
| `typical_start_time` | wave start time — affects how hot it gets late race | race website |

→ `heat_prep_required` true if `avg_temp_c` > ~20 or high humidity.
→ `altitude_prep_required` true if `altitude_m` > ~1500.

## field_dynamics **[RAG]** mostly, congestion affects pacing advice

| Field | Meaning | Where to research |
|---|---|---|
| `field_size` | number of runners | race website |
| `wave_start` | corrals/waves used? | race website |
| `start_congestion_km` | how far before you can run your own pace (big races: 3–5km boxed in) | race reports |
| `pacing_culture` | `even` / `negative_split` / `go_out_hard` norms | race reports |
| `crowd_support` | `sparse` / `moderate` / `electric` (mental factor) | race reports |

## training_overlay **[DRIVES TRAINING]** — the derived prescriptions

These are **conclusions** you draw from the course/environment data above. This is what
the engine actually reads to modify Phase 3 & 4 sessions.

| Field | Meaning |
|---|---|
| `hill_loading_weeks` | how many weeks of dedicated hill work (flat race = 0; Boston/NYC = 4–6) |
| `downhill_loading` | true if course has significant descents needing quad/eccentric prep (Boston, Two Oceans) |
| `eccentric_focus` | true if downhill damage is the limiter — adds eccentric strength + downhill reps |
| `heat_prep_required` | derived from environment |
| `altitude_prep_required` | derived from environment |
| `back_to_backs` | ultras only — back-to-back long-run weekends |
| `race_profile_long_run` | description of the Phase-4 dress-rehearsal long run that mirrors this course |
| `phase3_session_modifiers` | list of strings — how Phase 3 quality sessions change for this race |
| `phase4_session_modifiers` | list of strings — how Phase 4 peak/rehearsal sessions change |

## race_strategy **[RAG]** — feeds the taper-week agent briefing

| Field | Meaning | Where to research |
|---|---|---|
| `recommended_pacing` | `even` / `negative_split` / `hold_first_half` | course strategy articles |
| `danger_zones` | list of "where people blow up" ("Boston: too fast on early downhills") | race reports, coaching articles |
| `split_strategy` | km/mile split plan narrative | elite splits, pacing guides |
| `fuelling_notes` | aid station spacing, gel timing, what's on course | race website (nutrition partner) |

## qualifying **[RAG]**

| Field | Meaning | Where to research |
|---|---|---|
| `is_qualifier` | does a time here qualify for something? | race website |
| `qualifier_for` | "Boston", "Comrades back-to-back", etc. | |
| `standards_note` | the qualifying times | race website |
| `registration_window` | when/how to enter (lottery, BQ, charity) | race website |

## knowledge_base **[RAG]** — the agent's deep knowledge

| Field | Meaning | Where to research |
|---|---|---|
| `course_guide` | a few paragraphs describing the course experience, km by km | course previews, first-person race reports |
| `common_mistakes` | list — the classic errors for this specific race | coaching articles, forums |
| `gear_notes` | shoe/clothing advice specific to course/weather (cobbles, rain, heat) | race reports |
| `sources` | list of URLs you researched — keep these for updating later | your research |

---

## Research workflow (suggested order per race)

1. **Official race website** → identity, distance, date, field size, start time, fuelling, qualifying
2. **Race GPX file** (download from race site or Strava) → import to Veloviewer/Strava for `elevation_profile`, `total_ascent/descent`, `net_elevation`
3. **Elevation chart + course strategy articles** → `key_segments`, `danger_zones`, `recommended_pacing`
4. **Weatherspark / timeanddate for city + month** → all of `environment`
5. **YouTube course preview + race reports** → `technical_notes`, `course_guide`, `common_mistakes`, `gear_notes`
6. **Then derive** `training_overlay` from everything above — this is the analytical step that turns research into a different program.

Keep every URL in `sources` so profiles can be refreshed each year.
