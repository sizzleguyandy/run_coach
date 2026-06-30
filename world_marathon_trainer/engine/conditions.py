"""Athlete condition handling — bounded, deterministic, safe.

An athlete can say anything ("knackered", "knee's sore", "got a cold"). The agent
does NOT decide what to do with that — it classifies the message into ONE of a
fixed set of conditions, and THIS engine decides the response. That keeps an LLM's
"infinite options" inside a safe, predictable decision table.

Two conditions are RED FLAGS — sharp/acute pain and systemic illness (fever/chest/
body). For these the engine returns REST + see-a-professional and the agent must
NOT offer a workout. The agent cannot override a red flag; it only relays it.

This is not medical advice; for pain/illness the response always points to a
professional.
"""

from __future__ import annotations

from .models import ConditionResponse

# The fixed taxonomy. The agent must map free text to exactly one code.
CONDITIONS = {
    "tired":            {"label": "Tired / heavy legs / poor sleep", "red_flag": False},
    "niggle":           {"label": "Mild soreness in a specific area (not sharp)", "red_flag": False},
    "pain":             {"label": "Sharp/acute pain, or it affects how you move", "red_flag": True},
    "illness_mild":     {"label": "Head cold, above the neck, no fever", "red_flag": False},
    "illness_systemic": {"label": "Fever, chest, or whole-body symptoms", "red_flag": True},
    "missed":           {"label": "Missed a session / travel / time-poor", "red_flag": False},
    "great":            {"label": "Feeling strong, want to do more", "red_flag": False},
    "stress":           {"label": "Life stress / low energy (not physical)", "red_flag": False},
}

_REST = {"kind": "rest", "description": "Rest day"}
_XTRAIN = {"kind": "cross_train",
           "description": "Low-impact cross-train 30 min (bike or pool), easy effort"}
_RECOVERY = {"kind": "easy",
             "description": "Very easy 30-40 min recovery jog, or rest if still flat"}


def _swap_today(today_session: dict | None, replacement: dict) -> dict:
    """Build a modified session, noting what it replaced."""
    was = (today_session or {}).get("description", "today's session")
    return {**replacement, "replaces": was}


def evaluate_condition(
    condition: str,
    severity: str = "moderate",        # "mild" | "moderate" | "severe"
    body_area: str | None = None,
    today_session: dict | None = None,
    phase: str | None = None,
) -> ConditionResponse:
    """Map a classified condition to a safe, deterministic response."""
    if condition not in CONDITIONS:
        return ConditionResponse(
            condition=condition, recognised=False, action="clarify",
            modified_session=None, escalate_to_professional=False,
            message=("I didn't catch how you're feeling. Tell me in a few words "
                     "(tired? a specific sore spot? sharp pain? a cold? short on "
                     "time?) and I'll adjust."),
            safety_note="",
        )

    sev = severity if severity in ("mild", "moderate", "severe") else "moderate"
    area = f" ({body_area})" if body_area else ""

    # ---- RED FLAGS — engine forbids training, recommends a professional --- #
    if condition == "pain":
        return ConditionResponse(
            condition=condition, recognised=True, action="stop",
            modified_session=_REST, escalate_to_professional=True,
            message=(f"Stop for today — don't run on sharp pain{area}. Rest it, "
                     f"and if it doesn't settle quickly or it changes how you "
                     f"walk/run, see a physio before running again."),
            safety_note="Sharp or movement-altering pain is a red flag: no running "
                        "until assessed.",
        )

    if condition == "illness_systemic":
        return ConditionResponse(
            condition=condition, recognised=True, action="rest",
            modified_session=_REST, escalate_to_professional=True,
            message=("No running while it's in your chest/body or you have a fever. "
                     "Rest fully, recover, and ease back only once symptoms have "
                     "cleared. See a doctor if you have a fever."),
            safety_note="Systemic illness (fever/chest/body): full rest, no training; "
                        "medical advice if febrile.",
        )

    # ---- niggle: a moderate/severe niggle is escalated toward pain -------- #
    if condition == "niggle":
        if sev == "severe":
            return ConditionResponse(
                condition=condition, recognised=True, action="stop",
                modified_session=_REST, escalate_to_professional=True,
                message=(f"That soreness{area} is strong enough that I'd treat it "
                         f"like a flag — rest today, and if it's no better in a "
                         f"couple of days or it sharpens, get it looked at."),
                safety_note="Severe soreness treated as potential injury.",
            )
        return ConditionResponse(
            condition=condition, recognised=True, action="swap_easy",
            modified_session=_swap_today(today_session, _XTRAIN),
            escalate_to_professional=False,
            message=(f"Let's protect that{area}. Swap today for low-impact "
                     f"cross-training or an easy short jog — nothing that loads it. "
                     f"If it eases as you warm up it's a niggle; if it sharpens or "
                     f"lingers past a few days, stop and get it checked."),
            safety_note="Monitor: escalate to rest + professional if it sharpens or "
                        "persists.",
        )

    # ---- non-red-flag adjustments ---------------------------------------- #
    if condition == "tired":
        kind = (today_session or {}).get("kind")
        if kind in ("quality", "long"):
            mod = _swap_today(today_session, _RECOVERY)
            msg = ("Heavy legs — skip the hard work today. Easy 30-40 min recovery "
                   "(or rest if you're really flat). We don't chase quality on tired "
                   "legs; the adaptation comes from the easy days too.")
        else:
            mod = _swap_today(today_session, _REST)
            msg = ("Take it as a rest day — sleep and food are the session today. "
                   "Better to bank recovery than grind out a flat run.")
        return ConditionResponse(
            condition=condition, recognised=True, action="reduce",
            modified_session=mod, escalate_to_professional=False,
            message=msg, safety_note="",
        )

    if condition == "illness_mild":
        return ConditionResponse(
            condition=condition, recognised=True, action="easy_or_rest",
            modified_session=_swap_today(today_session, _RECOVERY),
            escalate_to_professional=False,
            message=("Head cold, above the neck, no fever: an easy 20-30 min is "
                     "usually fine, or just rest. Skip anything hard until you're "
                     "clear. If it drops to your chest or you spike a fever, stop "
                     "completely."),
            safety_note="Above-the-neck rule; escalate to rest if it becomes systemic.",
        )

    if condition == "missed":
        return ConditionResponse(
            condition=condition, recognised=True, action="prioritise",
            modified_session=None, escalate_to_professional=False,
            message=("No drama — we never cram. Prioritise this week's key session "
                     "(the long run or the quality day), and let the rest go. "
                     "Consistency over heroics; your Strava sync will reflect the "
                     "real volume and the plan adjusts itself."),
            safety_note="",
        )

    if condition == "great":
        return ConditionResponse(
            condition=condition, recognised=True, action="hold",
            modified_session=None, escalate_to_professional=False,
            message=("Love the enthusiasm — but the progression is deliberate. Hold "
                     "today's session as planned rather than piling on extra; that's "
                     "how we avoid the too-much-too-soon trap. Add a few strides if "
                     "you want a bit more pop."),
            safety_note="",
        )

    # stress
    return ConditionResponse(
        condition="stress", recognised=True, action="reduce",
        modified_session=_swap_today(today_session, _RECOVERY),
        escalate_to_professional=False,
        message=("Life stress is a real training load too. Keep it easy and aerobic "
                 "this week, protect your sleep, and we'll lift the intensity again "
                 "once things settle. Easy running helps; hard running on a frayed "
                 "system doesn't."),
        safety_note="",
    )
