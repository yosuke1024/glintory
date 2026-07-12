import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import desc

from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.models import (
    Opportunity,
    OpportunityPublicAlias,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
)
from glintory.domain.public_contract import (
    JuryPressFeedItemV1,
    JuryPressFeedV1,
    JuryPressReadinessV1,
    JuryPressReasonCode,
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
    to_public_completion_status,
)
from glintory.services.content_hashing import (
    calculate_opportunity_detail_canonical_hash,
)


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def evaluate_jurypress_readiness(
    op: Opportunity, ev_signals: list[Any]
) -> tuple[bool, list[str]]:
    min_score = int(os.environ.get("GLINTORY_JURYPRESS_MIN_SCORE", "60"))
    reasons: list[str] = []

    # Check scoring version first
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

    if (
        not is_stale
        and to_public_completion_status(op.enrichment_status) != "completed"
        and "ENRICHMENT_MISSING" not in reasons
    ):
        reasons.append("ENRICHMENT_MISSING")

    ja_fields = [
        op.title_ja,
        op.summary_ja,
        op.problem_ja,
        op.target_user_ja,
        op.current_workaround_ja,
        op.existing_solution_gap_ja,
        op.mvp_direction_ja,
        op.why_selected_ja,
        op.risks_ja,
    ]
    en_fields = [
        op.title_en,
        op.summary_en,
        op.problem_en,
        op.target_user_en,
        op.current_workaround_en,
        op.existing_solution_gap_en,
        op.mvp_direction_en,
        op.why_selected_en,
        op.risks_en,
    ]

    if to_public_completion_status(op.translation_status) != "completed" or any(
        f is None or len(f.strip()) == 0 for f in ja_fields
    ):
        reasons.append("JAPANESE_LOCALIZATION_MISSING")
    if to_public_completion_status(op.translation_status) != "completed" or any(
        f is None or len(f.strip()) == 0 for f in en_fields
    ):
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

    # Remove duplicates and preserve order
    seen = set()
    unique_reasons = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    ready = len(unique_reasons) == 0
    return ready, unique_reasons


def resolve_publication_lifecycle(session: Any, gen_time: datetime) -> None:
    from glintory.domain.enums import Confidence, OpportunityStatus
    from glintory.domain.models import Opportunity, OpportunitySignal

    all_db_opps = session.query(Opportunity).all()
    for op in all_db_opps:
        sig_count = (
            session.query(OpportunitySignal)
            .filter(
                OpportunitySignal.opportunity_id == op.id,
                OpportunitySignal.is_excluded.is_(False),
            )
            .count()
        )

        is_active_candidate = (
            op.current_scoring_version == "v2"
            and op.public_lifecycle not in ("merged", "retired")
            and sig_count > 0
            and (
                (
                    op.status == OpportunityStatus.INBOX
                    and op.gate_status == "passed"
                    and op.confidence in (Confidence.MEDIUM, Confidence.HIGH)
                )
                or (op.status == OpportunityStatus.RESEARCH)
            )
        )

        if is_active_candidate:
            op.public_lifecycle = "active"
        else:
            was_previously_published = (
                op.first_published_at is not None or op.public_content_hash is not None
            )
            if was_previously_published and op.public_lifecycle != "merged":
                if op.public_lifecycle != "retired":
                    op.public_lifecycle = "retired"
                    op.retired_at = gen_time
                    if op.current_scoring_version != "v2":
                        op.retired_reason = "SCORING_VERSION_CHANGED"
                    elif op.confidence == Confidence.LOW:
                        op.retired_reason = "CONFIDENCE_LOW"
                    elif op.status in (
                        OpportunityStatus.REJECTED,
                        OpportunityStatus.ARCHIVED,
                    ):
                        op.retired_reason = "STATUS_EXCLUDED"
                    else:
                        op.retired_reason = "CLUSTERING_EXCLUDED"
            elif op.public_lifecycle != "merged":
                op.public_lifecycle = "unregistered"
    session.flush()


