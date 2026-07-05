from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from glintory.collectors.base import CollectionContext
from glintory.collectors.hackernews import (
    HackerNewsCollector,
    HackerNewsSourceConfig,
)
from glintory.config import settings
from glintory.infrastructure.http import HttpxHttpClient


def test_hn_settings():
    assert hasattr(settings, "hn_api_url")
    assert hasattr(settings, "hn_web_item_url_template")
    assert hasattr(settings, "hn_text_max_chars")

    assert settings.hn_api_url == "https://hacker-news.firebaseio.com/v0"
    assert (
        settings.hn_web_item_url_template
        == "https://news.ycombinator.com/item?id={item_id}"
    )
    assert settings.hn_text_max_chars == 5000


def test_hn_source_config_valid():
    config = HackerNewsSourceConfig(
        feeds=["ask", "show", "new"],
        max_items_per_feed=10,
        include_jobs=True,
        include_dead=True,
        include_deleted=True,
        minimum_score=5,
        lookback_days=30,
    )
    assert config.feeds == ["ask", "show", "new"]
    assert config.max_items_per_feed == 10
    assert config.include_jobs is True
    assert config.include_dead is True
    assert config.include_deleted is True
    assert config.minimum_score == 5
    assert config.lookback_days == 30


def test_hn_source_config_validation():
    # feeds is required and cannot be empty
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(feeds=[])

    # invalid feed names
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(feeds=["invalid"])  # type: ignore

    # duplicate feeds stable deduplication
    config = HackerNewsSourceConfig(feeds=["ask", "show", "ask"])
    assert config.feeds == ["ask", "show"]

    # max_items_per_feed range (1 to 100)
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(max_items_per_feed=0)
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(max_items_per_feed=101)

    # minimum_score non-negative
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(minimum_score=-1)

    # lookback_days range (1 to 3650 or None)
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(lookback_days=0)
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(lookback_days=3651)

    config_none_lookback = HackerNewsSourceConfig(lookback_days=None)
    assert config_none_lookback.lookback_days is None

    # include_jobs=False with job feed should fail
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(feeds=["job"], include_jobs=False)

    # Unknown key forbid
    with pytest.raises(ValidationError):
        HackerNewsSourceConfig(feeds=["ask"], unknown_key="invalid")  # type: ignore


