import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from dotenv import load_dotenv

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./coach.db")

# Railway (and most cloud providers) give a plain postgres:// URL.
# SQLAlchemy async requires the +asyncpg driver scheme.
if _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql://") and "+asyncpg" not in _raw_url:
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

DATABASE_URL = _raw_url
_is_sqlite = DATABASE_URL.startswith("sqlite")

# SQLite: busy timeout + WAL mode so concurrent writes don't deadlock.
# PostgreSQL: no special connect args needed.
_connect_args = {"timeout": 20} if _is_sqlite else {}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    # PostgreSQL connection pool tuning (ignored for SQLite)
    pool_size=5 if not _is_sqlite else 1,
    max_overflow=10 if not _is_sqlite else 0,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")    # readers never block writers
        cursor.execute("PRAGMA synchronous=NORMAL")  # safe + faster than FULL
        cursor.execute("PRAGMA busy_timeout=20000")  # 20s wait on locked DB
        cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _migrate_schema(conn) -> None:
    """In-place schema migrations that run before create_all.

    Kept deliberately small and idempotent: each step checks the live schema
    first, so running it on an already-migrated database is a no-op and it is
    safe on every boot.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    if "athletes" not in inspector.get_table_names():
        return   # fresh database — create_all builds the current schema

    columns = {c["name"] for c in inspector.get_columns("athletes")}

    # telegram_id -> athlete_ref: the identity is an opaque reference, not a
    # Telegram-specific field. Supported by SQLite 3.25+ and PostgreSQL.
    if "telegram_id" in columns and "athlete_ref" not in columns:
        conn.execute(text("ALTER TABLE athletes RENAME COLUMN telegram_id TO athlete_ref"))
        import logging
        logging.getLogger(__name__).info(
            "schema: renamed athletes.telegram_id -> athletes.athlete_ref"
        )


async def init_db():
    async with engine.begin() as conn:
        from coach_core import models  # noqa: F401
        await conn.run_sync(_migrate_schema)
        await conn.run_sync(Base.metadata.create_all)
