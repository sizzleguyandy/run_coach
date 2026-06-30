"""Initialise the SQLite database (create tables if missing).

Idempotent — safe to run repeatedly. Honours WMT_DB_URL (default: wmt.db next to
the project). Run:  python scripts/init_db.py
"""

from __future__ import annotations

import os
import sys

# Make the project package importable when run as a script.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.db import init_db, DB_URL  # noqa: E402


def main() -> int:
    init_db()
    print(f"[init_db] schema ready at {DB_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
