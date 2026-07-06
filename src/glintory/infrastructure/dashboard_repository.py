from collections.abc import Sequence
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import CollectionRun, Signal, Source


class DashboardRepository:
    """Repository that queries real-time database summary statistics and recent records for the dashboard."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_real_summary(self) -> dict[str, Any]:
        """Calculates total counts and last run statistics for active signals and collection runs."""
        total_signals = (
            self.session.execute(select(func.count(Signal.id))).scalar() or 0
        )

        total_sources_with_signals = (
            self.session.execute(
                select(func.count(func.distinct(Signal.source_id)))
            ).scalar()
            or 0
        )

        last_success_at = self.session.execute(
            select(func.max(CollectionRun.completed_at)).where(
                CollectionRun.status == CollectionRunStatus.SUCCEEDED
            )
        ).scalar()

        # Find latest completed or running run status
        latest_run = self.session.execute(
            select(CollectionRun.status)
            .order_by(CollectionRun.started_at.desc())
            .limit(1)
        ).first()

        last_status = latest_run[0] if latest_run else None

        return {
            "total_signals": total_signals,
            "total_sources_with_signals": total_sources_with_signals,
            "last_success_at": last_success_at,
            "last_collection_status": last_status,
        }

    def get_recent_signals(self) -> Sequence[dict[str, Any]]:
        """Queries the 5 most recent signals using the standard non-query sorting criteria."""
        published_null_sort = case((Signal.published_at.is_(None), 1), else_=0)

        stmt = (
            select(
                Signal.id,
                Signal.title,
                Source.name.label("source_name"),
                Signal.signal_type,
                Signal.published_at,
                Signal.collected_at,
            )
            .join(Source, Signal.source_id == Source.id)
            .order_by(
                published_null_sort.asc(),
                Signal.published_at.desc(),
                Signal.collected_at.desc(),
                Signal.id.asc(),
            )
            .limit(5)
        )

        results = self.session.execute(stmt).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "source_name": r.source_name,
                "signal_type": r.signal_type,
                "published_at": r.published_at,
                "collected_at": r.collected_at,
            }
            for r in results
        ]
