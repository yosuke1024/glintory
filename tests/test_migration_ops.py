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
            text("SELECT trigger_type, status FROM collection_runs WHERE id = 'run-1'")
        )
        res = result.first()
        assert res is not None
        assert res[0] == "cli"
        assert res[1] == "running"

    # 4. Insert another running run for same source (should fail due to uq_collection_runs_source_running)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine.begin() as conn:
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
            text("SELECT status, error_summary FROM collection_runs WHERE id = 'run-5'")
        )
        r5 = result.first()
        assert r5 is not None
        assert r5[0] == "failed"
        assert "Abandoned" in r5[1]

    # Clean up inspector
    inspector = inspect(engine)
    columns_after = [c["name"] for c in inspector.get_columns("collection_runs")]
    assert "trigger_type" not in columns_after


def test_migration_check_constraints_and_repair(temp_db_path):
    project_root = pathlib.Path(__file__).parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    # 1. Upgrade to the revision prior to check constraints (d445f5753c74)
    command.upgrade(alembic_cfg, "d445f5753c74")

    engine = create_engine(f"sqlite:///{temp_db_path}")

    # Insert invalid data that violates future constraints
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sources (id, name, source_type, enabled, auth_required, config, consecutive_failures, created_at, updated_at) "
                "VALUES ('src-repair', 'Repair Source', 'github', 1, 0, '{}', 0, '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )
        # run-invalid-status has status='nonsense', completed_at=NULL, error_count=0
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, error_summary, run_metadata, created_at, completed_at) "
                "VALUES ('run-invalid-status', 'src-repair', 'nonsense', '2026-07-07 00:00:00', 'cli', 0, 0, 0, 0, 0, 0, NULL, '{}', '2026-07-07 00:00:00', NULL)"
            )
        )
        # run-invalid-trigger has trigger_type='nonsense'
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-invalid-trigger', 'src-repair', 'running', '2026-07-07 00:00:00', 'nonsense', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 00:00:00')"
            )
        )

    # 2. Upgrade to head (which includes the repair and constraints)
    command.upgrade(alembic_cfg, "head")

    # 3. Check if repair worked as expected
    with engine.connect() as conn:
        r_status = conn.execute(
            text(
                "SELECT status, completed_at, error_count, error_summary FROM collection_runs WHERE id = 'run-invalid-status'"
            )
        ).first()
        assert r_status is not None
        assert r_status[0] == "failed"
        assert (
            r_status[1] is not None
        )  # completed_at must be populated with migration execution time
        assert r_status[2] >= 1  # error_count must be at least 1
        assert (
            r_status[3]
            == "Invalid collection run status was repaired during schema migration."
        )

        r_trigger = conn.execute(
            text(
                "SELECT trigger_type FROM collection_runs WHERE id = 'run-invalid-trigger'"
            )
        ).first()
        assert r_trigger is not None
        assert r_trigger[0] == "cli"  # trigger_type must be repaired to 'cli'

    # 4. Check if constraints prevent inserting invalid values now
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-invalid-status-new', 'src-repair', 'nonsense', '2026-07-07 00:00:00', 'cli', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 00:00:00')"
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-invalid-trigger-new', 'src-repair', 'running', '2026-07-07 00:00:00', 'nonsense', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 00:00:00')"
            )
        )

    # 5. Check if we can downgrade and constraints are removed
    command.downgrade(alembic_cfg, "d445f5753c74")
    # After downgrade, we can insert nonsense again
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO collection_runs (id, source_id, status, started_at, trigger_type, fetched_count, inserted_count, updated_count, duplicate_count, warning_count, error_count, run_metadata, created_at) "
                "VALUES ('run-invalid-status-new', 'src-repair', 'nonsense', '2026-07-07 00:00:00', 'cli', 0, 0, 0, 0, 0, 0, '{}', '2026-07-07 00:00:00')"
            )
        )


