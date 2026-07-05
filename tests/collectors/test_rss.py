from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from glintory.collectors.base import CollectionContext
from glintory.collectors.rss import (
    RSSCollector,
    RSSInvalidFeedError,
    RSSSourceConfig,
)
from glintory.domain.enums import SignalType
from glintory.infrastructure.http import HttpxHttpClient


# Config validation tests
def test_config_valid():
    config = RSSSourceConfig(feed_url="https://example.com/feed.xml")
    assert config.feed_url == "https://example.com/feed.xml"
    assert config.max_items == 50
    assert config.max_entries_to_scan == 200
    assert config.lookback_days == 180
    assert config.include_undated is True
    assert config.signal_type == SignalType.TREND
    assert config.default_tags == []
    assert config.default_categories == ["rss"]


def test_config_validation_rules():
    # 1. feed_url must be HTTP/S
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="ftp://example.com/feed.xml")
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="file:///etc/passwd")
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="data:text/html,hello")

    # 2. embedded credentials rejection
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://user:pass@example.com/feed.xml")

    # 3. localhost and local IP rejection
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://localhost/feed.xml")
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://127.0.0.1/feed.xml")
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://192.168.1.1/feed.xml")
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://[::1]/feed.xml")

    # 4. max_items bounds
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", max_items=0)
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", max_items=201)

    # 5. max_entries_to_scan bounds and comparison
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", max_entries_to_scan=0)
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", max_entries_to_scan=1001)
    with pytest.raises(ValidationError):
        RSSSourceConfig(
            feed_url="https://example.com/feed", max_items=50, max_entries_to_scan=40
        )

    # 6. lookback_days bounds
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", lookback_days=0)
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", lookback_days=3651)

    # 7. manual SignalType rejection
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", signal_type=SignalType.MANUAL)

    # 8. unknown key forbidden
    with pytest.raises(ValidationError):
        RSSSourceConfig(feed_url="https://example.com/feed", unknown_key="invalid")  # type: ignore


def test_config_mutable_defaults():
    config1 = RSSSourceConfig(feed_url="https://example.com/feed.xml")
    config2 = RSSSourceConfig(feed_url="https://example.com/feed.xml")
    config1.default_tags.append("new-tag")
    config1.default_categories.append("new-cat")
    assert "new-tag" not in config2.default_tags
    assert "new-cat" not in config2.default_categories


# HTTP and Parser integration tests
RSS20_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed 2.0</title>
    <link>http://example.com</link>
    <language>en</language>
    <item>
      <title>Test Title 1</title>
      <link>http://example.com/item1</link>
      <description>This is item 1 description</description>
      <pubDate>Mon, 06 Jul 2026 00:00:00 +0000</pubDate>
      <guid>guid-1</guid>
      <author>John Doe</author>
      <category>tech</category>
    </item>
  </channel>
</rss>"""

RSS10_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
  <channel rdf:about="http://example.com/feed.rdf">
    <title>Test Feed RDF</title>
    <link>http://example.com</link>
    <description>RDF channel desc</description>
  </channel>
  <item rdf:about="http://example.com/item-rdf">
    <title>RDF Title</title>
    <link>http://example.com/item-rdf</link>
    <description>RDF item desc</description>
  </item>
</rdf:RDF>"""

ATOM10_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <link href="http://example.com"/>
  <updated>2026-07-06T00:00:00Z</updated>
  <id>urn:uuid:1234</id>
  <entry>
    <title>Atom Title 1</title>
    <link href="http://example.com/atom1"/>
    <id>atom-guid-1</id>
    <updated>2026-07-06T00:00:00Z</updated>
    <summary>Atom summary content</summary>
    <author><name>Alice Smith</name></author>
    <category term="programming"/>
  </entry>
