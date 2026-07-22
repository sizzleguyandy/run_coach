# Race-Specific Training Spec (Race Profiles)

**Status:** Implemented — data in `engine/race_profiles.py`, tag effects in
`engine/workouts.py`, threading in `engine/plan_builder.py` (via
`preset_race_id`). Plan responses carry a `race_profile` key showing the
active tags.
**Goal:** When an athlete selects a preset race (e.g. Cape Town Marathon), the
*training block itself* — not just the race-day pacing guide — adapts to that
race's specific demands.
**Non-goal:** Rebuilding the core. Volume curve, phase allocation, paces, and
the adaptation loop are untouched. This is a thin data overlay on the existing
workout builder.

---

## 1. The principle: demand tags, not per-race code

The system already does race-specific training in exactly one dimension:
`hilliness` drives a fixed replacement matrix in `engine/hills.py` (Phase II/III
quality sessions become hill work; high-hilliness adds downhill repeats and a
hilly long-run note). That mechanism is the template for everything below.

We generalize it: every race is described by a small set of **demand tags**
drawn from a **fixed vocabulary**. Each tag maps to exactly one deterministic,
pre-written plan modification. The engine implements the vocabulary once;
races are pure data.

**Adding a new race = filling in one data dict. Zero engine changes.**

Rules that keep it simple:

- A tag modifies *session content and notes only* — never weekly volume,
  never phase lengths, never paces. The Daniels math stays the single source
  of truth for load.
- Max 3 tags per race. A race is defined by its 1–3 signature demands, not a
  shopping list.
- No tags = exactly today's behaviour. The "Other race" path (no preset)
  keeps producing the identical generic plan. Nothing breaks for existing
  athletes.

---

## 2. Tag vocabulary (engine implements once)

| Tag | Plan effect (deterministic) | Code hook |
|---|---|---|
| `hilly_long_runs` | Long-run notes prescribe hilly terrain + effort-based climbing from Phase II onward | exists — `get_hilly_long_run_note()` |
| `hill_quality` | Quality-session hill replacement matrix | exists — `should_replace_with_hills()` |
| `downhill_resilience` | Fortnightly downhill repeats in Phase III/IV (eccentric quad prep — Comrades Down, Loch Ness) | exists — `get_downhill_session()`, currently gated on `hilliness=="high"`; re-gate on this tag |
| `trail_terrain` | Long runs annotated "on trail/technical terrain"; one Phase III quality session per fortnight swapped to time-on-feet trail run (Knysna) | new — note swap + 1 substitution in `build_week_days()` |
| `altitude` | Phase IV notes: arrival-timing guidance (arrive <48h or >2wks before); final 3 long runs flagged "at altitude if possible" (Soweto) | new — note append only |
| `heat_humidity` | Final 4 weeks: one easy run/wk annotated as heat-adaptation run (overdress or midday); hydration-practice notes on long runs (Durban) | new — note append only |
| `power_hike` | Phase II/III long runs include structured walk breaks ("8 min run / 1 min power-hike"); prepares ultra walking strategy (Comrades, Om Die Dam) | new — long-run note variant |
| `wind_exposure` | Long-run + race-week notes: practice pacing into wind, group-running cue (Manchester A56, Yorkshire, Brighton seafront) | new — note append only |

Note the split: **3 tags already exist** as hilliness behaviour — they're just
being renamed into the vocabulary. **5 new tags are note-level changes only**
(append/swap strings on sessions the plan already contains). Only
`trail_terrain` touches session *selection*, and it reuses the exact
substitution pattern hills already use.

---

## 3. The race profile block (data required per race)

Each preset in `race_presets_sa.py` / `race_presets_uk.py` gains one optional
`training` key. Schema:

```python
"cape_town_marathon": {
    # ... existing preset fields unchanged ...
    "training": {
        "tags": [],                    # 0–3 from the vocabulary above (§2)
        "long_run_note": None,         # optional str — race-flavoured long-run
                                       #   line appended from Phase III onward
        "race_week_note": None,        # optional str — one line in race week
    },
},
```