def test_migration_runtime_audit_fields(temp_db_path):
    project_root = pathlib.Path(__file__).parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    # 1. Upgrade to the revision prior to runtime audit fields (9d9d5e869311)
    command.upgrade(alembic_cfg, "9d9d5e869311")

    engine = create_engine(f"sqlite:///{temp_db_path}")

    # Insert dummy opportunity and enrichment (old schema)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO opportunities (id, title, proposed_solution, evidence_score, feasibility_score, penalty_score, total_score, confidence, status, created_at, updated_at) "
                "VALUES ('opp-1', 'Title', 'Solution', 10, 10, 0, 20, 'medium', 'inbox', '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO opportunity_enrichments (id, opportunity_id, status, model_provider, model_id, model_revision, model_sha256, runtime, runtime_version, prompt_version, input_hash, target_users, risks, tags, evidence_refs, started_at, created_at, updated_at) "
                "VALUES ('enrich-1', 'opp-1', 'succeeded', 'qwen', 'model-file', 'rev', 'sha', 'llama.cpp', 'b5092', 'v1', 'hash1', '[]', '[]', '[]', '[]', '2026-07-07 00:00:00', '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )

    # 2. Upgrade to head
    command.upgrade(alembic_cfg, "head")

    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("opportunity_enrichments")]
    # Check if the 2 columns exist
    assert "runtime_commit" in columns
    assert "runtime_binary_sha256" in columns

    # 3. Check if new error_code constraint permits LLM_CONFIGURATION_INVALID
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO opportunity_enrichments (id, opportunity_id, status, model_provider, model_id, model_revision, model_sha256, runtime, runtime_version, prompt_version, input_hash, target_users, risks, tags, evidence_refs, started_at, error_code, created_at, updated_at) "
                "VALUES ('enrich-2', 'opp-1', 'failed', 'qwen', 'model-file', 'rev', 'sha', 'llama.cpp', 'b5092', 'v1', 'hash2', '[]', '[]', '[]', '[]', '2026-07-07 00:00:00', 'LLM_CONFIGURATION_INVALID', '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )

    # 4. Check if other nonsense error_code is rejected
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO opportunity_enrichments (id, opportunity_id, status, model_provider, model_id, model_revision, model_sha256, runtime, runtime_version, prompt_version, input_hash, target_users, risks, tags, evidence_refs, started_at, error_code, created_at, updated_at) "
                "VALUES ('enrich-3', 'opp-1', 'failed', 'qwen', 'model-file', 'rev', 'sha', 'llama.cpp', 'b5092', 'v1', 'hash3', '[]', '[]', '[]', '[]', '2026-07-07 00:00:00', 'LLM_NONSENSE_ERROR', '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )

    # 5. Downgrade back to 9d9d5e869311
    # Clean up enrich-2 first to avoid violating old check constraint during downgrade
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM opportunity_enrichments WHERE id = 'enrich-2'"))

    command.downgrade(alembic_cfg, "9d9d5e869311")

    inspector = inspect(engine)
    columns_after = [c["name"] for c in inspector.get_columns("opportunity_enrichments")]
    # Check if the 2 columns are deleted
    assert "runtime_commit" not in columns_after
    assert "runtime_binary_sha256" not in columns_after

    # 6. Check if LLM_CONFIGURATION_INVALID is rejected by the old constraint after downgrade
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO opportunity_enrichments (id, opportunity_id, status, model_provider, model_id, model_revision, model_sha256, runtime, runtime_version, prompt_version, input_hash, target_users, risks, tags, evidence_refs, started_at, error_code, created_at, updated_at) "
                "VALUES ('enrich-4', 'opp-1', 'failed', 'qwen', 'model-file', 'rev', 'sha', 'llama.cpp', 'b5092', 'v1', 'hash4', '[]', '[]', '[]', '[]', '2026-07-07 00:00:00', 'LLM_CONFIGURATION_INVALID', '2026-07-07 00:00:00', '2026-07-07 00:00:00')"
            )
        )
