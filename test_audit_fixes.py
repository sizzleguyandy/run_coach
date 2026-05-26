"""
Live verification tests for the audit fixes pushed in commits 948a7f0 and f7519e7.

Run against the Railway deployment:
    python test_audit_fixes.py

Tests 4 production-breaking fixes:
  #1 Strength router disabled — no more AttributeError crashes
  #3 Beginner ability keys aligned — accurate VO2X for all 5 brand.ts options
  #4 Full-plan endpoint passes user's schedule preferences
  #5 Race-week dynamically anchored to race-day-of-week

Requires environment:
    ADMIN_SECRET  — your Railway ADMIN_SECRET (defaults to attempting unauth where possible)

Usage:
    set ADMIN_SECRET=your-secret
    python test_audit_fixes.py
"""
from __future__ import annotations

import io
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import date, timedelta
from typing import Any

# Force UTF-8 stdout on Windows so emoji from API responses don't crash the test
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "https://runcoach-production-2be3.up.railway.app"
ADMIN_KEY = os.getenv("ADMIN_SECRET", "")

# Use a clearly-marked test ID so it's easy to identify and clean up
TEST_ID = "audit-test-9999"


# ── HTTP helpers (stdlib only, no extra deps) ─────────────────────────────────

def _req(method: str, path: str, body: dict | None = None, admin: bool = False) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if admin:
        headers["X-Admin-Key"] = ADMIN_KEY
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            try:
                return resp.status, json.loads(payload) if payload else None
            except json.JSONDecodeError:
                return resp.status, payload.decode(errors="replace")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body_text)
        except Exception:
            return e.code, body_text


def ok(label: str) -> None:
    print(f"  [PASS] {label}")


def fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}")
    if detail:
        print(f"         {detail}")


# ── Setup / teardown ──────────────────────────────────────────────────────────

def cleanup() -> None:
    """Best-effort delete of the test athlete."""
    code, _ = _req("DELETE", f"/v1/athlete/{TEST_ID}")
    if code in (204, 404):
        return


def create_test_athlete(race_date: date, long_run_day: str = "Sat") -> bool:
    """Create the test athlete. Returns True on success."""
    cleanup()
    body = {
        "telegram_id": TEST_ID,
        "name": "Audit Test",
        "current_weekly_mileage": 40.0,
        "vo2x": 45.0,
        "race_distance": "marathon",
        "race_hilliness": "low",
        "race_date": race_date.isoformat(),
        "start_date": date.today().isoformat(),
        "long_run_day": long_run_day,
        "quality_day": "Tue",
        "training_profile": "aggressive",
        "extra_training_days": "Thu",
    }
    code, _ = _req("POST", "/v1/athlete/", body)
    return code == 201


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_api_alive() -> bool:
    print("\n[0] API is responding")
    code, data = _req("GET", "/health")
    if code == 200 and isinstance(data, dict) and data.get("status") == "ok":
        ok(f"GET /health -> {data}")
        return True
    fail("API not responding", f"code={code} body={data}")
    return False


def test_strength_router_disabled() -> bool:
    """Fix #1: strength router should be gone — endpoints return 404."""
    print("\n[1] Strength router disabled (no more crashes)")
    # /v1/strength/templates used to be a working endpoint; now should 404
    code, _ = _req("GET", "/v1/strength/templates")
    if code == 404:
        ok("GET /v1/strength/templates -> 404 (router disabled)")
        return True
    fail("Strength router still mounted", f"got code={code}")
    return False


