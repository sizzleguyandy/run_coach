"""
hevy.py — read the workout text Hevy posts to Strava.

Hevy (and Hevy Coach programs) post each workout to Strava with a
description like:

    Logged with hevyapp.com

    Chest Press (Machine)
    "Alternating"
    Set 1: 32.5 kg x 5
    Set 2: 32.5 kg x 7

    Lat Pulldown (Cable)
    Set 1: 70 kg x 5

This turns that into exercises -> sets (kg, reps), works out what the
session trained (lower body / upper body / full body / core), and gives
the numbers the coach needs: total sets, volume, and the lower-body work
that matters for the next run. Pure functions, no network.
"""
import re

LBS = 0.45359237

# Order matters: core patterns first, so "leg raise" counts as core, not legs.
CORE = re.compile(r"plank|crunch|sit[- ]?up|\bab\b|abs\b|ab wheel|dead ?bug|pallof|russian twist|leg raise|knee raise|"
                  r"hollow|bird ?dog|wood ?chop|v[- ]?up|flutter|mountain climber|side bend|cable crunch|toes to bar",
                  re.I)
LOWER = re.compile(r"squat|deadlift|\brdl\b|romanian|lunge|leg press|leg extension|leg curl|hamstring|calf|calves|"
                   r"hip thrust|glute|step[- ]?up|split squat|bulgarian|hack|good ?morning|box jump|jump|plyo|"
                   r"nordic|abduct|adduct|kettlebell swing|sled|pistol|wall sit|thruster|clean|snatch|hip hinge",
                   re.I)
UPPER = re.compile(r"press|bench|pull|row|curl|dip|fly|flye|raise|lat\b|pulldown|chest|shoulder|tricep|bicep|"
                   r"push[- ]?up|chin[- ]?up|face pull|shrug|skull|extension", re.I)
# Full-body lifts that also load the legs heavily.
LEGS_TOO = re.compile(r"thruster|clean|snatch|burpee|kettlebell swing|deadlift", re.I)

SET_LINE = re.compile(r"^\s*Set\s+(\d+)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$", re.I)


def region(name):
    if CORE.search(name):
        return "core"
    if LOWER.search(name):
        return "lower"
    if UPPER.search(name):
        return "upper"
    return "other"


def _parse_set(n, label, body):
    s = {"set": int(n), "type": (label or "").strip().lower() or None, "weight_kg": None, "reps": None,
         "duration_sec": None, "distance_km": None, "rpe": None}
    b = body.strip()
    m = re.search(r"([\d.]+)\s*(kg|lbs?|lb)\s*[x×]\s*(\d+)", b, re.I)
    if m:
        w = float(m.group(1))
        s["weight_kg"] = round(w * LBS, 1) if m.group(2).lower().startswith("lb") else w
        s["reps"] = int(m.group(3))
    else:
        m = re.search(r"(\d+)\s*reps?", b, re.I) or re.search(r"^[x×]\s*(\d+)$", b)
        if m:
            s["reps"] = int(m.group(1))
    d = re.search(r"(\d+):(\d{2})(?::(\d{2}))?", b)
    if d and s["reps"] is None:
        parts = [int(x) for x in d.groups() if x is not None]
        s["duration_sec"] = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
    k = re.search(r"([\d.]+)\s*(km|mi|m)\b", b, re.I)
    if k and s["weight_kg"] is None:
        v = float(k.group(1))
        s["distance_km"] = {"km": v, "mi": v * 1.609, "m": v / 1000}[k.group(2).lower()]
    r = re.search(r"rpe\s*([\d.]+)|@\s*([\d.]+)", b, re.I)
    if r:
        s["rpe"] = float(r.group(1) or r.group(2))
    return s


def parse_description(text):
    """Returns the structured workout, or None if the text isn't a Hevy-style log."""
    if not text or not SET_LINE.search(text.replace("\r", "")) and "Set 1" not in text:
        return None
    exercises, current, pending_name = [], None, None
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.lower().startswith("logged with"):
            continue
        m = SET_LINE.match(line)
        if m:
            if current is None:
                current = {"name": pending_name or "Unnamed exercise", "note": None, "sets": []}
                exercises.append(current)
                pending_name = None
            current["sets"].append(_parse_set(*m.groups()))
            continue
        if current is not None and not current["sets"] and line.startswith(('"', "“")):
            current["note"] = line.strip('"“”')
            continue
        if current is None and pending_name and line.startswith(('"', "“")):
            current = {"name": pending_name, "note": line.strip('"“”'), "sets": []}
            exercises.append(current)
            pending_name = None
            continue
        # A new exercise name.
        current, pending_name = None, line
    exercises = [e for e in exercises if e["sets"]]
    if not exercises:
        return None
    totals = {"lower": 0, "upper": 0, "core": 0, "other": 0}
    volume = 0.0
    for e in exercises:
        e["region"] = region(e["name"])
        working = [s for s in e["sets"] if (s["type"] or "") not in ("warm-up", "warmup", "warm up")]
        totals[e["region"]] += len(working)
        if LEGS_TOO.search(e["name"]) and e["region"] != "lower":
            totals["lower"] += len(working)
        volume += sum((s["weight_kg"] or 0) * (s["reps"] or 0) for s in working)
        best = max(working or e["sets"], key=lambda s: ((s["weight_kg"] or 0) * (1 + (s["reps"] or 0) / 30), s["reps"] or 0))
        e["top_set"] = {"weight_kg": best["weight_kg"], "reps": best["reps"]}
    lifting = totals["lower"] + totals["upper"]
    if lifting == 0:
        focus = "core" if totals["core"] else "other"
    elif totals["lower"] >= 0.65 * lifting:
        focus = "lower"
    elif totals["upper"] >= 0.65 * lifting:
        focus = "upper"
    else:
        focus = "full"
    return {"source": "hevy", "exercises": exercises, "focus": focus,
            "total_sets": sum(len(e["sets"]) for e in exercises), "volume_kg": round(volume, 1),
            "sets_by_region": totals}


def leg_summary(workout, limit=3):
    """Short text of the leg work, e.g. 'Squat 5x5 @ 80 kg, Walking Lunge 3x12'."""
    parts = []
    for e in workout.get("exercises", []):
        if e["region"] != "lower" and not LEGS_TOO.search(e["name"]):
            continue
        working = [s for s in e["sets"] if (s["type"] or "") not in ("warm-up", "warmup", "warm up")] or e["sets"]
        reps = [s["reps"] for s in working if s["reps"]]
        top = e["top_set"]
        common = max(set(reps), key=reps.count) if reps else "?"
        txt = f"{e['name']} {len(working)}x{common}"
        if top.get("weight_kg"):
            txt += f" @ {top['weight_kg']:g} kg"
        parts.append(txt)
    return ", ".join(parts[:limit])


def e1rm(weight_kg, reps):
    """Epley estimate, for comparing sets of different reps over time."""
    if not weight_kg or not reps:
        return None
    return weight_kg * (1 + reps / 30)
