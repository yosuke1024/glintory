from dataclasses import dataclass

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session


class FTS5UnavailableError(RuntimeError):
    """Exception raised when SQLite FTS5 extension is not available."""

    pass


def check_fts5_available(connection: Connection) -> None:
    """Verifies if FTS5 support is enabled in the current SQLite environment.

    Creates a temporary virtual table and drops it immediately.
    Raises FTS5UnavailableError if FTS5 is not supported.
    """
    try:
        connection.execute(
            text("CREATE VIRTUAL TABLE temp.glintory_fts5_probe USING fts5(value);")
        )
        connection.execute(text("DROP TABLE temp.glintory_fts5_probe;"))
    except Exception:
        # Wrap sqlite exception to prevent leaking SQL query or database credentials
        raise FTS5UnavailableError(
            "SQLite build with FTS5 support is required"
        ) from None


@dataclass(frozen=True, slots=True)
class FTSIndexStatus:
    signal_count: int
    indexed_count: int
    is_consistent: bool


def get_fts_index_status(session: Session) -> FTSIndexStatus:
    """Compares the total signal count in database against the FTS5 index count.

    This function does not auto-repair. Used by tests or future diagnostic tools.
    """
    # Verify FTS5 is supported before querying
    check_fts5_available(session.connection())

    # Query total signals count
    signal_count = session.execute(text("SELECT COUNT(*) FROM signals")).scalar()
    if signal_count is None:
        signal_count = 0

    # Query total indexed signals count
    try:
        indexed_count = session.execute(
            text("SELECT COUNT(*) FROM signals_fts")
        ).scalar()
    except (DBAPIError, OperationalError) as e:
        # Re-raise clean error or let the database system exception bubble up
        # (will be handled by routing layer to return 503 if table does not exist)
        raise e

    if indexed_count is None:
        indexed_count = 0

    is_consistent = signal_count == indexed_count
    return FTSIndexStatus(
        signal_count=signal_count,
        indexed_count=indexed_count,
        is_consistent=is_consistent,
    )


def rebuild_fts_index(session: Session) -> None:
    """Rebuilds the FTS5 index from the source signals table."""
    session.execute(text("INSERT INTO signals_fts(signals_fts) VALUES ('rebuild');"))
    session.commit()
