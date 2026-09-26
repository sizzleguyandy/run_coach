"""
plan_review.py — the weekly plan readjustment rules.

Every week, the finished week is reviewed against what was planned and
the plan is readjusted from real data, not the original intake guess.
This module only DECIDES (pure function); coach_tools.review_week
gathers the numbers and applies the decision by regenerating the plan
from the next week onward.

Inputs: planned vs actual volume for the reviewed week and the one
before, any warning signals that reached their threshold, and — if the
athlete answered the check-in — how it felt, pain and illness.

Decisions:
  general fitness  progress   plan continues as scheduled (next step up)
                   repeat     next week repeats the reviewed week's level
                   step_back  drop one level (two poor weeks in a row)
  race             on_track   plan continues as scheduled
                   hold       next week repeats the reviewed volume, then growth resumes
                   step_back  rebuild from what they actually ran lately
  either           pause      pain that changes how they walk/run: no plan change,
                              stop running and get it checked
The taper and race week are never rebuilt (only pause applies there).
"""

GF_PROGRESS_MIN = 0.90       # share of planned minutes done to move up
RACE_ON_TRACK_MIN = 0.80     # share of planned km done to keep building
POOR_WEEK = 0.50             # below this = a poor week
OVER_PLAN = 1.20             # above this = ran a lot more than planned
ACTION_SIGNALS = {"long_run_hr_drift", "distance_shortfall"}


def _pct(actual, planned):
    if not planned:
        return None
    return actual / planned


def decide(goal_type, week, prev_week, active_signals, feedback=None, taper_protected=False):
    """week / prev_week: {"planned": volume, "actual": volume, "sessions_planned": n, "sessions_done": n}
    (volume in minutes for general fitness, km for race). prev_week may be None.
    feedback: {"felt": "comfortable"|"hard"|"too_hard", "pain": "none"|"niggle"|"gait_changing", "ill": bool}
    Returns {"decision", "reasons": [...], "completion", "prev_completion"}."""
    fb = feedback or {}
    felt, pain, ill = fb.get("felt"), fb.get("pain"), bool(fb.get("ill"))
    comp = _pct(week["actual"], week["planned"])
    if comp is None:                                  # no volume planned (shouldn't happen): use sessions
        comp = _pct(week["sessions_done"], week["sessions_planned"]) or 0.0
    prev = _pct(prev_week["actual"], prev_week["planned"]) if prev_week else None
    signals = sorted(set(active_signals) & ACTION_SIGNALS)
    reasons = [f"did {comp:.0%} of the planned {'minutes' if goal_type == 'general_fitness' else 'km'} "
               f"({week['sessions_done']} of {week['sessions_planned']} sessions logged)"]
    if not fb:
        reasons.append("no feedback from the athlete -- decided from logged data only")

    def out(decision, *why):
        return {"decision": decision, "reasons": reasons + list(why), "completion": round(comp, 2),
                "prev_completion": round(prev, 2) if prev is not None else None}

    if pain == "gait_changing":
        return out("pause", "pain that changes how they walk or run: stop running and get it checked; "
                            "the plan waits")
    over = comp > OVER_PLAN
    over_note = ("ran well over the plan -- that's the main way a slow ramp breaks; stick to the plan"
                 if over else None)

    if goal_type == "general_fitness":
        if prev is not None and comp < POOR_WEEK and prev < POOR_WEEK:
            return out("step_back", "two poor weeks in a row: drop back a level")
        problems = []
        if comp < GF_PROGRESS_MIN:
            problems.append("not enough of the week done to move up")
        if felt in ("hard", "too_hard"):
            problems.append(f"felt {felt.replace('_', ' ')}")
        if pain == "niggle":
            problems.append("a niggle")
        if ill:
            problems.append("illness")
        if signals:
            problems.append("warning signs: " + ", ".join(signals))
        if problems:
            return out("repeat", "repeat the level: " + "; ".join(problems))
        return out("progress", *([over_note] if over_note else []))

    # race
    if taper_protected:
        return out("on_track", "taper / race week: the plan is not rebuilt now",
                   *([over_note] if over_note else []))
    if comp < POOR_WEEK and (prev is None or prev < 0.6):
        return out("step_back", "rebuild from what they've actually been running")
    problems = []
    if comp < RACE_ON_TRACK_MIN:
        problems.append("less than 80% of the planned volume")
    if felt in ("hard", "too_hard"):
        problems.append(f"felt {felt.replace('_', ' ')}")
    if pain == "niggle":
        problems.append("a niggle")
    if ill:
        problems.append("illness")
    if signals:
        problems.append("warning signs: " + ", ".join(signals))
    if problems:
        return out("hold", "hold this volume for a week before building again: " + "; ".join(problems))
    return out("on_track", *([over_note] if over_note else []))
