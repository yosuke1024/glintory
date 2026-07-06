from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from glintory.infrastructure.fts import (
    FTS5UnavailableError,
    check_fts5_available,
)


def test_check_fts5_available_success() -> None:
    """Verifies that check_fts5_available completes successfully on normal environments."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        check_fts5_available(conn)


def test_check_fts5_available_failure() -> None:
    """Verifies that check_fts5_available raises a sanitized FTS5UnavailableError when FTS5 is missing."""
    mock_conn = MagicMock()
    # Simulate the sqlite3 operational error "no such module: fts5"
    mock_conn.execute.side_effect = OperationalError(
        "CREATE VIRTUAL TABLE...", {}, Exception("no such module: fts5")
    )

    with pytest.raises(FTS5UnavailableError) as exc_info:
        check_fts5_available(mock_conn)

    # Verify exact user-facing error message is used
    assert str(exc_info.value) == "SQLite build with FTS5 support is required"
