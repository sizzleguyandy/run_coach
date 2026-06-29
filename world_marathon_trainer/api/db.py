"""Database engine + session for the persistence layer (SQLite via SQLAlchemy 2.0).

SQLite for the MVP — single file, zero-config. The store/ORM use plain SQLAlchemy
so a later move to Postgres is mostly a connection-string change.

DB path is configurable via WMT_DB_URL (default: sqlite file next to this package).
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wmt.db"
)
DB_URL = os.environ.get("WMT_DB_URL", f"sqlite:///{_DEFAULT_PATH}")

# check_same_thread=False so FastAPI's threadpool can share the engine.
engine = create_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create tables if they do not exist."""
    from . import orm  # noqa: F401  (register models)
    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yields a session, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