def select_active_public_opportunities(session: Any) -> list[Opportunity]:
    from glintory.domain.enums import Confidence, OpportunityStatus
    from glintory.domain.models import Opportunity

    return (
        session.query(Opportunity)
        .filter(
            Opportunity.current_scoring_version == "v2",
            Opportunity.public_lifecycle == "active",
            (
                (Opportunity.status == OpportunityStatus.INBOX)
                & (Opportunity.gate_status == "passed")
                & (Opportunity.confidence.in_([Confidence.MEDIUM, Confidence.HIGH]))
            )
            | (Opportunity.status == OpportunityStatus.RESEARCH),
        )
        .order_by(
            Opportunity.total_score.desc(),
            Opportunity.last_scored_at.desc(),
            Opportunity.id.desc(),
        )
        .all()
    )


def generate_public_contract(
    session: Any,
    temp_build_dir: str,
    base_path: str = "",
    site_url: str = "",
    gen_time: datetime | None = None,
) -> dict:
    from glintory.domain.models import Opportunity

    if gen_time is None:
        gen_time = datetime.now(UTC)

    data_v1_dir = os.path.join(temp_build_dir, "data", "v1")
    opps_dir = os.path.join(data_v1_dir, "opportunities")
    feeds_dir = os.path.join(data_v1_dir, "feeds")
    schemas_dir = os.path.join(data_v1_dir, "schemas")

    os.makedirs(opps_dir, exist_ok=True)
    os.makedirs(feeds_dir, exist_ok=True)
    os.makedirs(schemas_dir, exist_ok=True)

    # 2. Write JSON Schema files
    schema_map = {
        "manifest.schema.json": PublicManifestV1,
        "opportunity-list.schema.json": PublicOpportunityListV1,
        "opportunity-detail.schema.json": PublicOpportunityDetailV1,
        "jurypress-feed.schema.json": JuryPressFeedV1,
    }
    for filename, model in schema_map.items():
        with open(os.path.join(schemas_dir, filename), "w") as f:
            json.dump(model.model_json_schema(), f, indent=2, ensure_ascii=False)

    # 3. Retrieve all opportunities (resolved by resolve_publication_lifecycle beforehand)
    all_db_opps = session.query(Opportunity).all()

    summary_items = []
    jurypress_ready_items = []

    # Query active & retired ones to output
    sorted_opps = sorted(
        [o for o in all_db_opps if o.public_lifecycle in ("active", "retired")],
        key=lambda o: o.public_id,
    )

    for op in sorted_opps:
        # Load evidence signals
        ev_signals = (
            session.query(
                Signal,
                OpportunitySignal.relevance_score,
                OpportunitySignal.evidence_summary_en,
                OpportunitySignal.evidence_summary_ja,
                OpportunitySignal.relation_type,
            )
            .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
            .filter(OpportunitySignal.opportunity_id == op.id)
            .filter(OpportunitySignal.is_excluded.is_(False))
            .all()
        )

        # Sort signals: relevance_score DESC, published_at ASC, signal_id ASC
        def get_ev_sort_key(ev_info):
            sig = ev_info[0]
            rel_score = ev_info[1]
            pub_at = sig.published_at or sig.collected_at
            pub_at_str = (
                pub_at.isoformat()
                if isinstance(pub_at, datetime)
                else str(pub_at or "")
            )
            return (-float(rel_score or 0.0), pub_at_str, sig.id)

        sorted_ev_signals = sorted(ev_signals, key=get_ev_sort_key)

        ready, reasons = evaluate_jurypress_readiness(op, sorted_ev_signals)

        mapped_evidences = []
        for sig, rel_score, sum_en, sum_ja, _ in sorted_ev_signals:
            exc = sig.excerpt or ""
            exc_limit = exc[:500] if exc else None
            mapped_evidences.append(
                PublicEvidenceV1(
                    signal_id=sig.id,
                    role=cast(
                        Literal["demand", "supply", "context", "unknown"],
                        sig.signal_role.value
                        if hasattr(sig.signal_role, "value")
                        else str(sig.signal_role),
                    ),
                    source_type=sig.source.source_type if sig.source else "unknown",
                    source_name=sig.source.name if sig.source else "unknown",
                    title=sig.title,
                    url=sig.canonical_url,
                    published_at=sig.published_at or sig.collected_at,
                    relevance_score=rel_score,
                    summary_ja=sum_ja,
                    summary_en=sum_en,
                    excerpt=exc_limit,
                )
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
                                name=c.get("name", ""),
                                score=c.get("score", 0),
                                maximum=c.get("max", 0),
                                explanation=c.get("reason", ""),
                            )
                        )

        # Sort score components by name ASC for deterministic details
        score_components.sort(key=lambda c: c.name)

        # Localization Status logic
        if to_public_completion_status(op.translation_status) == "failed":
            loc_ja_status = "failed"
        elif (
            to_public_completion_status(op.translation_status) == "completed"
            and op.title_ja
            and op.summary_ja
        ):
            loc_ja_status = "completed"
        else:
            loc_ja_status = "pending"

        if to_public_completion_status(op.translation_status) == "failed":
            loc_en_status = "failed"
        elif (
            to_public_completion_status(op.translation_status) == "completed"
            and op.title_en
            and op.summary_en
        ):
            loc_en_status = "completed"
        else:
            loc_en_status = "pending"

        stage = "research" if op.status == OpportunityStatus.RESEARCH else "published"

        detail_model = PublicOpportunityDetailV1(
            public_id=op.public_id,
            public_lifecycle=cast(
                Literal["active", "merged", "retired"], op.public_lifecycle
            ),
            stage=cast(Literal["published", "research"], stage),
            revision=op.public_revision or 1,
            content_hash="",
            first_published_at=op.first_published_at,
            last_published_at=op.last_published_at,
            localization=PublicOpportunityLocalizationDetailV1(
                ja=PublicOpportunityLocalizationDetailItemV1(
                    status=cast(
                        Literal["pending", "completed", "failed"], loc_ja_status
                    ),
                    title=op.title_ja,
                    summary=op.summary_ja,
                    target_user=op.target_user_ja,
                    problem=op.problem_ja,
                    current_workaround=op.current_workaround_ja,
                    existing_solution_gap=op.existing_solution_gap_ja,
                    mvp_direction=op.mvp_direction_ja,
                    why_selected=op.why_selected_ja,
                    risks=op.risks_ja,
                ),
                en=PublicOpportunityLocalizationDetailItemV1(
                    status=cast(
                        Literal["pending", "completed", "failed"], loc_en_status
                    ),
                    title=op.title_en,
                    summary=op.summary_en,
                    target_user=op.target_user_en,
                    problem=op.problem_en,
                    current_workaround=op.current_workaround_en,
                    existing_solution_gap=op.existing_solution_gap_en,
                    mvp_direction=op.mvp_direction_en,
                    why_selected=op.why_selected_en,
                    risks=op.risks_en,
                ),
            )
            if op.public_lifecycle == "active"
            else None,
            score=PublicOpportunityScoreDetailV1(
                total=op.total_score or 0,
                evidence=op.evidence_score or 0,
                feasibility=op.feasibility_score or 0,
                penalty=op.penalty_score or 0,
                confidence=cast(
                    Literal["low", "medium", "high"],
                    op.confidence.value
                    if hasattr(op.confidence, "value")
                    else str(op.confidence),
                ),
                components=score_components,
                independent_evidence_count=op.independent_evidence_count,
                demand_evidence_count=op.demand_evidence_count,
            )
            if op.public_lifecycle == "active"
            else None,
            gate=PublicOpportunityGateV1(
                version=cast(Literal["v2", "v3"], op.gate_version or "v3"),
                status=cast(
                    Literal["passed", "rejected", "failed"],
                    op.gate_status or "rejected",
                ),
                reason=op.gate_reason or "",
            )
            if op.public_lifecycle == "active"
            else None,
            evidence=mapped_evidences if op.public_lifecycle == "active" else None,
            jurypress=JuryPressReadinessV1(
                ready=ready, reasons=[cast(JuryPressReasonCode, r) for r in reasons]
            )
            if op.public_lifecycle == "active"
            else None,
            enrichment_status=to_public_completion_status(op.enrichment_status)
            if op.public_lifecycle == "active"
            else None,
            translation_status=to_public_completion_status(op.translation_status)
            if op.public_lifecycle == "active"
            else None,
            retired_at=(
                op.retired_at
                if op.public_lifecycle == "retired" and hasattr(op, "retired_at")
                else (op.updated_at if op.public_lifecycle == "retired" else None)
            ),
            retired_reason=op.retired_reason
            if op.public_lifecycle == "retired" and hasattr(op, "retired_reason")
            else (
                "retired"
                if op.public_lifecycle == "retired"
                else None
            ),
        )

        stable_hash = calculate_opportunity_detail_canonical_hash(detail_model)

        # Update revision and content hash in DB if changed or initial publish
        if op.public_content_hash is None:
            op.public_revision = 1
            op.public_content_hash = stable_hash
            op.first_published_at = gen_time
            op.last_published_at = gen_time
        elif op.public_content_hash != stable_hash:
            op.public_revision += 1
            op.public_content_hash = stable_hash
            op.last_published_at = gen_time

        # Update model with finalized DB hash and revision values
        detail_model.content_hash = op.public_content_hash
        detail_model.revision = op.public_revision
        detail_model.first_published_at = op.first_published_at
        detail_model.last_published_at = op.last_published_at

        # Write detail file
        with open(os.path.join(opps_dir, f"{op.public_id}.json"), "w") as f:
            f.write(detail_model.model_dump_json(indent=2))

        # Build Summary Item for List (only for active opportunities)
        if op.public_lifecycle == "active":
            summary_model = PublicOpportunitySummaryV1(
                public_id=op.public_id,
                public_lifecycle=cast(
                    Literal["active", "merged", "retired"], op.public_lifecycle
                ),
                stage=cast(Literal["published", "research"], stage),
                revision=op.public_revision,
                content_hash=op.public_content_hash,
                first_published_at=op.first_published_at,
                last_published_at=op.last_published_at,
                localization=PublicOpportunityLocalizationListV1(
                    ja=PublicOpportunityLocalizationItemV1(
                        status=cast(
                            Literal["pending", "completed", "failed"], loc_ja_status
                        ),
                        title=op.title_ja,
                        summary=op.summary_ja,
                    ),
                    en=PublicOpportunityLocalizationItemV1(
                        status=cast(
                            Literal["pending", "completed", "failed"], loc_en_status
                        ),
                        title=op.title_en,
                        summary=op.summary_en,
                    ),
                ),
                score=PublicOpportunityScoreListV1(
                    total=op.total_score or 0,
                    confidence=cast(
                        Literal["low", "medium", "high"],
                        op.confidence.value
                        if hasattr(op.confidence, "value")
                        else str(op.confidence),
                    ),
                ),
                evidence=PublicOpportunityEvidenceMetricsV1(
                    total=len(ev_signals),
                    independent=op.independent_evidence_count,
                    demand=op.demand_evidence_count,
                    source_types=op.source_type_count,
                    source_domains=op.source_domain_count,
                ),
                jurypress=JuryPressReadinessV1(
                    ready=ready, reasons=[cast(JuryPressReasonCode, r) for r in reasons]
                ),
                detail_url=f"{base_path}/data/v1/opportunities/{op.public_id}.json",
                html_url=f"{base_path}/opportunities/{op.public_id}/",
            )
            summary_items.append(summary_model)

            # Feed list item for JuryPress
            if ready and stage == "published":
                jurypress_ready_items.append(
                    JuryPressFeedItemV1(
                        public_id=op.public_id,
                        revision=op.public_revision,
                        content_hash=op.public_content_hash,
                        score=op.total_score or 0,
                        confidence=cast(
                            Literal["low", "medium", "high"],
                            op.confidence.value
                            if hasattr(op.confidence, "value")
                            else str(op.confidence),
                        ),
                        title_ja=op.title_ja or "",
                        title_en=op.title_en or "",
                        detail_url=f"{base_path}/data/v1/opportunities/{op.public_id}.json",
                    )
                )

    # 4. Generate Merged Detail JSONs (Aliases)
    aliases = session.query(OpportunityPublicAlias).all()
    for alias in aliases:
        merged_model = PublicOpportunityDetailV1(
            public_id=alias.old_public_id,
            public_lifecycle="merged",
            revision=1,
            content_hash="",
            canonical_public_id=alias.canonical_public_id,
            canonical_detail_url=f"{base_path}/data/v1/opportunities/{alias.canonical_public_id}.json",
        )
        stable_hash = calculate_opportunity_detail_canonical_hash(merged_model)
        merged_model.content_hash = stable_hash
        with open(os.path.join(opps_dir, f"{alias.old_public_id}.json"), "w") as f:
            f.write(merged_model.model_dump_json(indent=2))

    session.flush()

    # 5. Write List JSON
    list_model = PublicOpportunityListV1(
        schema_version="1.1.0",
        generated_at=gen_time,
        count=len(summary_items),
        items=summary_items,
    )
    with open(os.path.join(data_v1_dir, "opportunities.json"), "w") as f:
        f.write(list_model.model_dump_json(indent=2))

    # Calculate dataset/manifest content hash on the entire Canonical Dataset
    dataset = {
        "opportunities": [],
        "jurypress_feed": [],
        "aliases": [],
        "tombstones": [],
    }

    # Populate active opportunities
    for item in sorted(summary_items, key=lambda x: x.public_id):
        dataset["opportunities"].append(
            {
                "public_id": item.public_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
                "public_lifecycle": item.public_lifecycle,
                "stage": item.stage,
                "jurypress_ready": item.jurypress.ready,
                "jurypress_reasons": [str(r) for r in item.jurypress.reasons],
            }
        )

    # Populate JuryPress ready feed items
    for item in sorted(jurypress_ready_items, key=lambda x: x.public_id):
        dataset["jurypress_feed"].append(
            {
                "public_id": item.public_id,
                "revision": item.revision,
                "content_hash": item.content_hash,
            }
        )

    # Populate aliases (merged)
    for alias in sorted(aliases, key=lambda x: x.old_public_id):
        dataset["aliases"].append(
            {
                "old_public_id": alias.old_public_id,
                "canonical_public_id": alias.canonical_public_id,
            }
        )

    # Populate retired (tombstones)
    retired_items = [o for o in sorted_opps if o.public_lifecycle == "retired"]
    for op in sorted(retired_items, key=lambda x: x.public_id):
        dataset["tombstones"].append(
            {
                "public_id": op.public_id,
                "revision": op.public_revision,
                "content_hash": op.public_content_hash,
                "retired_at": (
                    op.retired_at.isoformat()
                    if hasattr(op, "retired_at") and isinstance(op.retired_at, datetime)
                    else (
                        op.updated_at.isoformat()
                        if isinstance(op.updated_at, datetime)
                        else str(op.updated_at)
                    )
                ),
                "retired_reason": op.retired_reason
                if hasattr(op, "retired_reason")
                else "retired",
            }
        )

    # Serialize to deterministic JSON
    dataset_serialized = json.dumps(
        dataset, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    manifest_content_hash = hashlib.sha256(
        dataset_serialized.encode("utf-8")
    ).hexdigest()

    # Sort JuryPress Feed items by score DESC, then public_id ASC for deterministic feed
    sorted_jurypress_ready_items = sorted(
        jurypress_ready_items, key=lambda item: (-item.score, item.public_id)
    )

    # 6. Write JuryPress Feed JSON
    feed_model = JuryPressFeedV1(
        schema_version="1.1.0",
        generated_at=gen_time,
        content_hash=manifest_content_hash,
        count=len(sorted_jurypress_ready_items),
        items=sorted_jurypress_ready_items,
    )
    with open(os.path.join(feeds_dir, "jurypress.json"), "w") as f:
        f.write(feed_model.model_dump_json(indent=2))

    # 7. Write Manifest
    git_commit = get_git_commit()
    manifest_model = PublicManifestV1(
        contract="glintory-public-data",
        schema_version="1.1.0",
        generated_at=gen_time,
        dataset_revision=gen_time.strftime("%Y%m%dT%H%M%SZ"),
        source_commit=git_commit,
        content_hash=manifest_content_hash,
        counts=PublicManifestCountsV1(
            published_opportunities=len(
                [x for x in summary_items if x.stage == "published"]
            ),
            research_candidates=len(
                [x for x in summary_items if x.stage == "research"]
            ),
            jurypress_ready=len(sorted_jurypress_ready_items),
        ),
        endpoints=PublicManifestEndpointsV1(
            opportunities=f"{base_path}/data/v1/opportunities.json",
            jurypress=f"{base_path}/data/v1/feeds/jurypress.json",
        ),
    )
    with open(os.path.join(data_v1_dir, "manifest.json"), "w") as f:
        f.write(manifest_model.model_dump_json(indent=2))

    return {
        "published_opportunities": len(
            [x for x in summary_items if x.stage == "published"]
        ),
        "research_candidates": len([x for x in summary_items if x.stage == "research"]),
        "jurypress_ready": len(sorted_jurypress_ready_items),
        "manifest_content_hash": manifest_content_hash,
    }
