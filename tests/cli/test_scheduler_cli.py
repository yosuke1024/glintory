import json
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.cli import main
from glintory.domain.models import Source


@pytest.fixture
def cli_db_env(tmp_path, monkeypatch):
    db_file = tmp_path / "cli_test.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Force settings and env
    monkeypatch.setenv("GLINTORY_DATABASE_URL", db_url)
    monkeypatch.setattr("glintory.config.settings.database_url", db_url)

    # Initialize DB using Alembic migrations
    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine)

    # Initialize some mock data
    session = session_factory()
    src = Source(id="src-1234-5678", name="test-source", source_type="rss")
    session.add(src)
    session.commit()
    session.close()

    yield db_url

    # Reset cache connections to close locks
    from glintory.infrastructure.database import reset_db_connections

    reset_db_connections()

    engine.dispose()

    if db_file.exists():
        db_file.unlink()


def test_schedule_cli_set_and_show(cli_db_env, capsys):
    # 1. Set schedule
    code = main(["schedule", "set", "--source", "test-source", "--every-minutes", "60"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "Schedule updated." in out
    assert "Interval: 60 minutes" in out

    # 2. Show schedule
    code = main(["schedule", "show", "--source", "test-source"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "Source: test-source" in out
    assert "Interval: 60 minutes" in out

    # 3. Disable schedule
    code = main(["schedule", "disable", "--source", "test-source"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "disabled" in out

    # 4. Enable schedule
    code = main(["schedule", "enable", "--source", "test-source"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "enabled" in out


def test_schedule_cli_list(cli_db_env, capsys):
    # Set schedule first
    main(["schedule", "set", "--source", "test-source", "--every-minutes", "60"])
    capsys.readouterr()

    # List schedules
    code = main(["schedule", "list"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "Source: test-source" in out
    assert "Interval: 60 minutes" in out

    # List schedules in JSON format
    code = main(["schedule", "list", "--json"])
    assert code == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["source_name"] == "test-source"
    assert data[0]["interval_minutes"] == 60


def test_schedule_cli_validation(cli_db_env, capsys):
    # Invalid interval
    code = main(["schedule", "set", "--source", "test-source", "--every-minutes", "4"])
    assert code == 2

    # Naive date format
    code = main(
        [
            "schedule",
            "set",
            "--source",
            "test-source",
            "--every-minutes",
            "60",
            "--first-run-at",
            "2026-07-12T00:00:00",
        ]
    )
    assert code == 2

    # Past date
    code = main(
        [
            "schedule",
            "set",
            "--source",
            "test-source",
            "--every-minutes",
            "60",
            "--first-run-at",
            "2026-07-10T00:00:00Z",
        ]
    )
    assert code == 2


def test_scheduler_cli_run_once(cli_db_env, capsys):
    # 1. Run once (should succeed but do nothing since no schedules are due)
    code = main(["scheduler", "run", "--once"])
    assert code == 0

    # 2. Run once in JSON mode
    code = main(["scheduler", "run", "--once", "--json"])
    assert code == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["exit_code"] == 0
    assert "tick" in data
    assert "owner_token" not in data
    assert "owner_token_hidden" not in data


def test_scheduler_cli_run_continuous_rejected(cli_db_env, capsys):
    # Continuous mode should return exit code 2
    code = main(["scheduler", "run"])
    assert code == 2
    out, err = capsys.readouterr()
    assert "Continuous scheduler mode has been removed" in err
