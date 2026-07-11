import hashlib
import json
import os
import subprocess
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.models import Opportunity, OpportunitySignal, ScoreSnapshot, Signal
from glintory.domain.public_contract import (
    JuryPressFeedItemV1,
    JuryPressFeedV1,
    JuryPressReadinessV1,
    PublicEvidenceV1,
    PublicManifestCountsV1,
    PublicManifestEndpointsV1,
    PublicManifestV1,
    PublicOpportunityDetailV1,
    PublicOpportunityEvidenceMetricsV1,
    PublicOpportunityGateV1,
    PublicOpportunityListV1,
    PublicOpportunityLocalizationDetailItemV1,
    PublicOpportunityLocalizationDetailV1,
    PublicOpportunityLocalizationItemV1,
    PublicOpportunityLocalizationListV1,
    PublicOpportunityScoreDetailV1,
    PublicOpportunityScoreListV1,
    PublicOpportunitySummaryV1,
    ScoreComponentV1,
)
from glintory.services.content_hashing import calculate_opportunity_content_hash


def get_git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def evaluate_jurypress_readiness(op: Opportunity, ev_signals: list[Any]) -> tuple[bool, list[str]]:
    min_score = int(os.environ.get("GLINTORY_JURYPRESS_MIN_SCORE", "60"))
    reasons = []

    if op.current_scoring_version != "v2":
        reasons.append("INVALID_SCORING_VERSION")
    if op.gate_status != "passed":
        reasons.append("GATE_REJECTED")
    if op.status in (OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED):
        reasons.append("STATUS_EXCLUDED")
    if op.confidence not in (Confidence.MEDIUM, Confidence.HIGH):
        reasons.append("LOW_CONFIDENCE")
    if (op.total_score or 0) < min_score:
        reasons.append("SCORE_BELOW_THRESHOLD")
    if op.independent_evidence_count < 2:
        reasons.append("INSUFFICIENT_INDEPENDENT_EVIDENCE")
    if op.demand_evidence_count < 1:
        reasons.append("INSUFFICIENT_DEMAND_EVIDENCE")

    is_stale = False
    if not op.enriched_at:
        reasons.append("ENRICHMENT_MISSING")
    elif op.evidence_updated_at and op.evidence_updated_at > op.enriched_at:
        reasons.append("ENRICHMENT_STALE")
        is_stale = True

    if op.enrichment_status not in ("completed", "succeeded") and not is_stale:
        reasons.append("ENRICHMENT_MISSING")

    ja_fields = [op.title_ja, op.summary_ja, op.problem_ja, op.target_user_ja, op.current_workaround_ja, op.existing_solution_gap_ja, op.mvp_direction_ja, op.why_selected_ja, op.risks_ja]
    en_fields = [op.title_en, op.summary_en, op.problem_en, op.target_user_en, op.current_workaround_en, op.existing_solution_gap_en, op.mvp_direction_en, op.why_selected_en, op.risks_en]

    if any(f is None or len(f.strip()) == 0 for f in ja_fields):
        reasons.append("JAPANESE_LOCALIZATION_MISSING")
    if any(f is None or len(f.strip()) == 0 for f in en_fields):
        reasons.append("ENGLISH_LOCALIZATION_MISSING")

    has_ev_summary = False
    for ev_sig in ev_signals:
        sum_en = ev_sig[2]
        sum_ja = ev_sig[3]
        if (sum_ja and len(sum_ja.strip()) > 0) or (sum_en and len(sum_en.strip()) > 0):
            has_ev_summary = True
            break
    if not has_ev_summary:
        reasons.append("EVIDENCE_SUMMARY_MISSING")

    return len(reasons) == 0, reasons


