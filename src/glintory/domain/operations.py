from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from glintory.domain.enums import CollectionRunStatus


class CollectionTriggerType(StrEnum):
    CLI = "cli"
    WEB = "web"
    SCHEDULED = "scheduled"


@dataclass(frozen=True, slots=True)
class SourceOperationItem:
    id: str
    name: str
    source_type: str
    enabled: bool
    auth_required: bool

    config_summary: str | Mapping[str, object]

    latest_run_id: str | None
    latest_run_status: CollectionRunStatus | None
    latest_run_started_at: datetime | None
    latest_run_finished_at: datetime | None

    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    is_running: bool


@dataclass(frozen=True, slots=True)
class CollectionRunListItem:
    id: str
    source_id: str
    source_name: str
    source_type: str

    trigger_type: CollectionTriggerType
    status: CollectionRunStatus

    started_at: datetime
    finished_at: datetime | None

    fetched_count: int
    inserted_count: int
    updated_count: int
    duplicate_count: int
    warning_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class CollectionRunDetail:
    id: str
    source_id: str
    source_name: str
    source_type: str

    trigger_type: CollectionTriggerType
    status: CollectionRunStatus

    started_at: datetime
    finished_at: datetime | None

    fetched_count: int
    inserted_count: int
    updated_count: int
    duplicate_count: int
    warning_count: int
    error_count: int

    safe_error_summary: str | None
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ManualCollectionResult:
    source_id: str
    source_name: str
    collection_run_id: str
    status: CollectionRunStatus

    fetched_count: int
    inserted_count: int
    updated_count: int
    duplicate_count: int
    warning_count: int
    error_count: int


class SourceNotFoundError(ValueError):
    """Raised when a source is not found."""

    pass


class SourceDisabledError(ValueError):
    """Raised when an operation is requested on a disabled source."""

    pass


class SourceAlreadyRunningError(ValueError):
    """Raised when attempting to run a source that is already running."""

    pass


class CollectionRunNotFoundError(ValueError):
    """Raised when a collection run is not found."""

    pass


class CollectionRunAlreadyFinalizedError(ValueError):
    """Raised when attempting to finalize a collection run that is already in a terminal status."""

    pass


class CollectionOperationError(ValueError):
    """Base exception for source collection operation errors."""

    pass
