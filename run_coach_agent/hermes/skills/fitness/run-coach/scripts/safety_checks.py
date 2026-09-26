"""
safety_checks.py — deterministic checks on a new athlete's intake.

Three layers protect the plan from bad or dangerous input:

1. intake_checks(): impossible or suspicious numbers (a longest run
   bigger than the 4-week total, a race date in the past, 600 km a
   month...). "block" stops that row; "warn" goes to the owner.
2. free-text screen: a keyword net over what the athlete wrote
   (injuries, "anything else", what fitter means to them). Any real
   free text is then read by an independent reviewer agent; the
   keyword hits are passed along as hints. Nothing is cleared on
   keywords alone and nothing is missed because a keyword didn't match.
3. race verification: compare_race_sources() checks two independently
   found descriptions of the race against each other and the form.

All pure functions; coach_tools.py stores results and enforces the gates.
"""
import re
from datetime import date

# (pattern, label, level) -- level "medical" means a doctor should clear
# them; "injury" means the coach must take it into account.
RED_FLAGS = [
    (r"heart|cardiac|cardiomyo|arrhythm|a-?fib|atrial|palpitat|pacemaker|stent|bypass|angina|valve", "heart", "medical"),
    (r"chest (pain|tight)", "chest pain", "medical"),
    (r"faint|black ?out|collaps|passed out|dizz", "fainting / dizziness", "medical"),
    (r"blood pressure|hypertens", "blood pressure", "medical"),
    (r"stroke|\btia\b|blood clot|\bdvt\b|embolism", "stroke / clot", "medical"),
    (r"seizure|epilep|convuls", "seizures", "medical"),
    (r"diabet|insulin", "diabetes", "medical"),
    (r"pregnan|postpartum|post-partum|gave birth|had a baby", "pregnancy / postpartum", "medical"),
    (r"surgery|operation|\bop\b|keyhole|replacement|reconstruct", "surgery", "medical"),
    (r"concussion|head injur", "concussion", "medical"),
    (r"cancer|chemo|radiotherap", "cancer treatment", "medical"),
    (r"kidney|renal", "kidney", "medical"),
    (r"anorexi|bulimi|eating disorder|\bred-?s\b|\bamenorr", "eating disorder / RED-S", "medical"),
    (r"long covid|breathless|short of breath", "breathlessness", "medical"),
    (r"doctor|gp said|physio said|specialist|cardiologist|not allowed|told (me )?not to", "medical advice mentioned", "medical"),
    (r"asthma|inhaler", "asthma", "injury"),
    (r"fractur|\bbroken\b|\bbroke\b|stress reaction", "fracture", "injury"),
    (r"achilles|plantar|shin splint|\bit ?band|patell|meniscus|\bacl\b|hamstring|\bcalf\b|knee|\bhips?\b|back pain|sciatica",
     "musculoskeletal", "injury"),
]
TRIVIAL = re.compile(r"^\s*(none|no|nope|n/?a|nil|nothing|-+|\.+|no injuries|not really)\s*[.!]?\s*$", re.I)


def free_text(norm):
    """The athlete's own words worth screening, trivial answers removed."""
    parts = []
    for key, label in (("injury_history", "Injuries"), ("anything_else", "Anything else"),
                       ("fitter_meaning", "What fitter means"), ("fixed_commitments", "Commitments")):
        v = str(norm.get(key) or "").strip()
        if v and not TRIVIAL.match(v):
            parts.append(f"{label}: {v}")
    return "\n".join(parts)


def keyword_hits(text):
    hits, low = [], text.lower()
    for pattern, label, level in RED_FLAGS:
        m = re.search(pattern, low)
        if m:
            s = max(0, m.start() - 40)
            hits.append({"label": label, "level": level, "excerpt": text[s:m.end() + 40].strip()})
    return hits


def _n(v):
    try:
        return float(re.search(r"\d+(\.\d+)?", str(v)).group()) if v not in (None, "") else None
    except AttributeError:
        return None