def generate_public_contract(
    session: Session,
    temp_build_dir: str,
    base_path: str,
    site_url: str,
    gen_time: datetime
) -> dict[str, Any]:
    # 1. Output directories setup
    data_v1_dir = os.path.join(temp_build_dir, "data", "v1")
    opps_dir = os.path.join(data_v1_dir, "opportunities")
    feeds_dir = os.path.join(data_v1_dir, "feeds")
    schemas_dir = os.path.join(data_v1_dir, "schemas")

    os.makedirs(opps_dir, exist_ok=True)
    os.makedirs(feeds_dir, exist_ok=True)
    os.makedirs(schemas_dir, exist_ok=True)

    # 2. Write JSON Schemas
    schema_map = {
        "manifest.schema.json": PublicManifestV1,
        "opportunity-list.schema.json": PublicOpportunityListV1,
        "opportunity-detail.schema.json": PublicOpportunityDetailV1,
        "jurypress-feed.schema.json": JuryPressFeedV1
    }
    for filename, model in schema_map.items():
        with open(os.path.join(schemas_dir, filename), "w") as f:
            json.dump(model.model_json_schema(), f, indent=2, ensure_ascii=False)

    # 3. Retrieve all v2 context opportunities
    opportunities = session.query(Opportunity).filter(
        Opportunity.current_scoring_version == "v2"
    ).all()

    summary_items = []
    jurypress_ready_items = []

    for op in opportunities:
        # Load evidence signals
        ev_signals = (
            session.query(
                Signal,
                OpportunitySignal.relevance_score,
                OpportunitySignal.evidence_summary_en,
                OpportunitySignal.evidence_summary_ja,
                OpportunitySignal.relation_type
            )
            .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
            .filter(OpportunitySignal.opportunity_id == op.id)
            .filter(OpportunitySignal.is_excluded.is_(False))
            .all()
        )

        # Retrieve score components from the latest snapshot
        snapshots = (
            session.query(ScoreSnapshot)
            .filter(ScoreSnapshot.opportunity_id == op.id)
            .order_by(desc(ScoreSnapshot.created_at))
            .all()
        )
        score_components = []
        if snapshots:
            explanation = snapshots[0].explanation
            for cat in ["evidence", "feasibility", "penalties"]:
                if cat in explanation and "components" in explanation[cat]:
                    for c in explanation[cat]["components"]:
                        score_components.append(
                            ScoreComponentV1(
                                name=c["name"],
                                score=c["score"],
                                maximum=c["maximum"],
                                explanation=c["explanation"]
                            )
                        )

        # JuryPress Readiness Evaluation
        ready, reasons = evaluate_jurypress_readiness(op, ev_signals)

        # Map evidences using model constraint of max 500 characters on excerpt
        mapped_evidences = []
        for sig, rel_score, sum_en, sum_ja, _ in ev_signals:
            exc = sig.excerpt or ""
            exc_limit = exc[:500] if exc else None
            mapped_evidences.append(
                PublicEvidenceV1(
                    signal_id=sig.id,
                    role=sig.signal_role.value if hasattr(sig.signal_role, "value") else str(sig.signal_role),
                    source_type=sig.source.source_type if sig.source else "unknown",
                    source_name=sig.source.name if sig.source else "unknown",
                    title=sig.title,
                    url=sig.canonical_url,
                    published_at=sig.published_at or sig.collected_at,
                    relevance_score=rel_score,
                    summary_ja=sum_ja,
                    summary_en=sum_en,
                    excerpt=exc_limit
                )
            )

        # Content Hash stable recalculation
        ev_list_for_hash = []
        for sig, rel_score, sum_en, sum_ja, _ in ev_signals:
            ev_list_for_hash.append({
                "signal_id": sig.id,
                "role": sig.signal_role.value if hasattr(sig.signal_role, "value") else str(sig.signal_role),
                "title": sig.title,
                "url": sig.canonical_url,
                "published_at": sig.published_at,
                "relevance_score": rel_score,
                "summary_ja": sum_ja,
                "summary_en": sum_en,
                "excerpt": sig.excerpt
            })
        stable_hash = calculate_opportunity_content_hash(op, ev_list_for_hash)

        # Build Detail V1
        loc_ja_status = "completed" if (op.title_ja and op.summary_ja) else "pending"
        loc_en_status = "completed" if (op.title_en and op.summary_en) else "pending"

        detail_model = PublicOpportunityDetailV1(
            schema_version="1.0.0",
            public_id=op.public_id,
            revision=op.public_revision,
            content_hash=stable_hash,
            first_published_at=op.first_published_at,
            last_published_at=op.last_published_at,
            localization=PublicOpportunityLocalizationDetailV1(
                ja=PublicOpportunityLocalizationDetailItemV1(
                    status=loc_ja_status,
                    title=op.title_ja or op.title,
                    summary=op.summary_ja or "",
                    target_user=op.target_user_ja,
                    problem=op.problem_ja,
                    current_workaround=op.current_workaround_ja,
                    existing_solution_gap=op.existing_solution_gap_ja,
                    mvp_direction=op.mvp_direction_ja,
                    why_selected=op.why_selected_ja,
                    risks=op.risks_ja
                ),
                en=PublicOpportunityLocalizationDetailItemV1(
                    status=loc_en_status,
                    title=op.title_en or op.title,
                    summary=op.summary_en or "",
                    target_user=op.target_user_en,
                    problem=op.problem_en,
                    current_workaround=op.current_workaround_en,
                    existing_solution_gap=op.existing_solution_gap_en,
                    mvp_direction=op.mvp_direction_en,
                    why_selected=op.why_selected_en,
                    risks=op.risks_en
                )
            ),
            score=PublicOpportunityScoreDetailV1(
                total=op.total_score or 0,
                evidence=op.evidence_score or 0,
                feasibility=op.feasibility_score or 0,
                penalty=op.penalty_score or 0,
                confidence=op.confidence.value if hasattr(op.confidence, "value") else str(op.confidence),
                version="v2",
                components=score_components
            ),
            gate=PublicOpportunityGateV1(
                version="v2",
                status=op.gate_status or "rejected",
                reason=op.gate_reason or ""
            ),
            evidence=mapped_evidences,
            jurypress=JuryPressReadinessV1(ready=ready, reasons=reasons)
        )

        # Write detail file
        with open(os.path.join(opps_dir, f"{op.public_id}.json"), "w") as f:
            f.write(detail_model.model_dump_json(indent=2))

        # Build Summary Item for List
        summary_model = PublicOpportunitySummaryV1(
            public_id=op.public_id,
            revision=op.public_revision,
            content_hash=stable_hash,
            first_published_at=op.first_published_at,
            last_published_at=op.last_published_at,
            localization=PublicOpportunityLocalizationListV1(
                ja=PublicOpportunityLocalizationItemV1(
                    status=loc_ja_status,
                    title=op.title_ja or op.title,
                    summary=op.summary_ja or ""
                ),
                en=PublicOpportunityLocalizationItemV1(
                    status=loc_en_status,
                    title=op.title_en or op.title,
                    summary=op.summary_en or ""
                )
            ),
            score=PublicOpportunityScoreListV1(
                total=op.total_score or 0,
                confidence=op.confidence.value if hasattr(op.confidence, "value") else str(op.confidence),
                version="v2"
            ),
            evidence=PublicOpportunityEvidenceMetricsV1(
                total=len(ev_signals),
                independent=op.independent_evidence_count,
                demand=op.demand_evidence_count,
                source_types=op.source_type_count,
                source_domains=op.source_domain_count
            ),
            jurypress=JuryPressReadinessV1(ready=ready, reasons=reasons),
            detail_url=f"{base_path}/data/v1/opportunities/{op.public_id}.json",
            html_url=f"{base_path}/opportunities/{op.public_id}/"
        )
        summary_items.append(summary_model)

        # Feed list
        if ready:
            jurypress_ready_items.append(
                JuryPressFeedItemV1(
                    public_id=op.public_id,
                    revision=op.public_revision,
                    content_hash=stable_hash,
                    score=op.total_score or 0,
                    confidence=op.confidence.value if hasattr(op.confidence, "value") else str(op.confidence),
                    title_ja=op.title_ja or op.title,
                    title_en=op.title_en or op.title,
                    detail_url=f"{base_path}/data/v1/opportunities/{op.public_id}.json"
                )
            )

    # 4. Write List JSON
    list_model = PublicOpportunityListV1(
        schema_version="1.0.0",
        generated_at=gen_time,
        count=len(summary_items),
        items=summary_items
    )
    with open(os.path.join(data_v1_dir, "opportunities.json"), "w") as f:
        f.write(list_model.model_dump_json(indent=2))

    # Calculate dataset/manifest content hash
    sorted_summaries = sorted(summary_items, key=lambda x: x.public_id)
    hash_payload = [f"{item.public_id}:{item.revision}:{item.content_hash}" for item in sorted_summaries]
    manifest_raw_str = ",".join(hash_payload)
    manifest_content_hash = hashlib.sha256(manifest_raw_str.encode("utf-8")).hexdigest()

    # 5. Write JuryPress Feed JSON
    feed_model = JuryPressFeedV1(
        schema_version="1.0.0",
        generated_at=gen_time,
        content_hash=manifest_content_hash,
        count=len(jurypress_ready_items),
        items=jurypress_ready_items
    )
    with open(os.path.join(feeds_dir, "jurypress.json"), "w") as f:
        f.write(feed_model.model_dump_json(indent=2))

    # 6. Write Manifest
    git_commit = get_git_commit()
    manifest_model = PublicManifestV1(
        contract="glintory-public-data",
        schema_version="1.0.0",
        generated_at=gen_time,
        dataset_revision=gen_time.strftime("%Y%m%dT%H%M%SZ"),
        source_commit=git_commit,
        content_hash=manifest_content_hash,
        counts=PublicManifestCountsV1(
            published_opportunities=len(summary_items),
            jurypress_ready=len(jurypress_ready_items)
        ),
        endpoints=PublicManifestEndpointsV1(
            opportunities=f"{base_path}/data/v1/opportunities.json",
            jurypress=f"{base_path}/data/v1/feeds/jurypress.json"
        )
    )
    with open(os.path.join(data_v1_dir, "manifest.json"), "w") as f:
        f.write(manifest_model.model_dump_json(indent=2))

    return {
        "published_opportunities": len(summary_items),
        "jurypress_ready": len(jurypress_ready_items),
        "manifest_content_hash": manifest_content_hash
    }
