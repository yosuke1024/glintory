import contextlib
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.enrichment_contract import PROMPT_VERSION, SCHEMA_VERSION
from glintory.domain.enums import OpportunityStatus, SignalType
from glintory.domain.models import (
    Opportunity,
    OpportunitySignal,
    ScheduleExecution,
    Signal,
    Source,
    SourceSchedule,
)
from glintory.infrastructure.opportunity_enrichment_repository import (
    OpportunityEnrichmentRepository,
)
from glintory.infrastructure.opportunity_query import check_stale
from glintory.services.publishing_templates import (
    CSS_CONTENT,
    DETAIL_TEMPLATE,
    DIAGNOSTICS_TEMPLATE,
    INDEX_TEMPLATE,
    LIST_TEMPLATE,
)


def validate_site_url(url: str | None) -> str:
    if not url:
        raise ValueError("SITE_URL_REQUIRED")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("INVALID_SITE_URL_SCHEME")
    if not parsed.netloc:
        raise ValueError("INVALID_SITE_URL_NETLOC")
    if parsed.username or parsed.password:
        raise ValueError("INVALID_SITE_URL_CREDENTIALS")
    if parsed.query:
        raise ValueError("INVALID_SITE_URL_QUERY")
    if parsed.fragment:
        raise ValueError("INVALID_SITE_URL_FRAGMENT")
    return url


def safe_url(url: str | None) -> str:
    if not url:
        return "#"
    url_lower = url.lower().strip()
    if url_lower.startswith(("http://", "https://")):
        return url
    return "#"


def format_datetime(val: Any) -> str:
    if not val:
        return "N/A"
    if isinstance(val, str):
        return val
    try:
        from datetime import timedelta, timezone

        # Convert to JST (UTC+9)
        jst = timezone(timedelta(hours=9))
        val_jst = val.astimezone(jst)
        return val_jst.strftime("%Y-%m-%d %H:%M JST")
    except Exception:
        return val.isoformat()


def get_localized_data(
    op: Opportunity,
    locale: str,
) -> dict:
    if locale == "ja":
        return {
            "title": op.title_ja,
            "summary": op.summary_ja,
            "problem": op.problem_ja,
            "target_user": op.target_user_ja,
            "current_workaround": op.current_workaround_ja,
            "existing_solution_gap": op.existing_solution_gap_ja,
            "mvp_direction": op.mvp_direction_ja,
            "why_selected": op.why_selected_ja,
            "risks": op.risks_ja,
        }
    # "en" or fallback
    return {
        "title": op.title_en or op.title,
        "summary": op.summary_en or op.proposed_solution,
        "problem": op.problem_en,
        "target_user": op.target_user_en,
        "current_workaround": op.current_workaround_en,
        "existing_solution_gap": op.existing_solution_gap_en,
        "mvp_direction": op.mvp_direction_en,
        "why_selected": op.why_selected_en,
        "risks": op.risks_en,
    }


