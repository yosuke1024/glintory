import hashlib
import json
from datetime import datetime
from typing import Any, Literal, cast

from glintory.domain.public_contract import PublicOpportunityDetailV1


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

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def calculate_opportunity_detail_canonical_hash(
    detail: PublicOpportunityDetailV1,
) -> str:
    """Calculate deterministic SHA-256 hash for a PublicOpportunityDetailV1,

    excluding metadata management fields.
    """
    # Dump to dictionary representation
    data = detail.model_dump(mode="json")

    # Exclude metadata fields that are dynamically assigned after hashing
    exclude_fields = {
        "content_hash",
        "revision",
        "first_published_at",
        "last_published_at",
        "generated_at",
    }
    for f in exclude_fields:
        data.pop(f, None)

    # Serialize to deterministic JSON (compact, sorted keys)
    serialized = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def calculate_opportunity_content_hash(
    opp: Any, evidences: list[dict[str, Any]]
) -> str:
    """Calculate deterministic SHA-256 hash for an opportunity's content and its evidence

    by converting it to a Canonical PublicOpportunityDetailV1 representation.
    """

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

    from glintory.domain.public_contract import (
        JuryPressReadinessV1,
        PublicEvidenceV1,
        PublicOpportunityDetailV1,
        PublicOpportunityGateV1,
        PublicOpportunityLocalizationDetailItemV1,
        PublicOpportunityLocalizationDetailV1,
        PublicOpportunityScoreDetailV1,
    )

    mapped_ev = []
    for ev in sorted_ev:
        pub_at = ev.get("published_at")
        if not isinstance(pub_at, datetime):
            if isinstance(pub_at, str) and pub_at:
                try:
                    pub_at = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                except ValueError:
                    pub_at = datetime(1970, 1, 1)
            else:
                pub_at = datetime(1970, 1, 1)

        exc = ev.get("excerpt") or ""
        exc_limit = exc[:500] if exc else None

        mapped_ev.append(
            PublicEvidenceV1(
                signal_id=str(ev.get("signal_id") or ""),
                role=cast(
                    Literal["demand", "supply", "context", "unknown"],
                    ev.get("role") or "unknown",
                ),
                source_type=str(ev.get("source_type") or "unknown"),
                source_name=str(ev.get("source_name") or "unknown"),
                title=str(ev.get("title") or ""),
                url=str(ev.get("url") or ""),
                published_at=pub_at,
                relevance_score=float(ev.get("relevance_score") or 0.0),
                summary_ja=ev.get("summary_ja"),
                summary_en=ev.get("summary_en"),
                excerpt=exc_limit,
            )
        )

    # Determine localization status
    loc_ja_status = "completed" if (opp.title_ja and opp.summary_ja) else "pending"
    loc_en_status = "completed" if (opp.title_en and opp.summary_en) else "pending"

    lifecycle_raw: Any = getattr(opp, "public_lifecycle", "active") or "active"
    lifecycle = (
        lifecycle_raw.value if hasattr(lifecycle_raw, "value") else str(lifecycle_raw)
    )

    detail = PublicOpportunityDetailV1(
        public_id=opp.public_id
        if hasattr(opp, "public_id")
        else "opp_00000000000000000000000000000000",
        public_lifecycle=cast(Literal["active", "merged", "retired"], lifecycle),
        revision=getattr(opp, "public_revision", 1) or 1,
        content_hash="",
        first_published_at=getattr(opp, "first_published_at", None),
        last_published_at=getattr(opp, "last_published_at", None),
        localization=PublicOpportunityLocalizationDetailV1(
            ja=PublicOpportunityLocalizationDetailItemV1(
                status=cast(Literal["pending", "completed", "failed"], loc_ja_status),
                title=opp.title_ja,
                summary=opp.summary_ja,
                target_user=getattr(opp, "target_user_ja", None),
                problem=getattr(opp, "problem_ja", None),
                current_workaround=getattr(opp, "current_workaround_ja", None),
                existing_solution_gap=getattr(opp, "existing_solution_gap_ja", None),
                mvp_direction=getattr(opp, "mvp_direction_ja", None),
                why_selected=getattr(opp, "why_selected_ja", None),
                risks=getattr(opp, "risks_ja", None),
            ),
            en=PublicOpportunityLocalizationDetailItemV1(
                status=cast(Literal["pending", "completed", "failed"], loc_en_status),
                title=opp.title_en,
                summary=opp.summary_en,
                target_user=getattr(opp, "target_user_en", None),
                problem=getattr(opp, "problem_en", None),
                current_workaround=getattr(opp, "current_workaround_en", None),
                existing_solution_gap=getattr(opp, "existing_solution_gap_en", None),
                mvp_direction=getattr(opp, "mvp_direction_en", None),
                why_selected=getattr(opp, "why_selected_en", None),
                risks=getattr(opp, "risks_en", None),
            ),
        ),
        score=PublicOpportunityScoreDetailV1(
            total=int(opp.total_score or 0),
            evidence=int(getattr(opp, "evidence_score", 0) or 0),
            feasibility=int(getattr(opp, "feasibility_score", 0) or 0),
            penalty=int(getattr(opp, "penalty_score", 0) or 0),
            confidence=cast(
                Literal["low", "medium", "high"],
                opp.confidence.value
                if hasattr(opp.confidence, "value")
                else str(opp.confidence or "low"),
            ),
            version="v2",
            components=[],
            independent_evidence_count=int(
                getattr(opp, "independent_evidence_count", 0) or 0
            ),
            demand_evidence_count=int(getattr(opp, "demand_evidence_count", 0) or 0),
        ),
        gate=PublicOpportunityGateV1(
            version="v2",
            status=cast(
                Literal["passed", "rejected", "failed"],
                getattr(opp, "gate_status", "rejected") or "rejected",
            ),
            reason=getattr(opp, "gate_reason", "") or "",
        ),
        evidence=mapped_ev,
        jurypress=JuryPressReadinessV1(ready=False, reasons=[]),
        enrichment_status=cast(
            Literal["pending", "completed", "failed"],
            getattr(opp, "enrichment_status", "pending") or "pending",
        ),
        translation_status=cast(
            Literal["pending", "completed", "failed"],
            getattr(opp, "translation_status", "pending") or "pending",
        ),
    )

    return calculate_opportunity_detail_canonical_hash(detail)
