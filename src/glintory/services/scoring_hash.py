import hashlib
import json
from datetime import date

from glintory.domain.scoring import OpportunityScoringInput


def calculate_scoring_input_hash(
    scoring_version: str,
    as_of_date: date,
    scoring_input: OpportunityScoringInput,
) -> str:
    """Calculate a deterministic SHA-256 hash of the opportunity scoring input."""
    # Sort signals by signal_id to ensure order independence
    sorted_signals = sorted(scoring_input.signals, key=lambda s: s.signal_id)

    signals_data = []
    for s in sorted_signals:
        excerpt = s.excerpt or ""
        meta = s.raw_metadata or {}

        # Technical specificity flags as specified:
        # - tags count >= 1
        # - GitHub `full_name` metadata present
        # - GitHub repository URL metadata present (e.g. 'repository_url' or 'html_url')
        # - programming language metadata present (e.g. 'language')
        # - outbound host metadata present (e.g. 'outbound_host' or 'outbound_url')
        has_tags = len(s.tags) >= 1
        has_github_fullname = "full_name" in meta
        has_github_repo_url = "repository_url" in meta or "html_url" in meta
        has_prog_lang = "language" in meta
        has_outbound_host = "outbound_host" in meta or "outbound_url" in meta

        specificity_flags = {
            "has_tags": has_tags,
            "has_github_fullname": has_github_fullname,
            "has_github_repo_url": has_github_repo_url,
            "has_prog_lang": has_prog_lang,
            "has_outbound_host": has_outbound_host,
        }

        # Convert enums to string values
        rel_type = (
            s.relation_type.value
            if hasattr(s.relation_type, "value")
            else str(s.relation_type)
        )
        sig_type = (
            s.signal_type.value
            if hasattr(s.signal_type, "value")
            else str(s.signal_type)
        )

        sig_dict = {
            "signal_id": s.signal_id,
            "relation_type": rel_type,
            "relevance_score": s.relevance_score,
            "evidence_origin": s.evidence_origin,
            "source_type": s.source_type,
            "signal_type": sig_type,
            "published_at": s.published_at.isoformat() if s.published_at else None,
            "excerpt_length": len(excerpt),
            "tags_count": len(s.tags) if s.tags else 0,
            "specificity_flags": specificity_flags,
        }
        signals_data.append(sig_dict)

    data_to_hash = {
        "scoring_version": scoring_version,
        "as_of_date": as_of_date.isoformat(),
        "opportunity_id": scoring_input.opportunity_id,
        "signals": signals_data,
    }

    # Generate a compact, key-sorted JSON representation
    json_bytes = json.dumps(
        data_to_hash, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    return hashlib.sha256(json_bytes).hexdigest()
