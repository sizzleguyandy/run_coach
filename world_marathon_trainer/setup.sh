#!/usr/bin/env bash
#
# World Marathon Trainer — backend setup.
# Installs deps, builds the SQLite database, runs the tests, and (optionally)
# seeds a demo athlete. Safe to re-run.
#
# Usage:
#   ./setup.sh              # deps + db + tests
#   ./setup.sh --seed       # also create a demo athlete to test the agent
#   ./setup.sh --no-tests   # skip the test run
#
set -euo pipefail
cd "$(dirname "$0")"

SEED=0
RUN_TESTS=1
for arg in "$@"; do
  case "$arg" in
    --seed) SEED=1 ;;
    --no-tests) RUN_TESTS=0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"

echo "==> 1/4  Installing Python dependencies"
"$PY" -m pip install -r requirements.txt

echo "==> 2/4  Building the SQLite database"
"$PY" scripts/init_db.py

if [ "$RUN_TESTS" -eq 1 ]; then
  echo "==> 3/4  Running tests"
  "$PY" -m pytest tests -q
else
  echo "==> 3/4  Skipping tests (--no-tests)"
fi

if [ "$SEED" -eq 1 ]; then
  echo "==> 4/4  Seeding a demo athlete"
  "$PY" scripts/seed_demo.py
else
  echo "==> 4/4  Skipping demo seed (pass --seed to create one)"
fi

cat <<'EOF'

✅ Backend ready.

Start the API:
    uvicorn api.main:app --port 8000        # docs at http://localhost:8000/docs

Then wire the agent — see docs/SETUP_RUNBOOK.md (ordered, agent-runnable).
EOF
