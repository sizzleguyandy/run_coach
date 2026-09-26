"""
trends.py — deterministic trend analysis over an athlete's history.

Pure computation, like analysis.py: callers pass in the athlete's logged
runs, program rows and signal events; this returns numbers, directions
and plain-language feedback statements with the evidence behind each.
The agent phrases and prioritises them — it never computes its own.

Trends need history, and they get richer as it builds up:

  tier                  unlocks when                      adds
  building_baseline     < 14 days of runs or < 4 runs     counts only + what's still needed
  early                 2-3 weeks                         weekly totals, consistency, week-on-week volume
  developing            4-7 weeks                         + aerobic fitness (pace at same HR), long-run HR
                                                          drift trend, missed-by-weekday, time-trial progression
  established           8+ weeks                          + last-4-weeks vs previous-4-weeks block comparison

Every metric reports how many runs/weeks it's based on, and a metric
without enough data says so instead of guessing.
"""
import re
import statistics
from datetime import date, datetime, timedelta

from analysis import HR_DRIFT_FLAG_BPM

MIN_DAYS = 14
MIN_RUNS = 4
TIER_DEVELOPING_WEEKS = 4
TIER_ESTABLISHED_WEEKS = 8
EF_MIN_RUNS = 4               # qualifying easy/long runs with HR
EF_MIN_SPAN_DAYS = 21
EF_WINDOW_DAYS = 14           # compare the first 14 days of EF data with the last 14
DIRECTION_PCT = 0.02          # changes smaller than 2% are "stable"
DRIFT_MIN_RUNS = 3
EASY_TYPES = {"easy", "long"}


# ---- helpers ------------------------------------------------------

def _d(iso):
    return datetime.fromisoformat(iso).date() if "T" in iso else date.fromisoformat(iso[:10])


def _week_start(d):
    return d - timedelta(days=d.weekday())


def _pace_str(sec_per_km):
    if sec_per_km is None:
        return "n/a"
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d}/km"


def _direction(change_frac, higher_is_better=True):
    if change_frac is None:
        return None
    if abs(change_frac) < DIRECTION_PCT:
        return "stable"
    good = change_frac > 0 if higher_is_better else change_frac < 0
    return "improving" if good else "declining"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _run_minutes(r):
    return r.get("elapsed_time_min") or r.get("moving_time_min") or 0.0


# ---- sections -------------------------------------------------------

def weekly_table(runs, sessions, first_day, today):
    weeks = []
    ws = _week_start(first_day)
    while ws <= today:
        we = ws + timedelta(days=6)
        wk_runs = [r for r in runs if ws <= r["_date"] <= we]
        past = [s for s in sessions if ws <= s["_date"] <= min(we, today) and s["session_type"] != "rest"]
        # Sessions dated today count as planned only once done/missed.
        past = [s for s in past if s["_date"] < today or s["status"] != "pending"]
        all_wk = [s for s in sessions if ws <= s["_date"] <= we and s["session_type"] != "rest"]
        phases = {s.get("week_phase") for s in all_wk}
        weeks.append({
            "week_start": ws.isoformat(),
            "complete": we < today,
            "runs": len(wk_runs),
            "km": round(sum(r.get("distance_km") or 0 for r in wk_runs), 1),
            "minutes": round(sum(_run_minutes(r) for r in wk_runs)),
            "longest_km": round(max((r.get("distance_km") or 0 for r in wk_runs), default=0), 1),
            "longest_min": round(max((_run_minutes(r) for r in wk_runs), default=0)),
            "phase": ("cutback" if phases & {"cutback", "consolidate"} else
                      next(iter(phases), None) if len(phases) == 1 else None),
            "planned_km": round(sum(s.get("prescribed_distance_km") or 0 for s in all_wk), 1),
            "planned_minutes": round(sum(s.get("prescribed_duration_min") or 0 for s in all_wk)),
            "planned": len(past),
            "done": sum(1 for s in past if s["status"] == "done"),
            "missed": sum(1 for s in past if s["status"] == "missed"),
            "unlogged": sum(1 for s in past if s["status"] == "pending"),
        })
        ws += timedelta(days=7)
    return weeks


