import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import CollectionRun, Source
from glintory.domain.operations import (
    CollectionRunDetail,
    CollectionRunListItem,
    SourceNotFoundError,
    SourceOperationItem,
)
from glintory.infrastructure.error_sanitizer import sanitize_error

# RegEx patterns for sanitization
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.\~=]+")
AUTH_HEADER_PATTERN = re.compile(r"(?i)authorization\s*:\s*[^\s]+")
QUERY_SECRET_PATTERN = re.compile(
    r"(?i)(api_key|token|auth|password|secret|key)=[^&\s\?]+"
)
DB_URL_PATTERN = re.compile(
    r"(?i)(sqlite|postgresql|mysql|mssql|mongodb|redis|amqp|odbc):\/\/[^\s]+"
)

REDACT_KEYS = {
    "authorization",
    "auth",
    "token",
    "cookie",
    "cookies",
    "db_url",
    "response_body",
    "response",
    "body",
    "xml",
    "rss_xml",
    "issue_body",
    "excerpt",
    "config",
    "source_config",
    "headers",
}


def sanitize_metadata_safe(metadata: Any) -> Any:
    if isinstance(metadata, dict):
        sanitized = {}
        for k, v in metadata.items():
            k_lower = k.lower()
            if any(rk in k_lower for rk in REDACT_KEYS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_metadata_safe(v)
        return sanitized
    if isinstance(metadata, (list, tuple)):
        return [sanitize_metadata_safe(item) for item in metadata]
    if isinstance(metadata, str):
        s = metadata
        s = BEARER_PATTERN.sub("Bearer [MASKED]", s)
        s = AUTH_HEADER_PATTERN.sub("Authorization: [MASKED]", s)
        s = QUERY_SECRET_PATTERN.sub(r"\1=[MASKED]", s)
        s = DB_URL_PATTERN.sub(r"\1://[MASKED]", s)
        if len(s) > 1000 and (
            "<xml" in s.lower() or "<rss" in s.lower() or "<feed" in s.lower()
        ):
            return "[REDACTED XML]"
        return s
    return metadata


class SourceOperationsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _list_sources_internal(
        self,
    ) -> Sequence[tuple[SourceOperationItem, dict[str, Any]]]:
        # 1. Fetch all sources
        sources = (
            self.session.query(Source)
            .order_by(Source.name.asc(), Source.id.asc())
            .all()
        )
        if not sources:
            return []

        source_ids = [s.id for s in sources]

        # 2. Fetch the latest run for each source in 1 query
        latest_runs = {}
        rn_sub = (
            self.session.query(
                CollectionRun.id.label("run_id"),
                func.row_number()
                .over(
                    partition_by=CollectionRun.source_id,
                    order_by=(CollectionRun.started_at.desc(), CollectionRun.id.desc()),
                )
                .label("rn"),
            )
            .filter(CollectionRun.source_id.in_(source_ids))
            .subquery()
        )
        runs = (
            self.session.query(CollectionRun)
            .join(rn_sub, CollectionRun.id == rn_sub.c.run_id)
            .filter(rn_sub.c.rn == 1)
            .all()
        )
        for r in runs:
            latest_runs[r.source_id] = r

        # 3. Build SourceOperationItem
        items = []
        for source in sources:
            latest_run = latest_runs.get(source.id)
            is_running = (
                latest_run.status == CollectionRunStatus.RUNNING
                if latest_run
                else False
            )

            # Build a safe config summary (relying on collector or empty mapping here, Service handles collector summary)
            # But the repo must return a clean, non-sensitive config summary, usually empty mapping which is overridden by service.
            config_summary = {}

            # Parse timestamps to ensure timezone-aware UTC
            latest_run_started_at = None
            latest_run_finished_at = None
            if latest_run:
                latest_run_started_at = latest_run.started_at
                if latest_run_started_at and latest_run_started_at.tzinfo is None:
                    latest_run_started_at = latest_run_started_at.replace(tzinfo=UTC)

                # completed_at mapped to finished_at
                latest_run_finished_at = latest_run.completed_at
                if latest_run_finished_at and latest_run_finished_at.tzinfo is None:
                    latest_run_finished_at = latest_run_finished_at.replace(tzinfo=UTC)

            last_success_at = source.last_success_at
            if last_success_at and last_success_at.tzinfo is None:
                last_success_at = last_success_at.replace(tzinfo=UTC)

            last_failure_at = source.last_failure_at
            if last_failure_at and last_failure_at.tzinfo is None:
                last_failure_at = last_failure_at.replace(tzinfo=UTC)

            item = SourceOperationItem(
                id=source.id,
                name=source.name,
                source_type=source.source_type,
                enabled=source.enabled,
                auth_required=source.auth_required,
                config_summary=config_summary,
                latest_run_id=latest_run.id if latest_run else None,
                latest_run_status=latest_run.status if latest_run else None,
                latest_run_started_at=latest_run_started_at,
                latest_run_finished_at=latest_run_finished_at,
                last_success_at=last_success_at,
                last_failure_at=last_failure_at,
                consecutive_failures=source.consecutive_failures,
                is_running=is_running,
            )
            items.append((item, source.config))

        return items

    def list_sources(self) -> Sequence[SourceOperationItem]:
        return [item for item, _ in self._list_sources_internal()]

    def list_sources_with_config(
        self,
    ) -> Sequence[tuple[SourceOperationItem, dict[str, Any]]]:
        return self._list_sources_internal()

    def get_source_detail(self, source_id: str) -> Source | None:
        return self.session.get(Source, source_id)

    def set_enabled(self, source_id: str, enabled: bool) -> bool:
        source = self.get_source_detail(source_id)
        if not source:
            raise SourceNotFoundError(f"Source with ID {source_id} not found.")

        if source.enabled == enabled:
            return True

        source.enabled = enabled
        source.updated_at = datetime.now(UTC)
        self.session.flush()
        return True

    def list_collection_runs(
        self,
        *,
        source_id: str | None = None,
        status: str | None = None,
        trigger_type: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[CollectionRunListItem], int]:
        query = self.session.query(
            CollectionRun, Source.name, Source.source_type
        ).outerjoin(Source, CollectionRun.source_id == Source.id)

        if source_id:
            query = query.filter(CollectionRun.source_id == source_id)
        if status:
            query = query.filter(CollectionRun.status == status)
        if trigger_type:
            query = query.filter(CollectionRun.trigger_type == trigger_type)

        total_count = query.count()

        runs = (
            query.order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        items = []
        for run, source_name, source_type in runs:
            started_at = run.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)

            finished_at = run.completed_at
            if finished_at and finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=UTC)

            s_name = source_name or "Deleted Source"
            s_type = source_type or "unknown"

            items.append(
                CollectionRunListItem(
                    id=run.id,
                    source_id=run.source_id or "",
                    source_name=s_name,
                    source_type=s_type,
                    trigger_type=run.trigger_type,
                    status=run.status,
                    started_at=started_at,
                    finished_at=finished_at,
                    fetched_count=run.fetched_count,
                    inserted_count=run.inserted_count,
                    updated_count=run.updated_count,
                    duplicate_count=run.duplicate_count,
                    warning_count=run.warning_count,
                    error_count=run.error_count,
                )
            )

        return items, total_count

    def list_source_collection_runs(
        self, source_id: str, limit: int = 10
    ) -> Sequence[CollectionRunListItem]:
        # Implementation of listing runs for a single source, e.g., for detail screen
        items, _ = self.list_collection_runs(source_id=source_id, limit=limit, offset=0)
        return items

    def get_collection_run_detail(self, run_id: str) -> CollectionRunDetail | None:
        run = self.session.get(CollectionRun, run_id)
        if not run:
            return None

        source_name = "Deleted Source"
        source_type = "unknown"
        if run.source_id:
            s = self.get_source_detail(run.source_id)
            if s:
                source_name = s.name
                source_type = s.source_type

        started_at = run.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)

        finished_at = run.completed_at
        if finished_at and finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)

        # Sanitize metadata & error summary
        safe_metadata = sanitize_metadata_safe(run.run_metadata)
        safe_error = (
            sanitize_error(run.error_summary or "") if run.error_summary else None
        )

        return CollectionRunDetail(
            id=run.id,
            source_id=run.source_id or "",
            source_name=source_name,
            source_type=source_type,
            trigger_type=run.trigger_type,
            status=run.status,
            started_at=started_at,
            finished_at=finished_at,
            fetched_count=run.fetched_count,
            inserted_count=run.inserted_count,
            updated_count=run.updated_count,
            duplicate_count=run.duplicate_count,
            warning_count=run.warning_count,
            error_count=run.error_count,
            safe_error_summary=safe_error,
            metadata=safe_metadata,
        )
