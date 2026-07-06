import json
from typing import Any, cast
from unittest.mock import patch

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from glintory.cli import build_parser, main, run_cli
from glintory.cli_config import ConfigLoadError, load_json_object
from glintory.collectors.github import GitHubCollector
from glintory.collectors.hackernews import HackerNewsCollector
from glintory.collectors.rss import RSSCollector
from glintory.config import Settings
from glintory.domain.models import Base
from glintory.infrastructure.schema_status import (
    DatabaseSchemaError,
    check_schema_status,
)
from tests.fakes.collectors import SuccessfulFakeCollector


@pytest.fixture
def test_db_path(tmp_path):
    db_file = tmp_path / "test_glintory.sqlite3"
    db_url = f"sqlite:///{db_file}"

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    head_rev = script.get_heads()[0]

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
            {"version": head_rev},
        )

    engine.dispose()
    return db_file


@pytest.fixture
def mock_settings(test_db_path):
    db_url = f"sqlite:///{test_db_path}"
    # Use patch to inject settings
    s = Settings(database_url=db_url)
    return s


# --- 1. Parser Tests ---
def test_parser_help():
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--help"])
    assert excinfo.value.code == 0


def test_parser_version():
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0


def test_parser_unknown_command():
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["unknown"])
    assert excinfo.value.code == 2


def test_parser_source_add_missing_args():
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["source", "add", "--name", "test"])
    assert excinfo.value.code == 2


def test_parser_collect_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["collect", "--source", "test", "--all"])
    assert excinfo.value.code == 2

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["collect"])
    assert excinfo.value.code == 2


# --- 2. JSON Config Loader Tests ---
def test_json_loader_valid(tmp_path):
    f = tmp_path / "valid.json"
    f.write_text('{"key": "value"}', encoding="utf-8")
    data = load_json_object(str(f))
    assert data == {"key": "value"}


def test_json_loader_array_rejected(tmp_path):
    f = tmp_path / "array.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigLoadError, match="must be a JSON object"):
        load_json_object(str(f))


def test_json_loader_null_rejected(tmp_path):
    f = tmp_path / "null.json"
    f.write_text("null", encoding="utf-8")
    with pytest.raises(ConfigLoadError, match="must be a JSON object"):
        load_json_object(str(f))


def test_json_loader_invalid_json(tmp_path):
    f = tmp_path / "invalid.json"
    f.write_text('{"key": }', encoding="utf-8")
    with pytest.raises(ConfigLoadError, match="Invalid JSON format"):
        load_json_object(str(f))


def test_json_loader_utf8_invalid(tmp_path):
    f = tmp_path / "invalid_utf8.json"
    # Write invalid UTF-8 bytes
    f.write_bytes(b'{"key": \xff}')
    with pytest.raises(ConfigLoadError, match="Failed to decode"):
        load_json_object(str(f))


def test_json_loader_not_found():
    with pytest.raises(ConfigLoadError, match="does not exist"):
        load_json_object("nonexistent.json")


def test_json_loader_is_directory(tmp_path):
    with pytest.raises(ConfigLoadError, match="is a directory"):
        load_json_object(str(tmp_path))


def test_json_loader_too_large(tmp_path):
    f = tmp_path / "large.json"
    # Create file larger than 64KiB
    f.write_text('{"key": "' + ("x" * 70000) + '"}', encoding="utf-8")
    with pytest.raises(ConfigLoadError, match="exceeds the 64KiB limit"):
        load_json_object(str(f))


# --- 3. Config Validation Tests ---
def test_github_config_validation(mock_settings):
    collector = GitHubCollector(mock_settings)
    valid_cfg = {
        "repository_queries": [{"query": "python"}],
        "per_page": 50,
    }
    validated = collector.validate_config(valid_cfg)
    assert validated["per_page"] == 50
    assert len(cast(Any, validated["repository_queries"])) == 1

    # Invalid: no queries
    with pytest.raises(ValidationError):
        collector.validate_config({"per_page": 50})


