from datetime import UTC, datetime

from glintory.collectors.base import RawItem
from glintory.domain.enums import SignalType
from glintory.services.normalization import SignalNormalizer


def test_hn_normalization_mapping():
    normalizer = SignalNormalizer()
    collected_at = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)

    # 1. Ask HN item
    ask_item = RawItem(
        external_id="hackernews:item:12345",
        url="https://news.ycombinator.com/item?id=12345",
        title="Ask HN: How to build a startup?",
        excerpt="Let's discuss.",
        author="startup_guy",
        published_at=datetime(2026, 7, 6, 11, 0, 0, tzinfo=UTC),
        item_type="hn_ask",
        metadata={
            "hn_id": 12345,
            "hn_item_type": "story",
            "score": 42,
            "descendants": 5,
            "kids_count": 3,
            "outbound_url": None,
            "outbound_host": None,
            "is_ask": True,
            "is_show": False,
            "is_job": False,
        },
    )

    result = normalizer.normalize_batch(
        source_id="hn_source",
        source_type="hackernews",
        collection_run_id="run_123",
        items=[ask_item],
        collected_at=collected_at,
    )

    assert len(result.errors) == 0
    assert len(result.signals) == 1
    sig = result.signals[0]

    assert sig.external_id == "hackernews:item:12345"
    assert sig.canonical_url == "https://news.ycombinator.com/item?id=12345"
    assert sig.title == "Ask HN: How to build a startup?"
    assert sig.excerpt == "Let's discuss."
    assert sig.author == "startup_guy"
    assert sig.published_at == datetime(2026, 7, 6, 11, 0, 0, tzinfo=UTC)
    assert sig.language is None
    assert sig.categories == ("hacker-news",)
    assert sig.tags == ("ask-hn",)
    assert sig.metrics == {"score": 42, "descendants": 5, "kids_count": 3}
    assert sig.signal_type == SignalType.REQUEST

    # Check content hashing rules
    # Identical item results in identical hash
    hash1 = sig.content_hash

    result2 = normalizer.normalize_batch(
        source_id="hn_source",
        source_type="hackernews",
        collection_run_id="run_123",
        items=[ask_item],
        collected_at=collected_at,
    )
    assert result2.signals[0].content_hash == hash1


def test_hn_normalization_types_and_tags():
    normalizer = SignalNormalizer()
    collected_at = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)

    # 2. Show HN
    show_item = RawItem(
        external_id="hackernews:item:1002",
        url="https://news.ycombinator.com/item?id=1002",
        title="Show HN: Glintory",
        excerpt="Check this out.",
        item_type="hn_show",
        metadata={"hn_id": 1002, "score": 10},
    )

    # 3. Job
    job_item = RawItem(
        external_id="hackernews:item:1003",
        url="https://news.ycombinator.com/item?id=1003",
        title="Google is hiring AI Engineers",
        item_type="hn_job",
        metadata={"hn_id": 1003},
    )

    # 4. Story
    story_item = RawItem(
        external_id="hackernews:item:1004",
        url="https://news.ycombinator.com/item?id=1004",
        title="Some random news article",
        item_type="hn_story",
        metadata={"hn_id": 1004, "score": 25},
    )

    result = normalizer.normalize_batch(
        source_id="hn_source",
        source_type="hackernews",
        collection_run_id="run_123",
        items=[show_item, job_item, story_item],
        collected_at=collected_at,
    )

    assert len(result.errors) == 0
    assert len(result.signals) == 3

    assert result.signals[0].signal_type == SignalType.LAUNCH
    assert result.signals[0].tags == ("show-hn",)

    assert result.signals[1].signal_type == SignalType.JOB_DEMAND
    assert result.signals[1].tags == ("hn-job",)

    assert result.signals[2].signal_type == SignalType.TREND
    assert result.signals[2].tags == ()
