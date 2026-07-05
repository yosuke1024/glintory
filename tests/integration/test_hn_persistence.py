import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from glintory.collectors.defaults import build_default_collector_registry
from glintory.config import settings
from glintory.domain.enums import CollectionRunStatus, SignalType
from glintory.domain.models import Base, Signal, Source
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
async def test_hn_persistence_runs(db_session_factory, db_session):
    registry = build_default_collector_registry(settings)

    # 1. First Run: 3 HN items
    # We will simulate a Mock HTTP server returning 3 HN items
    # item 1001: Ask HN
    # item 1002: Show HN
    # item 1003: Job

    # Track title, text, score dynamically so we can modify them in successive runs
    mock_items = {
        "1001": {
            "id": 1001,
            "type": "story",
            "title": "Ask HN: How is life?",
            "text": "Discussion text",
            "score": 10,
            "time": 1783296000,
        },
        "1002": {
            "id": 1002,
            "type": "story",
            "title": "Show HN: My cool app",
            "text": "Launch description",
            "score": 25,
            "time": 1783296000,
        },
        "1003": {
            "id": 1003,
            "type": "job",
            "title": "Backend developer wanted",
            "time": 1783296000,
        },
    }

    item_1001_fail = False

    def handler(request: httpx.Request):
        url_str = str(request.url)

        feed_map = {
            "/askstories.json": [1001],
            "/showstories.json": [1002],
            "/jobstories.json": [1003],
        }
        for suffix, item_ids in feed_map.items():
            if url_str.endswith(suffix):
                return httpx.Response(200, json=item_ids)

        for item_id, item_data in mock_items.items():
            if f"/item/{item_id}.json" in url_str:
                if item_id == "1001" and item_1001_fail:
                    return httpx.Response(500, text="Internal Server Error")
                return httpx.Response(200, json=item_data)

        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    # Insert Source
    src = Source(
        name="Hacker News Source",
        source_type="hackernews",
        enabled=True,
        config={
            "feeds": ["ask", "show", "job"],
            "include_jobs": True,
            "max_items_per_feed": 5,
        },
    )
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry, http_client=http_client)

    # --- Run 1: First Run ---
    res1 = await service.run_source(src.id)
    assert res1.status == CollectionRunStatus.SUCCEEDED
    assert res1.fetched_count == 3
    assert res1.error_count == 0

    db_session.expire_all()
    # Confirm signals inserted
    signals = db_session.scalars(select(Signal)).all()
    assert len(signals) == 3

    # Assert correct SignalTypes
    sig_map = {s.external_id: s for s in signals}
    assert sig_map["hackernews:item:1001"].signal_type == SignalType.REQUEST
    assert sig_map["hackernews:item:1002"].signal_type == SignalType.LAUNCH
    assert sig_map["hackernews:item:1003"].signal_type == SignalType.JOB_DEMAND

    # --- Run 2: Identical Second Run ---
    res2 = await service.run_source(src.id)
    assert res2.status == CollectionRunStatus.SUCCEEDED
    assert res2.fetched_count == 3
    # No new signals should be inserted, duplicate count should reflect
    db_session.expire_all()
    signals_after_run2 = db_session.scalars(select(Signal)).all()
    assert len(signals_after_run2) == 3

    # --- Run 3: Changed Item (update score or title) ---
    mock_items["1001"]["score"] = 50
    mock_items["1002"]["title"] = "Show HN: My cool app (Updated)"

    res3 = await service.run_source(src.id)
    assert res3.status == CollectionRunStatus.SUCCEEDED
    # Signal total count should remain 3, but their updated_count will increase or they get updated
    db_session.expire_all()
    signals_after_run3 = db_session.scalars(select(Signal)).all()
    assert len(signals_after_run3) == 3

    sig1001 = db_session.scalar(
        select(Signal).where(Signal.external_id == "hackernews:item:1001")
    )
    assert sig1001.metrics["score"] == 50

    sig1002 = db_session.scalar(
        select(Signal).where(Signal.external_id == "hackernews:item:1002")
    )
    assert sig1002.title == "Show HN: My cool app (Updated)"

    # --- Run 4: Partial Failure (1 item fails to fetch) ---
    item_1001_fail = True
    # Reset http client to use new transport context or handler value
    res4 = await service.run_source(src.id)
    assert res4.status == CollectionRunStatus.PARTIAL
    assert res4.fetched_count == 2
    assert res4.error_count == 1

    # --- Run 5: All Failed (e.g. invalid endpoint or feed failed) ---
    # We will simulate a totally failed run by creating a new source with invalid api_url
    bad_settings = settings.model_copy()
    bad_settings.hn_api_url = "https://invalid-hn.example.com/v0"
    bad_registry = build_default_collector_registry(bad_settings)

    # We need a new Http client that fails
    bad_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    bad_http_client = HttpxHttpClient(client=bad_client, max_retries=0)

    bad_service = CollectionService(
        db_session_factory, bad_registry, http_client=bad_http_client
    )
    res5 = await bad_service.run_source(src.id)
    assert res5.status == CollectionRunStatus.FAILED
    assert res5.fetched_count == 0


