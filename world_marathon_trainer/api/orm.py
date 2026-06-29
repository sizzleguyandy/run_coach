"""SQLAlchemy ORM models.

Two tables:
  athletes    — the plan inputs + identity an n8n workflow keys against
  activities  — Strava 'truth', one row per synced activity (dedup by Strava id)

The athlete row is everything build_plan() needs, so plans rebuild/adapt without
re-onboarding. Activities are the history the adaptation loop will read later.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Float, Integer, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Athlete(Base):
    __tablename__ = "athletes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="Athlete")

    # Strava linkage (per-athlete n8n workflow knows this).
    strava_athlete_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Race target
    race_id: Mapped[str] = mapped_column(String)
    race_date: Mapped[date] = mapped_column(Date)

    # Fitness inputs
    current_weekly_km: Mapped[float] = mapped_column(Float, default=0.0)
    training_days_per_week: Mapped[int] = mapped_column(Integer, default=4)
    can_run_10k_continuous: Mapped[bool] = mapped_column(Boolean, default=False)
    longest_continuous_run_min: Mapped[float] = mapped_column(Float, default=0.0)
    recent_race_distance_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recent_race_time_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    goal_marathon_time_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Cached fitness index (effective: adapted value if set, else race estimate)
    vdot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # VDOT set by the adaptation loop. When present it wins over race estimate.
    adapted_vdot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    activities: Mapped[list["Activity"]] = relationship(
        back_populates="athlete", cascade="all, delete-orphan"
    )


class Activity(Base):
    __tablename__ = "activities"

    # Strava activity id — primary key gives free dedup on re-sync.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    athlete_id: Mapped[str] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), index=True
    )

    start_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    activity_type: Mapped[str] = mapped_column(String, default="Run")
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    moving_time_min: Mapped[float] = mapped_column(Float, default=0.0)
    elapsed_time_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_elevation_gain_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_pace_min_per_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_heartrate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_heartrate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    suffer_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Original payload kept verbatim for fields we don't model yet.
    raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    athlete: Mapped["Athlete"] = relationship(back_populates="activities")


class AdaptationLog(Base):
    """Auditable record of every adaptation decision."""

    __tablename__ = "adaptation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    athlete_id: Mapped[str] = mapped_column(
        ForeignKey("athletes.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    week_index: Mapped[int] = mapped_column(Integer)
    weeks_to_race: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phase: Mapped[str] = mapped_column(String)

    vdot_before: Mapped[float] = mapped_column(Float)
    vdot_after: Mapped[float] = mapped_column(Float)

    # Full decision payload kept as JSON for audit / agent narration.
    decision: Mapped[str] = mapped_column(Text)