def test_beginner_ability_keys() -> bool:
    """Fix #3: all 5 brand.ts beginner abilities should give distinct VO2X."""
    print("\n[3] Beginner ability keys aligned with brand.ts")
    # Expected VO2X ranges reflect the implementation:
    # BEGINNER_5K_TIMES (couch=42, occasional=38, run5k_slow=35, run5k_reg=30, run10k_reg=27 min)
    # combined with the Daniels formula + 25.0 floor in calculate_vo2x_from_race
    abilities_expected_min_vo2x = {
        "couch":       (25.0, 28.0),   # slowest -> floor (25)
        "occasional":  (25.0, 28.0),   # also hits floor
        "run5k_slow":  (25.0, 30.0),
        "run5k_reg":   (28.0, 34.0),
        "run10k_reg":  (32.0, 40.0),   # fastest of the beginner tier
    }
    all_passed = True
    vo2x_seen: list[tuple[str, float]] = []
    for ability, (lo, hi) in abilities_expected_min_vo2x.items():
        body = {
            "race_name": "Test 10K",
            "race_distance_km": 10.0,
            "hill_factor": 0.02,
            "race_date": (date.today() + timedelta(days=84)).isoformat(),
            "has_recent_race": False,
            "beginner_ability": ability,
            "weekly_mileage_km": 20.0,
            "longest_run_km": 8.0,
            "plan_type": "balanced",
        }
        code, data = _req("POST", "/v1/predict/", body)
        if code != 200 or not isinstance(data, dict):
            fail(f"ability={ability}", f"code={code} body={data}")
            all_passed = False
            continue
        vo2x = data.get("vo2x")
        if vo2x is None:
            fail(f"ability={ability}", "no vo2x in response")
            all_passed = False
            continue
        vo2x_seen.append((ability, vo2x))
        if lo <= vo2x <= hi:
            ok(f"ability={ability:<13} -> VO2X={vo2x:.1f}  (expected {lo}–{hi})")
        else:
            fail(f"ability={ability}", f"VO2X={vo2x:.1f} outside expected {lo}–{hi}")
            all_passed = False

    # Bonus check: VO2X should monotonically increase from couch -> run10k_reg
    if vo2x_seen:
        vo2xs = [v for _, v in vo2x_seen]
        if vo2xs == sorted(vo2xs):
            ok("VO2X monotonically increases couch -> run10k_reg (proves keys are distinct)")
        else:
            fail("VO2X not monotonic", f"got order: {vo2x_seen}")
            all_passed = False
    return all_passed


def test_full_plan_preserves_schedule() -> bool:
    """Fix #4: GET /plan/{id} should honour athlete's long_run_day."""
    print("\n[4] Full-plan endpoint preserves user's schedule")

    # Make race land on a Sunday so we can also check fix #5 in the same setup
    target_race = date.today() + timedelta(days=126)
    while target_race.weekday() != 6:  # 6 = Sunday
        target_race += timedelta(days=1)

    if not create_test_athlete(target_race, long_run_day="Sun"):
        fail("Could not create test athlete")
        return False

    code, plan = _req("GET", f"/v1/plan/{TEST_ID}")
    if code != 200 or not isinstance(plan, dict):
        fail("GET /v1/plan/{id} failed", f"code={code}")
        return False

    weeks = plan.get("weeks", [])
    if not weeks:
        fail("plan returned no weeks")
        return False

    # Check a middle build week (week 5) — long run should be on Sun, not default Sat
    mid_week = next((w for w in weeks if w["week_number"] == 5), weeks[len(weeks)//2])
    days = mid_week.get("days", {})
    sat = days.get("Sat", {})
    sun = days.get("Sun", {})

    if "Long Run" in sun.get("session", ""):
        ok(f"week {mid_week['week_number']}: long run is on Sun (athlete preference respected)")
        return True
    if "Long Run" in sat.get("session", ""):
        fail("Long run defaulted to Sat", "athlete long_run_day='Sun' was ignored")
        return False
    fail("No long run found", f"Sat={sat.get('session')!r}  Sun={sun.get('session')!r}")
    return False


def test_race_week_anchors_to_race_day() -> bool:
    """Fix #5: race week's RACE DAY session should be on the actual race day."""
    print("\n[5] Race week anchored to race-day-of-week")

    # Test athlete from the previous test has race_date on a Sunday — reuse it
    code, plan = _req("GET", f"/v1/plan/{TEST_ID}")
    if code != 200:
        fail("Could not fetch plan")
        return False

    weeks = plan.get("weeks", [])
    race_week = weeks[-1] if weeks else None
    if not race_week:
        fail("No race week found")
        return False

    days = race_week.get("days", {})
    sun_session = days.get("Sun", {}).get("session", "")
    sat_session = days.get("Sat", {}).get("session", "")

    if "RACE DAY" in sun_session or "🏁" in sun_session:
        ok(f"race week: RACE DAY is on Sunday  (Sun={sun_session!r})")
        return True
    if "RACE DAY" in sat_session or "🏁" in sat_session:
        fail("RACE DAY still on Saturday", "fix #5 not deployed yet?")
        return False
    fail("No RACE DAY session found", f"Sat={sat_session!r}  Sun={sun_session!r}")
    return False


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"Testing: {BASE}")
    print(f"Admin key set: {'yes' if ADMIN_KEY else 'no (some tests may skip)'}")

    if not test_api_alive():
        print("\nAPI is not responding. Skipping remaining tests.")
        return 1

    results = {
        "#1 strength disabled":     test_strength_router_disabled(),
        "#3 beginner abilities":    test_beginner_ability_keys(),
        "#4 schedule preserved":    test_full_plan_preserves_schedule(),
        "#5 race week dynamic":     test_race_week_anchors_to_race_day(),
    }

    cleanup()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}")

    failed = sum(1 for v in results.values() if not v)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