@pytest.mark.asyncio
async def test_hn_persistence_multi_source_identity(db_session_factory, db_session):
    registry = build_default_collector_registry(settings)

    # 6. Multi-source Identity: HN signal and GitHub signal share the same outbound URL.
    # But because they are from different sources, they are stored as separate signals.
    # HN outbound_url = 'https://example.com/article'
    # GitHub repository html_url = 'https://example.com/article'

    shared_url = "https://example.com/shared-article"

    # HN handler
    def hn_handler(request: httpx.Request):
        url_str = str(request.url)
        if url_str.endswith("/askstories.json"):
            return httpx.Response(200, json=[9001])
        if "/item/9001.json" in url_str:
            return httpx.Response(
                200,
                json={
                    "id": 9001,
                    "type": "story",
                    "title": "Ask HN: Shared link?",
                    "url": shared_url,
                    "time": 1783296000,
                },
            )
        return httpx.Response(404)

    # GitHub handler
    def gh_handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "incomplete_results": False,
                "items": [
                    {
                        "id": 8001,
                        "full_name": "repo/shared",
                        "html_url": shared_url,  # Same URL
                        "owner": {"login": "owner1"},
                        "created_at": "2026-07-06T00:00:00Z",
                    }
                ],
            },
        )

    # Insert Sources
    src_hn = Source(
        name="HN Source",
        source_type="hackernews",
        enabled=True,
        config={"feeds": ["ask"], "max_items_per_feed": 5},
    )
    src_gh = Source(
        name="GitHub Source",
        source_type="github",
        enabled=True,
        config={"repository_queries": [{"query": "test"}], "per_page": 5},
    )
    db_session.add_all([src_hn, src_gh])
    db_session.commit()

    # Process HN
    hn_client = httpx.AsyncClient(transport=httpx.MockTransport(hn_handler))
    hn_http_client = HttpxHttpClient(client=hn_client)
    hn_service = CollectionService(
        db_session_factory, registry, http_client=hn_http_client
    )
    await hn_service.run_source(src_hn.id)

    # Process GitHub
    gh_client = httpx.AsyncClient(transport=httpx.MockTransport(gh_handler))
    gh_http_client = HttpxHttpClient(client=gh_client)
    gh_service = CollectionService(
        db_session_factory, registry, http_client=gh_http_client
    )
    await gh_service.run_source(src_gh.id)

    # Verify signals count in DB is 2
    db_session.expire_all()
    signals = db_session.scalars(select(Signal)).all()
    assert len(signals) == 2

    # Verify external_id and source_id are isolated
    sig_hn = db_session.scalar(
        select(Signal).where(Signal.external_id == "hackernews:item:9001")
    )
    assert sig_hn is not None
    assert sig_hn.source_id == str(src_hn.id)
    # The canonical_url for HN is discussion URL news.ycombinator.com/item?id=9001
    assert sig_hn.canonical_url == "https://news.ycombinator.com/item?id=9001"
    assert sig_hn.raw_metadata["outbound_url"] == shared_url

    sig_gh = db_session.scalar(
        select(Signal).where(Signal.external_id == "github:repository:8001")
    )
    assert sig_gh is not None
    assert sig_gh.source_id == str(src_gh.id)
    assert sig_gh.canonical_url == shared_url
