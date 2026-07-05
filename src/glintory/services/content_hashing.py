import hashlib
import json
from datetime import datetime
from typing import Any


def generate_content_hash(
    hash_version: str,
    source_type: str,
    item_type: str,
    canonical_url: str,
    title: str,
    excerpt: str,
    author: str | None,
    published_at: datetime | None,
    metadata: dict[str, Any],
) -> str:
    published_at_str = published_at.isoformat() if published_at is not None else None

    payload = {
        "hash_version": hash_version,
        "source_type": source_type,
        "item_type": item_type,
        "canonical_url": canonical_url,
        "title": title,
        "excerpt": excerpt,
        "author": author,
        "published_at": published_at_str,
        "metadata": metadata,
    }

    # Compact, sorted keys for deterministic JSON serialization
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
