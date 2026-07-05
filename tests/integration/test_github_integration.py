import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.collectors.defaults import build_default_collector_registry
from glintory.config import settings
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, CollectionRun, Source
from glintory.infrastructure.http import HttpxHttpClient
from glintory.services.collection import CollectionService


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def db_session(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


@pytest.mark.asyncio
async def test_github_integration_success(db_session_factory, db_session):
    # Setup registry with default build
    registry = build_default_collector_registry(settings)

    # 1. Success case: return valid items for repo & issues
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "repositories" in url_str:
            items = [
                {
                    "id": 1,
                    "full_name": "repo/1",
                    "html_url": "http://github.com/repo/1",
                    "owner": {"login": "owner1"},
                    "created_at": "2026-07-06T00:00:00Z",
                }
            ]
        else:
            items = [
                {
                    "id": 2,
                    "title": "issue/1",
                    "html_url": "http://github.com/repo/1/issues/1",
                    "user": {"login": "user1"},
                    "created_at": "2026-07-06T01:00:00Z",
                }
            ]
        return httpx.Response(
            200, json={"total_count": 1, "incomplete_results": False, "items": items}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    # Insert source
    src = Source(
        name="My GitHub Source",
        source_type="github",
        enabled=True,
        config={
            "repository_queries": [{"query": "topic:self-hosted"}],
            "issue_queries": [{"query": "too expensive"}],
            "per_page": 5,
        },
    )
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry, http_client=http_client)
    res = await service.run_source(src.id)

    assert res.status == CollectionRunStatus.SUCCEEDED
    assert res.fetched_count == 2
    assert res.error_count == 0

    # Verify DB state
    db_session.expire_all()
    db_run = db_session.get(CollectionRun, res.run_id)
    assert db_run is not None
    assert db_run.status == CollectionRunStatus.SUCCEEDED
    assert db_run.fetched_count == 2

    db_src = db_session.get(Source, src.id)
    assert db_src is not None
    assert db_src.last_success_at is not None
    assert db_src.consecutive_failures == 0


@pytest.mark.asyncio
async def test_github_integration_partial(db_session_factory, db_session):
    registry = build_default_collector_registry(settings)

    # 2. Partial case: repositories success (200), issues fail (422)
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "repositories" in url_str:
            items = [
                {
                    "id": 1,
                    "full_name": "repo/1",
                    "html_url": "http://github.com/repo/1",
                    "owner": {"login": "owner1"},
                    "created_at": "2026-07-06T00:00:00Z",
                }
            ]
            return httpx.Response(
                200,
                json={"total_count": 1, "incomplete_results": False, "items": items},
            )
        return httpx.Response(422, text="Unprocessable Entity")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    src = Source(
        name="My GitHub Source",
        source_type="github",
        enabled=True,
        config={
            "repository_queries": [{"query": "topic:self-hosted"}],
            "issue_queries": [{"query": "too expensive"}],
            "per_page": 5,
        },
    )
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry, http_client=http_client)
    res = await service.run_source(src.id)

    assert res.status == CollectionRunStatus.PARTIAL
    assert res.fetched_count == 1
    assert res.error_count == 1

    # Verify DB state
    db_session.expire_all()
    db_run = db_session.get(CollectionRun, res.run_id)
    assert db_run is not None
    assert db_run.status == CollectionRunStatus.PARTIAL
    assert db_run.fetched_count == 1

    db_src = db_session.get(Source, src.id)
    assert db_src is not None
    assert db_src.last_success_at is not None  # partial updates success_at
    assert db_src.last_error is not None


@pytest.mark.asyncio
async def test_github_integration_failure(db_session_factory, db_session):
    registry = build_default_collector_registry(settings)

    # 3. Failure case: all queries fail (500)
    def handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client, max_retries=0)  # fast fail

    src = Source(
        name="My GitHub Source",
        source_type="github",
        enabled=True,
        config={
            "repository_queries": [{"query": "topic:self-hosted"}],
            "issue_queries": [{"query": "too expensive"}],
            "per_page": 5,
        },
    )
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry, http_client=http_client)
    res = await service.run_source(src.id)

    assert res.status == CollectionRunStatus.FAILED
    assert res.fetched_count == 0
    assert res.error_count == 2

    # Verify DB state
    db_session.expire_all()
    db_run = db_session.get(CollectionRun, res.run_id)
    assert db_run is not None
    assert db_run.status == CollectionRunStatus.FAILED

    db_src = db_session.get(Source, src.id)
    assert db_src is not None
    assert db_src.consecutive_failures == 1
    assert db_src.last_error is not None