@pytest.mark.asyncio
async def test_hn_collector_collect_success():
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if url_str.endswith("/askstories.json"):
            return httpx.Response(200, json=[1001, 1002])
        if url_str.endswith("/item/1001.json"):
            return httpx.Response(
                200,
                json={
                    "id": 1001,
                    "type": "story",
                    "by": "author1",
                    "time": 1783296000,  # 2026-07-06T00:00:00Z
                    "title": "Ask HN: How is it going?",
                    "text": "Tell me <p>everything</p>.",
                    "score": 10,
                    "descendants": 2,
                    "kids": [2001, 2002],
                },
            )
        if url_str.endswith("/item/1002.json"):
            return httpx.Response(
                200,
                json={
                    "id": 1002,
                    "type": "story",
                    "by": "author2",
                    "time": 1783296000,
                    "title": "Another Ask HN",
                    "text": "Hello &amp; welcome.",
                    "score": 5,
                    "descendants": 0,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    fixed_now = datetime(2026, 7, 6, 1, 0, 0, tzinfo=UTC)

    context = CollectionContext(
        source_id="hn_test",
        source_name="Hacker News",
        source_type="hackernews",
        source_config={"feeds": ["ask"], "max_items_per_feed": 5},
        max_items=100,
        http=http_client,
    )

    collector = HackerNewsCollector(settings, clock=lambda: fixed_now)
    result = await collector.collect(context)

    assert len(result.errors) == 0
    assert len(result.warnings) == 0
    assert len(result.items) == 2

    item1 = result.items[0]
    assert item1.external_id == "hackernews:item:1001"
    assert item1.url == "https://news.ycombinator.com/item?id=1001"
    assert item1.title == "Ask HN: How is it going?"
    assert item1.excerpt == "Tell me everything ."
    assert item1.author == "author1"
    assert item1.published_at == datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)
    assert item1.item_type == "hn_ask"
    assert item1.metadata["score"] == 10
    assert item1.metadata["descendants"] == 2
    assert item1.metadata["kids_count"] == 2
    assert item1.metadata["hn_id"] == 1001
    assert item1.metadata["hn_item_type"] == "story"


@pytest.mark.asyncio
async def test_hn_collector_invalid_ids():
    def handler(request: httpx.Request):
        return httpx.Response(
            200, json=[1001, "invalid_id", -5, True, None, {"id": 1002}, 1003]
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="hn_test",
        source_name="Hacker News",
        source_type="hackernews",
        source_config={"feeds": ["ask"]},
        max_items=100,
        http=http_client,
    )

    collector = HackerNewsCollector(settings)
    result = await collector.collect(context)
    assert len(result.items) == 0
    invalid_errors = [e for e in result.errors if "invalid id" in e.message.lower()]
    assert len(invalid_errors) > 0


@pytest.mark.asyncio
async def test_hn_collector_deduplication_and_priority():
    def handler(request: httpx.Request):
        url_str = str(request.url)
        # ID 2001 is in both top and new.
        if url_str.endswith("/topstories.json") or url_str.endswith("/newstories.json"):
            return httpx.Response(200, json=[2001])
        if url_str.endswith("/item/2001.json"):
            return httpx.Response(
                200,
                json={
                    "id": 2001,
                    "type": "story",
                    "by": "author1",
                    "time": 1783296000,
                    "title": "Top Story Title",
                    "score": 100,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="hn_test",
        source_name="Hacker News",
        source_type="hackernews",
        source_config={"feeds": ["top", "new"]},
        max_items=100,
        http=http_client,
    )

    collector = HackerNewsCollector(settings)
    result = await collector.collect(context)

    # It should have duplicate warning, but items should be fetched only once
    assert len(result.items) == 1
    assert result.metadata["duplicate_item_id_count"] == 1
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "duplicate_items"


@pytest.mark.asyncio
async def test_hn_collector_lookback_and_filters():
    fixed_now = datetime(2026, 7, 6, 1, 0, 0, tzinfo=UTC)

    # 2001 is inside lookback (time=1783296000, which is 2026-07-06T00:00:00Z)
    # 2002 is outside lookback (time=1767225600, which is 2026-01-01T00:00:00Z)
    # 2003 has low score (score=1)
    # 2004 is a job
    def handler(request: httpx.Request):
        url_str = str(request.url)
        if url_str.endswith("/topstories.json"):
            return httpx.Response(200, json=[2001, 2002, 2003, 2004])
        if url_str.endswith("/item/2001.json"):
            return httpx.Response(
                200,
                json={
                    "id": 2001,
                    "type": "story",
                    "time": 1783296000,
                    "title": "Within lookback",
                    "score": 10,
                },
            )
        if url_str.endswith("/item/2002.json"):
            return httpx.Response(
                200,
                json={
                    "id": 2002,
                    "type": "story",
                    "time": 1767225600,  # 2026-01-01T00:00:00Z
                    "title": "Outside lookback",
                    "score": 10,
                },
            )
        if url_str.endswith("/item/2003.json"):
            return httpx.Response(
                200,
                json={
                    "id": 2003,
                    "type": "story",
                    "time": 1783296000,
                    "title": "Low score",
                    "score": 1,
                },
            )
        if url_str.endswith("/item/2004.json"):
            return httpx.Response(
                200,
                json={
                    "id": 2004,
                    "type": "job",
                    "time": 1783296000,
                    "title": "Job Story",
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    context = CollectionContext(
        source_id="hn_test",
        source_name="Hacker News",
        source_type="hackernews",
        source_config={
            "feeds": ["top"],
            "lookback_days": 10,
            "minimum_score": 5,
            "include_jobs": True,
        },
        max_items=100,
        http=http_client,
    )

    collector = HackerNewsCollector(settings, clock=lambda: fixed_now)
    result = await collector.collect(context)

    # 2001: accepted
    # 2002: skipped_old_count = 1
    # 2003: skipped_low_score_count = 1
    # 2004: accepted (jobs are not subject to score limit)
    assert len(result.items) == 2
    assert result.items[0].external_id == "hackernews:item:2001"
    assert result.items[1].external_id == "hackernews:item:2004"
    assert result.metadata["skipped_old_count"] == 1
    assert result.metadata["skipped_low_score_count"] == 1
