import contextlib
import pathlib
import sqlite3
from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from glintory.config import settings

# Engine references that can be overridden for testing
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def reset_db_connections() -> None:
    """
    Resets the global engine and session local caches.
    Used for testing when settings.database_url changes.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine(database_url: str | None = None) -> Engine:
    """
    Returns the SQLAlchemy Engine. Creates one if it doesn't exist.
    """
    global _engine

    # If database_url is provided, it is likely from tests, so recreate Engine
    if database_url is not None:
        return _create_engine_instance(database_url)

    if _engine is None:
        _engine = _create_engine_instance(settings.database_url)

    return _engine


def _create_engine_instance(db_url: str) -> Engine:
    """
    Creates and configures a SQLAlchemy engine with SQLite pragmas.
    """
    # 1. Automatically create parent directory for file-based SQLite databases
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
        if db_path and db_path != ":memory:":
            path = pathlib.Path(db_path).resolve()
            # Ignore directory creation failures (e.g. read-only filesystem or permission errors)
            # so that actual connection attempts handle the error correctly and bubble it up through check_database_connection.
            with contextlib.suppress(Exception):
                path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(db_url)

    # Register connection listeners to apply SQLite-specific pragmas
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.execute("PRAGMA busy_timeout = 5000;")

            # Apply WAL mode only for file-based SQLite databases.
            # WAL mode is not suitable or safe for in-memory databases.
            cursor.execute("PRAGMA database_list;")
            db_list = cursor.fetchall()
            for db in db_list:
                # db is a tuple like (seq, name, file)
                # If 'file' is not empty and not None, it is a file-backed database.
                if db[2]:
                    cursor.execute("PRAGMA journal_mode = WAL;")
                    break
            cursor.close()

    return engine


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """
    Returns the SessionLocal factory. Creates one if it doesn't exist.
    """
    global _SessionLocal

    if database_url is not None:
        engine = get_engine(database_url)
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)

    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI Dependency to yield a database session.
    """
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def check_database_connection(db: Session) -> bool:
    """
    Checks if the database is reachable by executing a simple SELECT 1 statement.
    """
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