That is the whole schema: **one list + two optional strings.** The engine
reads it via a single helper (`get_training_profile(preset_id)`) returning a
safe empty default when the preset is unknown or has no `training` block.

### What you need to know about a race to fill it in

| Question to answer | Feeds |
|---|---|
| What are this race's 1–3 signature physical demands? | `tags` |
| Sustained climbing? Where in the race? | `hilly_long_runs`, `hill_quality` |
| Punishing descents (net-downhill or big drops late)? | `downhill_resilience` |
| Surface: road, trail, technical? | `trail_terrain` |
| Start-line altitude > 1,500 m? | `altitude` |
| Expected race-day temp > 22 °C or high humidity? | `heat_humidity` |
| Will most athletes walk sections (ultras)? | `power_hike` |
| Exposed/coastal/notorious wind sections? | `wind_exposure` |
| One sentence of terrain flavour for long runs | `long_run_note` |
| One race-week reminder unique to this event | `race_week_note` |

Everything else the plan needs (**distance, date, hilliness, elevation,
coordinates**) is already in the preset. The checkpoint/pacing/cut-off layer
stays where it is (`race_knowledge.py` + `.md` files) — that's race-*day*, not
training.

---

## 4. Filled examples (current 12 presets)

| Race | tags | long_run_note |
|---|---|---|
| Comrades | `hilly_long_runs`, `downhill_resilience`, `power_hike` | "Long runs on sustained hills; practice power-hiking climbs at ≤T effort." |
| Two Oceans | `hilly_long_runs`, `power_hike` | "Include one long sustained climb mid-run (Chapman's Peak simulation)." |
| Cape Town | `wind_exposure` | "Practice goal-pace segments into wind on exposed routes." |
| Soweto | `altitude`, `hilly_long_runs` | "If not Joburg-based, expect ~4% slower paces at altitude — run by effort." |
| Durban Intl | `heat_humidity` | "Flat course: run long runs on flat routes at metronomic even pace." |
| Knysna | `trail_terrain`, `hilly_long_runs` | "Long runs on forest trail; practice descending with short controlled stride." |
| London | — (generic) | "Practice patient starts: first 3 km deliberately slower than goal pace." |
| Manchester | `wind_exposure` | None |
| Brighton | `wind_exposure` | "Seafront miles: practice pacing in wind both directions." |
| Edinburgh | — (generic) | "Net-downhill course: practice controlled downhill running at goal pace." |
| Yorkshire | `wind_exposure` | None |
| Loch Ness | `downhill_resilience`, `hilly_long_runs` | "Rolling terrain throughout — never bank time on descents." |

(London/Edinburgh show that "no tags + one note" is a valid, honest profile —
not every race needs mechanical changes.)

---

## 5. Implementation footprint (when built)

1. **`engine/race_profiles.py`** (new, data + one getter, ~80 lines): the
   `training` blocks above + `get_training_profile()`. Pure, no I/O — follows
   engine rules.
2. **`plan_builder.build_full_plan()`**: accepts optional `preset_race_id`,
   resolves the profile, passes it to `build_week_days()`. (~5 lines; routers
   already have `athlete.preset_race_id` available at every call site.)
3. **`workouts.build_week_days()`**: applies tags at the three existing
   decision points — quality-session choice (reuse hills substitution
   pattern), long-run note assembly, race-week notes. (~40 lines.)
4. **`hills.py`**: re-gate `get_downhill_session()` on `downhill_resilience`
   tag with `hilliness=="high"` kept as fallback for non-preset athletes.
   (~5 lines.)

No DB changes. No API changes (plan responses just carry richer `notes`).
No change for athletes without a preset. Deterministic and unit-testable:
same inputs → same plan, tag effects assertable per session.

## 6. Positioning honesty check

After this ships, the claim "select Cape Town, not generic marathon, and the
plan adapts to Cape Town" is true at both layers:

- **Training block** — sessions and notes shaped by the race's demand tags
  (this spec).
- **Race day** — personalised splits, cut-offs, medals, weather, knowledge
  (already shipped).
