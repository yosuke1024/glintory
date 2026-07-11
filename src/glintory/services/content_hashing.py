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


def calculate_opportunity_content_hash(opp: Any, evidences: list[dict[str, Any]]) -> str:
    """Calculate deterministic SHA-256 hash for an opportunity's content and its evidence."""
    # Stable evidence sort key: relevance_score DESC, published_at ASC, signal_id ASC
    def get_sort_key(ev: dict[str, Any]) -> tuple[float, str, str]:
        rev_score = -float(ev.get("relevance_score", 0.0) or 0.0)
        pub_at = ev.get("published_at")
        if isinstance(pub_at, datetime):
            pub_at_str = pub_at.isoformat()
        else:
            pub_at_str = str(pub_at or "")
        sig_id = str(ev.get("signal_id") or "")
        return (rev_score, pub_at_str, sig_id)

    sorted_ev = sorted(evidences, key=get_sort_key)

    serialized_ev = []
    for ev in sorted_ev:
        pub_at = ev.get("published_at")
        if isinstance(pub_at, datetime):
            pub_at_str = pub_at.isoformat()
        else:
            pub_at_str = str(pub_at) if pub_at else None

        exc = ev.get("excerpt") or ""
        exc_limit = exc[:500] if exc else None

        serialized_ev.append({
            "signal_id": ev.get("signal_id"),
            "role": ev.get("role"),
            "title": ev.get("title"),
            "url": ev.get("url"),
            "published_at": pub_at_str,
            "relevance_score": ev.get("relevance_score"),
            "summary_ja": ev.get("summary_ja"),
            "summary_en": ev.get("summary_en"),
            "excerpt": exc_limit
        })

    payload = {
        "title": opp.title,
        "summary_ja": opp.summary_ja,
        "summary_en": opp.summary_en,
        "problem_ja": opp.problem_ja,
        "problem_en": opp.problem_en,
        "target_user_ja": opp.target_user_ja,
        "target_user_en": opp.target_user_en,
        "current_workaround_ja": opp.current_workaround_ja,
        "current_workaround_en": opp.current_workaround_en,
        "existing_solution_gap_ja": opp.existing_solution_gap_ja,
        "existing_solution_gap_en": opp.existing_solution_gap_en,
        "mvp_direction_ja": opp.mvp_direction_ja,
        "mvp_direction_en": opp.mvp_direction_en,
        "why_selected_ja": opp.why_selected_ja,
        "why_selected_en": opp.why_selected_en,
        "risks_ja": opp.risks_ja,
        "risks_en": opp.risks_en,
        "total_score": int(opp.total_score or 0),
        "evidence_score": int(opp.evidence_score or 0),
        "feasibility_score": int(opp.feasibility_score or 0),
        "penalty_score": int(opp.penalty_score or 0),
        "confidence": opp.confidence.value if hasattr(opp.confidence, "value") else str(opp.confidence or ""),
        "evidences": serialized_ev
    }

    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

