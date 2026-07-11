import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.enums import OpportunityStatus
from glintory.domain.models import (
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)
from glintory.infrastructure.local_llm_client import (
    OpportunityEnrichmentProvider,
    OpportunityEnrichmentRequest,
    OpportunityEnrichmentResponse,
)
from glintory.infrastructure.opportunity_enrichment_repository import (
    OpportunityEnrichmentRepository,
)

logger = logging.getLogger(__name__)

from glintory.domain.enrichment_contract import PROMPT_VERSION, SCHEMA_VERSION


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

        # 1. Verify Infrastructure early before creating running records
        verify_infra = getattr(self.provider, "verify_infrastructure", None)
        if verify_infra is not None and callable(verify_infra):
            try:
                verify_infra()
            except ValueError as e:
                if str(e) == "LLM_CONFIGURATION_INVALID":
                    logger.error("LLM_CONFIGURATION_INVALID")
                else:
                    logger.error("LLM_RUNTIME_START_FAILED")
                raise
            except Exception:
                logger.error("LLM_RUNTIME_START_FAILED")
                raise

        # 2. Select Qualifying Opportunities
        session = self.session_factory()
        repo = OpportunityEnrichmentRepository(session)
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

        # 3. Build requests and manage state records before LLM execution
        batch_items = []
        skipped_count = 0
        failed_count = 0
        warning_codes = []

        for opp, score_hash, evidences in selected_opps:
            if not evidences:
                logger.warning(f"Skipping opportunity {opp.id} (LLM_NO_EVIDENCE).")
                skipped_count += 1
                warning_codes.append("LLM_NO_EVIDENCE")
                continue

            started_at = self.clock()
            input_hash = self.calculate_input_hash(
                opportunity_id=opp.id,
                score_input_hash=score_hash,
                evidences=evidences,
            )

            # Check for duplication
            session = self.session_factory()
            db_repo = OpportunityEnrichmentRepository(session)
            try:
                existing = db_repo.get_enrichment_by_input_hash(
                    opp.id, input_hash, PROMPT_VERSION
                )
                if existing:
                    if existing.status == "succeeded" and not force:
                        logger.info(
                            f"Skipping opportunity {opp.id} (matching input hash already succeeded)."
                        )
                        skipped_count += 1
                        continue
                    session.delete(existing)
                    session.flush()

                # Register enrichment as running in DB
                runtime_ver = "unknown"
                runtime_commit = None
                runtime_bin_sha = None
                runtime_desc = getattr(self.provider, "runtime_descriptor", None)
                if runtime_desc is not None:
                    runtime_ver = runtime_desc.version
                    runtime_commit = runtime_desc.commit
                    runtime_bin_sha = runtime_desc.binary_sha256
                enrichment = db_repo.create_enrichment(
                    opportunity_id=opp.id,
                    status="running",
                    model_provider="qwen",
                    model_id=settings.local_llm_model_file,
                    model_revision=settings.local_llm_model_revision,
                    model_sha256=settings.local_llm_model_sha256,
                    runtime="llama.cpp",
                    runtime_version=runtime_ver,
                    prompt_version=PROMPT_VERSION,
                    input_hash=input_hash,
                    started_at=started_at,
                    runtime_commit=runtime_commit,
                    runtime_binary_sha256=runtime_bin_sha,
                )
                session.commit()
                enrichment_id = enrichment.id
            except Exception:
                session.rollback()
                logger.error("LLM_ENRICHMENT_RECORD_CREATE_FAILED")
                failed_count += 1
                continue
            finally:
                session.close()

            # Build Request with strict token budget
            conf_str = (
                opp.confidence.value
                if hasattr(opp.confidence, "value")
                else str(opp.confidence)
            )
            try:
                req = self.build_budgeted_request(
                    opp_id=opp.id,
                    title=opp.title,
                    summary=opp.proposed_solution or "",
                    confidence=conf_str,
                    evidences=evidences,
                )
            except ValueError as e:
                if str(e) == "LLM_INPUT_BUDGET_EXCEEDED":
                    completed_at = self.clock()
                    duration_ms = int(
                        (completed_at - started_at).total_seconds() * 1000
                    )
                    logger.error("LLM_INPUT_BUDGET_EXCEEDED")
                    session = self.session_factory()
                    db_repo = OpportunityEnrichmentRepository(session)
                    try:
                        db_repo.update_enrichment_result(
                            enrichment_id=enrichment_id,
                            status="failed",
                            completed_at=completed_at,
                            duration_ms=duration_ms,
                            error_code="LLM_INPUT_BUDGET_EXCEEDED",
                        )
                        session.commit()
                    except Exception:
                        session.rollback()
                        logger.error("LLM_RESULT_PERSISTENCE_FAILED")
                    finally:
                        session.close()

                    failed_count += 1
                    warning_codes.append("LLM_INPUT_BUDGET_EXCEEDED")
                    continue
                raise

            batch_items.append(
                {
                    "request": req,
                    "opp_id": opp.id,
                    "enrichment_id": enrichment_id,
                    "started_at": started_at,
                }
            )

        if not batch_items:
            final_warnings = []
            for w in warning_codes:
                if w not in final_warnings:
                    final_warnings.append(w)
            return OpportunityEnrichmentRunResult(
                operational_status="success",
                selected_count=len(selected_opps),
                succeeded_count=0,
                failed_count=failed_count,
                skipped_count=skipped_count,
                warning_codes=tuple(final_warnings),
            )

        # 4. Batch LLM Execution
        requests = [item["request"] for item in batch_items]
        responses = []
        try:
            responses = list(self.provider.enrich_many(requests))
        except Exception:
            logger.error("LLM_RUNTIME_START_FAILED")
            responses = [
                OpportunityEnrichmentResponse(
                    status="failed",
                    error_code="LLM_RUNTIME_START_FAILED",
                )
                for _ in batch_items
            ]

        # Verify Response Count Conformance
        if len(responses) > len(batch_items):
            logger.error("LLM_PROVIDER_CONTRACT_FAILED")
            # Provider Contract Error: fail all running records and raise exception
            completed_at = self.clock()
            for item in batch_items:
                session = self.session_factory()
                db_repo = OpportunityEnrichmentRepository(session)
                try:
                    db_repo.update_enrichment_result(
                        enrichment_id=item["enrichment_id"],
                        status="failed",
                        completed_at=completed_at,
                        duration_ms=int(
                            (completed_at - item["started_at"]).total_seconds() * 1000
                        ),
                        error_code="LLM_PROVIDER_CONTRACT_FAILED",
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                finally:
                    session.close()
            raise ValueError("LLM_PROVIDER_CONTRACT_FAILED")
        if len(responses) < len(batch_items):
            while len(responses) < len(batch_items):
                responses.append(
                    OpportunityEnrichmentResponse(
                        status="failed",
                        error_code="LLM_INFERENCE_FAILED",
                    )
                )

        # 5. Save results
        succeeded_count = 0

        for item, res in zip(batch_items, responses, strict=True):
            enrichment_id = item["enrichment_id"]
            completed_at = self.clock()
            duration_ms = res.duration_ms or int(
                (completed_at - item["started_at"]).total_seconds() * 1000
            )

            session = self.session_factory()
            db_repo = OpportunityEnrichmentRepository(session)
            try:
                db_repo.update_enrichment_result(
                    enrichment_id=enrichment_id,
                    status=res.status,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    error_code=res.error_code,
                    english=res.english,
                    japanese=res.japanese,
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
            except Exception:
                session.rollback()
                logger.error("LLM_RESULT_PERSISTENCE_FAILED")
                failed_count += 1
            finally:
                session.close()

        final_warnings = []
        if failed_count > 0:
            if succeeded_count > 0:
                final_warnings.append("LLM_ENRICHMENT_PARTIAL")
            else:
                final_warnings.append("LLM_ENRICHMENT_FAILED")

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

    def build_budgeted_request(
        self,
        opp_id: str,
        title: str,
        summary: str,
        confidence: str,
        evidences: list[dict[str, Any]],
    ) -> OpportunityEnrichmentRequest:
        max_chars = settings.local_llm_max_input_chars

        # Limit title to 500 chars, summary to 2000 chars
        title_limit = title[:500]
        summary_limit = summary[:2000]

        req_content = {
            "opportunity_id": opp_id,
            "title": title_limit,
            "summary": summary_limit,
            "evidence_count": 0,
            "confidence": confidence,
            "evidence": [],
        }

        # Check base budget
        base_json = json.dumps(req_content, ensure_ascii=False)
        if len(base_json) > max_chars:
            raise ValueError("LLM_INPUT_BUDGET_EXCEEDED")

        sorted_ev = sorted(
            evidences, key=lambda e: e.get("relevance_score", 0.0), reverse=True
        )
        selected_evidences = []
        for ev in sorted_ev:
            excerpt_str = ev.get("excerpt") or ""
            excerpt_truncated = excerpt_str[:1000]
            ev_title = (ev.get("title") or "")[:500]
            source_name = (ev.get("source_name") or "")[:200]

            ev_item = {
                "id": ev["id"],
                "source_name": source_name,
                "signal_type": ev.get("signal_type") or "",
                "title": ev_title,
                "excerpt": excerpt_truncated,
                "published_at": ev["published_at"].isoformat()
                if ev.get("published_at")
                else None,
                "canonical_url": ev.get("canonical_url") or "",
                "relevance_score": ev.get("relevance_score", 0.0),
            }

            test_ev = selected_evidences + [ev_item]
            test_content = req_content.copy()
            test_content["evidence"] = test_ev
            test_content["evidence_count"] = len(test_ev)

            test_json_str = json.dumps(test_content, ensure_ascii=False)
            if len(test_json_str) > max_chars:
                break
            selected_evidences = test_ev

        if evidences and not selected_evidences:
            raise ValueError("LLM_INPUT_BUDGET_EXCEEDED")

        return OpportunityEnrichmentRequest(
            opportunity_id=opp_id,
            title=title_limit,
            summary=summary_limit,
            evidence_count=len(selected_evidences),
            confidence=confidence,
            evidence=selected_evidences,
        )

    def calculate_input_hash(
        self,
        *,
        opportunity_id: str,
        score_input_hash: str | None,
        evidences: list[dict[str, Any]],
    ) -> str:
        sorted_ev = sorted(evidences, key=lambda e: e["id"])
        ev_hash_strs = []
        for e in sorted_ev:
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
            limit = 5

        query = session.query(Opportunity)
        if opportunity_id:
            query = query.filter(Opportunity.id == opportunity_id)
        else:
            query = query.filter(
                Opportunity.status.in_(
                    [
                        OpportunityStatus.INBOX,
                        OpportunityStatus.WATCH,
                        OpportunityStatus.VALIDATE,
                        OpportunityStatus.BUILD,
                    ]
                )
            )

        opportunities = query.all()

        candidates = []
        for opp in opportunities:
            snapshots = (
                session.query(ScoreSnapshot)
                .filter(ScoreSnapshot.opportunity_id == opp.id)
                .order_by(desc(ScoreSnapshot.created_at))
                .all()
            )
            score_hash = snapshots[0].input_hash if snapshots else None

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
                .all()
            )

            evidences = []
            for sig, rel_score, src_name, src_type in ev_signals:
                evidences.append(
                    {
                        "id": sig.id,
                        "content_hash": sig.content_hash,
                        "title": sig.title,
                        "excerpt": sig.excerpt,
                        "canonical_url": sig.canonical_url,
                        "source_name": src_name,
                        "source_type": src_type,
                        "published_at": sig.published_at or sig.collected_at,
                        "relevance_score": rel_score,
                    }
                )

            is_affected = opp.id in affected_opportunity_ids
            latest_enrich = repo.get_latest_successful_enrichment(opp.id)

            is_eligible = False
            if is_affected or not latest_enrich:
                is_eligible = True
            else:
                current_hash = self.calculate_input_hash(
                    opportunity_id=opp.id,
                    score_input_hash=score_hash,
                    evidences=evidences,
                )
                if latest_enrich.input_hash != current_hash:
                    is_eligible = True

            if is_eligible:
                candidates.append((opp, score_hash, evidences))

        # Stable Sort: Affected first -> Total Score DESC -> Evidence Updated At DESC -> Opp ID ASC
        def sort_key(item):
            o, _, evs = item
            aff_key = 0 if o.id in affected_opportunity_ids else 1
            score_key = -o.total_score
            ev_date = o.evidence_updated_at or datetime.min.replace(tzinfo=UTC)
            return (aff_key, score_key, -ev_date.timestamp(), o.id)

        candidates.sort(key=sort_key)
        return candidates[:limit]
