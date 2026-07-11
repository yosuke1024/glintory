from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class DueOccurrence:
    scheduled_for: datetime
    next_run_at: datetime
    coalesced_count: int


def calculate_due_occurrence(
    *,
    current_next_run_at: datetime,
    now: datetime,
    interval_minutes: int,
) -> DueOccurrence | None:
    # Validate timezone awareness (must be UTC)
    if current_next_run_at.tzinfo is None or current_next_run_at.tzinfo.utcoffset(
        current_next_run_at
    ) != timedelta(0):
        raise ValueError("current_next_run_at must be timezone-aware UTC datetime.")
    if now.tzinfo is None or now.tzinfo.utcoffset(now) != timedelta(0):
        raise ValueError("now must be timezone-aware UTC datetime.")
    if not (5 <= interval_minutes <= 525600):
        raise ValueError("interval_minutes must be between 5 and 525600.")

    # If the scheduled run is in the future, it is not due yet
    if current_next_run_at > now:
        return None

    # Calculate missed intervals using integer seconds to avoid float issues
    diff_seconds = int((now - current_next_run_at).total_seconds())
    interval_seconds = interval_minutes * 60

    missed_count = diff_seconds // interval_seconds

    scheduled_for = current_next_run_at + timedelta(
        seconds=missed_count * interval_seconds
    )
    next_run_at = scheduled_for + timedelta(seconds=interval_seconds)

    return DueOccurrence(
        scheduled_for=scheduled_for,
        next_run_at=next_run_at,
        coalesced_count=missed_count,
    )
