import dataclasses
import uuid

from glintory.domain.search import SignalDetail, SignalSearchFilters, SignalSearchPage
from glintory.infrastructure.signal_search import SignalSearchRepository
from glintory.services.search_query import build_safe_fts_query


class SignalQueryService:
    """Service responsible for validating search parameters, building safe FTS5 queries,
    and managing the data-mapping logic between database objects and domain search types.
    """

    def __init__(self, repo: SignalSearchRepository) -> None:
        self.repo = repo

    def search(
        self,
        filters: SignalSearchFilters,
    ) -> SignalSearchPage:
        """Validates filter parameters, parses the search query, and executes the search query."""
        # 1. Enforce strict filter rules
        if filters.per_page not in (10, 25, 50, 100):
            raise ValueError("per_page must be one of 10, 25, 50, or 100")
        if filters.page < 1:
            raise ValueError("page must be 1 or greater")
        if (
            filters.published_from
            and filters.published_to
            and filters.published_from > filters.published_to
        ):
            raise ValueError("published_from date cannot be after published_to date")

        # 2. Build safe FTS match expression if query text is provided
        match_expression = None
        if filters.query and filters.query.strip():
            parsed_query = build_safe_fts_query(filters.query)
            match_expression = parsed_query.match_expression

        # 3. Fetch from repository
        page_result = self.repo.search(filters, match_expression=match_expression)

        # 4. Truncate excerpt length to a maximum of 500 characters for listing display
        truncated_items = []
        for item in page_result.items:
            excerpt_truncated = item.excerpt
            if len(excerpt_truncated) > 500:
                excerpt_truncated = excerpt_truncated[:500] + "..."

            truncated_items.append(dataclasses.replace(item, excerpt=excerpt_truncated))

        return SignalSearchPage(
            items=truncated_items,
            total_count=page_result.total_count,
            page=page_result.page,
            per_page=page_result.per_page,
            total_pages=page_result.total_pages,
        )

    def get_detail(
        self,
        signal_id: str,
    ) -> SignalDetail | None:
        """Handles UUID validation and fetches detailed information of a single Signal."""
        # Safe UUID verification
        try:
            uuid.UUID(signal_id)
        except ValueError:
            # Return None for invalid UUID formatting, routing will handle it as HTTP 404
            return None

        return self.repo.get_detail(signal_id)
