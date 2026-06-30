# Update Runbook (agent-runnable)

You are updating the running World Marathon Trainer to the latest code. The box can
read GitHub, so this is a pull-and-apply. Run the steps in order; they're
idempotent — safe to run anytime. **Never delete the database** (`wmt.db` holds
real athlete data) without explicit human approval.

Set these once:

```bash
REPO="<absolute path to the run_coach clone>"       # e.g. /home/you/run_coach
PROJECT="$REPO/world_marathon_trainer"
BRANCH="claude/google-health-downloader-p24p2g"      # current dev branch
COACH_HOME="$HOME/.hermes-runcoach"
```

## Step 1 — Pull the latest code

```bash
cd "$REPO"
git fetch origin "$BRANCH"
BEFORE=$(git rev-parse HEAD)
git checkout "$BRANCH" && git pull origin "$BRANCH"
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
  echo "Already up to date — nothing to apply."; exit 0
fi
echo "Updated $BEFORE -> $AFTER. Changes:"
git log --oneline "$BEFORE..$AFTER"
```

If "Already up to date", stop here.

## Step 2 — Install any new dependencies

```bash
cd "$PROJECT"
python -m pip install -r requirements.txt
```

## Step 3 — Restart the engine

```bash
pkill -f "uvicorn api.main" 2>/dev/null
cd "$PROJECT"
nohup uvicorn api.main:app --port 8000 > /tmp/wmt_api.log 2>&1 &
sleep 2
curl -s http://localhost:8000/health        # expect {"status":"ok",...}
```

The SQLite DB is untouched — athletes and history persist.

## Step 4 — Refresh the coach persona (SOUL)

The persona can change between versions; always recopy it (idempotent):

```bash
cp "$PROJECT/docs/COACH_AGENT.md" "$COACH_HOME/SOUL.md"
```

## Step 5 — Reload the coach's tools + persona

Restart the Run Coach instance (so it reloads SOUL.md), or in its session run
`/reload-mcp` for tool changes. After it's back, confirm its tool list includes
the expected tools (e.g. `report_condition` if this update added it).

## Step 6 — Verify

```bash
cd "$PROJECT"
python -m pytest tests -q
WMT_API_BASE=http://localhost:8000 python scripts/smoke_onboarding.py
```

- All tests green + smoke test ALL PASS → update applied.
- **If a test fails mentioning a missing column/table** → this update changed the
  DB schema and needs a migration. **STOP — do not delete `wmt.db`.** Report it to
  the human; a migration (small `ALTER TABLE`, or a backed-up recreate) is needed
  before proceeding.

## Step 7 — Report

Tell the human what changed (the `git log` from step 1) and that the update is
live, or report exactly where it stopped.

---

> When this branch is merged to `main`, change `BRANCH` above to `main`.
