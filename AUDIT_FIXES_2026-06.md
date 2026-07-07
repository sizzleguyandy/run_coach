# TR3D Run Coach — System Audit & Fixes (June 2026)

A full-system audit was run across the coaching engine, FastAPI backend,
Telegram bot, and security/config surface (~15,000 lines of Python), followed
by three batches of fixes. Every fix below was verified empirically (executed
against the real code, and where relevant against a live test instance of the
API) before being pushed to `master`.

**Commits:** `81c5d8d` → `446d64f` (see git log for full messages)

---

## 1. Training-plan engine (pre-audit sessions)

### 48 km template base-mileage gate — `phases.py`, `volume.py`, `plan_builder.py`, `routers/plan.py`
Races of 21 km and up now require a 48 km/week base before Phase 2 quality
templates begin. Athletes starting below the base get an extended Phase 1;
athletes who cannot reach it (peak-cap or not enough weeks) get a
base-building + taper plan with an explanatory warning. 5k/10k are exempt.
New API surface: `base_building` block in the plan payload and
`base_building_warning` on the current-week response.

### Daniels per-pace quality-volume caps — `workout_templates.py`, `workouts.py`
The quality day's distance was a flat 20 % of weekly volume for every phase,
producing incoherent sessions (a 21 km "Repetitions" day describing a 4 km
workout) and over-dosing hard running by up to 4×. Quality sessions now use
Daniels' hard-volume caps:

| Phase | Pace | Cap |
|---|---|---|
| II  | R (repetition) | lesser of 5 % weekly or 8 km |
| III | I (interval)   | lesser of 8 % weekly or 10 km |
| IV  | T (threshold)  | 10 % weekly, minimum 6.4 km |

Session distance = warm-up/cool-down + hard work + recovery jogs. The freed
mileage is redistributed onto easy/medium-long days (`_rebalance_to_target`)
so weekly volume stays on target and adaptation compliance is unaffected.
Ultra plans (volume carried by back-to-back long runs) are untouched.

---

## 2. Full-system audit

Four parallel reviews covered: engine math, API routers/models, Telegram bot
handlers, and security/secrets. Headline results:

- **No leaked secrets** anywhere in the (now public) repo or its git history —
  all tokens/webhooks come from environment variables.
- The plan-generation core, TRUEPACE weather math, hills engine, billing math,
  SQL usage (fully parameterised), and DB session handling were sound.
- The serious issues clustered in **API authentication** (deferred — demo
  stage; see "Known outstanding" below), the **race predictor**, and a set of
  wrong-key / formatting bugs in the bot.

---

## 3. Batch 1 — critical fixes (`938aab8`)

### Race predictor returned wrong times — `engine/predictor.py`
- **Same-distance predictions** returned the marathon-equivalent time instead
  of the runner's actual time: a 20:00 5K produced a **3:06** 5K "prediction".
  Now returns the demonstrated time (verified: 20:00 5K → 0:19 goal).
- **Cross-distance scaling was linear** instead of the Riegel power law,
  disagreeing with the VO2X path by up to ~9 %. Both paths now use the same
  exponents (1.06 up to marathon; 1.10/1.12 for ultras) and agree within
  ~1.4 % on all tested conversions.

### Brute-forceable link codes — `handlers/mycode.py`, `routers/mobile.py`
Codes were `NAME-` + 4 digits (10,000 combinations, non-crypto random) and the
unauthenticated `/mobile/athlete/by-code/{code}` lookup returned the athlete's
telegram_id, name, race, and VO2X — bulk-harvestable in minutes.
- Codes now use `secrets` with an 8-char unambiguous alphabet
  (`ANDY-7K2M9QX4`, ~8.5 × 10¹¹ combinations).
- Legacy 4-digit codes are silently upgraded next time `/mycode` is used.
- The lookup endpoint is rate-limited per IP (10/min → HTTP 429, verified over
  live HTTP). Removed a dead duplicate generator in `mobile.py`.

### CORS wildcard + credentials — `main.py`
`allow_origins=["*"]` combined with `allow_credentials=True` (the default when
`ALLOWED_ORIGINS` was unset) let any website make credentialed cross-origin
calls. The two can no longer combine: an explicit `ALLOWED_ORIGINS` allowlist
enables credentials; the unset/dev fallback is `*` **without** credentials.
> Deployment action: set `ALLOWED_ORIGINS` on Railway to the real app origins.

---

## 4. Batch 2 — HTML safety + wrong-key bugs (`8adbf7c`)

### Athlete names HTML-escaped — `formatting.py`, `handlers/reminder.py`
Names were interpolated raw into `parse_mode="HTML"` messages in the main
menu, both dashboards, and all reminder/race-prep messages. A name containing
`<` (e.g. "Andy <3") made Telegram reject the message — permanently breaking
`/start`, `/menu`, and every daily reminder for that account, silently.
All name sites now escape (`_esc()` helper added to reminder.py, matching the
pattern onboarding already used).

