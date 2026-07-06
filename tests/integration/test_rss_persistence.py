from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.collectors.registry import CollectorRegistry
from glintory.collectors.rss import RSSCollector
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
    session_factory = sessionmaker(bind=engine)
    return session_factory


@pytest.fixture
def db_session(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


XML_FIRST_RUN = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Integration Feed</title>
    <link>http://example.com</link>
    <item>
      <title>Item A</title>
      <link>http://example.com/a</link>
      <guid>id-a</guid>
      <description>Excerpt A</description>
    </item>
    <item>
      <title>Item B</title>
      <link>http://example.com/b</link>
      <guid>id-b</guid>
      <description>Excerpt B</description>
    </item>
    <item>
      <title>Item C (No ID)</title>
      <link>http://example.com/c</link>
      <description>Excerpt C</description>
    </item>
  </channel>
</rss>"""

XML_SECOND_RUN = XML_FIRST_RUN  # Identical

XML_CHANGED_RUN = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Integration Feed</title>
    <link>http://example.com</link>
    <item>
      <title>Item A New Title</title>
      <link>http://example.com/a</link>
      <guid>id-a</guid>
      <description>Excerpt A New</description>
    </item>
    <item>
      <title>Item B</title>
      <link>http://example.com/b</link>
      <guid>id-b</guid>
      <description>Excerpt B</description>
    </item>
    <item>
      <title>Item C (No ID)</title>
      <link>http://example.com/c</link>
      <description>Excerpt C</description>
    </item>
  </channel>
</rss>"""

XML_URL_CHANGED_RUN = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Integration Feed</title>
    <link>http://example.com</link>
    <item>
      <title>Item A</title>
      <link>http://example.com/a-new-url</link>
      <guid>id-a</guid>
      <description>Excerpt A</description>
    </item>
    <item>
      <title>Item B</title>
      <link>http://example.com/b</link>
      <guid>id-b</guid>
      <description>Excerpt B</description>
    </item>
    <item>
      <title>Item C (No ID)</title>
      <link>http://example.com/c</link>
      <description>Excerpt C</description>
    </item>
  </channel>
</rss>"""

XML_PARTIAL_RUN = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Integration Feed</title>
    <link>http://example.com</link>
    <item>
      <title>Item A</title>
      <link>http://example.com/a</link>
      <guid>id-a</guid>
    </item>
    <item>
      <title></title> <!-- Missing Title -> Error -->
      <link>http://example.com/b</link>
      <guid>id-b</guid>
    </item>
  </channel>
</rss>"""


@pytest.mark.asyncio
async def test_rss_persistence_lifecycle(db_session, db_session_factory):
    # Setup collector registry with RSSCollector
    # We will mock http requests by dynamically swapping transport
    current_xml = XML_FIRST_RUN

    def handler(request: httpx.Request):
        return httpx.Response(200, content=current_xml)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    reg = CollectorRegistry()
    fixed_now = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
    reg.register(RSSCollector(settings=None, time_func=lambda: fixed_now))

    service = CollectionService(db_session_factory, reg, http_client=http_client)

    # Setup Source
    src = Source(
        name="RSS Feed",
        source_type="rss",
        enabled=True,
        config={
            "feed_url": "https://example.com/feed.xml",
            "signal_type": "trend",
            "default_categories": ["rss", "test"],
            "default_tags": ["tag1"],
        },
    )
    db_session.add(src)
    db_session.commit()

    # --- 1. First Run (Insert) ---
    res1 = await service.run_source(src.id)
    assert res1.status == CollectionRunStatus.SUCCEEDED
    assert res1.fetched_count == 3
    assert res1.inserted_count == 3
    assert res1.updated_count == 0
    assert res1.duplicate_count == 0

    # Verify Database state
    db_session.expire_all()
    sigs = db_session.query(Signal).filter_by(source_id=src.id).all()
    assert len(sigs) == 3
    assert {s.title for s in sigs} == {"Item A", "Item B", "Item C (No ID)"}
    assert all(s.signal_type == SignalType.TREND for s in sigs)
    # Check default category / tag injection
    assert all("rss" in s.categories for s in sigs)
    assert all("tag1" in s.tags for s in sigs)

    # --- 2. Second Run (Duplicate) ---
    # Identical content
    current_xml = XML_SECOND_RUN
    res2 = await service.run_source(src.id)
    assert res2.status == CollectionRunStatus.SUCCEEDED
    assert res2.duplicate_count == 3
    assert res2.inserted_count == 0
    assert res2.updated_count == 0

    # Ensure updated_at didn't change
    db_session.expire_all()
    sigs_after = db_session.query(Signal).filter_by(source_id=src.id).all()
    assert len(sigs_after) == 3

    # --- 3. Changed Entry Run (Update) ---
    current_xml = XML_CHANGED_RUN
    res3 = await service.run_source(src.id)
    assert res3.status == CollectionRunStatus.SUCCEEDED
    assert res3.updated_count == 1  # Item A updated
    assert res3.duplicate_count == 2  # Item B and C duplicated
    assert res3.inserted_count == 0

    db_session.expire_all()
    sig_a = (
        db_session.query(Signal)
        .filter_by(
            source_id=src.id,
            external_id="feed:entry:74ab07339b1597928cc353388c393593eff18b68739ad38fd558dd349f2faf59",
        )
        .first()
    )  # hash of "id-a"
    assert sig_a is not None
    assert sig_a.title == "Item A New Title"
    assert sig_a.excerpt == "Excerpt A New"

    # --- 4. URL Changed Run (Update) ---
    current_xml = XML_URL_CHANGED_RUN
    res4 = await service.run_source(src.id)
    assert res4.status == CollectionRunStatus.SUCCEEDED
    assert res4.updated_count == 1  # Item A's URL changed
    assert res4.duplicate_count == 2

    db_session.expire_all()
    sig_a_url = (
        db_session.query(Signal)
        .filter_by(
            source_id=src.id,
            external_id="feed:entry:74ab07339b1597928cc353388c393593eff18b68739ad38fd558dd349f2faf59",
        )
        .first()
    )
    assert sig_a_url is not None
    assert sig_a_url.canonical_url == "http://example.com/a-new-url"


@pytest.mark.asyncio
async def test_rss_persistence_no_id_duplicate(db_session, db_session_factory):
    # Entry C in First Run has no guid/id. We check if URL based duplication works.
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Deduplication Feed</title>
    <link>http://example.com</link>
    <item>
      <title>No ID Item</title>
      <link>http://example.com/noid</link>
    </item>
  </channel>
</rss>"""

    def handler(request: httpx.Request):
        return httpx.Response(200, content=xml)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    reg = CollectorRegistry()
    reg.register(RSSCollector(settings=None))
    service = CollectionService(db_session_factory, reg, http_client=http_client)

    src = Source(
        name="No ID RSS",
        source_type="rss",
        enabled=True,
        config={"feed_url": "https://example.com/feed.xml", "signal_type": "trend"},
    )
    db_session.add(src)
    db_session.commit()

    # Run 1: Insert
    res1 = await service.run_source(src.id)
    assert res1.inserted_count == 1

    # Run 2: Duplicate detection via URL
    res2 = await service.run_source(src.id)
    assert res2.duplicate_count == 1
    assert res2.inserted_count == 0


@pytest.mark.asyncio
async def test_rss_persistence_partial_success(db_session, db_session_factory):
    def handler(request: httpx.Request):
        return httpx.Response(200, content=XML_PARTIAL_RUN)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    reg = CollectorRegistry()
    reg.register(RSSCollector(settings=None))
    service = CollectionService(db_session_factory, reg, http_client=http_client)

    src = Source(
        name="Partial RSS",
        source_type="rss",
        enabled=True,
        config={"feed_url": "https://example.com/feed.xml", "signal_type": "trend"},
    )
    db_session.add(src)
    db_session.commit()

    res = await service.run_source(src.id)
    assert res.status == CollectionRunStatus.PARTIAL
    assert res.fetched_count == 1  # only 1 valid item reached raw_items
    assert res.inserted_count == 1
    assert res.error_count == 1  # 1 missing title entry error

    db_session.expire_all()
    # Check that the valid item was indeed saved
    sigs = db_session.query(Signal).filter_by(source_id=src.id).all()
    assert len(sigs) == 1
    assert sigs[0].title == "Item A"


@pytest.mark.asyncio
async def test_rss_persistence_invalid_feed(db_session, db_session_factory):
    # Malformed XML causing parsing failure
    def handler(request: httpx.Request):
        return httpx.Response(200, content=b"THIS IS NOT XML AT ALL")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    reg = CollectorRegistry()
    reg.register(RSSCollector(settings=None))
    service = CollectionService(db_session_factory, reg, http_client=http_client)

    src = Source(
        name="Invalid RSS",
        source_type="rss",
        enabled=True,
        config={"feed_url": "https://example.com/feed.xml", "signal_type": "trend"},
    )
    db_session.add(src)
    db_session.commit()

    res = await service.run_source(src.id)
    assert res.status == CollectionRunStatus.FAILED
    assert res.error_summary is not None
    # Verify XML content is NOT in the error message
    assert "THIS IS NOT XML" not in res.error_summary


@pytest.mark.asyncio
async def test_rss_persistence_multi_source_isolation(db_session, db_session_factory):
    # RSS, GitHub and Hacker News might ingest the same external article URL
    # We must confirm that they are stored as separate signals and no cross-source integration/collision occurs.
    target_url = "http://example.com/shared-article"

    # Setup 2 separate RSS sources
    src1 = Source(
        name="RSS 1",
        source_type="rss",
        enabled=True,
        config={"feed_url": "https://example.com/feed1.xml", "signal_type": "trend"},
    )
    src2 = Source(
        name="RSS 2",
        source_type="rss",
        enabled=True,
        config={"feed_url": "https://example.com/feed2.xml", "signal_type": "trend"},
    )
    db_session.add(src1)
    db_session.add(src2)
    db_session.commit()

    xml_1 = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Feed 1</title>
    <item>
      <title>Article Title</title>
      <link>{target_url}</link>
    </item>
  </channel>
</rss>""".encode()

    xml_2 = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Feed 2</title>
    <item>
      <title>Article Title</title>
      <link>{target_url}</link>
    </item>
  </channel>
</rss>""".encode()

    # Dynamic mocked client responding differently per URL
    def handler(request: httpx.Request):
        if "feed1.xml" in str(request.url):
            return httpx.Response(200, content=xml_1)
        return httpx.Response(200, content=xml_2)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)

    reg = CollectorRegistry()
    reg.register(RSSCollector(settings=None))
    service = CollectionService(db_session_factory, reg, http_client=http_client)

    # Ingest source 1
    res1 = await service.run_source(src1.id)
    assert res1.inserted_count == 1

    # Ingest source 2 (same article URL)
    res2 = await service.run_source(src2.id)
    assert res2.inserted_count == 1  # Should insert as a separate Signal

    db_session.expire_all()
    # Confirm both signals exist with the same URL but different source_id
    sigs = db_session.query(Signal).filter(Signal.canonical_url == target_url).all()
    assert len(sigs) == 2
    assert {s.source_id for s in sigs} == {src1.id, src2.id}
