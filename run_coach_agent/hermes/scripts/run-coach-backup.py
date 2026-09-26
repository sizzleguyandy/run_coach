#!/usr/bin/env python3
# Run-coach database backup, for a no-agent cron job:
#   hermes cron create "every day at 2am" --no-agent --script run-coach-backup.py --name run-coach-backup
# Backs up BOTH databases in $HERMES_HOME/run_coach/:
#   coach.db    live coaching state (plans, sessions, signals)
#   history.db  permanent record of every run, incl. original files
# to run_coach/backups/<name>-YYYY-MM-DD.db, keeping the newest 30 of each.
# Silent on success (empty stdout = no message); prints and exits 1 on failure.
import datetime, glob, os, sqlite3, sys

home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # $HERMES_HOME/scripts/..
coach = os.environ.get("COACH_DB") or os.path.join(home, "run_coach", "coach.db")
data_dir = os.path.dirname(coach)
dbs = {"coach": coach,
       "history": os.environ.get("RUN_HISTORY_DB") or os.path.join(data_dir, "history.db")}
out_dir = os.path.join(data_dir, "backups")
today = datetime.date.today().isoformat()
failed = []
for name, db in dbs.items():
    if not os.path.exists(db):
        continue                                  # nothing to back up yet
    os.makedirs(out_dir, exist_ok=True)
    try:
        src, dst = sqlite3.connect(db), sqlite3.connect(os.path.join(out_dir, f"{name}-{today}.db"))
        src.backup(dst)                           # consistent copy even while in use
        dst.close(); src.close()
    except Exception as e:
        failed.append(f"{name}: {e}")
        continue
    for old in sorted(glob.glob(os.path.join(out_dir, f"{name}-*.db")))[:-30]:
        os.remove(old)
if failed:
    print("run-coach backup FAILED -- " + "; ".join(failed))
    sys.exit(1)
