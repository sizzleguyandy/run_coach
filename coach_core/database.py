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


async def init_db():
    async with engine.begin() as conn:
        from coach_core import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
