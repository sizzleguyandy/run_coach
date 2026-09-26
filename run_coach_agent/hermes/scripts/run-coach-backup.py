#!/usr/bin/env python3
# Run-coach database backup, for a no-agent cron job:
#   hermes cron create "every day at 2am" --no-agent --script run-coach-backup.py --name run-coach-backup
# Writes $HERMES_HOME/run_coach/backups/coach-YYYY-MM-DD.db and keeps the newest 30.
# Silent on success (empty stdout = no message); prints and exits 1 on failure.
import datetime, glob, os, sqlite3, sys

home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # $HERMES_HOME/scripts/..
db = os.environ.get("COACH_DB") or os.path.join(home, "run_coach", "coach.db")
if not os.path.exists(db):
    sys.exit(0)                                   # nothing to back up yet
out_dir = os.path.join(os.path.dirname(db), "backups")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, f"coach-{datetime.date.today().isoformat()}.db")
try:
    src, dst = sqlite3.connect(db), sqlite3.connect(out)
    src.backup(dst)                               # consistent copy even while in use
    dst.close(); src.close()
except Exception as e:
    print(f"run-coach backup FAILED: {e}")
    sys.exit(1)
for old in sorted(glob.glob(os.path.join(out_dir, "coach-*.db")))[:-30]:
    os.remove(old)
