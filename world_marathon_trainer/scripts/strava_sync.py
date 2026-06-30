"""Strava -> World Marathon Trainer sync.

Closes the loop: pulls an athlete's runs from Strava, maps them to the engine's
sync contract, and POSTs them — then triggers adaptation. Two modes:

  onboard : pull ~8 weeks of history and create the athlete (slot-in from Strava)
  sync    : pull recent runs, POST /sync, then POST /adapt   (the daily job)

Designed to be run per athlete (cron, or an n8n Execute-Command / HTTP node).
A `--from-file` source lets you test or do manual imports without Strava creds.

Strava OAuth (one-time per athlete) gives a long-lived refresh_token; this script
exchanges it for a short-lived access_token on each run, so it's stateless.

Env / flags:
  --api-base        WMT API (default http://localhost:8000 or WMT_API_BASE)
  --athlete-id      target athlete (sync mode)
  Strava (network source):
    --client-id / --client-secret / --refresh-token   (or STRAVA_* env)
  File source (testing / manual):
    --from-file path/to/strava_activities.json
  onboard mode:
    --onboard --name --race-id --race-date YYYY-MM-DD --days N

Examples:
  # daily sync for one athlete
  STRAVA_CLIENT_ID=.. STRAVA_CLIENT_SECRET=.. STRAVA_REFRESH_TOKEN=.. \
    python scripts/strava_sync.py --athlete-id abc123

  # manual import / test from a file
  python scripts/strava_sync.py --athlete-id abc123 --from-file runs.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


# --------------------------------------------------------------------------- #
# Mapping (pure — the bug-prone part, unit-tested)
# --------------------------------------------------------------------------- #
def is_run(act: dict) -> bool:
    t = (act.get("sport_type") or act.get("type") or "").lower()
    return "run" in t


def map_strava_activity(act: dict) -> dict:
    """Map a raw Strava activity to the engine's sync contract.

    Strava units: distance metres, times seconds, speed m/s.
    """
    dist_m = act.get("distance") or 0.0
    moving_s = act.get("moving_time") or 0.0
    elapsed_s = act.get("elapsed_time")
    speed = act.get("average_speed")  # m/s

    pace = None
    if speed:
        pace = round(1000.0 / (speed * 60.0), 3)   # min/km

    return {
        "id": str(act["id"]),
        "start_date": act.get("start_date"),       # already ISO 8601
        "activity_type": act.get("sport_type") or act.get("type") or "Run",
        "name": act.get("name"),
        "distance_km": round(dist_m / 1000.0, 3),
        "moving_time_min": round(moving_s / 60.0, 3),
        "elapsed_time_min": round(elapsed_s / 60.0, 3) if elapsed_s else None,
        "total_elevation_gain_m": act.get("total_elevation_gain"),
        "average_pace_min_per_km": pace,
        "average_heartrate": act.get("average_heartrate"),
        "max_heartrate": act.get("max_heartrate"),
        "suffer_score": act.get("suffer_score"),
    }


def map_activities(raw: list[dict]) -> list[dict]:
    return [map_strava_activity(a) for a in raw if is_run(a)]


# --------------------------------------------------------------------------- #
# Strava (network)
# --------------------------------------------------------------------------- #
def refresh_access_token(client_id: str, client_secret: str,
                         refresh_token: str) -> str:
    r = httpx.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_strava_activities(access_token: str, after_epoch: int,
                            per_page: int = 200) -> list[dict]:
    """Fetch activities after `after_epoch`, paginating until exhausted."""
    out: list[dict] = []
    page = 1
    while True:
        r = httpx.get(STRAVA_ACTIVITIES_URL,
                      headers={"Authorization": f"Bearer {access_token}"},
                      params={"after": after_epoch, "per_page": per_page,
                              "page": page},
                      timeout=60)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return out


# --------------------------------------------------------------------------- #
# Engine (our API)
# --------------------------------------------------------------------------- #
def post_sync(api_base: str, athlete_id: str, activities: list[dict]) -> dict:
    r = httpx.post(f"{api_base}/athlete/{athlete_id}/sync",
                   json={"activities": activities, "recompute_weekly_km": True},
                   timeout=60)
    r.raise_for_status()
    return r.json()


def post_adapt(api_base: str, athlete_id: str) -> dict:
    r = httpx.post(f"{api_base}/athlete/{athlete_id}/adapt", timeout=60)
    r.raise_for_status()
    return r.json()


def post_onboard(api_base: str, name: str, race_id: str, race_date: str,
                 days: int, activities: list[dict]) -> dict:
    r = httpx.post(f"{api_base}/onboard", json={
        "name": name, "race_id": race_id, "race_date": race_date,
        "training_days_committed": days, "preview": False,
        "activities": activities,
    }, timeout=60)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def get_raw_activities(args, after_epoch: int) -> list[dict]:
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    client_id = args.client_id or os.environ.get("STRAVA_CLIENT_ID")
    client_secret = args.client_secret or os.environ.get("STRAVA_CLIENT_SECRET")
    refresh_token = args.refresh_token or os.environ.get("STRAVA_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        sys.exit("error: provide --from-file OR Strava client-id/secret/refresh-token")
    token = refresh_access_token(client_id, client_secret, refresh_token)
    return fetch_strava_activities(token, after_epoch)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Strava -> World Marathon Trainer sync")
    p.add_argument("--api-base",
                   default=os.environ.get("WMT_API_BASE", "http://localhost:8000"))
    p.add_argument("--athlete-id")
    p.add_argument("--from-file")
    p.add_argument("--client-id")
    p.add_argument("--client-secret")
    p.add_argument("--refresh-token")
    p.add_argument("--days-back", type=int, default=14,
                   help="sync mode: how far back to pull (default 14)")
    # onboard mode
    p.add_argument("--onboard", action="store_true")
    p.add_argument("--name")
    p.add_argument("--race-id")
    p.add_argument("--race-date")
    p.add_argument("--days", type=int, help="committed training days/week")
    args = p.parse_args()

    if args.onboard:
        after = int((datetime.now(timezone.utc) - timedelta(weeks=8)).timestamp())
        raw = get_raw_activities(args, after)
        activities = map_activities(raw)
        if not (args.name and args.race_id and args.race_date and args.days):
            sys.exit("onboard needs --name --race-id --race-date --days")
        res = post_onboard(args.api_base, args.name, args.race_id,
                           args.race_date, args.days, activities)
        prof = res.get("profile", {})
        ass = res.get("assessment", {})
        print(f"[onboard] athlete_id={res.get('athlete_id')}")
        print(f"  derived {prof.get('weekly_km')} km/wk, VDOT {prof.get('implied_vdot')} "
              f"({prof.get('confidence')}); entry {ass.get('entry_phase')}, "
              f"peak {ass.get('target_peak_km')} km")
        return 0

    # sync mode
    if not args.athlete_id:
        sys.exit("sync needs --athlete-id")
    after = int((datetime.now(timezone.utc)
                 - timedelta(days=args.days_back)).timestamp())
    raw = get_raw_activities(args, after)
    activities = map_activities(raw)
    sync_res = post_sync(args.api_base, args.athlete_id, activities)
    print(f"[sync] {sync_res}")
    adapt_res = post_adapt(args.api_base, args.athlete_id)
    if adapt_res.get("adapted"):
        print(f"[adapt] vdot {adapt_res['vdot_before']} -> {adapt_res['vdot_after']} "
              f"({adapt_res.get('vdot_delta'):+}); "
              f"compliance {adapt_res['volume_compliance']*100:.0f}%")
        for n in adapt_res.get("notes", []):
            print(f"  note: {n}")
        for f in adapt_res.get("flags", []):
            print(f"  FLAG: {f}")
    else:
        print(f"[adapt] {adapt_res.get('reason', 'no change')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
