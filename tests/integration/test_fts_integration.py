import os
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from glintory.collectors.base import RawItem
from glintory.config import settings
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import CollectionRun, Signal, Source
from glintory.domain.search import SignalSearchFilters
from glintory.infrastructure.database import reset_db_connections
from glintory.infrastructure.signal_search import SignalSearchRepository
from glintory.services.signal_ingestion import SignalIngestionService


@pytest.fixture
def fts_integration_session_factory(tmp_path):
    """Sets up temporary database for pipeline ingestion testing."""
    db_file = tmp_path / "test_fts_integration.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Override database_url
    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    # Apply migrations
    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed source and collection run
    src = Source(id="src-int", name="Integration Source", source_type="github")
    session.add(src)
    session.flush()

    run = CollectionRun(
        id="run-int", source_id="src-int", status=CollectionRunStatus.RUNNING
    )
    session.add(run)
    session.commit()
    session.close()

    yield session_factory

    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_fts_integration_pipeline_insert_update_delete(
    fts_integration_session_factory,
) -> None:
    """Verifies FTS index synchronization at every stage of the ingestion lifecycle."""
    service = SignalIngestionService(fts_integration_session_factory)
    collected_at = datetime.now(UTC)

    # 1. Ingest initial items
    items = [
        RawItem(
            external_id="item-int-1",
            url="https://github.com/test/int-project",
            title="Ingestion Integration Project",
            excerpt="A self-hosted sync project",
            item_type="repository",
            metadata={"description": "A self-hosted sync project"},
        )
    ]

    result = service.ingest(
        source_id="src-int",
        source_type="github",
        collection_run_id="run-int",
        raw_items=items,
        collected_at=collected_at,
    )

    assert result.inserted_count == 1
    signal_id = result.signal_ids[0]

    # 2. Check FTS search immediately after ingestion
    session = fts_integration_session_factory()
    repo = SignalSearchRepository(session)

    # Check title search
    search_res = repo.search(
        SignalSearchFilters(query="Integration"), match_expression='"Integration"'
    )
    assert search_res.total_count == 1
    assert search_res.items[0].id == signal_id

    # Check description/excerpt search
    search_res_desc = repo.search(
        SignalSearchFilters(query="self-hosted"), match_expression='"self-hosted"'
    )
    assert search_res_desc.total_count == 1

    # 3. Update title
    items_updated = [
        RawItem(
            external_id="item-int-1",
            url="https://github.com/test/int-project",
            title="Updated Ingestion Title",
            excerpt="A self-hosted sync project",
            item_type="repository",
            metadata={"description": "A self-hosted sync project"},
        )
    ]

    result_upd = service.ingest(
        source_id="src-int",
        source_type="github",
        collection_run_id="run-int",
        raw_items=items_updated,
        collected_at=collected_at,
    )
    assert result_upd.updated_count == 1

    # Check FTS index reflects new title and removes old title matching
    session.close()
    session = fts_integration_session_factory()
    repo = SignalSearchRepository(session)

    res_new = repo.search(
        SignalSearchFilters(query="Updated"), match_expression='"Updated"'
    )
    assert res_new.total_count == 1

    res_old = repo.search(
        SignalSearchFilters(query="Integration"), match_expression='"Integration"'
    )
    assert res_old.total_count == 0

    # 4. Check duplicate collection (no FTS trigger fires, counts should stay same)
    result_dup = service.ingest(
        source_id="src-int",
        source_type="github",
        collection_run_id="run-int",
        raw_items=items_updated,
        collected_at=collected_at + timedelta(hours=1),
    )
    assert result_dup.duplicate_count == 1

    fts_count = session.execute(text("SELECT COUNT(*) FROM signals_fts")).scalar()
    sig_count = session.execute(text("SELECT COUNT(*) FROM signals")).scalar()
    assert fts_count == 1
    assert sig_count == 1

    # 5. Delete signal and check FTS index updates
    sig = session.query(Signal).filter_by(id=signal_id).first()
    session.delete(sig)
    session.commit()

    res_del = repo.search(
        SignalSearchFilters(query="Updated"), match_expression='"Updated"'
    )
    assert res_del.total_count == 0

    fts_count_after = session.execute(text("SELECT COUNT(*) FROM signals_fts")).scalar()
    assert fts_count_after == 0

    session.close()
