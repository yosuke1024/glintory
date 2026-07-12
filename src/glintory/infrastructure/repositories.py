import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import CollectionRun, Signal, Source
from glintory.domain.operations import (
    CollectionRunAlreadyFinalizedError,
    CollectionRunNotFoundError,
    CollectionTriggerType,
)
from glintory.domain.signals import NormalizedSignal, SignalIdentityCollisionError
from glintory.infrastructure.error_sanitizer import sanitize_error
from glintory.services.json_safety import sanitize_run_metadata


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

    def create(
        self,
        name: str,
        source_type: str,
        config: dict[str, Any],
        enabled: bool = True,
        auth_required: bool = False,
    ) -> Source:
        name = name.strip()
        if not name:
            raise ValueError("Source name cannot be empty.")
        if len(name) > 100:
            raise ValueError("Source name cannot exceed 100 characters.")

        if self.get_by_name(name) is not None:
            raise ValueError(f"Source with name '{name}' already exists.")

        source = Source(
            name=name,
            source_type=source_type,
            config=config,
            enabled=enabled,
            auth_required=auth_required,
        )
        self.session.add(source)
        try:
            self.session.flush()
        except IntegrityError as e:
            raise ValueError(f"Database constraint violated: {e.orig}") from e
        return source

    def list_all(self) -> list[Source]:
        return self.session.query(Source).order_by(Source.name.asc()).all()

    def list_enabled(self) -> list[Source]:
        return (
            self.session.query(Source)
            .filter(Source.enabled)
            .order_by(Source.name.asc())
            .all()
        )

    def get_by_name(self, name: str) -> Source | None:
        return self.session.query(Source).filter(Source.name == name).first()

    def get_by_identifier(self, identifier: str) -> Source | None:
        source = self.get_by_name(identifier)
        if source:
            return source
        try:
            uuid.UUID(identifier)
            return self.get_by_id(identifier)
        except ValueError:
            return None

    def update_config(self, source_id: str, config: dict[str, Any]) -> Source:
        source = self.get_by_id(source_id)
        if not source:
            raise ValueError(f"Source with ID {source_id} not found.")
        source.config = config
        source.updated_at = datetime.now(UTC)
        self.session.flush()
        return source

    def set_enabled(self, source_id: str, enabled: bool) -> Source:
        source = self.get_by_id(source_id)
        if not source:
            raise ValueError(f"Source with ID {source_id} not found.")
        source.enabled = enabled
        source.updated_at = datetime.now(UTC)
        self.session.flush()
        return source


class CollectionRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_running(
        self,
        source_id: str,
        trigger_type: CollectionTriggerType = CollectionTriggerType.CLI,
        started_at: datetime | None = None,
    ) -> CollectionRun:
        run = CollectionRun(
            source_id=source_id,
            trigger_type=trigger_type,
            status=CollectionRunStatus.RUNNING,
            started_at=started_at or datetime.now(UTC),
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
        inserted_count: int,
        updated_count: int,
        duplicate_count: int,
        warning_count: int,
        skipped_count: int = 0,
        run_metadata: Mapping[str, object] | None = None,
    ) -> None:
        run = self.session.get(CollectionRun, run_id)
        if not run:
            raise CollectionRunNotFoundError(f"Collection run {run_id} not found.")
        if run.status != CollectionRunStatus.RUNNING:
            raise CollectionRunAlreadyFinalizedError(
                f"Collection run {run_id} is already in terminal status: {run.status}"
            )

        meta_dict, was_truncated = sanitize_run_metadata(run_metadata)
        if was_truncated:
            warning_count += 1

        run.status = CollectionRunStatus.SUCCEEDED
        run.completed_at = completed_at
        run.fetched_count = fetched_count
        run.inserted_count = inserted_count
        run.updated_count = updated_count
        run.duplicate_count = duplicate_count
        run.warning_count = warning_count
        run.error_count = 0
        run.skipped_count = skipped_count
        run.run_metadata = meta_dict

    def finish_partial(
        self,
        run_id: str,
        completed_at: datetime,
        fetched_count: int,
        inserted_count: int,
        updated_count: int,
        duplicate_count: int,
        warning_count: int,
        error_count: int,
        error_summary: str,
        skipped_count: int = 0,
        error_type: str | None = None,
        sanitized_error_message: str | None = None,
        run_metadata: Mapping[str, object] | None = None,
    ) -> None:
        run = self.session.get(CollectionRun, run_id)
        if not run:
            raise CollectionRunNotFoundError(f"Collection run {run_id} not found.")
        if run.status != CollectionRunStatus.RUNNING:
            raise CollectionRunAlreadyFinalizedError(
                f"Collection run {run_id} is already in terminal status: {run.status}"
            )

        meta_dict, was_truncated = sanitize_run_metadata(run_metadata)
        if was_truncated:
            warning_count += 1

        run.status = CollectionRunStatus.PARTIAL
        run.completed_at = completed_at
        run.fetched_count = fetched_count
        run.inserted_count = inserted_count
        run.updated_count = updated_count
        run.duplicate_count = duplicate_count
        run.warning_count = warning_count
        run.error_count = error_count
        run.error_summary = sanitize_error(error_summary)
        run.skipped_count = skipped_count
        run.error_type = error_type
        run.sanitized_error_message = sanitized_error_message
        run.run_metadata = meta_dict

    def finish_failed(
        self,
        *,
        run_id: str,
        completed_at: datetime,
        fetched_count: int = 0,
        inserted_count: int = 0,
        updated_count: int = 0,
        duplicate_count: int = 0,
        warning_count: int = 0,
        error_count: int = 1,
        error_summary: str,
        skipped_count: int = 0,
        error_type: str | None = None,
        sanitized_error_message: str | None = None,
        run_metadata: Mapping[str, object] | None = None,
    ) -> None:
        if error_count < 1:
            raise ValueError("error_count must be at least 1")

        run = self.session.get(CollectionRun, run_id)
        if not run:
            raise CollectionRunNotFoundError(f"Collection run {run_id} not found.")
        if run.status != CollectionRunStatus.RUNNING:
            raise CollectionRunAlreadyFinalizedError(
                f"Collection run {run_id} is already in terminal status: {run.status}"
            )

        meta_dict, was_truncated = sanitize_run_metadata(run_metadata)
        if was_truncated:
            warning_count += 1

        run.status = CollectionRunStatus.FAILED
        run.completed_at = completed_at
        run.fetched_count = fetched_count
        run.inserted_count = inserted_count
        run.updated_count = updated_count
        run.duplicate_count = duplicate_count
        run.warning_count = warning_count
        run.error_count = error_count
        run.error_summary = sanitize_error(error_summary)
        run.skipped_count = skipped_count
        run.error_type = error_type
        run.sanitized_error_message = sanitized_error_message
        run.run_metadata = meta_dict


class SignalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_external_id(self, source_id: str, external_id: str) -> Signal | None:
        return (
            self.session.query(Signal)
            .filter(Signal.source_id == source_id, Signal.external_id == external_id)
            .first()
        )

    def find_by_canonical_url(
        self, source_id: str, canonical_url: str
    ) -> Signal | None:
        return (
            self.session.query(Signal)
            .filter(
                Signal.source_id == source_id, Signal.canonical_url == canonical_url
            )
            .first()
        )

    def insert(self, signal: NormalizedSignal) -> Signal:
        from glintory.domain.enums import SignalDocumentKind, SourceSpecificity
        
        doc_kind = signal.document_kind or SignalDocumentKind.UNKNOWN
        opp_anchor = False if signal.opportunity_anchor is None else signal.opportunity_anchor
        disc_eligible = False if signal.discovery_eligible is None else signal.discovery_eligible
        src_spec = signal.source_specificity or SourceSpecificity.UNKNOWN

        sig = Signal(
            source_id=signal.source_id,
            collection_run_id=signal.collection_run_id,
            external_id=signal.external_id,
            canonical_url=signal.canonical_url,
            title=signal.title,
            excerpt=signal.excerpt,
            author=signal.author,
            published_at=signal.published_at,
            collected_at=signal.collected_at,
            language=signal.language,
            signal_type=signal.signal_type,
            signal_role=signal.signal_role,
            categories=list(signal.categories),
            tags=list(signal.tags),
            metrics=dict(signal.metrics),
            raw_metadata=dict(signal.raw_metadata),
            content_hash=signal.content_hash,
            freshness_score=signal.freshness_score,
            source_quality_score=signal.source_quality_score,
            document_kind=doc_kind,
            opportunity_anchor=opp_anchor,
            discovery_eligible=disc_eligible,
            source_specificity=src_spec,
            created_at=signal.collected_at,
            updated_at=signal.collected_at,
        )
        self.session.add(sig)
        self.session.flush()
        return sig

    def update_existing(self, existing: Signal, incoming: NormalizedSignal) -> None:
        if existing.canonical_url == incoming.canonical_url:
            if existing.external_id is None and incoming.external_id is not None:
                existing.external_id = incoming.external_id
            elif (
                existing.external_id is not None
                and incoming.external_id is not None
                and existing.external_id != incoming.external_id
            ):
                raise SignalIdentityCollisionError(
                    f"Identity collision: Different external_id {incoming.external_id} for URL {incoming.canonical_url}"
                )
        elif (
            existing.external_id == incoming.external_id
            and existing.external_id is not None
        ):
            other = (
                self.session.query(Signal)
                .filter(
                    Signal.source_id == incoming.source_id,
                    Signal.canonical_url == incoming.canonical_url,
                    Signal.id != existing.id,
                )
                .first()
            )
            if other is not None:
                raise SignalIdentityCollisionError(
                    f"Identity collision: Canonical URL {incoming.canonical_url} already used by another signal"
                )
            existing.canonical_url = incoming.canonical_url

        existing.collection_run_id = incoming.collection_run_id
        existing.collected_at = incoming.collected_at
        existing.title = incoming.title
        existing.excerpt = incoming.excerpt
        existing.author = incoming.author
        existing.published_at = incoming.published_at
        existing.language = incoming.language
        existing.signal_type = incoming.signal_type
        existing.signal_role = incoming.signal_role
        existing.categories = list(incoming.categories)
        existing.tags = list(incoming.tags)
        existing.metrics = dict(incoming.metrics)
        existing.raw_metadata = dict(incoming.raw_metadata)
        existing.content_hash = incoming.content_hash
        existing.freshness_score = incoming.freshness_score
        existing.source_quality_score = incoming.source_quality_score
        from glintory.domain.enums import SignalDocumentKind, SourceSpecificity
        existing.document_kind = incoming.document_kind or SignalDocumentKind.UNKNOWN
        existing.opportunity_anchor = False if incoming.opportunity_anchor is None else incoming.opportunity_anchor
        existing.discovery_eligible = False if incoming.discovery_eligible is None else incoming.discovery_eligible
        existing.source_specificity = incoming.source_specificity or SourceSpecificity.UNKNOWN
