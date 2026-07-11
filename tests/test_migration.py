import os
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from glintory.config import settings
from glintory.infrastructure.database import reset_db_connections


@pytest.fixture
def test_db_path(tmp_path):
    db_file = tmp_path / "test_migration.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Store original settings and environment variables
    original_url = settings.database_url

    # Overwrite settings directly because Settings was instantiated at import-time
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()  # Reset cache to apply new settings

    yield db_file

    if db_file.exists():
        db_file.unlink()

    # Restore settings and environment variables
    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_alembic_migrations(test_db_path):
    project_root = pathlib.Path(__file__).parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    # 1. Upgrade to head
    command.upgrade(alembic_cfg, "head")

    # 2. Check if all 8 tables + alembic_version exist
    engine = create_engine(f"sqlite:///{test_db_path}")
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    expected_tables = {
        "sources",
        "collection_runs",
        "signals",
        "opportunities",
        "opportunity_signals",
        "score_snapshots",
        "decisions",
        "notes",
        "alembic_version",
        "signals_fts",
        "source_schedules",
        "scheduler_leases",
        "schedule_executions",
        "opportunity_enrichments",
    }

    for table in expected_tables:
        assert table in tables, f"Table {table} not found after migration"

    # 3. Downgrade to base
    command.downgrade(alembic_cfg, "base")

    # 4. Check if custom tables are deleted (alembic_version remains)
    inspector = inspect(engine)
    tables_after_downgrade = inspector.get_table_names()
    for table in expected_tables - {"alembic_version"}:
        assert table not in tables_after_downgrade, (
            f"Table {table} still exists after downgrade"
        )

    # 5. Upgrade to head again
    command.upgrade(alembic_cfg, "head")
    inspector = inspect(engine)
    tables_re_upgraded = inspector.get_table_names()
    for table in expected_tables:
        assert table in tables_re_upgraded, (
            f"Table {table} not found after re-migration"
        )
