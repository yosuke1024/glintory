import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from glintory.collectors.base import CollectionContext
from glintory.collectors.registry import CollectorRegistry
from glintory.domain.enums import CollectionRunStatus
from glintory.infrastructure.error_sanitizer import sanitize_error
from glintory.infrastructure.http import HttpxHttpClient
from glintory.infrastructure.repositories import (
    CollectionRunRepository,
    SourceRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectionExecutionResult:
    run_id: str
    status: CollectionRunStatus
    fetched_count: int
    warning_count: int
    error_count: int
    error_summary: str | None = None


class CollectionService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        registry: CollectorRegistry,
        http_client=None,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self._http_client = http_client or HttpxHttpClient()

    async def run_source(
        self,
        source_id: str,
        *,
        max_items: int | None = None,
    ) -> CollectionExecutionResult:
        # 1. Fetch Source, Check registry, Create running CollectionRun (First Transaction)
        session = self.session_factory()
        try:
            source_repo = SourceRepository(session)
            run_repo = CollectionRunRepository(session)

            source = source_repo.get_by_id(source_id)
            if not source:
                raise ValueError(f"Source with ID {source_id} not found.")

            if not source.enabled:
                raise ValueError(f"Source '{source.name}' is disabled.")

            # Get collector. Will raise CollectorNotFoundError if unregistered
            collector = self.registry.get(source.source_type)

            # Create running collection run record
            run = run_repo.create_running(source.id)
            run_id = run.id

            source_name = source.name
            source_type = source.source_type

            # Create immutable collection context
            context = CollectionContext(
                source_id=source.id,
                source_name=source.name,
                source_type=source.source_type,
                source_config=source.config,
                max_items=max_items if max_items is not None else 100,
                http=self._http_client,
            )

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        # 2. Execute Collector (Outside of DB Transaction)
        start_time = time.monotonic()
        completed_at = datetime.now(UTC)
        try:
            result = await collector.collect(context)
        except asyncio.CancelledError as e:
            completed_at = datetime.now(UTC)
            error_summary = "Job execution was cancelled."

            # Record cancellation in DB (Separate Transaction)
            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(run_id, completed_at, error_summary)
                source_repo.record_failure(source_id, completed_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
            raise e
        except (KeyboardInterrupt, SystemExit) as e:
            completed_at = datetime.now(UTC)
            error_summary = f"Job execution terminated: {type(e).__name__}."
            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(run_id, completed_at, error_summary)
                source_repo.record_failure(source_id, completed_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
            raise e
        except Exception as e:
            # Fatal collector exceptions
            completed_at = datetime.now(UTC)
            error_summary = sanitize_error(str(e))

            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(run_id, completed_at, error_summary)
                source_repo.record_failure(source_id, completed_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(
                "Collection finished. source_id=%s source_name=%s source_type=%s "
                "collection_run_id=%s status=%s fetched_count=%d warning_count=%d "
                "error_count=%d duration_ms=%d",
                source_id,
                source_name,
                source_type,
                run_id,
                CollectionRunStatus.FAILED.value,
                0,
                0,
                1,
                duration_ms,
            )

            return CollectionExecutionResult(
                run_id=run_id,
                status=CollectionRunStatus.FAILED,
                fetched_count=0,
                warning_count=0,
                error_count=1,
                error_summary=error_summary,
            )

        # 3. Analyze Results and Update DB State (Separate Transaction)
        completed_at = datetime.now(UTC)
        fetched_count = len(result.items)
        warning_count = len(result.warnings)
        error_count = len(result.errors)

        if error_count > 0:
            if fetched_count >= 1:
                status = CollectionRunStatus.PARTIAL
            else:
                status = CollectionRunStatus.FAILED
            # Build safe error summary
            raw_errors = ", ".join(e.message for e in result.errors)
            error_summary = sanitize_error(raw_errors)
        else:
            status = CollectionRunStatus.SUCCEEDED
            error_summary = None

        session = self.session_factory()
        try:
            source_repo = SourceRepository(session)
            run_repo = CollectionRunRepository(session)

            if status == CollectionRunStatus.SUCCEEDED:
                run_repo.finish_succeeded(
                    run_id, completed_at, fetched_count, warning_count
                )
                source_repo.record_success(source_id, completed_at)
            elif status == CollectionRunStatus.PARTIAL:
                run_repo.finish_partial(
                    run_id,
                    completed_at,
                    fetched_count,
                    warning_count,
                    error_count,
                    error_summary or "",
                )
                source_repo.record_partial(
                    source_id,
                    success_at=completed_at,
                    failure_at=completed_at,
                    error_msg=error_summary or "",
                )
            else:
                # FAILED status from collector result
                run_repo.finish_failed(run_id, completed_at, error_summary or "")
                source_repo.record_failure(source_id, completed_at, error_summary or "")

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "Collection finished. source_id=%s source_name=%s source_type=%s "
            "collection_run_id=%s status=%s fetched_count=%d warning_count=%d "
            "error_count=%d duration_ms=%d",
            source_id,
            source_name,
            source_type,
            run_id,
            status.value,
            fetched_count,
            warning_count,
            error_count,
            duration_ms,
        )

        return CollectionExecutionResult(
            run_id=run_id,
            status=status,
            fetched_count=fetched_count,
            warning_count=warning_count,
            error_count=error_count,
            error_summary=error_summary,
        )