def intake_checks(norm, goal_type, race_date_iso, distance_km, today):
    """Returns [{"level": "block"|"warn", "code", "message"}]."""
    out = []
    add = lambda level, code, msg: out.append({"level": level, "code": code, "message": msg})
    longest, total = _n(norm.get("longest_run_km")), _n(norm.get("total_km_4wk"))
    weight = _n(norm.get("weight_kg"))
    if longest is not None and total is not None and total > 0 and longest > total:
        add("warn", "longest_gt_total", f"Longest run ({longest:g} km) is more than the 4-week total ({total:g} km) "
                                        f"-- the two answers may be swapped.")
    if total is not None and total > 600:
        add("warn", "total_implausible", f"{total:g} km in 4 weeks is very high -- check it isn't miles, metres or a typo.")
    if longest is not None and longest > 60:
        add("warn", "longest_implausible", f"Longest run {longest:g} km in the last 4 weeks -- check it.")
    if weight is not None and not 30 <= weight <= 250:
        add("warn", "weight_implausible", f"Weight {weight:g} kg looks wrong (pounds?).")
    if goal_type == "race":
        if race_date_iso:
            rd = date.fromisoformat(race_date_iso)
            days = (rd - today).days
            if days < 0:
                add("block", "race_in_past", f"Race date {race_date_iso} is in the past -- wrong year, or wrong race?")
            elif days < 14:
                add("warn", "race_too_soon", f"Race is only {days} days away -- no real build is possible; "
                                             f"the coach can only offer a short maintain-and-taper.")
            elif days > 550:
                add("warn", "race_far", f"Race is {days // 30} months away -- check the year.")
        if total in (None, 0):
            add("warn", "no_running_base", "No recent running for a race goal -- a general-fitness block first "
                                           "is likely (the plan builder will say so).")
        if distance_km and longest is not None and distance_km >= 21 and longest < 5 and race_date_iso:
            weeks = (date.fromisoformat(race_date_iso) - today).days // 7
            if weeks < 16:
                add("warn", "big_jump", f"{distance_km:g} km race in {weeks} weeks with a longest recent run of "
                                        f"{longest:g} km -- expect to talk about a later race or a finish-only goal.")
    else:
        cont = norm.get("continuous_run")
        weekly = norm.get("weekly_run_minutes")
        if cont and "30+" in str(cont) and weekly and str(weekly).strip().lower().startswith("none"):
            add("warn", "gf_inconsistent", "Says they can run 30+ min non-stop but runs 'None' per week -- ask which "
                                           "is current.")
    return out


def compare_race_sources(form, primary, secondary):
    """form/primary/secondary: {"race_date", "distance_km", "elevation_gain_m", ...}.
    Returns {"status": "verified"|"mismatch", "differences": [...], "agreed": {...}}."""
    diffs = []
    p_src, s_src = str(primary.get("source") or "").strip(), str(secondary.get("source") or "").strip()
    if not p_src or not s_src:
        diffs.append("each description must name its source")
    elif p_src.rstrip("/").lower() == s_src.rstrip("/").lower():
        diffs.append("both descriptions came from the same source -- the second check must be independent")

    def same_date(a, b):
        return a and b and str(a)[:10] == str(b)[:10]

    def close(a, b, rel, absolute):
        return a is not None and b is not None and abs(a - b) <= max(rel * max(a, b), absolute)

    pd, sd = primary.get("race_date"), secondary.get("race_date")
    if not same_date(pd, sd):
        diffs.append(f"race date: source 1 says {pd}, source 2 says {sd}")
    elif form.get("race_date") and not same_date(pd, form["race_date"]):
        diffs.append(f"race date: the form says {form['race_date']}, both sources say {pd}")
    pk, sk = _n(primary.get("distance_km")), _n(secondary.get("distance_km"))
    if not close(pk, sk, 0.03, 0.3):
        diffs.append(f"distance: source 1 says {pk} km, source 2 says {sk} km")
    elif form.get("distance_km") and not close(pk, _n(form["distance_km"]), 0.03, 0.3):
        diffs.append(f"distance: the form says {form['distance_km']} km, both sources say {pk} km")
    pe, se = _n(primary.get("elevation_gain_m")), _n(secondary.get("elevation_gain_m"))
    elevation = None
    if pe is not None and se is not None:
        if close(pe, se, 0.25, 60):
            elevation = round((pe + se) / 2)
        else:
            diffs.append(f"elevation gain: source 1 says {pe:g} m, source 2 says {se:g} m")
    else:
        elevation = pe if pe is not None else se
    return {"status": "mismatch" if diffs else "verified", "differences": diffs,
            "agreed": {"race_date": str(pd)[:10] if pd else None, "distance_km": pk,
                       "elevation_gain_m": elevation,
                       "elevation_note": None if (pe is not None and se is not None) else
                       ("only one source gave elevation" if elevation is not None else "no source gave elevation"),
                       "terrain_notes": primary.get("terrain") or secondary.get("terrain")}}
