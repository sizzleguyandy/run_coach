Run-coach setup: athlete data lives in $HERMES_HOME/run_coach/coach.db (live plans) and history.db (permanent, append-only record of every run), only via the run-coach skill's coach_tools.py. Never raw SQL.
§
Athlete facts (health, injuries, plans, runs, HR zones) belong in the coach DB via run-coach tools, never in MEMORY.md/USER.md, which are shared across all chats.
§
Every athlete chat: first get_athlete_summary with chat_ref from the Current Session Context (telegram:<user id>); athletes are pre-linked from the Sheet's Telegram ID. If welcome_pending, send the welcome first. Unlinked: ask their form email, link_chat.
§
Check-ins run as one cron job per athlete (gate script run-coach-due-<athlete_id>.py in $HERMES_HOME/scripts). Create it with install_checkin_gate from inside that athlete's chat so delivery goes to them.
§
Strava: athletes connect via strava_connect_link (they paste back the localhost address). Their Strava data is shown only to them -- in the owner's chat use audience "owner"; never relay it.
