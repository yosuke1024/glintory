import hashlib
import json
import os
import pathlib
import sys
import tarfile
import zipfile
from unittest import mock

import alembic.command
import alembic.config
import pytest

# Resolve sys.path so we can import modules in this project
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import create_submission  # type: ignore
import insert_fixture_db  # type: ignore
import submission_pipeline  # type: ignore

from glintory.config import settings
from glintory.infrastructure.database import reset_db_connections


def get_file_sha256(filepath: str) -> str:
    if not os.path.exists(filepath):
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def calculate_dir_hash(directory: pathlib.Path) -> dict[str, str]:
    hashes = {}
    if not directory.exists():
        return hashes
    for root, _, files in os.walk(directory):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, directory)
            if (
                "__pycache__" in rel_path
                or ".pytest_cache" in rel_path
                or ".venv" in rel_path
            ):
                continue
            # Ignore standard pytest execution logs to prevent false positives
            if rel_path in ("pytest.log", "pytest_migration.log", "uv_sync.log"):
                continue
            h = hashlib.sha256()
            try:
                with open(full_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        h.update(chunk)
                hashes[rel_path] = h.hexdigest()
            except Exception:
                pass
    return hashes


@pytest.fixture(scope="module", autouse=True)
def check_workspace_immutability():
    logs_dir = PROJECT_ROOT / "logs"
    dist_dir = PROJECT_ROOT / "dist"
    zip_path = PROJECT_ROOT / "submission-glintory.zip"
    sha_path = PROJECT_ROOT / "submission-glintory.zip.sha256"
    attestation_path = PROJECT_ROOT / "submission-glintory.attestation.json"
    final_log_path = PROJECT_ROOT / "final-zip-verification.log"

    logs_before = calculate_dir_hash(logs_dir)
    dist_before = calculate_dir_hash(dist_dir)

    zip_exists_before = zip_path.exists()
    zip_hash_before = get_file_sha256(str(zip_path)) if zip_exists_before else None

    sha_exists_before = sha_path.exists()
    sha_hash_before = get_file_sha256(str(sha_path)) if sha_exists_before else None

    att_exists_before = attestation_path.exists()
    att_hash_before = (
        get_file_sha256(str(attestation_path)) if att_exists_before else None
    )

    final_log_exists_before = final_log_path.exists()
    final_log_hash_before = (
        get_file_sha256(str(final_log_path)) if final_log_exists_before else None
    )

    yield

    logs_after = calculate_dir_hash(logs_dir)
    dist_after = calculate_dir_hash(dist_dir)

    zip_exists_after = zip_path.exists()
    zip_hash_after = get_file_sha256(str(zip_path)) if zip_exists_after else None

    sha_exists_after = sha_path.exists()
    sha_hash_after = get_file_sha256(str(sha_path)) if sha_exists_after else None

    att_exists_after = attestation_path.exists()
    att_hash_after = (
        get_file_sha256(str(attestation_path)) if att_exists_after else None
    )

    final_log_exists_after = final_log_path.exists()
    final_log_hash_after = (
        get_file_sha256(str(final_log_path)) if final_log_exists_after else None
    )

    assert logs_before == logs_after, (
        "Workspace logs directory was modified during tests!"
    )
    assert dist_before == dist_after, (
        "Workspace dist directory was modified during tests!"
    )
    assert zip_exists_before == zip_exists_after, (
        "Workspace zip file existence changed!"
    )
    if zip_exists_before:
        assert zip_hash_before == zip_hash_after, "Workspace zip file content changed!"
    assert sha_exists_before == sha_exists_after, (
        "Workspace sha file existence changed!"
    )
    if sha_exists_before:
        assert sha_hash_before == sha_hash_after, "Workspace sha file content changed!"
    assert att_exists_before == att_exists_after, (
        "Workspace attestation file existence changed!"
    )
    if att_exists_before:
        assert att_hash_before == att_hash_after, (
            "Workspace attestation file content changed!"
        )
    assert final_log_exists_before == final_log_exists_after, (
        "Workspace final log file existence changed!"
    )
    if final_log_exists_before:
        assert final_log_hash_before == final_log_hash_after, (
            "Workspace final log file content changed!"
        )


@pytest.fixture
def clean_db_file(tmp_path):
    db_file = tmp_path / "test_verify.db"
    db_url = f"sqlite:///{db_file}"
    return db_file, db_url


def test_migration_and_fixture_seeding_order(clean_db_file):
    db_file, db_url = clean_db_file
    original_url = settings.database_url

    try:
        settings.database_url = db_url
        os.environ["DATABASE_URL"] = db_url
        os.environ["GLINTORY_DATABASE_URL"] = db_url
        reset_db_connections()

        with pytest.raises(SystemExit) as exit_info:
            insert_fixture_db.main()
        assert exit_info.value.code == 1

        alembic_cfg = alembic.config.Config(str(PROJECT_ROOT / "alembic.ini"))
        alembic.command.upgrade(alembic_cfg, "head")

        insert_fixture_db.main()
    finally:
        settings.database_url = original_url
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("GLINTORY_DATABASE_URL", None)
        reset_db_connections()


def test_run_command_logged_relative_path(tmp_path):
    cwd_dir = tmp_path / "workspace"
    cwd_dir.mkdir()
    log_file_rel = cwd_dir / "logs" / "test_run.log"

    res = submission_pipeline.run_command_logged(
        cmd=["echo", "Hello World"],
        cwd=cwd_dir,
        log_file=log_file_rel,
        manifest_log_path="logs/test_run.log",
    )

    assert log_file_rel.exists()
    assert log_file_rel.read_text(encoding="utf-8").strip() == "Hello World"
    assert res["status"] == "passed"
    assert res["log_file"] == "logs/test_run.log"


def test_package_hash_reproducibility(tmp_path):
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    (dir1 / "a.txt").write_bytes(b"hello")
    (dir1 / "b.txt").write_bytes(b"world")

    (dir1 / "SUBMISSION_MANIFEST.json").write_bytes(b"manifest")
    (dir1 / "submission-glintory.zip.sha256").write_bytes(b"sha256")

    hash1 = submission_pipeline.calculate_dir_content_hash(dir1)

    zip_path = tmp_path / "test_pkg.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(dir1 / "a.txt", "a.txt")
        zf.write(dir1 / "b.txt", "b.txt")
        zf.write(dir1 / "SUBMISSION_MANIFEST.json", "SUBMISSION_MANIFEST.json")

    hash2 = submission_pipeline.calculate_zip_content_hash(zip_path)
    assert hash1 == hash2


def test_manifest_hash_tampering_detection(tmp_path):
    zip_path = tmp_path / "test_tampered.zip"
    manifest_data = {
        "package_content_hash": "correcthash_xxx",
        "git_commit_before": "hash",
        "git_commit_after": "hash",
        "working_tree_clean_before": True,
        "working_tree_clean_after": True,
        "quality_gates": {},
    }

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(PROJECT_ROOT / "pyproject.toml", "pyproject.toml")
        zf.writestr("SUBMISSION_MANIFEST.json", json.dumps(manifest_data))

    recalc_hash = submission_pipeline.calculate_zip_content_hash(zip_path)
    assert recalc_hash != manifest_data["package_content_hash"]


def test_working_tree_clean_after_check():
    with mock.patch("subprocess.check_output") as mock_git:

        def mock_git_call(cmd, *args, **kwargs):
            return {
                ("git", "rev-parse", "HEAD"): b"commit_sha_123\n",
                ("git", "status", "--porcelain"): b"M modified_file.py\n",
                ("git", "log", "-1", "--format=%T"): b"tree_sha_123\n",
            }[tuple(cmd)]

        mock_git.side_effect = mock_git_call

        with pytest.raises(SystemExit) as exit_info:
            create_submission.main()
        assert exit_info.value.code == 1


def test_self_verification_failure_cleanup(tmp_path, monkeypatch):
    parent_logs_dir = PROJECT_ROOT / "logs"
    parent_dist_dir = PROJECT_ROOT / "dist"
    parent_zip = PROJECT_ROOT / "submission-glintory.zip"

    parent_logs_before = calculate_dir_hash(parent_logs_dir)
    parent_dist_before = calculate_dir_hash(parent_dist_dir)
    parent_zip_exists = parent_zip.exists()
    parent_zip_hash = get_file_sha256(str(parent_zip)) if parent_zip_exists else None

    monkeypatch.chdir(tmp_path)

    fake_git_dir = tmp_path / ".git"
    fake_git_dir.mkdir()

    config = submission_pipeline.SubmissionConfig(root_dir=tmp_path)

    with (
        mock.patch("subprocess.check_output") as mock_git,
        mock.patch("subprocess.run") as mock_run,
    ):

        def mock_git_call(cmd, *args, **kwargs):
            cmd_tuple = tuple(cmd)
            if cmd_tuple == ("git", "rev-parse", "HEAD"):
                return b"fake_commit_123\n"
            if cmd_tuple == ("git", "status", "--porcelain"):
                return b""
            if cmd_tuple == ("git", "ls-files"):
                return b"pyproject.toml\nscripts/create_submission.py\n"
            raise ValueError(f"Unexpected git command: {cmd}")

        mock_git.side_effect = mock_git_call

        def mock_run_side_effect(cmd, *args, **kwargs):
            cwd = kwargs.get("cwd", "")
            stdout = kwargs.get("stdout")

            if stdout and hasattr(stdout, "write"):
                if "ruff" in cmd:
                    stdout.write("All checks passed!\n")
                elif "pyright" in cmd:
                    stdout.write("0 errors\n")
                elif "validate-contract" in cmd:
                    stdout.write("validation passed\n")
                elif "inspect-jurypress-feed" in cmd:
                    stdout.write("opp_f1111111111111111111111111111111\n")
                elif "pytest" in cmd:
                    if (
                        "temp_verify" in str(cwd)
                        or "self_verify" in str(cmd)
                        or "verify" in str(cwd)
                    ):
                        stdout.write("mocked command output\n")
                    else:
                        stdout.write("435 passed in 79.57s\n")
                else:
                    stdout.write("some output\n")
                stdout.flush()

            if "temp_verify" in str(cwd) or "verify" in str(cwd):
                ret = mock.Mock()
                ret.returncode = 1
                return ret

            if "git" in cmd and "archive" in cmd:
                tar_path = pathlib.Path(cmd[4])
                with tarfile.open(tar_path, "w") as tar:
                    dummy_toml = tmp_path / "dummy_pyproject.toml"
                    dummy_toml.write_text("pyproject")
                    tar.add(dummy_toml, arcname="pyproject.toml")

                    dummy_script = tmp_path / "dummy_script.py"
                    dummy_script.write_text("script")
                    tar.add(dummy_script, arcname="scripts/create_submission.py")

                dist_dir = config.source_dir / "dist"
                dist_dir.mkdir(parents=True, exist_ok=True)

                opp_dir = (
                    dist_dir / "opportunities/opp_f1111111111111111111111111111111/en"
                )
                opp_dir.mkdir(parents=True, exist_ok=True)

                for p in [
                    "index.html",
                    "opportunities/index.html",
                    "opportunities/opp_f1111111111111111111111111111111/index.html",
                    "opportunities/opp_f1111111111111111111111111111111/en/index.html",
                ]:
                    (dist_dir / p).write_text("dummy")
                (dist_dir / "sitemap.xml").write_text(
                    "opp_f1111111111111111111111111111111"
                )

            ret = mock.Mock()
            ret.returncode = 0
            return ret

        mock_run.side_effect = mock_run_side_effect

        with pytest.raises(SystemExit) as exit_info:
            create_submission.main(config)

        assert exit_info.value.code == 1

    assert calculate_dir_hash(parent_logs_dir) == parent_logs_before, (
        "Parent logs/ was modified!"
    )
    assert calculate_dir_hash(parent_dist_dir) == parent_dist_before, (
        "Parent dist/ was modified!"
    )
    assert parent_zip.exists() == parent_zip_exists
    if parent_zip_exists:
        assert get_file_sha256(str(parent_zip)) == parent_zip_hash

    assert not config.zip_filename.exists(), (
        "Temporary ZIP should have been cleaned up!"
    )
    assert not config.sha_filename.exists(), (
        "Temporary SHA should have been cleaned up!"
    )
    assert not config.attestation_filename.exists(), (
        "Temporary attestation JSON should have been cleaned up!"
    )
    assert not config.final_log_filename.exists()


def test_validate_log_content(tmp_path):
    log_file = tmp_path / "test.log"

    # 1. Mock log detection
    log_file.write_text("mocked command output\n")
    with pytest.raises(ValueError, match="mocked command output"):
        submission_pipeline.validate_log_content("pytest", log_file, "passed", 0)

    # 2. NUL bytes detection
    log_file.write_bytes(b"some normal text\0with null bytes")
    with pytest.raises(ValueError, match="contains NUL bytes"):
        submission_pipeline.validate_log_content("pytest", log_file, "passed", 0)

    # 3. Empty log detection
    log_file.write_bytes(b"")
    with pytest.raises(ValueError, match="is empty"):
        submission_pipeline.validate_log_content("pytest", log_file, "passed", 0)

    # 4. Exit code mismatch
    log_file.write_text("passed")
    with pytest.raises(ValueError, match="has 'passed' status but non-zero exit code"):
        submission_pipeline.validate_log_content("pytest", log_file, "passed", 1)
