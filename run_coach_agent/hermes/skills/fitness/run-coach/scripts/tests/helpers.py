"""Test helper: complete the pre-plan reviews (free-text safety review and
race check) the way the coach would, so tests can build plans."""
import coach_tools as ct


def complete_reviews(athlete_id):
    tasks = ct.call_tool("get_review_tasks", {"athlete_id": athlete_id})
    assert tasks["ok"], tasks
    for t in tasks["tasks"]:
        if t["goal"].startswith("Safety-screen"):
            r = ct.call_tool("record_safety_review", {"athlete_id": athlete_id, "reviewer_outcome": "caution",
                                                      "own_outcome": "caution", "reasons": ["old knee injury"],
                                                      "notes": "test review"})
        else:
            s = ct.call_tool("get_athlete_summary", {"athlete_id": athlete_id})
            r = ct.call_tool("confirm_race_info", {"race_id": s["active_goal"]["race_id"], "note": "test"})
        assert r["ok"], r


def call_with_reviews(call_tool):
    """Wrap a tests' call function: finish the reviews before generate_program."""
    def call(name, args):
        if name == "generate_program":
            complete_reviews(args["athlete_id"])
        return call_tool(name, args)
    return call
