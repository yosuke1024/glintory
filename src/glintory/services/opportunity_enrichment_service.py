import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.models import (
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)
from glintory.infrastructure.local_llm_client import (
    LlamaServerContext,
    OpportunityEnrichmentProvider,
    OpportunityEnrichmentRequest,
    OpportunityEnrichmentResponse,
)
from glintory.infrastructure.opportunity_enrichment_repository import (
    OpportunityEnrichmentRepository,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class OpportunityEnrichmentRunResult:
    operational_status: str  # success, failed
    selected_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    warning_codes: tuple[str, ...]


class OpportunityEnrichmentService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        provider: OpportunityEnrichmentProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(UTC))

    def run_enrichment(
        self,
        *,
        affected_opportunity_ids: list[str] | None = None,
        opportunity_id: str | None = None,
        max_opportunities: int | None = None,
        force: bool = False,
    ) -> OpportunityEnrichmentRunResult:
        if not settings.local_llm_enabled:
            logger.info("Local LLM enrichment is disabled.")
            return OpportunityEnrichmentRunResult(
                operational_status="success",
                selected_count=0,
                succeeded_count=0,
                failed_count=0,
                skipped_count=0,
                warning_codes=(),
            )

        # 1. Validate Infrastructure (SHA-256 checks)
        # In case of verification failure, this will raise FileNotFoundError or ValueError
        # which bubbles up as Automation Infrastructure Failure.
        if hasattr(self.provider, "verify_infrastructure"):
            self.provider.verify_infrastructure()

        session = self.session_factory()
        repo = OpportunityEnrichmentRepository(session)

        # 2. Select Qualifying Opportunities
        selected_opps = []
        try:
            selected_opps = self._select_opportunities(
                session=session,
                repo=repo,
                affected_opportunity_ids=affected_opportunity_ids or [],
                opportunity_id=opportunity_id,
                max_opportunities=max_opportunities,
            )
        finally:
            session.close()

        if not selected_opps:
            logger.info("No opportunities selected for LLM enrichment.")
            return OpportunityEnrichmentRunResult(
                operational_status="success",
                selected_count=0,
                succeeded_count=0,
                failed_count=0,
                skipped_count=0,
                warning_codes=(),
            )

        # 3. Setup llama-server and execute enrichments
        succeeded_count = 0
        failed_count = 0
        skipped_count = 0
        warning_codes = []

        # We start the server context only if we have opportunities to process
        try:
            with LlamaServerContext(
                binary_path=settings.local_llm_binary_path,
                model_path=settings.local_llm_model_path,
                host=settings.local_llm_bind_address,
                port=settings.local_llm_port,
                timeout_seconds=30,
            ):
                for opp, score_hash, evidences in selected_opps:
                    started_at = self.clock()

                    # Calculate input hash
                    input_hash = self.calculate_input_hash(
                        opportunity_id=opp.id,
                        score_input_hash=score_hash,
                        evidences=evidences,
                    )

                    # Check for duplication
                    session = self.session_factory()
                    db_repo = OpportunityEnrichmentRepository(session)
                    try:
                        existing = db_repo.get_enrichment_by_input_hash(opp.id, input_hash)
                        if existing:
                            if existing.status == "succeeded" and not force:
                                logger.info(f"Skipping opportunity {opp.id} (matching input hash already succeeded).")
                                skipped_count += 1
                                continue
                            else:
                                session.delete(existing)
                                session.flush()

                        # Create enrichment record in 'running' state
                        enrichment = db_repo.create_enrichment(
                            opportunity_id=opp.id,
                            status="running",
                            model_provider="qwen",
                            model_id=settings.local_llm_model_file,
                            model_revision=settings.local_llm_model_revision,
                            model_sha256=settings.local_llm_model_sha256,
                            runtime="llama.cpp",
                            runtime_version="unknown",
                            prompt_version=PROMPT_VERSION,
                            input_hash=input_hash,
                            started_at=started_at,
                        )
                        session.commit()
                        enrichment_id = enrichment.id
                    except Exception as e:
                        session.rollback()
                        logger.error(f"Failed to create enrichment record: {e}")
                        failed_count += 1
                        continue
                    finally:
                        session.close()

                    # Execute LLM API call
                    req = OpportunityEnrichmentRequest(
                        opportunity_id=opp.id,
                        title=opp.title,
                        summary=opp.proposed_solution or "",
                        evidence_count=len(evidences),
                        confidence=opp.confidence.value if hasattr(opp.confidence, "value") else str(opp.confidence),
                        evidence=[
                            {
                                "id": ev["id"],
                                "source_name": ev["source_name"],
                                "signal_type": ev["signal_type"],
                                "title": ev["title"],
                                "excerpt": ev["excerpt"][:1000] if ev["excerpt"] else "",
                                "published_at": ev["published_at"].isoformat() if ev["published_at"] else None,
                                "canonical_url": ev["canonical_url"],
                                "relevance_score": ev["relevance_score"],
                            }
                            for ev in evidences[:5]
                        ],
                    )

                    res = self.provider.enrich(req)

                    # Persist Result
                    session = self.session_factory()
                    db_repo = OpportunityEnrichmentRepository(session)
                    completed_at = self.clock()
                    try:
                        db_repo.update_enrichment_result(
                            enrichment_id=enrichment_id,
                            status=res.status,
                            completed_at=completed_at,
                            duration_ms=res.duration_ms,
                            error_code=res.error_code,
                            generated_title=res.generated_title,
                            generated_summary=res.generated_summary,
                            problem_statement=res.problem_statement,
                            target_users=res.target_users,
                            why_now=res.why_now,
                            evidence_synthesis=res.evidence_synthesis,
                            build_direction=res.build_direction,
                            risks=res.risks,
                            tags=res.tags,
                            evidence_refs=res.evidence_refs,
                            llm_confidence=res.llm_confidence,
                        )
                        session.commit()
                        if res.status == "succeeded":
                            succeeded_count += 1
                        else:
                            failed_count += 1
                            if res.error_code:
                                warning_codes.append(res.error_code)
                    except Exception as e:
                        session.rollback()
                        logger.error(f"Failed to update enrichment record: {e}")
                        failed_count += 1
                    finally:
                        session.close()

        except Exception as e:
            logger.error(f"Local LLM Server Runtime failed: {e}")
            # If server context startup failed
            if str(e) == "LLM_RUNTIME_START_FAILED":
                raise RuntimeError("LLM_RUNTIME_START_FAILED") from e
            # Otherwise, all scheduled selections fail
            failed_count = len(selected_opps) - skipped_count
            warning_codes.append("LLM_RUNTIME_START_FAILED")

        # Determine overall Pipeline Warning Codes (for CLI JSON summary)
        final_warnings = []
        if failed_count > 0:
            if succeeded_count > 0:
                final_warnings.append("LLM_ENRICHMENT_PARTIAL")
            else:
                final_warnings.append("LLM_ENRICHMENT_FAILED")

        # Deduplicate warning codes
        for w in warning_codes:
            if w not in final_warnings:
                final_warnings.append(w)

        return OpportunityEnrichmentRunResult(
            operational_status="success",
            selected_count=len(selected_opps),
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            warning_codes=tuple(final_warnings),
        )

    def calculate_input_hash(
        self,
        *,
        opportunity_id: str,
        score_input_hash: str | None,
        evidences: list[dict[str, Any]],
    ) -> str:
        # Sort evidence by ID to guarantee deterministic ordering
        sorted_ev = sorted(evidences, key=lambda e: e["id"])
        ev_hash_strs = []
        for e in sorted_ev:
            # Format: ID:content_hash:relevance_score
            ev_hash_strs.append(f"{e['id']}:{e['content_hash']}:{e['relevance_score']}")

        parts = [
            opportunity_id,
            score_input_hash or "",
            ",".join(ev_hash_strs),
            settings.local_llm_model_file,
            settings.local_llm_model_revision,
            PROMPT_VERSION,
            SCHEMA_VERSION,
        ]
        raw_str = "|".join(parts)
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def _select_opportunities(
        self,
        session: Session,
        repo: OpportunityEnrichmentRepository,
        affected_opportunity_ids: list[str],
        opportunity_id: str | None = None,
        max_opportunities: int | None = None,
    ) -> list[tuple[Opportunity, str | None, list[dict[str, Any]]]]:
        limit = max_opportunities or settings.local_llm_max_opportunities
        if not (1 <= limit <= 50):
            limit = 10

        query = session.query(Opportunity).filter(
            Opportunity.status.notin_(
                [OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED]
            )
        )

        if opportunity_id:
            query = query.filter(Opportunity.id == opportunity_id)

        opportunities = query.all()
        if not opportunities:
            return []

        candidates = []
        for opp in opportunities:
            # 1. Fetch latest score snapshot hash
            snap = (
                session.query(ScoreSnapshot)
                .filter(
                    ScoreSnapshot.opportunity_id == opp.id,
                    ScoreSnapshot.scoring_version == settings.scoring_version,
                )
                .order_by(desc(ScoreSnapshot.created_at))
                .first()
            )
            score_hash = snap.input_hash if snap else None

            # 2. Fetch associated evidence signals
            ev_signals = (
                session.query(
                    Signal,
                    OpportunitySignal.relevance_score,
                    Source.name,
                    Source.source_type,
                )
                .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
                .join(Source, Signal.source_id == Source.id)
                .filter(OpportunitySignal.opportunity_id == opp.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .order_by(OpportunitySignal.relevance_score.desc())
                .all()
            )

            # Skip if opportunity has zero evidence
            if not ev_signals:
                continue

            ev_list = []
            for sig, rel_score, src_name, src_type in ev_signals:
                # Convert enums to string values
                sig_type_str = (
                    sig.signal_type.value
                    if hasattr(sig.signal_type, "value")
                    else str(sig.signal_type)
                )
                ev_list.append(
                    {
                        "id": sig.id,
                        "content_hash": sig.content_hash,
                        "relevance_score": rel_score,
                        "source_name": src_name,
                        "signal_type": sig_type_str,
                        "title": sig.title,
                        "excerpt": sig.excerpt,
                        "published_at": sig.published_at or sig.collected_at,
                        "canonical_url": sig.canonical_url,
                    }
                )

            # 3. Determine if eligible
            is_affected = opp.id in affected_opportunity_ids
            latest_success = repo.get_latest_successful_enrichment(opp.id)

            is_eligible = False
            if is_affected:
                is_eligible = True
            elif not latest_success:
                is_eligible = True
            else:
                # Check if input hash changed (stale)
                current_input_hash = self.calculate_input_hash(
                    opportunity_id=opp.id,
                    score_input_hash=score_hash,
                    evidences=ev_list,
                )
                if latest_success.input_hash != current_input_hash:
                    is_eligible = True

            if is_eligible:
                candidates.append((opp, score_hash, ev_list, is_affected))

        # 4. Sort Candidates based on priority:
        # Priority: Affected (True first) -> Total Score (DESC) -> Evidence Updated At (DESC) -> Opp ID (ASC)
        # In Python, sort is stable. We can sort multiple keys.
        # Boolean is_affected can be sorted by negation (False -> 0, True -> 1, so -1 for True first)
        def sort_key(item: Any) -> Any:
            opp_obj, _, _, affected_flag = item
            # We want affected_flag = True first (so -1, False -> 0)
            affected_val = -1 if affected_flag else 0
            score_val = -opp_obj.total_score
            evidence_dt = opp_obj.evidence_updated_at or opp_obj.created_at
            # Negate timestamp for DESC order
            dt_val = -int(evidence_dt.replace(tzinfo=UTC).timestamp())
            return (affected_val, score_val, dt_val, opp_obj.id)

        candidates.sort(key=sort_key)

        # Apply maximum limit
        sliced = candidates[:limit]

        # Return tuples containing (Opportunity, score_hash, evidences)
        return [(item[0], item[1], item[2]) for item in sliced]
