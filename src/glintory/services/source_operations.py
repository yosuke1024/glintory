import datetime as dt
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

from glintory.collectors.registry import CollectorRegistry
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.operations import (
    CollectionRunDetail,
    CollectionRunListItem,
    CollectionRunNotFoundError,
    CollectionTriggerType,
    ManualCollectionResult,
    SourceNotFoundError,
    SourceOperationItem,
)
from glintory.infrastructure.source_operations import SourceOperationsRepository
from glintory.services.collection import CollectionService


class SourceOperationsService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        registry: CollectorRegistry,
        collection_service: CollectionService,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.collection_service = collection_service

    def _validate_uuid(self, uuid_str: str) -> None:
        try:
            uuid.UUID(uuid_str)
        except ValueError as e:
            raise ValueError(f"Invalid UUID: {uuid_str}") from e

    def list_sources(self) -> Sequence[SourceOperationItem]:
        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            items_with_config = repo.list_sources_with_config()

            enriched_items = []
            for item, config in items_with_config:
                try:
                    collector = self.registry.get(item.source_type)
                    summary = collector.get_config_summary(config)
                except Exception:
                    summary = {}

                enriched_items.append(
                    SourceOperationItem(
                        id=item.id,
                        name=item.name,
                        source_type=item.source_type,
                        enabled=item.enabled,
                        auth_required=item.auth_required,
                        config_summary=summary,
                        latest_run_id=item.latest_run_id,
                        latest_run_status=item.latest_run_status,
                        latest_run_started_at=item.latest_run_started_at,
                        latest_run_finished_at=item.latest_run_finished_at,
                        last_success_at=item.last_success_at,
                        last_failure_at=item.last_failure_at,
                        consecutive_failures=item.consecutive_failures,
                        is_running=item.is_running,
                    )
                )
            return enriched_items
        finally:
            session.close()

    def get_source_detail(self, source_id: str) -> SourceOperationItem:
        self._validate_uuid(source_id)
        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            raw_source = repo.get_source_detail(source_id)
            if not raw_source:
                raise SourceNotFoundError(f"Source with ID {source_id} not found.")

            runs = repo.list_source_collection_runs(source_id, limit=1)
            latest_run = runs[0] if runs else None

            try:
                collector = self.registry.get(raw_source.source_type)
                summary = collector.get_config_summary(raw_source.config)
            except Exception:
                summary = {}

            is_running = (
                latest_run.status == CollectionRunStatus.RUNNING
                if latest_run
                else False
            )

            latest_run_started_at = latest_run.started_at if latest_run else None
            latest_run_finished_at = latest_run.finished_at if latest_run else None

            last_success_at = raw_source.last_success_at
            if last_success_at and last_success_at.tzinfo is None:
                last_success_at = last_success_at.replace(tzinfo=dt.UTC)

            last_failure_at = raw_source.last_failure_at
            if last_failure_at and last_failure_at.tzinfo is None:
                last_failure_at = last_failure_at.replace(tzinfo=dt.UTC)

            return SourceOperationItem(
                id=raw_source.id,
                name=raw_source.name,
                source_type=raw_source.source_type,
                enabled=raw_source.enabled,
                auth_required=raw_source.auth_required,
                config_summary=summary,
                latest_run_id=latest_run.id if latest_run else None,
                latest_run_status=latest_run.status if latest_run else None,
                latest_run_started_at=latest_run_started_at,
                latest_run_finished_at=latest_run_finished_at,
                last_success_at=last_success_at,
                last_failure_at=last_failure_at,
                consecutive_failures=raw_source.consecutive_failures,
                is_running=is_running,
            )
        finally:
            session.close()

    def enable_source(self, source_id: str) -> None:
        self._validate_uuid(source_id)
        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            repo.set_enabled(source_id, True)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def disable_source(self, source_id: str) -> None:
        self._validate_uuid(source_id)
        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            repo.set_enabled(source_id, False)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def collect_now(self, source_id: str) -> ManualCollectionResult:
        self._validate_uuid(source_id)

        execution_result = await self.collection_service.run_source(
            source_id=source_id,
            trigger_type=CollectionTriggerType.WEB,
        )

        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            source = repo.get_source_detail(source_id)
            source_name = source.name if source else "Unknown"
        finally:
            session.close()

        return ManualCollectionResult(
            source_id=source_id,
            source_name=source_name,
            collection_run_id=execution_result.run_id,
            status=execution_result.status,
            fetched_count=execution_result.fetched_count,
            inserted_count=execution_result.inserted_count,
            updated_count=execution_result.updated_count,
            duplicate_count=execution_result.duplicate_count,
            warning_count=execution_result.warning_count,
            error_count=execution_result.error_count,
        )

    def list_collection_runs(
        self,
        *,
        source_id: str | None = None,
        status: str | None = None,
        trigger_type: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[Sequence[CollectionRunListItem], int]:
        if page < 1:
            raise ValueError("Page number must be 1 or greater.")
        if per_page not in (10, 25, 50, 100):
            raise ValueError("per_page must be one of 10, 25, 50, or 100.")

        if status:
            from glintory.domain.enums import CollectionRunStatus

            if status not in [e.value for e in CollectionRunStatus]:
                raise ValueError(f"Invalid status value: {status}")

        if trigger_type:
            from glintory.domain.operations import CollectionTriggerType

            if trigger_type not in [e.value for e in CollectionTriggerType]:
                raise ValueError(f"Invalid trigger value: {trigger_type}")

        offset = (page - 1) * per_page

        if source_id:
            self._validate_uuid(source_id)

        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            return repo.list_collection_runs(
                source_id=source_id,
                status=status,
                trigger_type=trigger_type,
                limit=per_page,
                offset=offset,
            )
        finally:
            session.close()

    def get_collection_run_detail(self, run_id: str) -> CollectionRunDetail:
        self._validate_uuid(run_id)
        session = self.session_factory()
        try:
            repo = SourceOperationsRepository(session)
            detail = repo.get_collection_run_detail(run_id)
            if not detail:
                raise CollectionRunNotFoundError(
                    f"Collection run with ID {run_id} not found."
                )
            return detail
        finally:
            session.close()