def test_hackernews_config_validation(mock_settings):
    collector = HackerNewsCollector(mock_settings)
    valid_cfg = {
        "feeds": ["ask", "show"],
        "max_items_per_feed": 10,
    }
    validated = collector.validate_config(valid_cfg)
    assert validated["max_items_per_feed"] == 10
    assert validated["feeds"] == ["ask", "show"]


def test_rss_config_validation(mock_settings):
    collector = RSSCollector(mock_settings)
    valid_cfg = {
        "feed_url": "https://example.com/rss.xml",
        "max_items": 10,
    }
    validated = collector.validate_config(valid_cfg)
    assert validated["feed_url"] == "https://example.com/rss.xml"

    # Invalid URL safety (local address)
    with pytest.raises(ValidationError):
        collector.validate_config({"feed_url": "http://localhost/rss.xml"})


# --- 4. Schema Status Tests ---
def test_schema_status_success(test_db_path):
    engine = create_engine(f"sqlite:///{test_db_path}")
    # Should not raise exception
    check_schema_status(engine)
    engine.dispose()


def test_schema_status_uninitialized(tmp_path):
    # Empty DB
    db_file = tmp_path / "empty.sqlite3"
    engine = create_engine(f"sqlite:///{db_file}")
    with pytest.raises(DatabaseSchemaError, match="Database is not initialized"):
        check_schema_status(engine)
    engine.dispose()


def test_schema_status_outdated_revision(test_db_path):
    engine = create_engine(f"sqlite:///{test_db_path}")
    # Override version to make it outdated
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'old_rev'"))

    with pytest.raises(DatabaseSchemaError, match="Database is not initialized"):
        check_schema_status(engine)
    engine.dispose()


def test_schema_status_connection_fail():
    # Invalid path / unreachable engine
    engine = create_engine("sqlite:////nonexistent_dir/test.db")
    with pytest.raises(DatabaseSchemaError, match="Database is unavailable"):
        check_schema_status(engine)
    engine.dispose()


