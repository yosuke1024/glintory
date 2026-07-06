import json
import logging
import uuid
from typing import Sequence

from glintory.domain.opportunities import (
    OpportunityDetail,
    OpportunityListFilters,
    OpportunityListPage,
    OpportunityListItem,
)
from glintory.infrastructure.opportunity_query import OpportunityQueryRepository

logger = logging.getLogger(__name__)


class OpportunityQueryService:
    def __init__(self, repository: OpportunityQueryRepository) -> None:
        self.repository = repository

    def list_opportunities(
        self,
        filters: OpportunityListFilters,
    ) -> OpportunityListPage:
        """Validate filters and retrieve paginated opportunities."""
        # Validate per_page
        per_page = filters.per_page
        if per_page not in (10, 25, 50, 100):
            per_page = 25

        # Validate page
        page = max(1, filters.page)

        validated_filters = OpportunityListFilters(
            status=filters.status,
            confidence=filters.confidence,
            generation_method=filters.generation_method,
            minimum_score=filters.minimum_score,
            page=page,
            per_page=per_page,
        )

        return self.repository.list_opportunities(validated_filters)

    def get_detail(self, opportunity_id: str) -> OpportunityDetail | None:
        """Validate UUID and retrieve opportunity details, parsing existing projects safely."""
        # 1. UUID Validation
        try:
            uuid.UUID(opportunity_id)
        except ValueError:
            logger.warning(
                "Invalid UUID format for opportunity_id: %s", opportunity_id
            )
            return None

        # 2. Retrieve detail from repository
        detail = self.repository.get_detail(opportunity_id)
        if not detail:
            return None

        # 3. Parse existing_projects safely
        # Note: repository temporarily wraps raw field as list if it exists, or empty list
        # We need to perform deep parsing over the raw field.
        # Since repository returns `existing_projects` as a list containing the raw string,
        # we can fetch the raw string from DB or use the repo's returned list.
        # However, to be extra safe and decoupled, we can query the raw string or retrieve
        # it through repo. Let's look up raw text.
        # Repository stores the original raw text under `opp.existing_projects`.
        # In our Repository, `existing_projects=[raw_existing_projects]` was returned.
        raw_text = detail.existing_projects[0] if detail.existing_projects else None
        parsed_projects = self._parse_existing_projects(raw_text)

        # 4. Construct final detail with parsed projects
        return OpportunityDetail(
            id=detail.id,
            title=detail.title,
            problem_statement=detail.problem_statement,
            target_user=detail.target_user,
            proposed_solution=detail.proposed_solution,
            existing_projects=parsed_projects,
            remaining_gap=detail.remaining_gap,
            mvp_scope=detail.mvp_scope,
            monetization_hypothesis=detail.monetization_hypothesis,
            distribution_hypothesis=detail.distribution_hypothesis,
            validation_method=detail.validation_method,
            generation_method=detail.generation_method,
            cluster_version=detail.cluster_version,
            status=detail.status,
            confidence=detail.confidence,
            evidence_score=detail.evidence_score,
            feasibility_score=detail.feasibility_score,
            penalty_score=detail.penalty_score,
            total_score=detail.total_score,
            current_scoring_version=detail.current_scoring_version,
            last_clustered_at=detail.last_clustered_at,
            last_scored_at=detail.last_scored_at,
            evidence=detail.evidence,
            latest_snapshot=detail.latest_snapshot,
            score_history=detail.score_history,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
        )

    def get_top_opportunities(self, limit: int = 3) -> Sequence[OpportunityListItem]:
        """Retrieve scored top opportunities for the Today screen."""
        if not (1 <= limit <= 20):
            limit = 3
        return self.repository.get_top_opportunities(limit)

    def _parse_existing_projects(self, raw: str | None) -> list[str]:
        """Safely parse existing_projects field from database."""
        if not raw:
            return []

        raw_stripped = raw.strip()
        if not raw_stripped:
            return []

        # Attempt to parse as JSON list
        if raw_stripped.startswith("[") and raw_stripped.endswith("]"):
            try:
                parsed = json.loads(raw_stripped)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
                # If not a list, log warning and fallback to empty
                logger.warning(
                    "existing_projects parsed JSON is not a list: %s", type(parsed)
                )
            except Exception as e:
                logger.warning(
                    "Failed to parse existing_projects JSON. Error: %s", str(e)
                )
        else:
            logger.warning(
                "existing_projects field format is invalid (not JSON list)."
            )

        return []
