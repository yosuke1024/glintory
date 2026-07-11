from datetime import UTC, datetime

import pytest

from glintory.services.schedule_calculation import (
    DueOccurrence,
    calculate_due_occurrence,
)


def test_calculate_due_occurrence_future():
    current = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 11, 59, 59, tzinfo=UTC)
    result = calculate_due_occurrence(
        current_next_run_at=current,
        now=now,
        interval_minutes=60,
    )
    assert result is None


def test_calculate_due_occurrence_exactly_due():
    current = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
    result = calculate_due_occurrence(
        current_next_run_at=current,
        now=now,
        interval_minutes=60,
    )
    assert result == DueOccurrence(
        scheduled_for=datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC),
        next_run_at=datetime(2026, 7, 11, 13, 0, 0, tzinfo=UTC),
        coalesced_count=0,
    )


def test_calculate_due_occurrence_multiple_missed():
    current = datetime(2026, 7, 11, 10, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 11, 13, 30, 0, tzinfo=UTC)
    result = calculate_due_occurrence(
        current_next_run_at=current,
        now=now,
        interval_minutes=60,
    )
    assert result == DueOccurrence(
        scheduled_for=datetime(2026, 7, 11, 13, 0, 0, tzinfo=UTC),
        next_run_at=datetime(2026, 7, 11, 14, 0, 0, tzinfo=UTC),
        coalesced_count=3,
    )


def test_calculate_due_occurrence_validation():
    current_naive = datetime(2026, 7, 11, 12, 0, 0)
    current_utc = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
    now_naive = datetime(2026, 7, 11, 12, 0, 0)
    now_utc = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)

    # Naive current
    with pytest.raises(
        ValueError, match="current_next_run_at must be timezone-aware UTC"
    ):
        calculate_due_occurrence(
            current_next_run_at=current_naive, now=now_utc, interval_minutes=60
        )

    # Naive now
    with pytest.raises(ValueError, match="now must be timezone-aware UTC"):
        calculate_due_occurrence(
            current_next_run_at=current_utc, now=now_naive, interval_minutes=60
        )

    # Invalid interval too small
    with pytest.raises(
        ValueError, match="interval_minutes must be between 5 and 525600"
    ):
        calculate_due_occurrence(
            current_next_run_at=current_utc, now=now_utc, interval_minutes=4
        )

    # Invalid interval too large
    with pytest.raises(
        ValueError, match="interval_minutes must be between 5 and 525600"
    ):
        calculate_due_occurrence(
            current_next_run_at=current_utc, now=now_utc, interval_minutes=525601
        )
