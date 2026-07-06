import pathlib

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text


class DatabaseSchemaError(Exception):
    pass


def get_alembic_config_path() -> pathlib.Path:
    current = pathlib.Path(__file__).resolve()
    for parent in current.parents:
        ini_path = parent / "alembic.ini"
        if ini_path.exists():
            return ini_path
    raise FileNotFoundError("alembic.ini not found")


def check_schema_status(engine: Engine) -> None:
    """Verifies that the database is accessible and has all migrations applied.

    Raises DatabaseSchemaError if check fails.
    """
    # 1. DB connection check
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        raise DatabaseSchemaError("Database is unavailable.") from e

    # 2. Read Alembic config
    try:
        ini_path = get_alembic_config_path()
        alembic_cfg = Config(str(ini_path))
        script = ScriptDirectory.from_config(alembic_cfg)
        expected_heads = script.get_heads()
    except Exception as e:
        raise DatabaseSchemaError(
            "Failed to load Alembic migration configurations."
        ) from e

    # 3. Read current DB revision
    try:
        with engine.connect() as conn:
            # Check if alembic_version table exists
            # In SQLite, checking this by querying sqlite_master is robust.
            res = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                )
            )
            if not res.fetchone():
                raise DatabaseSchemaError(
                    "Database is not initialized. Run: uv run alembic upgrade head"
                )

            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()
    except DatabaseSchemaError:
        raise
    except Exception as e:
        raise DatabaseSchemaError(
            "Database is not initialized. Run: uv run alembic upgrade head"
        ) from e

    # 4. Compare revision
    if not expected_heads:
        # If no migration exists in scripts (should not happen)
        return

    head_rev = expected_heads[0]
    if current_rev is None or current_rev != head_rev:
        raise DatabaseSchemaError(
            "Database is not initialized. Run: uv run alembic upgrade head"
        )
