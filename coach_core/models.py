from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Boolean, ForeignKey, Text
from datetime import datetime
from coach_core.database import Base


class Athlete(Base):
    __tablename__ = "athletes"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)

    # Plan type: "full" (phase-based) or "c25k" (beginner program)
    plan_type = Column(String, nullable=False, default="full")

    # Full plan fields (nullable for c25k athletes until graduation)
    current_weekly_mileage = Column(Float, nullable=True)
    vo2x = Column(Float, nullable=True)
    race_distance = Column(String, nullable=True)
    race_hilliness = Column(String, nullable=False, default="low")
    race_date = Column(Date, nullable=True)
    race_name = Column(String, nullable=True)
    preset_race_id = Column(String, nullable=True)
    start_date = Column(Date, nullable=False)

    # Training day preferences
    long_run_day = Column(String, nullable=False, default="Sat")  # preferred long run day
    quality_day  = Column(String, nullable=False, default="Tue")  # preferred hard session day
    extra_training_days = Column(String, nullable=False, default="Thu")  # comma-separated e.g. "Wed,Thu"

    # Training profile
    training_profile = Column(String, nullable=False, default="conservative")  # "conservative" or "aggressive"

    # C25K fields
    c25k_week = Column(Integer, nullable=True)
    c25k_completed = Column(Boolean, nullable=False, default=False)

    # Location for TRUEPACE weather adjustment
    latitude  = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    run_hour  = Column(Integer, nullable=True, default=7)

    # Reward / streak tracking
    streak_weeks  = Column(Integer, nullable=False, default=0)  # consecutive compliant weeks
    total_badges  = Column(Integer, nullable=False, default=0)  # badges earned (4 weeks = 1 badge)

    # ── Link code (v1.8) — short code to link Telegram ↔ mobile app ──────────
    # Generated on first /mycode call. Format: "NAME-XXXX" e.g. "ANDY-4821"
    link_code = Column(String, unique=True, nullable=True, index=True)

    # ── Anchor runs (v1.7) — club/group runs that are fixed each week ─────────
    # JSON: [{"day": "Tue", "km": 10.0}, {"day": "Thu", "km": 8.0}]
    # Max 2 anchors, easy days only. Non-anchor adjustable runs redistribute.
    anchor_runs = Column(String, nullable=True)

    # ── Strength module (v1.6) — columns frozen; calendar display uses
    # _get_strength_days() in formatting.py which derives days from the
    # plan, no DB columns required. Re-add when building the full feature.

    # VO2X pace-gap check (v1.6) — frozen pending migration
    # vo2x_pace_check_cooldown_until = Column(Date, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RunLog(Base):
    __tablename__ = "run_logs"

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    week_number = Column(Integer, nullable=False)
    day_name = Column(String, nullable=False)
    planned_distance_km = Column(Float, nullable=True)
    actual_distance_km = Column(Float, nullable=False)
    duration_minutes = Column(Float, nullable=True)
    rpe = Column(Integer, nullable=True)
    notes = Column(String, nullable=True)
    # v1.6: pace tracking for VO2X pace-gap check
    prescribed_pace_min_per_km = Column(Float, nullable=True)   # stored at log time; doesn't change with VO2X
    source = Column(String, nullable=True, default="manual")    # "manual" | "treadmill"
    logged_at = Column(DateTime, default=datetime.utcnow)


class VO2XHistory(Base):
    __tablename__ = "vo2x_history"

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    vo2x = Column(Float, nullable=False)
    source = Column(String, nullable=True)   # "initial"|"race"|"adjusted"|"c25k_graduation"|"pace_adjusted"
    effective_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Strength module tables (v1.6) ──────────────────────────────────────────

class StrengthTemplate(Base):
    __tablename__ = "strength_templates"

    id           = Column(Integer, primary_key=True)
    phase_name   = Column(String, nullable=False)   # Base|Repetitions|Intervals|Taper|C25K
    template_name = Column(String, nullable=False)
    structure    = Column(Text, nullable=False)      # JSON: {"exercises": [...]}
    difficulty   = Column(String, nullable=False, default="beginner")  # beginner|intermediate|advanced
    created_at   = Column(DateTime, default=datetime.utcnow)


class StrengthLog(Base):
    __tablename__ = "strength_logs"

    id              = Column(Integer, primary_key=True)
    athlete_id      = Column(Integer, ForeignKey("athletes.id"), nullable=False)
    log_date        = Column(Date, nullable=False)
    template_id     = Column(Integer, ForeignKey("strength_templates.id"), nullable=True)
    session_name    = Column(String, nullable=False, default="")
    exercises_done  = Column(Text, nullable=True)       # JSON: per-set weight/reps/rpe
    total_volume_load = Column(Float, nullable=True)    # sum(sets × reps × weight_kg)
    session_rpe     = Column(Integer, nullable=True)    # 1–10
    duration_min    = Column(Integer, nullable=True)
    notes           = Column(String, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
