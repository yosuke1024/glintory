import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import (
    case,
    column,
    func,
    literal,
    literal_column,
    select,
    table,
    text,
)
from sqlalchemy.orm import Session

from glintory.domain.models import Signal, Source
from glintory.domain.search import (
    SignalDetail,
    SignalSearchFilters,
    SignalSearchItem,
    SignalSearchPage,
)


class SignalSearchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self,
        filters: SignalSearchFilters,
        match_expression: str | None = None,
    ) -> SignalSearchPage:
        """Searches and filters signals using SQLite FTS5 if match_expression is provided."""
        # 1. Setup base select query
        stmt = select(
            Signal.id,
            Signal.title,
            Signal.excerpt,
            Signal.author,
            Signal.canonical_url,
            Signal.source_id,
            Source.name.label("source_name"),
            Source.source_type.label("source_type"),
            Signal.signal_type,
            Signal.published_at,
            Signal.collected_at,
            Signal.freshness_score,
        ).join(Source, Signal.source_id == Source.id)

        # Base count query
        count_stmt = (
            select(func.count())
            .select_from(Signal)
            .join(Source, Signal.source_id == Source.id)
        )

        # 2. Join FTS table if search query exists
        # Declare FTS5 virtual table without mapping it as ORM model
        signals_fts = table(
            "signals_fts",
            column("rowid"),
            column("title"),
            column("excerpt"),
            column("author"),
        )

        if match_expression:
            stmt = stmt.join(
                signals_fts, signals_fts.c.rowid == literal_column("signals.rowid")
            )
            stmt = stmt.where(text("signals_fts MATCH :match_expr")).params(
                match_expr=match_expression
            )

            count_stmt = count_stmt.join(
                signals_fts, signals_fts.c.rowid == literal_column("signals.rowid")
            )
            count_stmt = count_stmt.where(text("signals_fts MATCH :match_expr")).params(
                match_expr=match_expression
            )

            # Rank column using BM25
            rank_col = func.bm25(literal_column("signals_fts"), 8.0, 2.0, 1.0).label(
                "rank"
            )
            stmt = stmt.add_columns(rank_col)

        else:
            # Query has no match expression, rank is None
            stmt = stmt.add_columns(literal(None).label("rank"))

        # 3. Apply Filters
        # Source ID filter
        if filters.source_id:
            stmt = stmt.where(Signal.source_id == filters.source_id)
            count_stmt = count_stmt.where(Signal.source_id == filters.source_id)

        # Signal Type filter
        if filters.signal_type:
            stmt = stmt.where(Signal.signal_type == filters.signal_type)
            count_stmt = count_stmt.where(Signal.signal_type == filters.signal_type)

        # Published From / To filters
        if filters.published_from or filters.published_to:
            # Exclude undated signals when applying date filters
            stmt = stmt.where(Signal.published_at.is_not(None))
            count_stmt = count_stmt.where(Signal.published_at.is_not(None))

            if filters.published_from:
                from_dt = datetime.combine(filters.published_from, datetime.min.time())
                stmt = stmt.where(Signal.published_at >= from_dt)
                count_stmt = count_stmt.where(Signal.published_at >= from_dt)

            if filters.published_to:
                # Include the 'to' day by adding 1 day and querying strictly less than (<)
                to_dt = datetime.combine(
                    filters.published_to + timedelta(days=1), datetime.min.time()
                )
                stmt = stmt.where(Signal.published_at < to_dt)
                count_stmt = count_stmt.where(Signal.published_at < to_dt)

        # 4. Sorting logic
        # SQLite NULLS LAST simulation: case when published_at is NULL then 1 else 0
        published_null_sort = case((Signal.published_at.is_(None), 1), else_=0)

        if match_expression:
            stmt = stmt.order_by(
                text("rank ASC"),  # bm25 rank (smaller is more relevant)
                published_null_sort.asc(),
                Signal.published_at.desc(),
                Signal.collected_at.desc(),
                Signal.id.asc(),
            )
        else:
            stmt = stmt.order_by(
                published_null_sort.asc(),
                Signal.published_at.desc(),
                Signal.collected_at.desc(),
                Signal.id.asc(),
            )

        # 5. Execute count to calculate pagination
        total_count = self.session.execute(count_stmt).scalar() or 0

        # Validate per_page (allow only 10, 25, 50, 100)
        per_page = filters.per_page
        if per_page not in (10, 25, 50, 100):
            per_page = 25

        total_pages = math.ceil(total_count / per_page) if total_count > 0 else 0

        # Enforce page limits safely (return empty items if page is out of range)
        page = max(1, filters.page)
        offset = (page - 1) * per_page

        items: list[SignalSearchItem] = []
        if total_count > 0 and (offset < total_count):
            stmt = stmt.offset(offset).limit(per_page)
            results = self.session.execute(stmt).all()

            for row in results:
                # Retrieve rank if available (rank exists when match_expression is passed)
                rank_val = getattr(row, "rank", None)
                if rank_val is not None:
                    rank_val = float(rank_val)

                items.append(
                    SignalSearchItem(
                        id=row.id,
                        title=row.title,
                        excerpt=row.excerpt or "",
                        author=row.author,
                        canonical_url=row.canonical_url,
                        source_id=row.source_id,
                        source_name=row.source_name,
                        source_type=row.source_type,
                        signal_type=row.signal_type,
                        published_at=row.published_at,
                        collected_at=row.collected_at,
                        freshness_score=row.freshness_score,
                        rank=rank_val,
                    )
                )

        return SignalSearchPage(
            items=items,
            total_count=total_count,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
        )

    def get_detail(
        self,
        signal_id: str,
    ) -> SignalDetail | None:
        """Retrieves complete details of a single Signal including its Source type details."""
        stmt = (
            select(
                Signal,
                Source.name.label("source_name"),
                Source.source_type.label("source_type"),
            )
            .join(Source, Signal.source_id == Source.id)
            .where(Signal.id == signal_id)
        )
        row = self.session.execute(stmt).first()
        if not row:
            return None

        sig = row.Signal
        return SignalDetail(
            id=sig.id,
            source_id=sig.source_id,
            source_name=row.source_name,
            source_type=row.source_type,
            collection_run_id=sig.collection_run_id,
            external_id=sig.external_id,
            canonical_url=sig.canonical_url,
            title=sig.title,
            excerpt=sig.excerpt or "",
            author=sig.author,
            published_at=sig.published_at,
            collected_at=sig.collected_at,
            language=sig.language,
            signal_type=sig.signal_type,
            categories=sig.categories,
            tags=sig.tags,
            metrics=sig.metrics,
            raw_metadata=sig.raw_metadata,
            content_hash=sig.content_hash,
            freshness_score=sig.freshness_score,
            source_quality_score=sig.source_quality_score,
            created_at=sig.created_at,
            updated_at=sig.updated_at,
        )

    def get_active_sources(self) -> Sequence[dict[str, str]]:
        """Retrieves list of active sources that have at least one signal, sorted by name."""
        stmt = (
            select(Source.id, Source.name, Source.source_type)
            .join(Signal, Signal.source_id == Source.id)
            .distinct()
            .order_by(Source.name.asc())
        )
        results = self.session.execute(stmt).all()
        return [{"id": r.id, "name": r.name, "type": r.source_type} for r in results]