</feed>"""


@pytest.mark.asyncio
async def test_rss_20_success():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            content=RSS20_FIXTURE,
            headers={"Content-Type": "application/rss+xml"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    # Use a fixed collected_at clock
    fixed_now = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
    collector = RSSCollector(settings=None, time_func=lambda: fixed_now)

    context = CollectionContext(
        source_id="src-1",
        source_name="RSS 2.0 Feed",
        source_type="rss",
        source_config={"feed_url": "https://example.com/rss.xml"},
        max_items=10,
        http=http_client,
    )

    result = await collector.collect(context)

    assert len(result.errors) == 0
    assert len(result.warnings) == 0
    assert len(result.items) == 1

    item = result.items[0]
    assert item.title == "Test Title 1"
    assert item.url == "http://example.com/item1"
    assert item.excerpt == "This is item 1 description"
    assert item.author == "John Doe"
    assert item.published_at == datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)
    assert item.external_id is not None
    assert item.external_id.startswith("feed:entry:")
    assert item.metadata["feed_format"] == "rss20"
    assert item.metadata["entry_tags"] == ["tech"]
    assert item.metadata["entry_language"] == "en"

    # Metadata check
    assert result.metadata["feed_title"] == "Test Feed 2.0"
    assert result.metadata["feed_host"] == "example.com"
    assert result.metadata["feed_format"] == "rss20"
    assert result.metadata["fetched_entry_count"] == 1


@pytest.mark.asyncio
async def test_rss_10_success():
    def handler(request: httpx.Request):
        return httpx.Response(200, content=RSS10_FIXTURE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    fixed_now = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
    collector = RSSCollector(settings=None, time_func=lambda: fixed_now)

    context = CollectionContext(
        source_id="src-1",
        source_name="RSS 1.0 Feed",
        source_type="rss",
        source_config={"feed_url": "https://example.com/rss.rdf"},
        max_items=10,
        http=http_client,
    )

    result = await collector.collect(context)
    assert len(result.items) == 1
    assert result.items[0].title == "RDF Title"
    assert result.items[0].url == "http://example.com/item-rdf"
    # RSS 1.0 returns 'rss10' or similar version
    assert result.metadata["feed_format"] in ("rss10", "rss090")


@pytest.mark.asyncio
async def test_atom_10_success():
    def handler(request: httpx.Request):
        return httpx.Response(200, content=ATOM10_FIXTURE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    fixed_now = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
    collector = RSSCollector(settings=None, time_func=lambda: fixed_now)

    context = CollectionContext(
        source_id="src-1",
        source_name="Atom Feed",
        source_type="rss",
        source_config={"feed_url": "https://example.com/atom.xml"},
        max_items=10,
        http=http_client,
    )

    result = await collector.collect(context)
    assert len(result.items) == 1
    assert result.items[0].title == "Atom Title 1"
    assert result.items[0].url == "http://example.com/atom1"
    assert result.items[0].author == "Alice Smith"
    assert result.metadata["feed_format"] == "atom10"


@pytest.mark.asyncio
async def test_bozo_error_handling():
    # Strict validation fails on bozo = true
    # We construct a malformed XML to trigger bozo
    malformed_xml = b"<rss><channel><title>Malformed Feed<item><title>Item without tag closing"

    def handler(request: httpx.Request):
        return httpx.Response(200, content=malformed_xml)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    # 1. strict_parsing = false (should continue since we may extract some entries or at least warn)
    collector_lenient = RSSCollector(settings=None)
    context_lenient = CollectionContext(
        source_id="src-1",
        source_name="Lenient Bozo",
        source_type="rss",
        source_config={
            "feed_url": "https://example.com/feed",
            "strict_parsing": False,
        },
        max_items=10,
        http=http_client,
    )

    # If no entries can be parsed at all, even strict=False raises RSSInvalidFeedError
    with pytest.raises(RSSInvalidFeedError):
        await collector_lenient.collect(context_lenient)

    # Let's test bozo = True WITH entries (e.g. invalid attribute but valid entries)
    # feedparser can parse some entries but raises bozo warning
    bozo_with_entries_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" invalid-attr="bad">
  <channel>
    <title>Test Bozo Feed</title>
    <item>
      <title>Test Item</title>
      <link>http://example.com/item</link>
    </item>
  </channel>
</rss>"""

    def handler_bozo(request: httpx.Request):
        return httpx.Response(200, content=bozo_with_entries_xml)

    client_bozo = httpx.AsyncClient(transport=httpx.MockTransport(handler_bozo))
    http_client_bozo = HttpxHttpClient(client=client_bozo)

    # 2. Lenient with entries -> Succeeds with warning
    res_lenient = await collector_lenient.collect(
        CollectionContext(
            source_id="src-1",
            source_name="Lenient Bozo",
            source_type="rss",
            source_config={
                "feed_url": "https://example.com/feed",
                "strict_parsing": False,
            },
            max_items=10,
            http=http_client_bozo,
        )
    )
    assert len(res_lenient.items) == 1
    assert any(w.code == "bozo_warning" for w in res_lenient.warnings)

    # 3. Strict with entries -> Fails immediately
    context_strict = CollectionContext(
        source_id="src-1",
        source_name="Strict Bozo",
        source_type="rss",
        source_config={
            "feed_url": "https://example.com/feed",
            "strict_parsing": True,
        },
        max_items=10,
        http=http_client_bozo,
    )
    with pytest.raises(RSSInvalidFeedError):
        await collector_lenient.collect(context_strict)