def consistency(sessions, today, weeks_back=None):
    past = sorted((s for s in sessions if s["session_type"] != "rest"
                   and (s["_date"] < today or s["status"] != "pending")), key=lambda s: s["session_date"])
    if weeks_back:
        cutoff = _week_start(today) - timedelta(weeks=weeks_back)
        past = [s for s in past if s["_date"] >= cutoff]
    if not past:
        return None
    done = [s["status"] == "done" for s in past]
    current = 0
    for ok in reversed(done):
        if not ok:
            break
        current += 1
    longest = run_len = 0
    for ok in done:
        run_len = run_len + 1 if ok else 0
        longest = max(longest, run_len)
    return {"planned": len(past), "done": sum(done), "pct": round(100 * sum(done) / len(past)),
            "current_streak": current, "longest_streak": longest}


def missed_by_weekday(sessions, today):
    counts = {}
    for s in sessions:
        if s["session_type"] != "rest" and (s["status"] == "missed" or
                                            (s["status"] == "pending" and s["_date"] < today)):
            counts[s["day_of_week"]] = counts.get(s["day_of_week"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def aerobic_efficiency(runs):
    """Speed per heartbeat on easy/long runs (efficiency factor). Rising EF =
    faster at the same heart rate = aerobic fitness improving."""
    q = [r for r in runs
         if r.get("_session_type") in EASY_TYPES and r.get("avg_hr") and r.get("moving_time_min")
         and (r.get("distance_km") or 0) >= 2]
    for r in q:
        r["_ef"] = (r["distance_km"] * 1000 / r["moving_time_min"]) / r["avg_hr"]
    if len(q) < EF_MIN_RUNS:
        return {"available": False, "runs": len(q),
                "needs": f"{EF_MIN_RUNS} easy/long runs with heart rate (have {len(q)})"}
    q.sort(key=lambda r: r["_date"])
    span = (q[-1]["_date"] - q[0]["_date"]).days
    if span < EF_MIN_SPAN_DAYS:
        return {"available": False, "runs": len(q),
                "needs": f"easy runs with HR spread over {EF_MIN_SPAN_DAYS}+ days (have {span})"}
    early = [r for r in q if (r["_date"] - q[0]["_date"]).days < EF_WINDOW_DAYS]
    recent = [r for r in q if (q[-1]["_date"] - r["_date"]).days < EF_WINDOW_DAYS and r not in early]
    if len(early) < 2 or len(recent) < 2:
        return {"available": False, "runs": len(q),
                "needs": "at least 2 easy runs with HR in both the first and the latest 2 weeks"}
    ef_early, ef_recent = _mean(r["_ef"] for r in early), _mean(r["_ef"] for r in recent)
    ref_hr = round(statistics.median(r["avg_hr"] for r in q))
    pace = lambda ef: 1000 / (ef * ref_hr) * 60          # sec/km at the reference HR
    change = (ef_recent - ef_early) / ef_early
    return {"available": True, "runs": len(q), "early_runs": len(early), "recent_runs": len(recent),
            "span_days": span, "reference_hr": ref_hr,
            "pace_at_reference_hr_early": round(pace(ef_early)), "pace_at_reference_hr_recent": round(pace(ef_recent)),
            "pace_change_sec_per_km": round(pace(ef_recent) - pace(ef_early)),
            "change_pct": round(100 * change, 1), "direction": _direction(change),
            "caveat": "Heat, hills and tiredness all move this; a trend over several weeks matters, "
                      "a single run doesn't."}


def long_run_drift(runs):
    q = sorted((r for r in runs if r.get("_session_type") == "long" and r.get("hr_drift_delta") is not None),
               key=lambda r: r["_date"])
    if len(q) < DRIFT_MIN_RUNS:
        return {"available": False, "runs": len(q), "needs": f"{DRIFT_MIN_RUNS} long runs with heart rate"}
    half = len(q) // 2
    early, recent = q[:half] or q[:1], q[-half:] or q[-1:]
    e, r_ = _mean(x["hr_drift_delta"] for x in early), _mean(x["hr_drift_delta"] for x in recent)
    change = (r_ - e) / abs(e) if e else None
    return {"available": True, "runs": len(q),
            "early_avg_drift_bpm": round(e, 1), "recent_avg_drift_bpm": round(r_, 1),
            "early_avg_km": round(_mean(x["distance_km"] for x in early), 1),
            "recent_avg_km": round(_mean(x["distance_km"] for x in recent), 1),
            "flagged_runs": sum(1 for x in q if x["hr_drift_delta"] >= HR_DRIFT_FLAG_BPM),
            "direction": _direction(change, higher_is_better=False) if change is not None else "stable"}


def _best_segment(splits, km):
    times = [s.get("time_sec") for s in (splits or []) if s.get("time_sec")]
    k = int(km)
    if len(times) < k:
        return None
    return min(sum(times[i:i + k]) for i in range(len(times) - k + 1))


def time_trials(runs):
    out = []
    for r in sorted((r for r in runs if r.get("_session_type") == "time_trial"), key=lambda r: r["_date"]):
        m = re.search(r"(\d+(?:\.\d+)?) km timed", r.get("_session_label") or "")
        km = float(m.group(1)) if m else 5.0
        seg = _best_segment(r.get("_splits"), km)
        out.append({"date": r["_date"].isoformat(), "timed_km": km,
                    "time_sec": seg, "pace": _pace_str(seg / km) if seg else None,
                    "note": None if seg else "no usable per-km splits in this file"})
    timed = [t for t in out if t["time_sec"]]
    change = (timed[-1]["time_sec"] - timed[0]["time_sec"]) if len(timed) >= 2 else None
    return {"results": out, "change_sec_first_to_latest": change}


def _volume_trend(weeks, unit, n):
    complete = [w for w in weeks if w["complete"]]
    if len(complete) < 2 * n:
        return None
    prev, last = complete[-2 * n:-n], complete[-n:]
    a, b = _mean(w[unit] for w in prev), _mean(w[unit] for w in last)
    planned = _mean(w["planned_" + unit] for w in last)
    change = (b - a) / a if a else None
    return {"weeks_compared": n, "previous_avg": round(a, 1), "recent_avg": round(b, 1),
            "recent_planned_avg": round(planned, 1) if planned else None,
            "previous_was_easier_week": n == 1 and prev[0].get("phase") == "cutback",
            "recent_was_easier_week": n == 1 and last[0].get("phase") == "cutback",
            "change_pct": round(100 * change, 1) if change is not None else None,
            "direction": ("up" if change and change >= DIRECTION_PCT else
                          "down" if change and change <= -DIRECTION_PCT else "steady")}


def signal_history(events):
    out = {}
    for e in events:
        s = out.setdefault(e["signal_name"], {"flagged_events": 0, "times_threshold_met": 0,
                                               "last_flagged": None})
        if e["flagged"]:
            s["flagged_events"] += 1
            s["last_flagged"] = max(s["last_flagged"] or "", e["occurred_at"][:10])
        if e["threshold_met"]:
            s["times_threshold_met"] += 1
    return out


# ---- entry point -------------------------------------------------------

def build_trends(runs, sessions, signal_events, today, goal_type="race", ladder_progress=None):
    """runs: run_log rows (+ '_session_type', '_session_label', '_splits' for matched runs)
    sessions: non-superseded program rows. Returns the full trend report."""
    for r in runs:
        r["_date"] = _d(r["recorded_at"])
    for s in sessions:
        s["_date"] = _d(s["session_date"])
    unit = "minutes" if goal_type == "general_fitness" else "km"

    if not runs:
        return {"tier": "building_baseline", "runs_logged": 0, "days_of_data": 0,
                "feedback": [], "next_unlock": f"Trends start once {MIN_RUNS} runs over {MIN_DAYS} days are "
                                                f"logged. No runs logged yet."}
    first = min(r["_date"] for r in runs)
    days = (today - first).days + 1
    weeks_of_data = days // 7
    n = len(runs)

    if days < MIN_DAYS or n < MIN_RUNS:
        need = []
        if days < MIN_DAYS:
            need.append(f"{MIN_DAYS - days} more day(s)")
        if n < MIN_RUNS:
            need.append(f"{MIN_RUNS - n} more logged run(s)")
        return {"tier": "building_baseline", "runs_logged": n, "days_of_data": days,
                "first_run": first.isoformat(), "feedback": [],
                "next_unlock": "Trends start after " + " and ".join(need) + "."}

    tier = ("established" if weeks_of_data >= TIER_ESTABLISHED_WEEKS else
            "developing" if weeks_of_data >= TIER_DEVELOPING_WEEKS else "early")
    weeks = weekly_table(runs, sessions, first, today)
    report = {"tier": tier, "runs_logged": n, "days_of_data": days, "weeks_of_data": weeks_of_data,
              "first_run": first.isoformat(), "unit": unit, "weekly": weeks,
              "consistency_all": consistency(sessions, today),
              "consistency_last_4_weeks": consistency(sessions, today, weeks_back=4),
              "volume_week_on_week": _volume_trend(weeks, unit, 1),
              "signals": signal_history(signal_events)}
    feedback = []

    c4 = report["consistency_last_4_weeks"]
    if c4:
        feedback.append({"topic": "consistency", "evidence": f"{c4['planned']} sessions",
                         "text": f"{c4['done']} of {c4['planned']} planned sessions done in the last 4 weeks "
                                 f"({c4['pct']}%); "
                                 f"current streak {c4['current_streak']}."})
    vw = report["volume_week_on_week"]
    if vw:
        text = f"Last full week {vw['recent_avg']:g} {unit}"
        if vw["recent_planned_avg"]:
            text += f" of {vw['recent_planned_avg']:g} planned"
        text += f", vs {vw['previous_avg']:g} the week before ({vw['change_pct']:+g}%"
        text += (", a planned easier week)." if vw["recent_was_easier_week"] else
                 ", after a planned easier week)." if vw["previous_was_easier_week"] else ").")
        feedback.append({"topic": "volume", "evidence": "last 2 complete weeks", "text": text})
    if ladder_progress:
        report["walk_run_progress"] = ladder_progress
        feedback.append({"topic": "walk_run", "evidence": f"{ladder_progress['sessions_done']} sessions",
                         "text": ladder_progress["text"]})

    if tier in ("developing", "established"):
        ef = aerobic_efficiency(runs)
        report["aerobic_fitness"] = ef
        if ef["available"]:
            feedback.append({"topic": "aerobic_fitness", "evidence": f"{ef['runs']} easy runs over {ef['span_days']} days",
                             "text": f"At about HR {ef['reference_hr']}, easy pace went from "
                                     f"{_pace_str(ef['pace_at_reference_hr_early'])} to "
                                     f"{_pace_str(ef['pace_at_reference_hr_recent'])} "
                                     f"({ef['pace_change_sec_per_km']:+d} s/km): {ef['direction']}."})
        dr = long_run_drift(runs)
        report["long_run_drift"] = dr
        if dr["available"]:
            feedback.append({"topic": "endurance", "evidence": f"{dr['runs']} long runs",
                             "text": f"Long-run heart-rate drift {dr['early_avg_drift_bpm']:g} -> "
                                     f"{dr['recent_avg_drift_bpm']:g} bpm while long runs went "
                                     f"{dr['early_avg_km']:g} -> {dr['recent_avg_km']:g} km: {dr['direction']}."})
        mbw = missed_by_weekday(sessions, today)
        report["missed_by_weekday"] = mbw
        top = next(iter(mbw.items()), None)
        if top and top[1] >= 2:
            feedback.append({"topic": "schedule", "evidence": f"{sum(mbw.values())} missed/unlogged",
                             "text": f"{top[0]} is the most-missed day ({top[1]} times); consider moving that session."})
        tt = time_trials(runs)
        report["time_trials"] = tt
        if tt["change_sec_first_to_latest"] is not None:
            ch = tt["change_sec_first_to_latest"]
            feedback.append({"topic": "time_trial", "evidence": f"{len(tt['results'])} time trials",
                             "text": f"Time trial {'faster' if ch < 0 else 'slower'} by {abs(ch)//60:.0f}:"
                                     f"{abs(ch) % 60:02.0f} from first to latest."})
    if tier == "established":
        block = _volume_trend(weeks, unit, 4)
        report["block_4_vs_4"] = block
        if block:
            feedback.append({"topic": "block", "evidence": "8 complete weeks",
                             "text": f"Last 4 weeks averaged {block['recent_avg']:g} {unit}/week"
                                     + (f" (plan: {block['recent_planned_avg']:g})" if block["recent_planned_avg"] else "")
                                     + f" vs {block['previous_avg']:g} in the 4 before ({block['change_pct']:+g}%)."})
    for name, s in report["signals"].items():
        if s["times_threshold_met"]:
            feedback.append({"topic": "warning_history", "evidence": f"{s['flagged_events']} flagged events",
                             "text": f"'{name}' has reached its action threshold {s['times_threshold_met']} time(s) "
                                     f"(last flagged {s['last_flagged']})."})
    report["feedback"] = feedback
    report["next_unlock"] = {
        "early": f"Aerobic fitness, long-run endurance and schedule patterns unlock at "
                 f"{TIER_DEVELOPING_WEEKS} weeks of data ({TIER_DEVELOPING_WEEKS * 7 - days + 1} more days).",
        "developing": f"Block comparison (last 4 weeks vs previous 4) unlocks at {TIER_ESTABLISHED_WEEKS} weeks "
                      f"({TIER_ESTABLISHED_WEEKS * 7 - days + 1} more days).",
        "established": "All trends active; they sharpen as more weeks are logged.",
    }[tier]
    return report
