import os
import pathlib
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.collectors.base import CollectionResult
from glintory.collectors.registry import CollectorRegistry
from glintory.config import settings
from glintory.domain.models import Source
from glintory.infrastructure.database import get_db, reset_db_connections
from glintory.main import app
from glintory.services.collection import CollectionService
from glintory.services.signal_ingestion import SignalIngestionService

# A valid UUID to avoid ValueError / 404 in source detail
TEST_SOURCE_ID = "8fa4922b-2856-4c4f-8cfb-6f81a7db8e8a"


class FakeCollector:
    source_type = "hackernews"

    def validate_config(self, config):
        return config

    def get_config_summary(self, config):
        return "HN Config (max=10)"

    async def collect(self, context):
        return CollectionResult(items=[], warnings=[], errors=[])


@pytest.fixture
def test_web_db(tmp_path):
    db_file = tmp_path / "test_web_sources.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed source
    src = Source(
        id=TEST_SOURCE_ID,
        name="HN",
        source_type="hackernews",
        enabled=True,
        config={"max_items": 10},
    )
    session.add(src)
    session.commit()

    # Setup app state override for collection_service
    registry = CollectorRegistry()
    registry.register(FakeCollector())

    ingestion_service = SignalIngestionService(session_factory)
    collection_service = CollectionService(
        session_factory=session_factory,
        registry=registry,
        ingestion_service=ingestion_service,
    )

    orig_session_factory = getattr(app.state, "session_factory", None)
    orig_registry = getattr(app.state, "registry", None)
    orig_collection_service = getattr(app.state, "collection_service", None)

    app.state.session_factory = session_factory
    app.state.registry = registry
    app.state.collection_service = collection_service

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

    if orig_session_factory:
        app.state.session_factory = orig_session_factory
    if orig_registry:
        app.state.registry = orig_registry
    if orig_collection_service:
        app.state.collection_service = orig_collection_service

    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_get_sources_list(test_web_db):
    client = TestClient(app)
    response = client.get("/sources")
    assert response.status_code == 200
    assert "HN" in response.text
    assert "Sources Console" in response.text


def test_get_source_detail(test_web_db):
    client = TestClient(app)
    response = client.get(f"/sources/{TEST_SOURCE_ID}")
    assert response.status_code == 200
    assert "HN" in response.text
    assert "Collect Now" in response.text
    assert settings.web_csrf_cookie_name in response.cookies


def test_enable_disable_source(test_web_db):
    client = TestClient(app)
    res = client.get(f"/sources/{TEST_SOURCE_ID}")
    csrf_token = res.cookies.get(settings.web_csrf_cookie_name)
    assert csrf_token is not None
    client.cookies.set(settings.web_csrf_cookie_name, csrf_token)

    # Disable
    response = client.post(
        f"/sources/{TEST_SOURCE_ID}/disable",
        data={"csrf_token": csrf_token},
        headers={"referer": f"http://testserver/sources/{TEST_SOURCE_ID}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        f"/sources/{TEST_SOURCE_ID}?notice=source_disabled"
        in response.headers["location"]
    )

    # Enable
    response = client.post(
        f"/sources/{TEST_SOURCE_ID}/enable",
        data={"csrf_token": csrf_token},
        headers={"referer": f"http://testserver/sources/{TEST_SOURCE_ID}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        f"/sources/{TEST_SOURCE_ID}?notice=source_enabled" in response.headers["location"]
    )


def test_collect_source_manual(test_web_db):
    client = TestClient(app)
    res = client.get(f"/sources/{TEST_SOURCE_ID}")
    csrf_token = res.cookies.get(settings.web_csrf_cookie_name)
    assert csrf_token is not None
    client.cookies.set(settings.web_csrf_cookie_name, csrf_token)

    response = client.post(
        f"/sources/{TEST_SOURCE_ID}/collect",
        data={"csrf_token": csrf_token},
        headers={"referer": f"http://testserver/sources/{TEST_SOURCE_ID}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "notice=collection_succeeded" in response.headers["location"]


def test_get_collection_runs(test_web_db):
    client = TestClient(app)
    response = client.get("/collection-runs")
    assert response.status_code == 200
    assert "Collection Runs" in response.text


def test_api_v1_sources(test_web_db):
    client = TestClient(app)
    response = client.get("/api/v1/sources")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "HN"
    assert "config" not in data[0]
