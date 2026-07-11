import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glintory.collectors.base import CollectionContext
from glintory.collectors.registry import CollectorRegistry
from glintory.config import settings
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.operations import (
    CollectionTriggerType,
    SourceAlreadyRunningError,
    SourceDisabledError,
    SourceNotFoundError,
)
from glintory.infrastructure.error_sanitizer import sanitize_error
from glintory.infrastructure.http import HttpxHttpClient
from glintory.infrastructure.repositories import (
    CollectionRunRepository,
    SourceRepository,
)
from glintory.services.signal_ingestion import SignalIngestionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectionExecutionResult:
    run_id: str
    status: CollectionRunStatus
    fetched_count: int
    inserted_count: int
    updated_count: int
    duplicate_count: int
    warning_count: int
    error_count: int
    signal_ids: Sequence[str]
    error_summary: str | None = None


class CollectionService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        registry: CollectorRegistry,
        ingestion_service: SignalIngestionService | None = None,
        http_client=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.ingestion_service = ingestion_service or SignalIngestionService(
            session_factory
        )
        self._http_client = http_client or HttpxHttpClient()
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run_source(
        self,
        source_id: str,
        *,
        trigger_type: CollectionTriggerType = CollectionTriggerType.CLI,
        max_items: int | None = None,
    ) -> CollectionExecutionResult:
        # 1. Acquire Phase (Short Transaction)
        session = self.session_factory()
        try:
            source_repo = SourceRepository(session)
            run_repo = CollectionRunRepository(session)

            source = source_repo.get_by_id(source_id)
            if not source:
                raise SourceNotFoundError(f"Source with ID {source_id} not found.")

            if not source.enabled:
                raise SourceDisabledError(f"Source '{source.name}' is disabled.")

            # Get collector. Will raise CollectorNotFoundError if unregistered
            collector = self.registry.get(source.source_type)

            # Stale Run Recovery
            from datetime import timedelta

            from glintory.domain.models import CollectionRun

            stale_threshold = self.clock() - timedelta(
                minutes=settings.collection_stale_after_minutes
            )

            # Find active runs for this source
            active_runs = (
                session.query(CollectionRun)
                .filter(
                    CollectionRun.source_id == source_id,
                    CollectionRun.status == CollectionRunStatus.RUNNING,
                )
                .all()
            )

            now = self.clock()
            has_running = False
            for r in active_runs:
                started_at = r.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                if started_at < stale_threshold:
                    r.status = CollectionRunStatus.ABANDONED
                    r.completed_at = now
                    r.error_count = max(r.error_count or 0, 1)
                    r.error_summary = "Collection run was abandoned after exceeding the stale-run threshold."
                    source_repo.record_failure(
                        source_id,
                        now,
                        r.error_summary
                        or "Collection run was abandoned after exceeding the stale-run threshold.",
                    )
                else:
                    has_running = True

            if has_running:
                raise SourceAlreadyRunningError(
                    f"Source '{source.name}' is already running."
                )

            # Create running collection run record
            run = run_repo.create_running(source.id, trigger_type, started_at=now)
            run_id = run.id

            source_name = source.name
            source_type = source.source_type

            # Resolve max items limit
            config_max = source.config.get("max_items")
            if trigger_type == CollectionTriggerType.WEB:
                web_limit = settings.collection_web_max_items
                if config_max is not None:
                    resolved_max_items = min(int(config_max), web_limit)
                else:
                    resolved_max_items = web_limit
            elif max_items is not None:
                resolved_max_items = max_items
            elif config_max is not None:
                resolved_max_items = int(config_max)
            else:
                resolved_max_items = 100

            # Create immutable collection context
            context = CollectionContext(
                source_id=source.id,
                source_name=source.name,
                source_type=source.source_type,
                source_config=source.config,
                max_items=resolved_max_items,
                http=self._http_client,
            )

            session.commit()
        except IntegrityError:
            session.rollback()
            raise SourceAlreadyRunningError(
                f"Source with ID {source_id} is already running."
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        # 2. Collect Phase (No Transaction)
        start_time = time.monotonic()
        logger.info(
            "Collection started. source_id=%s source_type=%s trigger_type=%s collection_run_id=%s",
            source_id,
            source_type,
            trigger_type.value,
            run_id,
        )

        try:
            result = await collector.collect(context)
            items_collected_at = self.clock()
        except asyncio.CancelledError as e:
            finished_at = self.clock()
            error_summary = "Job execution was cancelled."

            # Record cancellation in DB (Separate Transaction)
            # Failure Mode: If finalization itself fails (e.g. DB unavailable),
            # we log the exception and let the run remain in 'running' state,
            # which will be recovered as 'abandoned' in the next stale recovery.
            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(
                    run_id=run_id,
                    completed_at=finished_at,
                    error_count=1,
                    error_summary=error_summary,
                )
                source_repo.record_failure(source_id, finished_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to finalize collection run on cancellation.")
            finally:
                session.close()

            raise e
        except (KeyboardInterrupt, SystemExit) as e:
            finished_at = self.clock()
            error_summary = f"Job execution terminated: {type(e).__name__}."

            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(
                    run_id=run_id,
                    completed_at=finished_at,
                    error_count=1,
                    error_summary=error_summary,
                )
                source_repo.record_failure(source_id, finished_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("Failed to finalize collection run on termination.")
            finally:
                session.close()
            raise e
        except Exception as e:
            # Fatal collector exceptions
            finished_at = self.clock()
            error_summary = sanitize_error(str(e))

            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(
                    run_id=run_id,
                    completed_at=finished_at,
                    error_count=1,
                    error_summary=error_summary,
                )
                source_repo.record_failure(source_id, finished_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception(
                    "Failed to finalize collection run on collector failure."
                )
            finally:
                session.close()

            return CollectionExecutionResult(
                run_id=run_id,
                status=CollectionRunStatus.FAILED,
                fetched_count=0,
                inserted_count=0,
                updated_count=0,
                duplicate_count=0,
                warning_count=0,
                error_count=1,
                signal_ids=[],
                error_summary=error_summary,
            )

        # 3. Ingest Phase (SignalIngestionService internally handles its own transaction)
        try:
            ingest_result = self.ingestion_service.ingest(
                source_id=source_id,
                source_type=source_type,
                collection_run_id=run_id,
                raw_items=result.items,
                collected_at=items_collected_at,
            )
        except Exception:
            logger.exception("Signal ingestion encountered a fatal error.")
            finished_at = self.clock()
            error_summary = "Signal ingestion failed."

            # Failure Mode: If finalization itself fails (e.g. DB unavailable),
            # we log/propagate the exception and allow the run to remain in 'running'
            # state, which will be recovered as 'abandoned' by stale recovery.
            session = self.session_factory()
            try:
                source_repo = SourceRepository(session)
                run_repo = CollectionRunRepository(session)
                run_repo.finish_failed(
                    run_id=run_id,
                    completed_at=finished_at,
                    fetched_count=len(result.items),
                    warning_count=len(result.warnings),
                    error_count=max(len(result.errors), 1),
                    error_summary=error_summary,
                    run_metadata=result.metadata,
                )
                source_repo.record_failure(source_id, finished_at, error_summary)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception(
                    "Failed to finalize collection run on ingestion failure."
                )
                raise
            finally:
                session.close()

            return CollectionExecutionResult(
                run_id=run_id,
                status=CollectionRunStatus.FAILED,
                fetched_count=len(result.items),
                inserted_count=0,
                updated_count=0,
                duplicate_count=0,
                warning_count=len(result.warnings),
                error_count=max(len(result.errors), 1),
                signal_ids=[],
                error_summary=error_summary,
            )

        # 4. Finalize Phase (Short Transaction)
        fetched_count = len(result.items)
        inserted_count = ingest_result.inserted_count
        updated_count = ingest_result.updated_count
        duplicate_count = ingest_result.duplicate_count
        signal_ids = ingest_result.signal_ids

        warning_count = len(result.warnings) + len(ingest_result.warnings)
        error_count = len(result.errors) + len(ingest_result.errors)

        all_errors = list(result.errors) + list(ingest_result.errors)
        if all_errors:
            raw_errors = ", ".join(err.message for err in all_errors)
            error_summary = sanitize_error(raw_errors)
        else:
            error_summary = None

        total_saved = inserted_count + updated_count + duplicate_count

        if error_count > 0:
            if total_saved >= 1:
                status = CollectionRunStatus.PARTIAL
            else:
                status = CollectionRunStatus.FAILED
        else:
            status = CollectionRunStatus.SUCCEEDED

        finished_at = self.clock()

        session = self.session_factory()
        try:
            source_repo = SourceRepository(session)
            run_repo = CollectionRunRepository(session)

            if status == CollectionRunStatus.SUCCEEDED:
                run_repo.finish_succeeded(
                    run_id=run_id,
                    completed_at=finished_at,
                    fetched_count=fetched_count,
                    inserted_count=inserted_count,
                    updated_count=updated_count,
                    duplicate_count=duplicate_count,
                    warning_count=warning_count,
                    run_metadata=result.metadata,
                )
                source_repo.record_success(source_id, finished_at)
            elif status == CollectionRunStatus.PARTIAL:
                run_repo.finish_partial(
                    run_id=run_id,
                    completed_at=finished_at,
                    fetched_count=fetched_count,
                    inserted_count=inserted_count,
                    updated_count=updated_count,
                    duplicate_count=duplicate_count,
                    warning_count=warning_count,
                    error_count=error_count,
                    error_summary=error_summary or "",
                    run_metadata=result.metadata,
                )
                source_repo.record_partial(
                    source_id,
                    success_at=finished_at,
                    failure_at=finished_at,
                    error_msg=error_summary or "",
                )
            else:
                run_repo.finish_failed(
                    run_id=run_id,
                    completed_at=finished_at,
                    fetched_count=fetched_count,
                    inserted_count=inserted_count,
                    updated_count=updated_count,
                    duplicate_count=duplicate_count,
                    warning_count=warning_count,
                    error_count=error_count,
                    error_summary=error_summary or "",
                    run_metadata=result.metadata,
                )
                source_repo.record_failure(source_id, finished_at, error_summary or "")

            session.commit()
        except Exception:
            session.rollback()
            # Failure Mode: If finalization itself fails (e.g. DB unavailable),
            # propagate the exception and let the run remain in 'running' state
            # so that it gets recovered by the stale-run recovery next time.
            logger.exception("Failed to finalize collection run in Finalize Phase.")
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

        # Sync warning_count to match db warnings if metadata was truncated
        from glintory.services.json_safety import sanitize_run_metadata as check_meta

        if result.metadata:
            _, was_truncated = check_meta(result.metadata)
            if was_truncated:
                warning_count += 1

        return CollectionExecutionResult(
            run_id=run_id,
            status=status,
            fetched_count=fetched_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
            duplicate_count=duplicate_count,
            warning_count=warning_count,
            error_count=error_count,
            signal_ids=signal_ids,
            error_summary=error_summary,
        )
