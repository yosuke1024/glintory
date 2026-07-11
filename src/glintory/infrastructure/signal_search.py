import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    case,
    func,
    literal,
    select,
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

        if match_expression:
            where_clauses = []
            sql_params: dict[str, Any] = {"match_expr": match_expression}

            if filters.source_id:
                where_clauses.append("signals.source_id = :source_id")
                sql_params["source_id"] = filters.source_id

            if filters.signal_type:
                sig_type_val = (
                    filters.signal_type.value
                    if hasattr(filters.signal_type, "value")
                    else filters.signal_type
                )
                where_clauses.append("signals.signal_type = :signal_type")
                sql_params["signal_type"] = sig_type_val

            if filters.published_from:
                from_dt = datetime.combine(filters.published_from, datetime.min.time())
                where_clauses.append("signals.published_at >= :published_from")
                sql_params["published_from"] = from_dt

            if filters.published_to:
                to_dt = datetime.combine(
                    filters.published_to + timedelta(days=1), datetime.min.time()
                )
                where_clauses.append("signals.published_at < :published_to")
                sql_params["published_to"] = to_dt

            where_sql = " AND ".join(where_clauses)
            where_clause_str = f"AND {where_sql}" if where_sql else ""

            # Count query using CTE matches
            count_sql = text(f"""
                WITH matches AS (
                    SELECT rowid
                    FROM signals_fts
                    WHERE signals_fts MATCH :match_expr
                )
                SELECT count(*) 
                FROM signals 
                JOIN matches ON signals.rowid = matches.rowid
                JOIN sources ON signals.source_id = sources.id 
                WHERE 1=1 {where_clause_str}
            """)
            total_count = self.session.execute(count_sql, sql_params).scalar() or 0

            # Validate per_page
            per_page = filters.per_page if filters.per_page in (10, 25, 50, 100) else 25
            total_pages = math.ceil(total_count / per_page) if total_count > 0 else 0
            page = max(1, filters.page)
            offset = (page - 1) * per_page

            items = []
            if total_count > 0 and (offset < total_count):
                # Retrieve query with rank ordering in the CTE
                retrieve_sql = text(f"""
                    WITH matches AS (
                        SELECT rowid, bm25(signals_fts, 8.0, 2.0, 1.0) AS rank
                        FROM signals_fts
                        WHERE signals_fts MATCH :match_expr
                    )
                    SELECT signals.id, signals.source_id, signals.canonical_url, 
                           signals.title, signals.excerpt, signals.author, 
                           signals.collected_at, signals.signal_type, 
                           signals.freshness_score, signals.source_quality_score, 
                           sources.name AS source_name, sources.source_type AS source_type,
                           signals.published_at,
                           matches.rank AS rank
                    FROM matches
                    JOIN signals ON signals.rowid = matches.rowid 
                    JOIN sources ON sources.id = signals.source_id 
                    WHERE 1=1 {where_clause_str}
                    ORDER BY rank ASC, 
                             (case when signals.published_at is NULL then 1 else 0 end) ASC,
                             signals.published_at DESC, 
                             signals.collected_at DESC, 
                             signals.id ASC
                    LIMIT :limit OFFSET :offset
                """)
                sql_params["limit"] = per_page
                sql_params["offset"] = offset

                results = self.session.execute(retrieve_sql, sql_params).all()
                for row in results:
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
                            rank=float(row.rank) if row.rank is not None else None,
                        )
                    )

            return SignalSearchPage(
                items=items,
                total_count=total_count,
                page=page,
                per_page=per_page,
                total_pages=total_pages,
            )

        # Fallback to SQLAlchemy query expression when match_expression is NOT provided
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

        items = []
        if total_count > 0 and (offset < total_count):
            stmt = stmt.offset(offset).limit(per_page)
            results = self.session.execute(stmt).all()

            for row in results:
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
                        rank=None,
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