### Plan notices now visible — `formatting.py`
The plan router attaches `base_building_warning` and `plan_note` to the week
payload for client display, but the bot never rendered them — the base-gate
feature was invisible. `/today` and `/plan` now show both.

### `vo2x_before` captured before mutation — `routers/log.py`
It was computed *after* `athlete.vo2x` had been overwritten, so it always
equalled `vo2x_after` and the level-up notification could never fire.
Verified end-to-end: adaptation now reports `before=50.0, after=50.5`.

### Two one-line key fixes — `handlers/reminder.py`
- Sunday game read `sessions_completed`; the API returns `sessions_logged` —
  runs count was always 0 and lives always 1.
- Strength reminder read the nonexistent `phase_name`; week payloads carry
  `phase` (int) — the message always said "Base phase". Now mapped properly.

---

## 5. Batch 3 — correctness & robustness (`446d64f`)

| Fix | File(s) | Before → After |
|---|---|---|
| C25K graduation | `routers/log.py` | Athletes finishing week 12 looped forever (`next_week > week_number` could never be true at the cap). Completing week 12 at ≥80 % compliance now sets `c25k_completed`. Verified live. |
| Two Oceans seeding table | `formatting.py` | Batch L spanned 130–150 (shadowing M–R) and a corrupt S row spanned 150–209 (shadowing T–Y). L capped at 133, S removed; table is now contiguous and every batch reachable. |
| Rest-week compliance | `engine/adaptation.py` | A jog during a planned 0 km week produced 5000 % compliance, a progressive boost, and a VO2X nudge. Rest weeks are now a no-op with a friendly note; normal-week nudges preserved. |
| Bot week number | `handlers/log_handler.py` | `/log` and `/progress` computed an uncapped local week, so runs logged after plan end vanished from dashboards. Both now use the server's capped current week (with a safe fallback). |
| AI reply delivery | `handlers/coach_chat.py`, `reminder.py` | Raw LLM output sent as HTML crashed the send when it contained a stray `<` — the athlete got nothing after waiting. All AI-content sends now fall back to plain text (`_send_ai_html`). |
| Exception text in errors | `reminder.py`, `log_handler.py` | Error replies embedded raw exception text in HTML; an exception containing `<` broke the error message itself. Now escaped. |
| `month=13` crash | `routers/log.py` | Month-summary endpoint 500'd on an invalid month; now returns 400. |
| `fmt_time` truncation | `engine/predictor.py` | 89.9 min rendered "1:29"; predictions were up to a minute fast. Now rounds to the nearest minute. |
| Injury-prone range | `engine/predictor.py` | The "optimistic" bound (0.94 × 1.07 = 1.006) was slower than the goal shown beside it. Range is now 0.97–1.16 and always contains the goal. |
| Onboarding EXPERIENCE step | `handlers/onboarding_v2.py` | Any unrecognised text (typos, emoji) silently routed the athlete to the beginner/C25K path. Now re-prompts with the option keyboard. |
| Reminder timezone | `handlers/reminder.py` | Clock was hardwired to UTC+2 (SA) even for UK athletes. Offset now configurable via `REMINDER_TZ_OFFSET_HOURS` (default 2). |

Also: `.gitignore` extended for SQLite WAL/SHM sidecars (`fdb90d5`).

---

## 6. Known outstanding (deliberately deferred)

- **API authentication (critical before launch).** All athlete-facing
  endpoints trust the caller-supplied `telegram_id` with no token/secret —
  anyone can read, modify, or delete any athlete. Deferred at the owner's
  request while in demo stage. Recommended fix: a shared-secret header from
  the bot enforced by a FastAPI dependency on every non-admin route, plus
  `secrets.compare_digest` for the admin key.
- **Per-athlete timezones** need a country/timezone column on `Athlete`
  (schema migration); the env offset is a global stopgap.
- **Predictor fitness modifier** uses marathon-scale thresholds for all
  distances (beginners targeting 5K/10K get the maximum 1.25× slowdown) —
  possibly intentional conservatism, review with real user data.
- **Comrades up/down year table** is hardcoded through 2032.
- **VO2X floor mismatch** (25.0 in adaptation vs 30.0 in paces) compresses
  very slow performances; beginner "couch" and "occasional" map to the same
  VO2X.
- **`create_athlete` VO2XHistory date** uses the raw start date while the
  athlete row stores the Monday-normalised one; `graduate_c25k` resets
  `start_date` without renumbering existing logs.
- **Concurrency**: streak/badge/VO2X read-modify-write across commits has no
  row locking (double-grant possible on request retries).
- **Admin dashboard** N+1 query (loads all run logs to count them) and a
  duplicated `/admin/dashboard` route definition.
- **Dependencies** are unpinned floor ranges — pin exact versions and add a
  lockfile + `pip-audit` in CI.
- **Structural Daniels-fidelity items** (product decisions): single quality
  day (no T-pace exposure until Phase IV), no M-pace long-run swap, no
  minute-based long-run cap, no Phase IV combination workouts.
