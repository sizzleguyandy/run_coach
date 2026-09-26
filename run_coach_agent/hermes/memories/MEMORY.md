Run-coach setup: all athlete data lives in SQLite at $HERMES_HOME/run_coach/coach.db and is only read or changed through the run-coach skill's coach_tools.py. Never raw SQL, never hand-edit the DB.
§
Athlete facts (health, injuries, plans, runs, HR zones) belong in the coach DB via run-coach tools, never in MEMORY.md/USER.md, which are shared across all chats.
§
Every athlete chat: first run get_athlete_summary with chat_ref from the Current Session Context (e.g. telegram:<user id>). If there's no link, ask for their intake-form email and run link_chat.
§
Check-ins run as one cron job per athlete (gate script run-coach-due-<athlete_id>.py in $HERMES_HOME/scripts). Create it with install_checkin_gate from inside that athlete's chat so delivery goes to them.
