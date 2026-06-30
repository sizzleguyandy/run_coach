# Applying updates to the running instance

A commit to GitHub does **not** change your live box. The running FastAPI engine
and the Run Coach agent only pick up changes when the new code is on that machine
and the services are restarted. Hermes can't pull from the repo, so updates are
applied by you / your build agent on the box.

Every change I deliver will tell you **which of steps B–E apply**. Step A is how
the code gets there.

## A. Get the new code onto the box

**If the box has the repo clone and can reach GitHub:**
```bash
cd <path>/world_marathon_trainer
git pull
```

**If it can't reach GitHub:** I'll send the changed files; write each to the same
relative path under `world_marathon_trainer/`, overwriting the old version.

## B. Restart the engine (needed if any `engine/` or `api/` file changed)

```bash
pkill -f "uvicorn api.main" 2>/dev/null
cd <path>/world_marathon_trainer
nohup uvicorn api.main:app --port 8000 > /tmp/wmt_api.log 2>&1 &
curl -s http://localhost:8000/health        # confirm it's back up
```
(The SQLite DB is untouched — athletes and history persist across restarts.)

## C. Reload MCP tools (needed if `coach_mcp_server.py` changed — new/changed tools)

In the Run Coach instance: `/reload-mcp` (or restart the instance). Confirm the
new tool appears in its tool list.

## D. Update the persona (needed if `docs/COACH_AGENT.md` changed)

```bash
cp <path>/world_marathon_trainer/docs/COACH_AGENT.md ~/.hermes-runcoach/SOUL.md
```
Restart the coach instance so it reloads SOUL.md.

## E. Verify

```bash
cd <path>/world_marathon_trainer
python -m pytest tests -q                          # all green
WMT_API_BASE=http://localhost:8000 python scripts/smoke_onboarding.py
```
Then spot-check the new feature in the coach TUI.

## DB schema changes — important

If an update adds a column/table (a change under `api/orm.py`), `init_db()` only
creates **missing tables**, it does NOT alter existing ones (SQLite limitation).
When I ship a schema change I'll say so explicitly and give the migration (either
a small `ALTER TABLE`, or "back up `wmt.db` and recreate"). Don't assume a restart
picks up new columns.

---

## Quick reference: which steps per change type

| What changed | A | B | C | D |
|---|---|---|---|---|
| engine logic (`engine/*.py`) | ✓ | ✓ | | |
| API endpoint/schema (`api/*.py`) | ✓ | ✓ | | |
| MCP tools (`coach_mcp_server.py`) | ✓ | | ✓ | |
| persona (`docs/COACH_AGENT.md`) | ✓ | | | ✓ |
| DB schema (`api/orm.py`) | ✓ | ✓ + migration | | |
