import asyncio
import logging
import time
from collections.abc import Callable, Sequence
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
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.ingestion_service = ingestion_service or SignalIngestionService(
            session_factory
        )
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
                inserted_count=0,
                updated_count=0,
                duplicate_count=0,
                warning_count=0,
                error_count=1,
                signal_ids=[],
                error_summary=error_summary,
            )

        # 3. Ingest signals using SignalIngestionService
        ingest_result = self.ingestion_service.ingest(
            source_id=source_id,
            source_type=source_type,
            collection_run_id=run_id,
            raw_items=result.items,
            collected_at=completed_at,
        )

        # 4. Integrate Collector results and Ingestion results
        fetched_count = len(result.items)
        inserted_count = ingest_result.inserted_count
        updated_count = ingest_result.updated_count
        duplicate_count = ingest_result.duplicate_count
        signal_ids = ingest_result.signal_ids

        warning_count = len(result.warnings) + len(ingest_result.warnings)
        error_count = len(result.errors) + len(ingest_result.errors)

        all_errors = list(result.errors) + list(ingest_result.errors)
        if all_errors:
            raw_errors = ", ".join(e.message for e in all_errors)
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

        session = self.session_factory()
        try:
            source_repo = SourceRepository(session)
            run_repo = CollectionRunRepository(session)

            if status == CollectionRunStatus.SUCCEEDED:
                run_repo.finish_succeeded(
                    run_id,
                    completed_at,
                    fetched_count,
                    inserted_count,
                    updated_count,
                    duplicate_count,
                    warning_count,
                )
                source_repo.record_success(source_id, completed_at)
            elif status == CollectionRunStatus.PARTIAL:
                run_repo.finish_partial(
                    run_id,
                    completed_at,
                    fetched_count,
                    inserted_count,
                    updated_count,
                    duplicate_count,
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
                run_repo.finish_failed(run_id, completed_at, error_summary or "")
                source_repo.record_failure(source_id, completed_at, error_summary or "")

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Count normalization vs persistence errors for logging
        normalization_error_count = 0
        persistence_error_count = 0
        normalizer_error_codes = {
            "invalid_url",
            "empty_title",
            "unsupported_item_type",
            "naive_datetime_not_allowed",
            "metadata_too_large",
            "normalization_error",
        }
        for err in ingest_result.errors:
            if err.code in normalizer_error_codes:
                normalization_error_count += 1
            else:
                persistence_error_count += 1

        logger.info(
            "Collection finished. source_id=%s source_name=%s source_type=%s "
            "collection_run_id=%s status=%s fetched_count=%d warning_count=%d "
            "error_count=%d duration_ms=%d inserted_count=%d updated_count=%d "
            "duplicate_count=%d normalized_count=%d normalization_error_count=%d "
            "persistence_error_count=%d",
            source_id,
            source_name,
            source_type,
            run_id,
            status.value,
            fetched_count,
            warning_count,
            error_count,
            duration_ms,
            inserted_count,
            updated_count,
            duplicate_count,
            total_saved,
            normalization_error_count,
            persistence_error_count,
        )

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