def calculate_current_hash(op_id: str, score_hash: str | None, ev_signals: list) -> str:
    sorted_ev = sorted(ev_signals, key=lambda e: e[0].id)
    ev_hash_strs = []
    for item in sorted_ev:
        sig, rel_score = item[0], item[1]
        ev_hash_strs.append(f"{sig.id}:{sig.content_hash}:{rel_score}")

    parts = [
        op_id,
        score_hash or "",
        ",".join(ev_hash_strs),
        settings.local_llm_model_file,
        settings.local_llm_model_revision,
        PROMPT_VERSION,
        SCHEMA_VERSION,
    ]
    raw_str = "|".join(parts)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def build_static_site(
    session: Session,
    output_dir: str,
    base_path: str = "",
    site_url: str | None = None,
    pixapps_url: str | None = None,
    generated_at: datetime | None = None,
) -> dict:
    from glintory.domain.models import OpportunityPublicAlias, PublishingRun
    from glintory.domain.public_contract import (
        is_enrichment_complete,
        is_translation_complete,
    )
    from glintory.services.public_contract_generator import (
        generate_public_contract,
        resolve_publication_lifecycle,
        select_active_public_opportunities,
    )

    valid_site_url = validate_site_url(site_url)
    gen_time = (generated_at or datetime.now(UTC)).replace(tzinfo=None)
    session.expire_all()
    resolve_publication_lifecycle(session, gen_time)

    # 1. Start PublishingRun auditing
    pub_run = PublishingRun(
        started_at=gen_time,
        status="running",
        published_count=0,
        jurypress_ready_count=0,
        dataset_content_hash="",
    )
    session.add(pub_run)
    session.flush()

    if base_path:
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        if base_path.endswith("/"):
            base_path = base_path[:-1]
    else:
        base_path = ""

    target_parent = os.path.dirname(os.path.abspath(output_dir))
    if target_parent:
        os.makedirs(target_parent, exist_ok=True)

    temp_build_dir = os.path.join(target_parent, f".tmp-build-{uuid.uuid4().hex}")
    os.makedirs(temp_build_dir, exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "opportunities"), exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "assets"), exist_ok=True)

    try:
        sources = session.query(Source).all()
        schedules = session.query(SourceSchedule).all()

        latest_exec = (
            session.query(ScheduleExecution)
            .order_by(ScheduleExecution.started_at.desc(), ScheduleExecution.id.desc())
            .first()
        )

        opportunities = select_active_public_opportunities(session)
        from glintory.domain.enums import Confidence
        top_ops = [
            o for o in opportunities
            if o.gate_status == "passed"
            and o.status == OpportunityStatus.INBOX
            and o.confidence in (Confidence.MEDIUM, Confidence.HIGH)
        ][:5]

        # All display opportunities for lists and detail pages
        all_display_opps = (
            session.query(Opportunity)
            .filter(
                Opportunity.current_scoring_version == "v2",
                (
                    Opportunity.public_lifecycle.in_(["active", "retired"])
                    | (Opportunity.status == OpportunityStatus.REJECTED)
                )
            )
            .order_by(
                Opportunity.total_score.desc(),
                Opportunity.last_scored_at.desc(),
                Opportunity.id.desc(),
            )
            .all()
        )

        signals_data = (
            session.query(Signal, Source.name, Source.source_type)
            .join(Source, Signal.source_id == Source.id)
            .order_by(
                Signal.created_at.desc(),
                Signal.id.desc(),
            )
            .limit(20)
            .all()
        )

        latest_signals = []
        for sig, src_name, src_type in signals_data[:10]:
            latest_signals.append(
                {
                    "title": sig.title,
                    "canonical_url": sig.canonical_url,
                    "source_name": src_name,
                    "source_type": src_type,
                    "published_at": sig.published_at
                    if sig.published_at
                    else sig.collected_at,
                }
            )

        # Generate legacy JSON for backwards compatibility
        latest_json_data = {
            "generated_at": gen_time.isoformat(),
            "scheduler": {
                "last_run": latest_exec.started_at.isoformat() if latest_exec else None,
                "status": latest_exec.status if latest_exec else "inactive",
            },
            "stats": {
                "total_sources": len(sources),
                "active_sources": len([s for s in schedules if s.enabled]),
                "total_opportunities": len(opportunities),
            },
            "top_opportunities": [
                {
                    "id": o.id,
                    "title_ja": o.title_ja,
                    "title_en": o.title_en or o.title,
                    "summary_ja": o.summary_ja,
                    "summary_en": o.summary_en or o.proposed_solution,
                    "translation_status": o.translation_status or "pending",
                    "total_score": o.total_score,
                    "confidence": o.confidence.value
                    if hasattr(o.confidence, "value")
                    else str(o.confidence),
                }
                for o in top_ops
            ],
        }
        with open(os.path.join(temp_build_dir, "data", "latest.json"), "w") as f:
            json.dump(latest_json_data, f, indent=2)

        ops_json_list = []
        for o in opportunities:
            ops_json_list.append(
                {
                    "id": o.id,
                    "title_ja": o.title_ja,
                    "title_en": o.title_en or o.title,
                    "summary_ja": o.summary_ja,
                    "summary_en": o.summary_en or o.proposed_solution,
                    "translation_status": o.translation_status or "pending",
                    "total_score": o.total_score,
                    "confidence": o.confidence.value
                    if hasattr(o.confidence, "value")
                    else str(o.confidence),
                    "status": o.status.value
                    if hasattr(o.status, "value")
                    else str(o.status),
                    "last_scored_at": o.last_scored_at.isoformat()
                    if o.last_scored_at
                    else None,
                }
            )
        with open(os.path.join(temp_build_dir, "data", "opportunities.json"), "w") as f:
            json.dump(ops_json_list, f, indent=2)

        # Generate versioned JSON feeds and JSON schemas
        contract_res = generate_public_contract(
            session=session,
            temp_build_dir=temp_build_dir,
            base_path=base_path,
            site_url=valid_site_url,
            gen_time=gen_time,
        )

        with open(os.path.join(temp_build_dir, "assets", "app.css"), "w") as f:
            f.write(CSS_CONTENT.strip())

        repo_url = os.environ.get("GLINTORY_REPOSITORY_URL")

        env = Environment(autoescape=select_autoescape(["html", "xml"]))
        env.filters["safe_url"] = safe_url
        env.filters["format_datetime"] = format_datetime

        index_template = env.from_string(INDEX_TEMPLATE)
        list_template = env.from_string(LIST_TEMPLATE)
        detail_template = env.from_string(DETAIL_TEMPLATE)

        active_sources = [s for s in schedules if s.enabled]
        total_sources_count = len(sources)
        active_sources_count = len(active_sources)

        rendered_index = index_template.render(
            base_path=base_path,
            pixapps_url=pixapps_url,
            latest_exec_time=latest_exec.started_at if latest_exec else None,
            latest_exec_status=latest_exec.status if latest_exec else "inactive",
            active_sources_count=active_sources_count,
            total_sources_count=total_sources_count,
            total_ops_count=len(opportunities),
            top_ops=top_ops,
            latest_signals=latest_signals,
            repo_url=repo_url,
        )
        with open(os.path.join(temp_build_dir, "index.html"), "w") as f:
            f.write(rendered_index)

        op_list_data = []
        for op in all_display_opps:
            ev_count = (
                session.query(OpportunitySignal)
                .filter(OpportunitySignal.opportunity_id == op.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .count()
            )
            # Calculate display stage/status
            if op.status == OpportunityStatus.REJECTED:
                stage = "rejected"
            elif op.gate_status == "passed" and op.status != OpportunityStatus.RESEARCH:
                stage = "published"
            else:
                stage = "research"

            op_list_data.append(
                {
                    "op": op,
                    "evidence_count": ev_count,
                    "evidence_updated_at": op.evidence_updated_at,
                    "last_scored_at": op.last_scored_at,
                    "stage": stage,
                }
            )

        rendered_list = list_template.render(
            base_path=base_path,
            op_list_data=op_list_data,
            repo_url=repo_url,
        )
        with open(
            os.path.join(temp_build_dir, "opportunities", "index.html"), "w"
        ) as f:
            f.write(rendered_list)

        enrich_repo = OpportunityEnrichmentRepository(session)
        for op in all_display_opps:
            ev_signals = (
                session.query(
                    Signal,
                    OpportunitySignal.relevance_score,
                    OpportunitySignal.evidence_summary_en,
                    OpportunitySignal.evidence_summary_ja,
                    Source.name,
                    Source.source_type,
                )
                .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
                .join(Source, Signal.source_id == Source.id)
                .filter(OpportunitySignal.opportunity_id == op.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .order_by(OpportunitySignal.relevance_score.desc())
                .all()
            )

            evidences = []
            for sig, rel_score, sum_en, sum_ja, src_name, src_type in ev_signals:
                from glintory.domain.enums import SignalRole

                role_val = sig.signal_role
                role_str = "不明"
                if role_val == SignalRole.DEMAND:
                    role_str = "需要"
                elif role_val == SignalRole.SUPPLY:
                    role_str = "供給"
                elif role_val == SignalRole.CONTEXT:
                    role_str = "背景情報"

                evidences.append(
                    {
                        "id": sig.id,
                        "title": sig.title,
                        "url": sig.canonical_url,
                        "excerpt": sig.excerpt,
                        "source_name": src_name,
                        "source_type": src_type,
                        "published_at": sig.published_at
                        if sig.published_at
                        else sig.collected_at,
                        "relevance_score": rel_score,
                        "summary_en": sum_en,
                        "summary_ja": sum_ja,
                        "signal_role": role_str,
                    }
                )

            score_is_stale = check_stale(
                op.current_scoring_version,
                op.last_scored_at,
                op.evidence_updated_at,
            )

            enrichment = enrich_repo.get_latest_successful_enrichment(op.id)

            # Determine LLM stale status based on computed input hash
            llm_is_stale = False
            if enrichment:
                # Find the latest score snapshot to get its input hash
                from sqlalchemy import desc

                from glintory.domain.models import ScoreSnapshot

                snapshots = (
                    session.query(ScoreSnapshot)
                    .filter(ScoreSnapshot.opportunity_id == op.id)
                    .order_by(desc(ScoreSnapshot.created_at))
                    .all()
                )
                score_hash = snapshots[0].input_hash if snapshots else None

                current_llm_hash = calculate_current_hash(op.id, score_hash, ev_signals)
                if enrichment.input_hash != current_llm_hash:
                    llm_is_stale = True

            # Render Japanese (Default Detailed Page)
            loc_data_ja = get_localized_data(op, "ja")
            translation_available_ja = (
                is_translation_complete(op.translation_status)
                and op.title_ja is not None
                and len(op.title_ja.strip()) > 0
                and op.summary_ja is not None
                and len(op.summary_ja.strip()) > 0
                and enrichment is not None
            )
            translation_fallback_ja = not translation_available_ja

            rendered_detail_ja = detail_template.render(
                base_path=base_path,
                op=op,
                evidences=evidences,
                score_is_stale=score_is_stale,
                llm_is_stale=llm_is_stale,
                repo_url=repo_url,
                enrichment=enrichment,
                locale="ja",
                loc_data=loc_data_ja,
                translation_available=translation_available_ja,
                translation_fallback=translation_fallback_ja,
            )

            op_dir = os.path.join(temp_build_dir, "opportunities", op.public_id)
            os.makedirs(op_dir, exist_ok=True)
            with open(os.path.join(op_dir, "index.html"), "w") as f:
                f.write(rendered_detail_ja)

            # Render English (Locale specific page)
            loc_data_en = get_localized_data(op, "en")
            translation_available_en = op.title_en is not None
            translation_fallback_en = not translation_available_en

            rendered_detail_en = detail_template.render(
                base_path=base_path,
                op=op,
                evidences=evidences,
                score_is_stale=score_is_stale,
                llm_is_stale=llm_is_stale,
                repo_url=repo_url,
                enrichment=enrichment,
                locale="en",
                loc_data=loc_data_en,
                translation_available=translation_available_en,
                translation_fallback=translation_fallback_en,
            )

            op_dir_en = os.path.join(op_dir, "en")
            os.makedirs(op_dir_en, exist_ok=True)
            with open(os.path.join(op_dir_en, "index.html"), "w") as f:
                f.write(rendered_detail_en)

        # Generate Redirect HTMLs for backwards compatibility
        # 1. From OpportunityPublicAlias table
        aliases = session.query(OpportunityPublicAlias).all()
        for alias in aliases:
            redir_dir = os.path.join(
                temp_build_dir, "opportunities", alias.old_public_id
            )
            os.makedirs(redir_dir, exist_ok=True)
            redir_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Redirecting...</title>
  <meta http-equiv="refresh" content="0; url={base_path}/opportunities/{alias.canonical_public_id}/">
  <link rel="canonical" href="{valid_site_url.rstrip("/")}{base_path}/opportunities/{alias.canonical_public_id}/">
</head>
<body>
  <p>Redirecting to <a href="{base_path}/opportunities/{alias.canonical_public_id}/">{alias.canonical_public_id}</a>...</p>
</body>
</html>"""
            with open(os.path.join(redir_dir, "index.html"), "w") as f:
                f.write(redir_html)

        # 2. From old UUIDs (op.id) to op.public_id
        for op in opportunities:
            if op.id != op.public_id:
                redir_dir = os.path.join(temp_build_dir, "opportunities", op.id)
                os.makedirs(redir_dir, exist_ok=True)
                redir_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Redirecting...</title>
  <meta http-equiv="refresh" content="0; url={base_path}/opportunities/{op.public_id}/">
  <link rel="canonical" href="{valid_site_url.rstrip("/")}{base_path}/opportunities/{op.public_id}/">
</head>
<body>
  <p>Redirecting to <a href="{base_path}/opportunities/{op.public_id}/">{op.public_id}</a>...</p>
</body>
</html>"""
                with open(os.path.join(redir_dir, "index.html"), "w") as f:
                    f.write(redir_html)

        # Render diagnostics page
        import sqlalchemy as sa

        from glintory.domain.enums import (
            CollectionRunStatus,
            Confidence,
            EvidenceRelationType,
        )
        from glintory.domain.models import CollectionRun

        # Load all v2 opportunities with their signals and sources for representative source type analysis
        v2_opps = (
            session.query(Opportunity)
            .filter(Opportunity.current_scoring_version == "v2")
            .all()
        )

        opp_rep_sources = {}
        for opp in v2_opps:
            links = (
                session.query(OpportunitySignal, Signal, Source.source_type)
                .join(Signal, OpportunitySignal.signal_id == Signal.id)
                .join(Source, Signal.source_id == Source.id)
                .filter(OpportunitySignal.opportunity_id == opp.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .all()
            )

            rep_source_type = None
            centroid_sigs = [
                (sig, source_type)
                for opp_sig, sig, source_type in links
                if opp_sig.relation_type
                in (EvidenceRelationType.SUPPORTING, EvidenceRelationType.RELATED)
            ]
            if centroid_sigs:
                sorted_sigs = sorted(
                    centroid_sigs, key=lambda x: (x[0].collected_at, x[0].id)
                )
                rep_source_type = sorted_sigs[0][1]
            opp_rep_sources[opp.id] = rep_source_type

        # Check if an opportunity is published based on strict rules
        def is_opp_published(opp):
            if opp.current_scoring_version != "v2":
                return False
            if opp.gate_status != "passed":
                return False
            if opp.status == OpportunityStatus.REJECTED:
                return False
            # Require completion of enrichment and translation
            return (
                opp.status == OpportunityStatus.INBOX
                and opp.confidence in (Confidence.MEDIUM, Confidence.HIGH)
                and is_enrichment_complete(opp.enrichment_status)
                and is_translation_complete(opp.translation_status)
            )

        # Compile pipeline stats by Source Type
        track_types = ["github", "hackernews", "rss"]
        pipeline_stats = {}
        for stype in track_types:
            enabled_count = (
                session.query(SourceSchedule)
                .join(Source, SourceSchedule.source_id == Source.id)
                .filter(
                    Source.source_type == stype,
                    SourceSchedule.enabled.is_(True),
                    Source.enabled.is_(True),
                )
                .count()
            )
            last_run_exec = (
                session.query(ScheduleExecution)
                .join(Source, ScheduleExecution.source_id == Source.id)
                .filter(Source.source_type == stype)
                .order_by(ScheduleExecution.started_at.desc())
                .first()
            )
            last_run_time = last_run_exec.started_at if last_run_exec else None

            collection_runs = (
                session.query(CollectionRun)
                .join(Source, CollectionRun.source_id == Source.id)
                .filter(Source.source_type == stype)
                .count()
            )

            run_sums = (
                session.query(
                    sa.func.sum(CollectionRun.fetched_count),
                    sa.func.sum(CollectionRun.inserted_count),
                    sa.func.sum(CollectionRun.updated_count),
                    sa.func.sum(CollectionRun.skipped_count),
                    sa.func.sum(
                        sa.case(
                            (CollectionRun.status == CollectionRunStatus.FAILED, 1),
                            else_=0,
                        )
                    ),
                )
                .join(Source, CollectionRun.source_id == Source.id)
                .filter(Source.source_type == stype)
                .first()
            )
            fetched_sum = int(run_sums[0] or 0) if run_sums else 0
            inserted_sum = int(run_sums[1] or 0) if run_sums else 0
            updated_sum = int(run_sums[2] or 0) if run_sums else 0
            persisted_sum = inserted_sum + updated_sum
            skipped_sum = int(run_sums[3] or 0) if run_sums else 0
            failed_sum = int(run_sums[4] or 0) if run_sums else 0

            # Signals associated with any v2 opportunity (submitted to analysis)
            signals_submitted = (
                session.query(Signal.id)
                .join(OpportunitySignal, OpportunitySignal.signal_id == Signal.id)
                .join(Opportunity, OpportunitySignal.opportunity_id == Opportunity.id)
                .join(Source, Signal.source_id == Source.id)
                .filter(
                    Source.source_type == stype,
                    Opportunity.current_scoring_version == "v2",
                )
                .distinct()
                .count()
            )

            # Filter opportunities by representative source type
            opps_by_type = [
                opp for opp in v2_opps if opp_rep_sources.get(opp.id) == stype
            ]
            candidates = len(opps_by_type)
            passed_count = sum(1 for opp in opps_by_type if opp.gate_status == "passed")
            rejected_count = sum(
                1 for opp in opps_by_type if opp.gate_status == "rejected"
            )
            scored_count = sum(
                1 for opp in opps_by_type if opp.last_scored_at is not None
            )
            enriched_count = sum(
                1 for opp in opps_by_type if is_enrichment_complete(opp.enrichment_status)
            )
            published_count = sum(1 for opp in opps_by_type if is_opp_published(opp))

            # Evidence used by PUBLISHED opportunities of this source type
            published_opp_ids = [opp.id for opp in v2_opps if is_opp_published(opp)]
            if published_opp_ids:
                evidence_used = (
                    session.query(OpportunitySignal)
                    .join(Signal, OpportunitySignal.signal_id == Signal.id)
                    .join(Source, Signal.source_id == Source.id)
                    .filter(
                        Source.source_type == stype,
                        OpportunitySignal.opportunity_id.in_(published_opp_ids),
                        OpportunitySignal.is_excluded.is_(False),
                    )
                    .count()
                )
            else:
                evidence_used = 0

            pipeline_stats[stype] = {
                "enabled_sources": enabled_count,
                "last_run": last_run_time,
                "collection_runs": collection_runs,
                "fetched": fetched_sum,
                "inserted": inserted_sum,
                "updated": updated_sum,
                "persisted": persisted_sum,
                "skipped": skipped_sum,
                "failed": failed_sum,
                "signals_analyzed": signals_submitted,
                "candidates": candidates,
                "gate_passed": passed_count,
                "gate_rejected": rejected_count,
                "scored": scored_count,
                "enriched": enriched_count,
                "published": published_count,
                "evidence_used": evidence_used,
            }

        # Current snapshot counts
        current_published = sum(1 for opp in v2_opps if is_opp_published(opp))
        current_research = sum(1 for opp in v2_opps if opp.status == OpportunityStatus.RESEARCH)
        current_rejected = sum(1 for opp in v2_opps if opp.status == OpportunityStatus.REJECTED)

        current_enrichment_pending = sum(
            1 for opp in v2_opps
            if opp.gate_status == "passed"
            and opp.status not in (OpportunityStatus.RESEARCH, OpportunityStatus.REJECTED)
            and not (is_enrichment_complete(opp.enrichment_status) and is_translation_complete(opp.translation_status))
        )

        current_total = current_published + current_research + current_rejected + current_enrichment_pending
        current_active_candidates = sum(
            1 for opp in v2_opps if opp.status in (OpportunityStatus.INBOX, OpportunityStatus.RESEARCH)
        )

        # Discovery statistics from new models
        current_discovery_leads = 0
        verified_discovery_leads = 0
        discovery_reports_processed = 0
        discovery_urls_extracted = 0
        primary_sources_dispatched = 0

        from glintory.domain.models import DiscoveryReport, DiscoveryLead
        try:
            current_discovery_leads = session.query(DiscoveryLead).count()
            verified_discovery_leads = session.query(DiscoveryLead).filter(DiscoveryLead.verification_status == "verified").count()
            discovery_reports_processed = session.query(DiscoveryReport).count()
            discovery_urls_extracted = current_discovery_leads
            primary_sources_dispatched = session.query(DiscoveryLead).filter(DiscoveryLead.dispatch_status == "dispatched").count()
        except Exception as e:
            logger.warning(f"Discovery tables query failed (might not be migrated yet): {e}")

        # Historical stats
        from glintory.domain.models import AnalysisRun
        historical_analysis_runs = 0
        historical_gate_passed = 0
        historical_gate_rejected = 0
        try:
            historical_analysis_runs = session.query(AnalysisRun).count()
            historical_gate_passed = session.query(sa.func.sum(sa.cast(AnalysisRun.gate_passed_count, sa.Integer))).scalar() or 0
            historical_gate_rejected = session.query(sa.func.sum(sa.cast(AnalysisRun.gate_rejected_count, sa.Integer))).scalar() or 0
        except Exception as e:
            logger.warning(f"AnalysisRun historical query failed: {e}")

        opp_candidates = current_total
        gate_passed = sum(1 for opp in v2_opps if opp.gate_status == "passed")
        gate_rejected = sum(1 for opp in v2_opps if opp.gate_status == "rejected")
        published_opps = current_published
        research_count = current_research
        rejected_count = current_rejected

        demand_only_count = 0
        multi_evidence_count = 0
        strong_single_demand_count = 0
        supply_only_rejected_count = 0
        single_show_hn_rejected_count = 0
        explicit_feasibility_rejected_count = 0

        gate_reason_counts = {}

        total_signals_in_clusters = 0
        singleton_cluster_count = 0
        cross_source_cluster_count = 0
        duplicate_evidence_removed_count = 0

        missing_facets_summary = {
            "target_user": 0,
            "problem": 0,
            "workaround": 0,
            "gap": 0,
            "mvp": 0,
        }

        for opp in v2_opps:
            gr = opp.gate_reason or "Unknown"
            gate_reason_counts[gr] = gate_reason_counts.get(gr, 0) + 1

            opp_signals = (
                session.query(Signal, Source.source_type)
                .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
                .join(Source, Signal.source_id == Source.id)
                .filter(OpportunitySignal.opportunity_id == opp.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .all()
            )

            sig_count = len(opp_signals)
            total_signals_in_clusters += sig_count
            if sig_count == 1:
                singleton_cluster_count += 1

            source_types_in_opp = {item[1] for item in opp_signals}
            if len(source_types_in_opp) >= 2:
                cross_source_cluster_count += 1

            ind_count = opp.independent_evidence_count
            duplicate_evidence_removed_count += max(0, sig_count - ind_count)

            d_count = opp.demand_evidence_count
            s_count = ind_count - d_count

            if d_count >= 1 and s_count == 0:
                demand_only_count += 1
            if ind_count >= 2:
                multi_evidence_count += 1
            if ind_count == 1 and opp.status == OpportunityStatus.INBOX:
                strong_single_demand_count += 1

            if d_count == 0:
                supply_only_rejected_count += 1

            if "Single Show HN" in gr:
                single_show_hn_rejected_count += 1
            elif (
                "Not suitable for solo" in gr
                or "heavy backend" in gr
                or "inference cost" in gr
                or "enterprise sales" in gr
            ):
                explicit_feasibility_rejected_count += 1

            from glintory.services.signal_facets import extract_signal_facets

            comb_t = " ".join((sig.title or "") for sig, _ in opp_signals)
            comb_e = " ".join((sig.excerpt or "") for sig, _ in opp_signals)
            facets = extract_signal_facets(comb_t, comb_e, "generic", SignalType.TREND)
            sc = facets["structural_completeness"]
            for k in missing_facets_summary:
                if sc.get(k) == "missing":
                    missing_facets_summary[k] += 1

        avg_signals_per_cluster = (
            (total_signals_in_clusters / len(v2_opps)) if v2_opps else 0.0
        )

        global_stats = {
            "current_published": current_published,
            "current_research": current_research,
            "current_rejected": current_rejected,
            "current_enrichment_pending": current_enrichment_pending,
            "current_total": current_total,
            "current_discovery_leads": current_discovery_leads,
            "verified_discovery_leads": verified_discovery_leads,
            "current_active_candidates": current_active_candidates,
            "historical_gate_passed": historical_gate_passed,
            "historical_gate_rejected": historical_gate_rejected,
            "historical_gate_reasons": gate_reason_counts,
            "historical_analysis_runs": historical_analysis_runs,
            "discovery_reports_processed": discovery_reports_processed,
            "discovery_urls_extracted": discovery_urls_extracted,
            "primary_sources_dispatched": primary_sources_dispatched,

            "opportunity_candidates": opp_candidates,
            "gate_passed": gate_passed,
            "gate_rejected": gate_rejected,
            "published_opportunities": published_opps,
            "research_count": research_count,
            "rejected_count": rejected_count,
            "demand_only_count": demand_only_count,
            "multi_evidence_count": multi_evidence_count,
            "strong_single_demand_count": strong_single_demand_count,
            "supply_only_rejected_count": supply_only_rejected_count,
            "single_show_hn_rejected_count": single_show_hn_rejected_count,
            "explicit_feasibility_rejected_count": explicit_feasibility_rejected_count,
            "gate_reason_counts": gate_reason_counts,
            "average_signals_per_cluster": round(avg_signals_per_cluster, 2),
            "singleton_cluster_count": singleton_cluster_count,
            "cross_source_cluster_count": cross_source_cluster_count,
            "duplicate_evidence_removed_count": duplicate_evidence_removed_count,
            "missing_facets_summary": missing_facets_summary,
        }

        runs = (
            session.query(CollectionRun, Source.name)
            .join(Source, CollectionRun.source_id == Source.id)
            .order_by(CollectionRun.created_at.desc())
            .limit(50)
            .all()
        )
        diagnostics_data = []
        for run, source_name in runs:
            diagnostics_data.append(
                {
                    "source_name": source_name,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "status": run.status.value
                    if hasattr(run.status, "value")
                    else str(run.status),
                    "fetched_count": run.fetched_count,
                    "inserted_count": run.inserted_count,
                    "updated_count": run.updated_count,
                    "duplicate_count": run.duplicate_count,
                    "skipped_count": run.skipped_count,
                    "warning_count": run.warning_count,
                    "error_count": run.error_count,
                    "error_type": run.error_type,
                    "sanitized_error_message": run.sanitized_error_message,
                }
            )
        # Load AnalysisRun, ScoringRun, PublishingRun
        from glintory.domain.models import AnalysisRun, PublishingRun, ScoringRun

        analysis_runs = (
            session.query(AnalysisRun)
            .order_by(AnalysisRun.started_at.desc())
            .limit(10)
            .all()
        )
        scoring_runs = (
            session.query(ScoringRun)
            .order_by(ScoringRun.started_at.desc())
            .limit(10)
            .all()
        )
        publishing_runs = (
            session.query(PublishingRun)
            .order_by(PublishingRun.started_at.desc())
            .limit(10)
            .all()
        )

        diagnostics_template = env.from_string(DIAGNOSTICS_TEMPLATE)
        rendered_diagnostics = diagnostics_template.render(
            base_path=base_path,
            diagnostics_data=diagnostics_data,
            pipeline_stats=pipeline_stats,
            global_stats=global_stats,
            repo_url=repo_url,
            analysis_runs=analysis_runs,
            scoring_runs=scoring_runs,
            publishing_runs=publishing_runs,
        )
        with open(os.path.join(temp_build_dir, "diagnostics.html"), "w") as f:
            f.write(rendered_diagnostics)

        robots_content = "User-agent: *\nAllow: /\n"
        with open(os.path.join(temp_build_dir, "robots.txt"), "w") as f:
            f.write(robots_content)

        with open(os.path.join(temp_build_dir, ".nojekyll"), "w") as f:
            f.write("")

        sitemap_items = []

        def get_now_str() -> str:
            return gen_time.strftime("%Y-%m-%d")

        target_site_url = valid_site_url.rstrip("/")

        def make_loc(path: str) -> str:
            full_url = f"{target_site_url}{path}"
            return xml_escape(full_url)

        sitemap_items.append(f"""  <url>
    <loc>{make_loc("/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""")

        sitemap_items.append(f"""  <url>
    <loc>{make_loc("/opportunities/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

        for op in opportunities:
            sitemap_items.append(f"""  <url>
    <loc>{make_loc(f"/opportunities/{op.public_id}/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>""")
            sitemap_items.append(f"""  <url>
    <loc>{make_loc(f"/opportunities/{op.public_id}/en/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>""")

        sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{"\n".join(sitemap_items)}
</urlset>
"""
        with open(os.path.join(temp_build_dir, "sitemap.xml"), "w") as f:
            f.write(sitemap_xml.strip())

        # Validate Contract before Swap
        from glintory.services.contract_validation import validate_public_contract

        data_v1_dir = os.path.join(temp_build_dir, "data", "v1")
        val_errors = validate_public_contract(data_v1_dir)
        if val_errors:
            raise ValueError(f"Contract Validation Failed: {val_errors}")

        backup_dir = None
        has_swapped = False
        if os.path.exists(output_dir):
            backup_dir = output_dir + f".bak-{uuid.uuid4().hex}"
            try:
                os.rename(output_dir, backup_dir)
                os.rename(temp_build_dir, output_dir)
                has_swapped = True
            except Exception:
                if os.path.exists(backup_dir) and not os.path.exists(output_dir):
                    os.rename(backup_dir, output_dir)
                raise
        else:
            os.rename(temp_build_dir, output_dir)

        try:
            # Update PublishingRun auditing upon success
            pub_run.published_count = contract_res["published_opportunities"]
            pub_run.jurypress_ready_count = contract_res["jurypress_ready"]
            pub_run.dataset_content_hash = contract_res["manifest_content_hash"]
            pub_run.completed_at = datetime.now(UTC)
            pub_run.status = "succeeded"
            session.commit()

            # Clean backup only after successful commit
            if backup_dir and os.path.exists(backup_dir):
                shutil.rmtree(backup_dir)
        except Exception as commit_err:
            if has_swapped and backup_dir and os.path.exists(backup_dir):
                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
                os.rename(backup_dir, output_dir)
            elif not has_swapped and not backup_dir:
                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
            raise commit_err

    except Exception as e:
        # DB Rollback
        with contextlib.suppress(Exception):
            session.rollback()

        # Update existing pub_run to failed status
        try:
            db_pub_run = session.query(PublishingRun).filter(PublishingRun.id == pub_run.id).first()
            if db_pub_run:
                db_pub_run.status = "failed"
                db_pub_run.completed_at = datetime.now(UTC)
                session.commit()
            else:
                fail_run = PublishingRun(
                    started_at=gen_time,
                    completed_at=datetime.now(UTC),
                    status="failed",
                    published_count=0,
                    jurypress_ready_count=0,
                    dataset_content_hash="",
                )
                session.add(fail_run)
                session.commit()
        except Exception:
            pass

        if os.path.exists(temp_build_dir):
            with contextlib.suppress(Exception):
                shutil.rmtree(temp_build_dir)
        raise e

    return {
        "opportunities_generated": len(opportunities),
        "total_files": (len(opportunities) * 2) + 8,
    }
