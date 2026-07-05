from datetime import datetime

from sqlalchemy.orm import Session

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import CollectionRun, Source


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, source_id: str) -> Source | None:
        return self.session.get(Source, source_id)

    def get_enabled_by_id(self, source_id: str) -> Source | None:
        source = self.get_by_id(source_id)
        if source and source.enabled:
            return source
        return None

    def record_success(self, source_id: str, success_at: datetime) -> None:
        source = self.get_by_id(source_id)
        if source:
            source.last_success_at = success_at
            source.consecutive_failures = 0
            source.last_error = None

    def record_partial(
        self,
        source_id: str,
        success_at: datetime,
        failure_at: datetime,
        error_msg: str,
    ) -> None:
        source = self.get_by_id(source_id)
        if source:
            source.last_success_at = success_at
            source.last_failure_at = failure_at
            source.consecutive_failures += 1
            source.last_error = error_msg

    def record_failure(
        self, source_id: str, failure_at: datetime, error_msg: str
    ) -> None:
        source = self.get_by_id(source_id)
        if source:
            source.last_failure_at = failure_at
            source.consecutive_failures += 1
            source.last_error = error_msg


class CollectionRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_running(self, source_id: str) -> CollectionRun:
        run = CollectionRun(
            source_id=source_id,
            status=CollectionRunStatus.RUNNING,
            fetched_count=0,
            inserted_count=0,
            updated_count=0,
            duplicate_count=0,
            warning_count=0,
            error_count=0,
        )
        self.session.add(run)
        self.session.flush()  # Populates run.id
        return run

    def finish_succeeded(
        self,
        run_id: str,
        completed_at: datetime,
        fetched_count: int,
        warning_count: int,
    ) -> None:
        run = self.session.get(CollectionRun, run_id)
        if run:
            run.status = CollectionRunStatus.SUCCEEDED
            run.completed_at = completed_at
            run.fetched_count = fetched_count
            run.warning_count = warning_count
            run.error_count = 0

    def finish_partial(
        self,
        run_id: str,
        completed_at: datetime,
        fetched_count: int,
        warning_count: int,
        error_count: int,
        error_summary: str,
    ) -> None:
        run = self.session.get(CollectionRun, run_id)
        if run:
            run.status = CollectionRunStatus.PARTIAL
            run.completed_at = completed_at
            run.fetched_count = fetched_count
            run.warning_count = warning_count
            run.error_count = error_count
            run.error_summary = error_summary

    def finish_failed(
        self, run_id: str, completed_at: datetime, error_summary: str
    ) -> None:
        run = self.session.get(CollectionRun, run_id)
        if run:
            run.status = CollectionRunStatus.FAILED
            run.completed_at = completed_at
            run.error_summary = error_summary
