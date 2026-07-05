from datetime import UTC, datetime, timedelta

from glintory.collectors.base import RawItem
from glintory.domain.enums import SignalType
from glintory.services.normalization import SignalNormalizer, calculate_freshness_score


def test_calculate_freshness_score():
    collected_at = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)

    # null published_at -> 0.50
    assert calculate_freshness_score(None, collected_at) == 0.50

    # <= 7 days -> 1.00
    assert (
        calculate_freshness_score(collected_at - timedelta(days=5), collected_at)
        == 1.00
    )
    assert (
        calculate_freshness_score(collected_at - timedelta(days=7), collected_at)
        == 1.00
    )

    # <= 30 days -> 0.85
    assert (
        calculate_freshness_score(collected_at - timedelta(days=8), collected_at)
        == 0.85
    )
    assert (
        calculate_freshness_score(collected_at - timedelta(days=30), collected_at)
        == 0.85
    )

    # <= 90 days -> 0.65
    assert (
        calculate_freshness_score(collected_at - timedelta(days=31), collected_at)
        == 0.65
    )
    assert (
        calculate_freshness_score(collected_at - timedelta(days=90), collected_at)
        == 0.65
    )

    # <= 365 days -> 0.40
    assert (
        calculate_freshness_score(collected_at - timedelta(days=91), collected_at)
        == 0.40
    )
    assert (
        calculate_freshness_score(collected_at - timedelta(days=365), collected_at)
        == 0.40
    )

    # > 365 days -> 0.20
    assert (
        calculate_freshness_score(collected_at - timedelta(days=366), collected_at)
        == 0.20
    )

    # Future date -> 1.00
    assert (
        calculate_freshness_score(collected_at + timedelta(days=1), collected_at)
        == 1.00
    )


def test_normalize_batch_success():
    collected_at = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    normalizer = SignalNormalizer()

    # RawItem 1: Repository
    repo_item = RawItem(
        external_id="repo-1",
        url="https://github.com/owner/repo",
        title="Cool Repository",
        excerpt="An awesome project",
        author="owner",
        published_at=collected_at - timedelta(days=5),
        item_type="repository",
        metadata={
            "topics": ["python", "ai"],
            "stargazers_count": 100,
            "watchers_count": 99,
            "forks_count": 50,
            "open_issues_count": 5,
            "score": 10,
            "language": "Python",
        },
    )

    # RawItem 2: Issue (Pain)
    issue_item = RawItem(
        external_id="issue-1",
        url="https://github.com/owner/repo/issues/1",
        title="This is hard to use",
        excerpt="The configuration is too complex.",
        author="user123",
        published_at=collected_at - timedelta(days=15),
        item_type="issue",
        metadata={
            "labels": ["help wanted"],
            "comments": 2,
            "reactions_total_count": 5,
        },
    )

    result = normalizer.normalize_batch(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        items=[repo_item, issue_item],
        collected_at=collected_at,
    )

    assert len(result.errors) == 0
    assert len(result.signals) == 2

    # Repo validation
    sig1 = result.signals[0]
    assert sig1.source_id == "src-1"
    assert sig1.collection_run_id == "run-1"
    assert sig1.external_id == "repo-1"
    assert sig1.canonical_url == "https://github.com/owner/repo"
    assert sig1.title == "Cool Repository"
    assert sig1.excerpt == "An awesome project"
    assert sig1.author == "owner"
    assert sig1.signal_type == SignalType.PROJECT
    assert sig1.tags == ("python", "ai")
    assert sig1.categories == ()
    assert sig1.metrics["stargazers_count"] == 100
    assert sig1.metrics["watchers_count"] == 99
    assert sig1.metrics["forks_count"] == 50
    assert sig1.metrics["open_issues_count"] == 5
    assert sig1.metrics["score"] == 10
    assert sig1.raw_metadata["language"] == "Python"
    assert sig1.freshness_score == 1.00
    assert sig1.source_quality_score == 0.5

    # Issue validation
    sig2 = result.signals[1]
    assert sig2.signal_type == SignalType.PAIN
    assert sig2.tags == ("help wanted",)
    assert sig2.metrics["comments"] == 2
    assert sig2.metrics["reactions_total_count"] == 5
    assert sig2.freshness_score == 0.85


def test_normalize_batch_mixed_validation_errors():
    collected_at = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    normalizer = SignalNormalizer()

    # Valid item
    valid_item = RawItem(
        external_id="repo-1",
        url="https://github.com/owner/repo",
        title="Valid Title",
        item_type="repository",
    )

    # Invalid URL item
    invalid_url_item = RawItem(
        external_id="repo-2",
        url="ftp://invalid-url.com",
        title="Invalid URL Title",
        item_type="repository",
    )

    # Empty title item
    empty_title_item = RawItem(
        external_id="repo-3",
        url="https://github.com/owner/repo3",
        title="   ",
        item_type="repository",
    )

    # Unsupported item type
    unsupported_type_item = RawItem(
        external_id="repo-4",
        url="https://github.com/owner/repo4",
        title="Unsupported Type",
        item_type="unknown_type",
    )

    # Future date warning item
    future_date_item = RawItem(
        external_id="repo-5",
        url="https://github.com/owner/repo5",
        title="Future Date",
        item_type="repository",
        published_at=collected_at + timedelta(days=2),
    )

    result = normalizer.normalize_batch(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        items=[
            valid_item,
            invalid_url_item,
            empty_title_item,
            unsupported_type_item,
            future_date_item,
        ],
        collected_at=collected_at,
    )

    # 2 signals successfully normalized (valid_item, future_date_item)
    assert len(result.signals) == 2
    assert result.signals[0].external_id == "repo-1"
    assert result.signals[1].external_id == "repo-5"

    # 3 errors recorded
    assert len(result.errors) == 3
    error_ids = {err.external_id for err in result.errors}
    assert error_ids == {"repo-2", "repo-3", "repo-4"}
    assert any(err.code == "unsupported_item_type" for err in result.errors)

    # 1 warning recorded (future date)
    assert len(result.warnings) == 1
    assert result.warnings[0].external_id == "repo-5"
    assert "future" in result.warnings[0].message.lower()
