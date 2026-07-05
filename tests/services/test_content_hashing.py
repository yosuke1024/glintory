from datetime import UTC, datetime

from glintory.services.content_hashing import generate_content_hash


def test_generate_content_hash_deterministic():
    dt = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    meta1 = {"a": 1, "b": 2}
    meta2 = {"b": 2, "a": 1}

    hash1 = generate_content_hash(
        hash_version="v1",
        source_type="github",
        item_type="issue",
        canonical_url="https://example.com/foo",
        title="Sample Title",
        excerpt="Sample Excerpt",
        author="alice",
        published_at=dt,
        metadata=meta1,
    )

    hash2 = generate_content_hash(
        hash_version="v1",
        source_type="github",
        item_type="issue",
        canonical_url="https://example.com/foo",
        title="Sample Title",
        excerpt="Sample Excerpt",
        author="alice",
        published_at=dt,
        metadata=meta2,
    )

    # Deterministic and order-independent for dict keys
    assert hash1 == hash2
    assert len(hash1) == 64
    assert hash1.islower()
    # Check hex structure
    int(hash1, 16)


def test_generate_content_hash_changes():
    dt = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    base_args = {
        "hash_version": "v1",
        "source_type": "github",
        "item_type": "issue",
        "canonical_url": "https://example.com/foo",
        "title": "Sample Title",
        "excerpt": "Sample Excerpt",
        "author": "alice",
        "published_at": dt,
        "metadata": {"a": 1},
    }

    h_base = generate_content_hash(**base_args)

    # Change title
    args_changed = base_args.copy()
    args_changed["title"] = "Changed Title"
    assert generate_content_hash(**args_changed) != h_base

    # Change excerpt
    args_changed = base_args.copy()
    args_changed["excerpt"] = "Changed Excerpt"
    assert generate_content_hash(**args_changed) != h_base

    # Change url
    args_changed = base_args.copy()
    args_changed["canonical_url"] = "https://example.com/bar"
    assert generate_content_hash(**args_changed) != h_base

    # Change version
    args_changed = base_args.copy()
    args_changed["hash_version"] = "v2"
    assert generate_content_hash(**args_changed) != h_base
