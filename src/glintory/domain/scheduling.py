from enum import StrEnum
from dataclasses import dataclass
from datetime import datetime

class ScheduleExecutionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED_BUSY = "skipped_busy"
    SKIPPED_DISABLED = "skipped_disabled"
    ABANDONED = "abandoned"

@dataclass(frozen=True, slots=True)
class SourceScheduleItem:
    source_id: str
    source_name: str
    source_type: str
    source_enabled: bool

    schedule_enabled: bool
    interval_minutes: int
    next_run_at: datetime

    last_execution_status: ScheduleExecutionStatus | None
    last_execution_at: datetime | None

    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True, slots=True)
class ClaimedScheduleExecution:
    execution_id: str
    source_id: str
    source_name: str
    source_type: str
    source_enabled: bool

    scheduled_for: datetime
    coalesced_count: int

@dataclass(frozen=True, slots=True)
class ScheduleExecutionItem:
    id: str
    source_id: str | None
    source_name: str
    source_type: str

    scheduled_for: datetime
    started_at: datetime
    completed_at: datetime | None
    status: ScheduleExecutionStatus

    collection_run_id: str | None
    coalesced_count: int
    safe_error_summary: str | None

@dataclass(frozen=True, slots=True)
class SchedulerTickResult:
    tick_started_at: datetime
    tick_completed_at: datetime

    due_schedule_count: int
    claimed_execution_count: int
    succeeded_count: int
    partial_count: int
    failed_count: int
    skipped_busy_count: int
    skipped_disabled_count: int
    abandoned_count: int

    execution_ids: tuple[str, ...]
    warnings: tuple[str, ...]


# Domain Errors
class ScheduleNotFoundError(Exception):
    pass

class ScheduleAlreadyExistsError(Exception):
    pass

class InvalidScheduleError(Exception):
    pass

class SchedulerLeaseUnavailableError(Exception):
    pass

class SchedulerLeaseLostError(Exception):
    pass

class ScheduleExecutionNotFoundError(Exception):
    pass

class ScheduleExecutionAlreadyFinalizedError(Exception):
    pass
