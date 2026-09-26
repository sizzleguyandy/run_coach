#!/usr/bin/env bash
# Install the run-coach agent into a Hermes home or profile.
#
#   ./install.sh                          # into ${HERMES_HOME:-~/.hermes}
#   ./install.sh ~/.hermes/profiles/run-coach   # into a dedicated profile (recommended)
#   ./install.sh <dir> --keep-soul        # don't replace that home's SOUL.md
#
# Safe to re-run for updates: code and docs are replaced; coach.db, backups,
# uploads, gate scripts and an existing MEMORY.md are never touched.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/hermes"
DEST=""
KEEP_SOUL=0
for arg in "$@"; do
  case "$arg" in
    --keep-soul) KEEP_SOUL=1 ;;
    -h|--help) sed -n 2,10p "$0"; exit 0 ;;
    *) DEST="$arg" ;;
  esac
done
DEST="${DEST:-${HERMES_HOME:-$HOME/.hermes}}"
DEST="${DEST/#\~/$HOME}"

python3 - <<'PY' || { echo "run-coach needs Python 3.10+ as python3" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

echo "Installing run-coach into $DEST"
mkdir -p "$DEST/skills/fitness" "$DEST/scripts" "$DEST/memories" \
         "$DEST/run_coach/uploads" "$DEST/run_coach/backups"

# 1. Skill (code + references). Data lives outside it, so replace wholesale.
rm -rf "$DEST/skills/fitness/run-coach"
cp -R "$SRC/skills/fitness/run-coach" "$DEST/skills/fitness/run-coach"
find "$DEST/skills/fitness/run-coach" -name __pycache__ -prune -exec rm -rf {} +
echo "  skill      -> skills/fitness/run-coach/"

# 2. Cron scripts (must live in \$HERMES_HOME/scripts).
cp "$SRC/scripts/run-coach-backup.py" "$DEST/scripts/run-coach-backup.py"
echo "  script     -> scripts/run-coach-backup.py"

# 3. Workspace rules (loaded by cron jobs via workdir).
cp "$SRC/run_coach/AGENTS.md" "$DEST/run_coach/AGENTS.md"
echo "  workspace  -> run_coach/AGENTS.md"

# 4. SOUL.md: identity. Back up whatever is there first.
if [ "$KEEP_SOUL" -eq 1 ]; then
  echo "  SOUL.md    kept as-is (--keep-soul). Add the run-coach lines from $SRC/SOUL.md yourself."
elif [ -f "$DEST/SOUL.md" ] && ! cmp -s "$SRC/SOUL.md" "$DEST/SOUL.md"; then
  bak="$DEST/SOUL.md.before-run-coach.$(date +%Y%m%d%H%M%S)"
  cp "$DEST/SOUL.md" "$bak"
  cp "$SRC/SOUL.md" "$DEST/SOUL.md"
  echo "  SOUL.md    replaced (previous saved as $(basename "$bak"))"
else
  cp "$SRC/SOUL.md" "$DEST/SOUL.md"
  echo "  SOUL.md    installed"
fi

# 5. MEMORY.md: agent-maintained. Seed only if absent or empty.
if [ ! -s "$DEST/memories/MEMORY.md" ]; then
  cp "$SRC/memories/MEMORY.md" "$DEST/memories/MEMORY.md"
  echo "  MEMORY.md  seeded"
else
  echo "  MEMORY.md  exists, left alone. First session: ask the agent to remember the"
  echo "             entries in $SRC/memories/MEMORY.md (it will consolidate to fit)."
fi

# 6. Self-test from the installed location (temporary DB, real code).
echo "Running tests..."
( cd "$DEST/skills/fitness/run-coach/scripts" && python3 -m unittest discover -s tests 2>&1 | tail -1 )
find "$DEST/skills/fitness/run-coach" -name __pycache__ -prune -exec rm -rf {} +

cat <<EOF

Done. Next:
  1. Start a NEW Hermes session for this home (SOUL.md and memory load at session start).
  2. Say: "/run-coach set up the coaching system" -- the agent follows
     references/playbooks.md section 0 (form Sheet intake job, backup job).
  3. Share the Google Form and the bot link with athletes.
EOF
