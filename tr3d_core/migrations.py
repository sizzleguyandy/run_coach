"""
Additive, idempotent startup migration.

This project has no migration framework — schema changes were historically
handled by deleting coach.db and letting create_all() rebuild it from
scratch. That's fine for local SQLite dev but destroys real athlete data on
a deployed database. add_missing_columns() closes that gap for the one
shape of change this codebase actually makes: new nullable columns on an
existing table. It runs on every startup, is a no-op once a column exists,
and never touches existing data or drops/renames anything.
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.schema import MetaData

logger = logging.getLogger(__name__)


async def add_missing_columns(engine: AsyncEngine, metadata: MetaData) -> None:
    async with engine.begin() as conn:
        table_names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))

        for table in metadata.sorted_tables:
            if table.name not in table_names:
                continue  # brand-new table — create_all() already built it in full

            existing_columns = await conn.run_sync(
                lambda c, t=table.name: {col["name"] for col in inspect(c).get_columns(t)}
            )

            for column in table.columns:
                if column.name in existing_columns:
                    continue
                if not column.nullable and column.server_default is None:
                    logger.warning(
                        "Skipping auto-migration of %s.%s: non-nullable column with no "
                        "default can't be added to an existing table automatically. "
                        "Add it as nullable (or with a server_default) instead.",
                        table.name, column.name,
                    )
                    continue

                col_type = column.type.compile(dialect=engine.sync_engine.dialect)
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"
                logger.info("Auto-migration: %s", ddl)
                await conn.execute(text(ddl))
