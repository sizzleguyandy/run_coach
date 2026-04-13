# TR3D Run Coach — System Freeze v1.5

**Freeze date:** 2026-04-06
**Status:** FROZEN — Feature complete. Bug fixes only.

---

## What is frozen

This freeze marks the completion of the TR3D Run Coach v1.5 feature set.
No new features should be merged into this codebase until the freeze is lifted.
Only bug fixes, hotfixes, and deployment configuration changes are permitted.

---

## Included in v1.5

### Core Engine
- [x] Daniels VDOT methodology (formula-based, not lookup tables)
- [x] 5 training intensities: Easy, Marathon, Threshold, Interval, Repetition
- [x] 4-phase plan structure (Base / Repetitions / Intervals / Threshold + Taper)
- [x] Closed-loop adaptation engine (volume + VDOT adjustment after each week)
- [x] Training profiles: balanced, conservative, injury_prone
- [x] Hill training adjustment by race hilliness
- [x] C25K beginner programme (12-week, 3 sessions/week, walk/run intervals)
- [x] Race predictor with Daniels velocity formula (fixes applied 2026-04-04)

### Race Presets
- [x] Cape Town Marathon
- [x] Two Oceans Ultra 56km
- [x] Comrades Marathon (Up/Down auto-detect)
- [x] Soweto Marathon
- [x] Om Die Dam Ultra 50km
- [x] Durban City Marathon
- [x] parkrun 5K

### Race Knowledge (RAG)
- [x] Cape Town Marathon intelligence doc
- [x] Two Oceans intelligence doc
- [x] Comrades intelligence doc
- [x] Soweto Marathon intelligence doc
- [x] Om Die Dam intelligence doc
- [x] Durban City Marathon intelligence doc
- [x] parkrun intelligence doc
- [x] Personalised VDOT checkpoint calculators for Comrades, Two Oceans, Cape Town, Soweto, Om Die Dam

### Telegram Bot
- [x] V2 onboarding: race-first, prediction-driven, VDOT path
- [x] Day selection in onboarding (long run / quality / 2x easy — all button-based)
- [x] Training day reset via Settings menu
- [x] Daily session reminders (run_hour based, SA timezone)
- [x] /today, /plan, /paces, /dashboard, /progress
- [x] /log (run logging with day / distance / duration / RPE)
- [x] /lograce (race result logging + VDOT recalculation)
- [x] TRUEPACE weather pace adjustment (/location + city selection)
- [x] Sunday evening weekly game (Crossing mini-app)
- [x] Level-up VDOT notification (mini-app)
- [x] Settings menu with training day reset
- [x] /reset profile

### n8n AI Workflows
- [x] today-coach: daily session explainer (Today Coach)
- [x] help-coach: RAG-powered conversational Q&A (Coach Chat)
- [x] report-coach: weekly, monthly, and race eve AI reports

### Report System
- [x] Weekly AI report (Sunday 19:00 SA) — compliance + RPE feedback
- [x] Monthly AI report (last Sunday of month, 08:00 SA) — trend analysis
- [x] Race eve briefing (15:00 SA day before race) — training summary, prediction, weather, strategy, hydration
- [x] Race prep milestones: 12wk / 8wk / 6wk / 4wk / 2wk / 3-day / race morning
- [x] Hidden test commands: /weekreport, /monthreport, /racereport

### Gamification / Loyalty
- [x] Streak tracking (consecutive compliant weeks)
- [x] Badge system (4 consecutive compliant weeks = 1 badge)
- [x] Loyalty discount display (up to 50% off at 4/4 weeks — display only, not billed)

### Infrastructure
- [x] FastAPI backend on SQLite + SQLAlchemy async
- [x] APScheduler hourly job for reminders
- [x] PicklePersistence for Telegram conversation state
- [x] TRUEPACE: Open-Meteo weather integration (no API key required)
- [x] Race-day weather fetch (Open-Meteo 2-day forecast)
- [x] Race start-line coordinates for all SA preset races

---

## Parked / Post v1.5

- [ ] Hevy gym integration (architecture built, not active)
- [ ] Notification first-line rule
- [ ] Friend Link sharing
- [ ] Telegram Payments (real billing)
- [ ] Landing website
- [ ] Native mobile app

---

## Permitted after freeze

- Bug fixes confirmed by /weekreport, /monthreport, /racereport test commands
- Deployment config changes (.env, server config)
- n8n workflow content tweaks (system prompt tuning)
- Database migrations if schema bugs are found
