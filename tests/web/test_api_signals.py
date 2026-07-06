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
def test_api_db(tmp_path):
    """Sets up temporary database for testing the JSON API routing layer."""
    db_file = tmp_path / "test_api.sqlite3"
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
    src = Source(id="src-api-1", name="HN Api", source_type="hackernews")
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


def test_api_list_signals(test_api_db) -> None:
    # Insert test signals
    sig = Signal(
        id="00000000-0000-0000-0000-000000000001",
        source_id="src-api-1",
        canonical_url="https://example.com/api-test",
        title="API Test Signal",
        excerpt="Short excerpt",
        author="api-author",
        published_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
        collected_at=datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC),
        signal_type=SignalType.PROJECT,
        content_hash="hash-api-1",
        freshness_score=1.0,
        source_quality_score=0.9,
        raw_metadata={"secret_key": "confidential"},
    )
    test_api_db.add(sig)
    test_api_db.commit()

    client = TestClient(app)
    response = client.get("/api/v1/signals")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]

    data = response.json()
    assert "items" in data
    assert "pagination" in data
    assert len(data["items"]) == 1

    item = data["items"][0]
    assert item["id"] == "00000000-0000-0000-0000-000000000001"
    assert item["title"] == "API Test Signal"
    assert item["source"]["id"] == "src-api-1"
    assert item["source"]["name"] == "HN Api"

    # Verify ISO 8601 formatting with Z suffix
    assert item["published_at"] == "2026-07-01T10:00:00Z"
    assert item["collected_at"] == "2026-07-06T12:00:00Z"

    # Exclude rank and raw metadata from list response
    assert "rank" not in item
    assert "raw_metadata" not in item


def test_api_detail_signals(test_api_db) -> None:
    sig = Signal(
        id="00000000-0000-0000-0000-000000000001",
        source_id="src-api-1",
        canonical_url="https://example.com/api-test",
        title="API Test Signal",
        excerpt="Short excerpt",
        author="api-author",
        published_at=datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC),
        collected_at=datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC),
        signal_type=SignalType.PROJECT,
        content_hash="hash-api-1",
        freshness_score=1.0,
        source_quality_score=0.9,
        raw_metadata={"secret_key": "confidential"},
    )
    test_api_db.add(sig)
    test_api_db.commit()

    client = TestClient(app)
    response = client.get("/api/v1/signals/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]

    data = response.json()
    assert data["id"] == "00000000-0000-0000-0000-000000000001"
    assert data["title"] == "API Test Signal"
    # Detailed API response must contain raw_metadata
    assert data["raw_metadata"] == {"secret_key": "confidential"}
    assert data["published_at"] == "2026-07-01T10:00:00Z"


def test_api_detail_signals_404(test_api_db) -> None:
    client = TestClient(app)
    response = client.get("/api/v1/signals/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
