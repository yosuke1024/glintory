import os
import pathlib
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import SignalType
from glintory.domain.models import Signal, Source
from glintory.domain.search import SignalSearchFilters
from glintory.infrastructure.database import reset_db_connections
from glintory.infrastructure.signal_search import SignalSearchRepository


@pytest.fixture
def fts_db_session(tmp_path):
    """Sets up a temporary SQLite file database with alembic migrations applied."""
    db_file = tmp_path / "test_search.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Temporarily override GLINTORY_DATABASE_URL
    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    # Run migrations
    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed sources
    src1 = Source(id="src-1", name="HackerNews Source", source_type="hackernews")
    src2 = Source(id="src-2", name="GitHub Source", source_type="github")
    session.add_all([src1, src2])
    session.commit()

    yield session

    session.close()
    if db_file.exists():
        db_file.unlink()

    # Restore settings
    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_repository_search_query_matching(fts_db_session) -> None:
    repo = SignalSearchRepository(fts_db_session)

    # Insert test signals
    sig1 = Signal(
        id="sig-1",
        source_id="src-1",
        canonical_url="https://news.ycombinator.com/item?id=1",
        title="Self-hosted alternative to Obsidian note syncing",
        excerpt="A great local-first sync tool for Obsidian markdown files.",
        author="alice",
        published_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PAIN,
        content_hash="hash1",
        freshness_score=1.0,
        source_quality_score=0.8,
    )
    sig2 = Signal(
        id="sig-2",
        source_id="src-1",
        canonical_url="https://news.ycombinator.com/item?id=2",
        title="Zero-config SQLite backup agent",
        excerpt="A shell script that backups database files.",
        author="bob",
        published_at=datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC),
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PROJECT,
        content_hash="hash2",
        freshness_score=0.9,
        source_quality_score=0.8,
    )
    sig3 = Signal(
        id="sig-3",
        source_id="src-2",
        canonical_url="https://github.com/c/obsidian-sync",
        title="Obsidian sync server in Go",
        excerpt="Is there an open source self-hosted backup alternative?",
        author="charlie",
        published_at=None,
        collected_at=datetime.now(UTC),
        signal_type=SignalType.REQUEST,
        content_hash="hash3",
        freshness_score=0.8,
        source_quality_score=0.7,
    )
    fts_db_session.add_all([sig1, sig2, sig3])
    fts_db_session.commit()

    # Test query matching: "alternative" -> sig1, sig3 should match
    res_alt = repo.search(
        SignalSearchFilters(query="alternative"), match_expression='"alternative"'
    )
    assert res_alt.total_count == 2
    matched_ids = [item.id for item in res_alt.items]
    assert "sig-1" in matched_ids
    assert "sig-3" in matched_ids

    # Test query matching: "sqlite" -> sig2 should match
    res_sql = repo.search(
        SignalSearchFilters(query="sqlite"), match_expression='"sqlite"'
    )
    assert res_sql.total_count == 1
    assert res_sql.items[0].id == "sig-2"

    # Test query matching: author search "alice" -> sig1 matches
    res_author = repo.search(
        SignalSearchFilters(query="alice"), match_expression='"alice"'
    )
    assert res_author.total_count == 1
    assert res_author.items[0].id == "sig-1"

    # Test multi-word AND search: "self-hosted alternative"
    # Wait, our words are: "self-hosted", "alternative"
    # But FTS tokenization of "self-hosted" splits on diacritics / hyphens unless unicode61 is configured otherwise.
    # Actually unicode61 tokenizer splits on punctuation like '-' as a word break.
    # So "self-hosted" might tokenise to "self" and "hosted".
    # Therefore, let's search with terms that are simple words.
    res_and = repo.search(
        SignalSearchFilters(query="sync server"), match_expression='"sync" AND "server"'
    )
    assert res_and.total_count == 1
    assert res_and.items[0].id == "sig-3"


def test_repository_filters(fts_db_session) -> None:
    repo = SignalSearchRepository(fts_db_session)

    sig1 = Signal(
        id="sig-1",
        source_id="src-1",
        canonical_url="https://news.ycombinator.com/item?id=1",
        title="Signal 1",
        excerpt="Excerpt 1",
        author="alice",
        published_at=datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC),
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PAIN,
        content_hash="hash1",
        freshness_score=1.0,
        source_quality_score=0.8,
    )
    sig2 = Signal(
        id="sig-2",
        source_id="src-2",
        canonical_url="https://github.com/foo",
        title="Signal 2",
        excerpt="Excerpt 2",
        author="bob",
        published_at=datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC),
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PROJECT,
        content_hash="hash2",
        freshness_score=0.9,
        source_quality_score=0.8,
    )
    sig3 = Signal(
        id="sig-3",
        source_id="src-2",
        canonical_url="https://github.com/bar",
        title="Signal 3",
        excerpt="Excerpt 3",
        author="charlie",
        published_at=None,
        collected_at=datetime.now(UTC),
        signal_type=SignalType.REQUEST,
        content_hash="hash3",
        freshness_score=0.8,
        source_quality_score=0.7,
    )
    fts_db_session.add_all([sig1, sig2, sig3])
    fts_db_session.commit()

    # Filter by source: src-2 -> sig2, sig3
    res_src = repo.search(SignalSearchFilters(source_id="src-2"))
    assert res_src.total_count == 2
    assert {item.id for item in res_src.items} == {"sig-2", "sig-3"}

    # Filter by type: pain -> sig1
    res_type = repo.search(SignalSearchFilters(signal_type=SignalType.PAIN))
    assert res_type.total_count == 1
    assert res_type.items[0].id == "sig-1"

    # Filter by published date: from 2026-07-02 -> sig2 (sig3 is excluded because published_at is NULL)
    res_from = repo.search(SignalSearchFilters(published_from=date(2026, 7, 2)))
    assert res_from.total_count == 1
    assert res_from.items[0].id == "sig-2"

    # Filter by published date range: from 2026-07-01 to 2026-07-01 -> sig1
    res_range = repo.search(
        SignalSearchFilters(
            published_from=date(2026, 7, 1), published_to=date(2026, 7, 1)
        )
    )
    assert res_range.total_count == 1
    assert res_range.items[0].id == "sig-1"


def test_repository_get_active_sources(fts_db_session) -> None:
    repo = SignalSearchRepository(fts_db_session)

    # Initially, no signals exist, so get_active_sources should be empty
    assert len(repo.get_active_sources()) == 0

    # Insert a signal for src-1
    sig1 = Signal(
        id="sig-1",
        source_id="src-1",
        canonical_url="https://news.ycombinator.com/item?id=1",
        title="Signal 1",
        signal_type=SignalType.PAIN,
        content_hash="hash1",
        freshness_score=1.0,
        source_quality_score=0.8,
    )
    fts_db_session.add(sig1)
    fts_db_session.commit()

    sources = repo.get_active_sources()
    assert len(sources) == 1
    assert sources[0]["id"] == "src-1"