# --- 5. Source Command CLI Integration Tests ---
def test_source_add_success(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"], "max_items_per_feed": 5}', encoding="utf-8")

    with patch("glintory.cli.Settings", return_value=mock_settings):
        code = main(
            [
                "source",
                "add",
                "--name",
                "hn-test",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
    assert code == 0
    captured = capsys.readouterr()
    assert "Source created." in captured.out
    assert "Name: hn-test" in captured.out
    assert "Type: hackernews" in captured.out


def test_source_add_json_output(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"]}', encoding="utf-8")

    with patch("glintory.cli.Settings", return_value=mock_settings):
        code = main(
            [
                "source",
                "add",
                "--name",
                "hn-json",
                "--type",
                "hackernews",
                "--config",
                str(f),
                "--json",
            ]
        )
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["name"] == "hn-json"
    assert data["source_type"] == "hackernews"
    assert data["enabled"] is True


def test_source_add_duplicate(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"]}', encoding="utf-8")

    with patch("glintory.cli.Settings", return_value=mock_settings):
        code = main(
            [
                "source",
                "add",
                "--name",
                "hn-dup",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
        assert code == 0

        # Try to add again
        code = main(
            [
                "source",
                "add",
                "--name",
                "hn-dup",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
    assert code == 2
    captured = capsys.readouterr()
    assert "already exists" in captured.err


def test_source_list(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"]}', encoding="utf-8")

    with patch("glintory.cli.Settings", return_value=mock_settings):
        main(
            [
                "source",
                "add",
                "--name",
                "hn-b",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
        main(
            [
                "source",
                "add",
                "--name",
                "hn-a",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
        capsys.readouterr()  # clear outputs

        code = main(["source", "list"])
        assert code == 0
        captured = capsys.readouterr()
        # Sort name ascending: hn-a should appear before hn-b
        lines = captured.out.strip().split("\n")
        assert "hn-a" in lines[1]
        assert "hn-b" in lines[2]


def test_source_show_github(mock_settings, tmp_path, capsys):
    f = tmp_path / "gh_cfg.json"
    f.write_text(
        '{"repository_queries": [{"query": "django"}], "per_page": 20}',
        encoding="utf-8",
    )

    with patch("glintory.cli.Settings", return_value=mock_settings):
        main(
            [
                "source",
                "add",
                "--name",
                "gh-show",
                "--type",
                "github",
                "--config",
                str(f),
            ]
        )
        capsys.readouterr()

        code = main(["source", "show", "gh-show"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Repository queries: 1" in captured.out
        assert "Issue queries: 0" in captured.out
        assert "Per page: 20" in captured.out


def test_source_update(mock_settings, tmp_path, capsys):
    f1 = tmp_path / "hn_cfg1.json"
    f1.write_text('{"feeds": ["ask"]}', encoding="utf-8")
    f2 = tmp_path / "hn_cfg2.json"
    f2.write_text('{"feeds": ["show"], "max_items_per_feed": 40}', encoding="utf-8")

    with patch("glintory.cli.Settings", return_value=mock_settings):
        main(
            [
                "source",
                "add",
                "--name",
                "hn-upd",
                "--type",
                "hackernews",
                "--config",
                str(f1),
            ]
        )

        code = main(["source", "update", "hn-upd", "--config", str(f2)])
        assert code == 0

        # Verify change
        capsys.readouterr()
        main(["source", "show", "hn-upd"])
        captured = capsys.readouterr()
        assert "Max items per feed: 40" in captured.out


def test_source_enable_disable(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"]}', encoding="utf-8")

    with patch("glintory.cli.Settings", return_value=mock_settings):
        main(
            [
                "source",
                "add",
                "--name",
                "hn-state",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )

        # disable
        code = main(["source", "disable", "hn-state"])
        assert code == 0
        capsys.readouterr()

        main(["source", "show", "hn-state"])
        captured = capsys.readouterr()
        assert "Enabled: no" in captured.out

        # enable
        code = main(["source", "enable", "hn-state"])
        assert code == 0
        capsys.readouterr()

        main(["source", "show", "hn-state"])
        captured = capsys.readouterr()
        assert "Enabled: yes" in captured.out


# --- 6. Collection Command CLI Tests ---
@pytest.mark.anyio
async def test_collect_single_succeeded(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"]}', encoding="utf-8")

    fake_collector = SuccessfulFakeCollector("hackernews")
    with (
        patch("glintory.cli.Settings", return_value=mock_settings),
        patch(
            "glintory.collectors.registry.CollectorRegistry.get",
            return_value=fake_collector,
        ),
    ):
        parser = build_parser()
        # add source
        args = parser.parse_args(
            [
                "source",
                "add",
                "--name",
                "hn-col",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
        await run_cli(args)
        capsys.readouterr()

        # collect source
        args = parser.parse_args(["collect", "--source", "hn-col"])
        code = await run_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "Status: succeeded" in captured.out


@pytest.mark.anyio
async def test_collect_all_succeeded(mock_settings, tmp_path, capsys):
    f = tmp_path / "hn_cfg.json"
    f.write_text('{"feeds": ["ask"]}', encoding="utf-8")

    fake_collector = SuccessfulFakeCollector("hackernews")
    with (
        patch("glintory.cli.Settings", return_value=mock_settings),
        patch(
            "glintory.collectors.registry.CollectorRegistry.get",
            return_value=fake_collector,
        ),
    ):
        parser = build_parser()
        # add source 1
        args = parser.parse_args(
            [
                "source",
                "add",
                "--name",
                "col-1",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
        await run_cli(args)
        # add source 2
        args = parser.parse_args(
            [
                "source",
                "add",
                "--name",
                "col-2",
                "--type",
                "hackernews",
                "--config",
                str(f),
            ]
        )
        await run_cli(args)
        capsys.readouterr()

        # collect all
        args = parser.parse_args(["collect", "--all"])
        code = await run_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "Sources: 2" in captured.out
        assert "Succeeded: 2" in captured.out
