from __future__ import annotations

import logging

from ..config import settings
from ..db import DATABASE_URL, engine
from ..migrations import run_db_migrations

logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("Running database migrations (job mode).")
    run_db_migrations(
        engine=engine,
        database_url=DATABASE_URL,
        expected_revision=settings.expected_alembic_revision or None,
        require_schema_ready=True,
    )
    logger.info("Database migrations completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
