import os
import pathlib
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import SignalType
from glintory.domain.models import Signal, Source
from glintory.infrastructure.database import get_db, reset_db_connections
from glintory.main import app


@pytest.fixture
def test_web_db(tmp_path):
    """Sets up temporary database for testing the web routing layer."""
    db_file = tmp_path / "test_web.sqlite3"
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

    # Seed initial sources
    src = Source(id="src-web-1", name="HN Web", source_type="hackernews")
    session.add(src)
    session.commit()

    # Override get_db dependency in FastAPI app
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield session

    session.close()
    app.dependency_overrides.clear()

    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_web_signals_list_empty(test_web_db) -> None:
    client = TestClient(app)
    response = client.get("/signals")
    assert response.status_code == 200
    # Page must contain Glintory branding and empty state
    assert "Glintory" in response.text
    assert "No signals found." in response.text
    assert "uv run glintory collect --all" in response.text


def test_web_signals_list_with_data(test_web_db) -> None:
    sig = Signal(
        id="00000000-0000-0000-0000-000000000002",
        source_id="src-web-1",
        canonical_url="https://example.com/test",
        title="Web Routing Test Signal",
        excerpt="An excerpt that will be shown in list view",
        author="web-author",
        published_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PAIN,
        content_hash="hash-web-1",
        freshness_score=1.0,
        source_quality_score=0.9,
    )
    test_web_db.add(sig)
    test_web_db.commit()

    client = TestClient(app)
    response = client.get("/signals")
    assert response.status_code == 200
    assert "Web Routing Test Signal" in response.text
    assert "web-author" in response.text
    assert 'href="/signals/00000000-0000-0000-0000-000000000002"' in response.text
    # Check that rank or raw metadata are NOT visible on listing page
    assert "raw_metadata" not in response.text


def test_web_signal_detail(test_web_db) -> None:
    sig = Signal(
        id="00000000-0000-0000-0000-000000000002",
        source_id="src-web-1",
        canonical_url="https://example.com/test",
        title="Web Routing Test Signal",
        excerpt="An excerpt that will be shown in detail view",
        author="web-author",
        published_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PAIN,
        content_hash="hash-web-1",
        freshness_score=1.0,
        source_quality_score=0.9,
        raw_metadata={"whitelisted_key": "whitelisted_value"},
    )
    test_web_db.add(sig)
    test_web_db.commit()

    client = TestClient(app)
    response = client.get("/signals/00000000-0000-0000-0000-000000000002")
    assert response.status_code == 200
    assert "Web Routing Test Signal" in response.text
    assert "whitelisted_key" in response.text
    assert "whitelisted_value" in response.text
    # Check target="_blank" and rel attribute for external link
    assert 'target="_blank"' in response.text
    assert 'rel="noopener noreferrer"' in response.text


def test_web_signal_detail_404(test_web_db) -> None:
    client = TestClient(app)
    # Invalid UUID or non-existent signal id should return 404
    response = client.get("/signals/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404

    response_invalid = client.get("/signals/invalid-uuid-string")
    assert response_invalid.status_code == 404


def test_web_today_dashboard_real_data(test_web_db) -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    # No demo data should be displayed
    assert "Local-First Markdown Note Sync Tool" not in response.text
    # Expected empty state text
    assert "No scored opportunities yet." in response.text
    assert "Please analyze and score opportunities using CLI:" in response.text
