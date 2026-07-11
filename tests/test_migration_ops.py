import os
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from glintory.config import settings
from glintory.infrastructure.database import reset_db_connections


@pytest.fixture
def temp_db_path(tmp_path):
    db_file = tmp_path / "test_migration_ops.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    yield db_file

    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_migration_ops_columns_and_indices(temp_db_path):
    project_root = pathlib.Path(__file__).parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    # 1. Upgrade to revision prior to our new one (7fa513398108)
    command.upgrade(alembic_cfg, "7fa513398108")

    engine = create_engine(f"sqlite:///{temp_db_path}")

    # Insert dummy source and collection run in old schema
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sources (id, name, source_type, enabled, auth_required, config, consecutive_failures, created_at, updated_at) "
                "VALUES ('src-1', 'HN', 'hackernews', 1, 0, '{}', 0, '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-1', 'src-1', 'running', '2026-07-07 00:00:00', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 00:00:00')"
            )
        )

    # 2. Upgrade to head
    command.upgrade(alembic_cfg, "head")

    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("collection_runs")]
    assert "trigger_type" in columns

    # 3. Check trigger_type backfilled to 'cli'
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT trigger_type, status FROM collection_runs WHERE id = 'run-1'"
            )
        )
        res = result.first()
        assert res is not None
        assert res[0] == "cli"
        assert res[1] == "running"

    # 4. Insert another running run for same source (should fail due to uq_collection_runs_source_running)
    with pytest.raises(Exception):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                    "VALUES ('run-2', 'src-1', 'running', '2026-07-07 01:00:00', 'web', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 01:00:00')"
                )
            )

    # 5. Terminal runs are allowed to have multiple instances
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-3', 'src-1', 'succeeded', '2026-07-07 02:00:00', 'web', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 02:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-4', 'src-1', 'failed', '2026-07-07 03:00:00', 'cli', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 03:00:00')"
            )
        )

    # Insert an abandoned run to test downgrade data conversion
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-5', 'src-1', 'abandoned', '2026-07-07 04:00:00', 'web', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 04:00:00')"
            )
        )

    # 6. Downgrade back to 7fa513398108
    command.downgrade(alembic_cfg, "7fa513398108")

    # The abandoned run should be converted to failed
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT status, error_summary FROM collection_runs WHERE id = 'run-5'"
            )
        )
        r5 = result.first()
        assert r5 is not None
        assert r5[0] == "failed"
        assert "Abandoned" in r5[1]

    # Clean up inspector
    inspector = inspect(engine)
    columns_after = [c["name"] for c in inspector.get_columns("collection_runs")]
    assert "trigger_type" not in columns_after
