import logging
import time
from datetime import UTC, date, datetime
from typing import Any

from glintory.domain.enums import OpportunityStatus
from glintory.domain.scoring import (
    OpportunityScore,
    OpportunityScoringResult,
    ScoreComponent,
)
from glintory.services.opportunity_scoring import OpportunityScoringEngine

logger = logging.getLogger(__name__)


class OpportunityScoringService:
    def __init__(
        self,
        session_factory: Any,
        repository_factory: Any,
        engine: OpportunityScoringEngine,
        scoring_version: str = "v1",
        clock: Any = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository_factory = repository_factory
        self.engine = engine
        self.scoring_version = scoring_version
        self.clock = clock or (lambda: datetime.now(UTC))

    def score_opportunities(
        self,
        *,
        opportunity_id: str | None = None,
        as_of_date: date | None = None,
        max_opportunities: int | None = None,
        dry_run: bool = False,
    ) -> OpportunityScoringResult:
        """Orchestrate the opportunity scoring process."""
        start_time = time.perf_counter()

        # 1. Validation
        if max_opportunities is not None and not (1 <= max_opportunities <= 10000):
            raise ValueError("max_opportunities must be between 1 and 10000.")

        # 2. Determine scoring date and time
        now_dt = self.clock()
        run_date = as_of_date or now_dt.date()

        # 3. Read Transaction to fetch opportunities and signals
        session = self.session_factory()
        repo = self.repository_factory(session)

        scoring_inputs = []
        latest_hashes = {}

        try:
            if opportunity_id:
                opp_input = repo.load_scoring_input_by_id(opportunity_id)
                if not opp_input:
                    # Perform detailed checks to raise precise errors
                    from glintory.domain.models import Opportunity

                    opp = session.get(Opportunity, opportunity_id)
                    if not opp:
                        raise ValueError(
                            f"Opportunity with ID {opportunity_id} not found."
                        )
                    if opp.status in (
                        OpportunityStatus.REJECTED,
                        OpportunityStatus.ARCHIVED,
                    ):
                        raise ValueError(
                            f"Opportunity with ID {opportunity_id} is rejected or archived."
                        )
                    # Opportunity exists but has no signals, so it's skipped
                    raise ValueError(
                        f"Opportunity with ID {opportunity_id} has no associated signals and cannot be scored."
                    )

                scoring_inputs = [opp_input]
            else:
                limit = max_opportunities or 1000
                scoring_inputs = repo.load_scoring_inputs(
                    active_only=True, max_opportunities=limit
                )

            # Retrieve latest snapshot hashes to determine what has changed
            for opp_input in scoring_inputs:
                snap = repo.load_latest_snapshot(
                    opp_input.opportunity_id, self.scoring_version
                )
                latest_hashes[opp_input.opportunity_id] = (
                    snap.input_hash if snap else None
                )
        finally:
            session.close()  # Read transaction closed

        # 4 & 5 & 6 & 7. Calculate scores on memory and detect unchanged inputs
        to_persist = []
        unchanged_count = 0
        scored_opportunity_ids = []

        for opp_input in scoring_inputs:
            # Run engine scoring rules
            score = self.engine.score(opp_input, as_of_date=run_date)

            latest_hash = latest_hashes.get(opp_input.opportunity_id)
            if latest_hash == score.input_hash:
                unchanged_count += 1
                continue

            to_persist.append(score)
            scored_opportunity_ids.append(opp_input.opportunity_id)

        # 8. Dry run check or no updates needed
        if dry_run or not to_persist:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            self._log_execution(
                scoring_version=self.scoring_version,
                as_of_date=run_date,
                dry_run=dry_run,
                analyzed_opportunity_count=len(scoring_inputs),
                scored_opportunity_count=len(to_persist),
                unchanged_opportunity_count=unchanged_count,
                skipped_opportunity_count=0,
                created_snapshot_count=0,
                updated_opportunity_count=0,
                duration_ms=duration_ms,
            )

            return OpportunityScoringResult(
                scoring_version=self.scoring_version,
                as_of_date=run_date,
                dry_run=dry_run,
                analyzed_opportunity_count=len(scoring_inputs),
                scored_opportunity_count=len(to_persist),
                unchanged_opportunity_count=unchanged_count,
                skipped_opportunity_count=0,
                created_snapshot_count=0,
                updated_opportunity_count=0,
                scored_opportunity_ids=tuple(scored_opportunity_ids),
                warnings=(),
            )

        # 9. Short Write Transaction to persist changes
        write_session = self.session_factory()
        write_repo = self.repository_factory(write_session)

        try:
            for score in to_persist:
                explanation = self._build_explanation(score)
                write_repo.persist_score(
                    opportunity_id=score.opportunity_id,
                    evidence_score=score.evidence_score,
                    feasibility_score=score.feasibility_score,
                    penalty_score=score.penalty_score,
                    total_score=score.total_score,
                    confidence=score.confidence,
                    scoring_version=score.scoring_version,
                    input_hash=score.input_hash,
                    as_of_date=score.as_of_date,
                    explanation=explanation,
                    last_scored_at=now_dt,
                )
            write_session.commit()
        except Exception as e:
            write_session.rollback()
            logger.error(
                f"Transaction failed during opportunity score persistence: {e}"
            )
            raise
        finally:
            write_session.close()

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        self._log_execution(
            scoring_version=self.scoring_version,
            as_of_date=run_date,
            dry_run=dry_run,
            analyzed_opportunity_count=len(scoring_inputs),
            scored_opportunity_count=len(to_persist),
            unchanged_opportunity_count=unchanged_count,
            skipped_opportunity_count=0,
            created_snapshot_count=len(to_persist),
            updated_opportunity_count=len(to_persist),
            duration_ms=duration_ms,
        )

        return OpportunityScoringResult(
            scoring_version=self.scoring_version,
            as_of_date=run_date,
            dry_run=dry_run,
            analyzed_opportunity_count=len(scoring_inputs),
            scored_opportunity_count=len(to_persist),
            unchanged_opportunity_count=unchanged_count,
            skipped_opportunity_count=0,
            created_snapshot_count=len(to_persist),
            updated_opportunity_count=len(to_persist),
            scored_opportunity_ids=tuple(scored_opportunity_ids),
            warnings=(),
        )

    def _build_explanation(self, score: OpportunityScore) -> dict[str, Any]:
        """Convert ScoreComponent list to JSON serializable dictionary."""

        def comp_to_dict(c: ScoreComponent) -> dict[str, Any]:
            return {
                "name": c.name,
                "score": c.score,
                "maximum": c.maximum,
                "explanation": c.explanation,
                "facts": dict(c.facts),
            }

        return {
            "schema_version": 1,
            "scoring_version": score.scoring_version,
            "as_of_date": score.as_of_date.isoformat(),
            "input_hash": score.input_hash,
            "totals": {
                "evidence_score": score.evidence_score,
                "feasibility_score": score.feasibility_score,
                "penalty_score": score.penalty_score,
                "total_score": score.total_score,
                "confidence": score.confidence.value,
            },
            "evidence": {
                "supporting_signal_count": score.supporting_signal_count,
                "related_signal_count": score.related_signal_count,
                "contradicting_signal_count": score.contradicting_signal_count,
                "distinct_origin_count": score.distinct_origin_count,
                "distinct_source_type_count": score.distinct_source_type_count,
                "components": [comp_to_dict(c) for c in score.evidence_components],
            },
            "feasibility": {
                "components": [comp_to_dict(c) for c in score.feasibility_components]
            },
            "penalties": {
                "components": [comp_to_dict(c) for c in score.penalty_components]
            },
        }

    def _log_execution(
        self,
        scoring_version: str,
        as_of_date: date,
        dry_run: bool,
        analyzed_opportunity_count: int,
        scored_opportunity_count: int,
        unchanged_opportunity_count: int,
        skipped_opportunity_count: int,
        created_snapshot_count: int,
        updated_opportunity_count: int,
        duration_ms: int,
    ) -> None:
        """Structured logging without leaking sensitive data or full structures."""
        logger.info(
            "Opportunity scoring run complete. "
            "scoring_version=%s as_of_date=%s dry_run=%s "
            "analyzed=%d scored=%d unchanged=%d skipped=%d "
            "snapshots_created=%d opportunities_updated=%d duration_ms=%d",
            scoring_version,
            as_of_date.isoformat(),
            "yes" if dry_run else "no",
            analyzed_opportunity_count,
            scored_opportunity_count,
            unchanged_opportunity_count,
            skipped_opportunity_count,
            created_snapshot_count,
            updated_opportunity_count,
            duration_ms,
        )