@pytest.mark.asyncio
async def test_lookback_filtering():
    # Fixes published_at parsing, lookback range checks, include_undated
    now_fixed = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)

    # Item 1: published 10 days ago (in range)
    # Item 2: published 200 days ago (out of range)
    # Item 3: no date (include_undated)
    feed_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Lookback Test</title>
    <item>
      <title>Recent Item</title>
      <link>http://example.com/recent</link>
      <pubDate>Fri, 26 Jun 2026 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Old Item</title>
      <link>http://example.com/old</link>
      <pubDate>Mon, 01 Dec 2025 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Undated Item</title>
      <link>http://example.com/undated</link>
    </item>
  </channel>
</rss>"""

    def handler(request: httpx.Request):
        return httpx.Response(200, content=feed_xml)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    collector = RSSCollector(settings=None, time_func=lambda: now_fixed)

    # Scenario A: lookback = 180, include_undated = True
    context_a = CollectionContext(
        source_id="src-1",
        source_name="Feed",
        source_type="rss",
        source_config={
            "feed_url": "https://example.com/feed",
            "lookback_days": 180,
            "include_undated": True,
        },
        max_items=10,
        http=http_client,
    )
    res_a = await collector.collect(context_a)
    assert len(res_a.items) == 2  # Recent Item, Undated Item
    assert {it.title for it in res_a.items} == {"Recent Item", "Undated Item"}

    # Scenario B: lookback = 180, include_undated = False
    context_b = CollectionContext(
        source_id="src-1",
        source_name="Feed",
        source_type="rss",
        source_config={
            "feed_url": "https://example.com/feed",
            "lookback_days": 180,
            "include_undated": False,
        },
        max_items=10,
        http=http_client,
    )
    res_b = await collector.collect(context_b)
    assert len(res_b.items) == 1  # Recent Item only
    assert res_b.items[0].title == "Recent Item"


@pytest.mark.asyncio
async def test_deduplication_and_limit():
    # Duplicate URLs / IDs within same feed run
    feed_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Dup Test</title>
    <item>
      <title>First Unique</title>
      <link>http://example.com/unique1</link>
      <guid>id-1</guid>
    </item>
    <item>
      <title>Second Duplicate of First (same ID)</title>
      <link>http://example.com/unique2</link>
      <guid>id-1</guid>
    </item>
    <item>
      <title>Third Duplicate (same URL)</title>
      <link>http://example.com/unique1</link>
      <guid>id-2</guid>
    </item>
    <item>
      <title>Fourth Unique</title>
      <link>http://example.com/unique4</link>
      <guid>id-4</guid>
    </item>
  </channel>
</rss>"""

    def handler(request: httpx.Request):
        return httpx.Response(200, content=feed_xml)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    collector = RSSCollector(settings=None)

    # Deduplication check
    context = CollectionContext(
        source_id="src-1",
        source_name="Feed",
        source_type="rss",
        source_config={"feed_url": "https://example.com/feed", "max_items": 10},
        max_items=100,
        http=http_client,
    )
    res = await collector.collect(context)
    assert len(res.items) == 2  # First Unique, Fourth Unique
    assert res.metadata["duplicate_entry_count"] == 2
    assert any(w.code == "duplicate_entries_ignored" for w in res.warnings)

    # Limit items check (config limit vs context limit)
    context_limit = CollectionContext(
        source_id="src-1",
        source_name="Feed",
        source_type="rss",
        source_config={"feed_url": "https://example.com/feed", "max_items": 1},
        max_items=100,
        http=http_client,
    )
    res_limit = await collector.collect(context_limit)
    assert len(res_limit.items) == 1
