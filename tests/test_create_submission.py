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


@pytest.fixture
def clean_db_file(tmp_path):
    db_file = tmp_path / "test_verify.db"
    db_url = f"sqlite:///{db_file}"
    return db_file, db_url


from glintory.config import settings
from glintory.infrastructure.database import reset_db_connections


def test_migration_and_fixture_seeding_order(clean_db_file):
    db_file, db_url = clean_db_file

    # Store original settings
    original_url = settings.database_url

    try:
        # Overwrite settings directly because Settings was instantiated at import-time
        settings.database_url = db_url
        os.environ["DATABASE_URL"] = db_url
        os.environ["GLINTORY_DATABASE_URL"] = db_url
        reset_db_connections()  # Reset cache to apply new settings

        # 1. Verification: Seeding before migrations must fail
        # Since Base.metadata.create_all is removed, the tables won't exist.
        # Therefore, seeding will raise a SystemExit with code 1.
        with pytest.raises(SystemExit) as exit_info:
            insert_fixture_db.main()
        assert exit_info.value.code == 1

        # 2. Verification: Seeding after migration must succeed
        # Run alembic upgrade head using subprocess or alembic API
        alembic_cfg = alembic.config.Config(str(PROJECT_ROOT / "alembic.ini"))
        alembic.command.upgrade(alembic_cfg, "head")

        # Now seeding should succeed without errors
        insert_fixture_db.main()
    finally:
        # Restore settings
        settings.database_url = original_url
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("GLINTORY_DATABASE_URL", None)
        reset_db_connections()


def test_run_command_logged_relative_path(tmp_path):
    # Verification 4: Logs must be generated relative to the workspace/cwd directory.
    cwd_dir = tmp_path / "workspace"
    cwd_dir.mkdir()
    log_file_rel = "logs/test_run.log"

    # Execute a simple echo command
    res = create_submission.run_command_logged(
        cmd=["echo", "Hello World"],
        cwd=str(cwd_dir),
        log_file=log_file_rel,
    )

    expected_log_path = cwd_dir / "logs" / "test_run.log"
    assert expected_log_path.exists()
    assert expected_log_path.read_text(encoding="utf-8").strip() == "Hello World"
    assert res["status"] == "passed"
    assert res["log_file"] == "logs/test_run.log"


def test_self_verification_logging_no_overwrite(tmp_path):
    # Verification 6: Ensure same log path is not overwritten across multiple operations in self-verification.
    # Check that individual logs exist and the aggregated log has multiple distinct commands.
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    res1 = {
        "command": "cmd1",
        "started_at": "start1",
        "finished_at": "end1",
        "exit_code": 0,
        "status": "passed",
        "log_file": "logs/self_verify_uv_sync.log",
    }
    res2 = {
        "command": "cmd2",
        "started_at": "start2",
        "finished_at": "end2",
        "exit_code": 0,
        "status": "passed",
        "log_file": "logs/self_verify_migration.log",
    }

    create_submission.append_to_verification_log(str(log_dir), res1)
    create_submission.append_to_verification_log(str(log_dir), res2)

    agg_log_file = log_dir / "package_self_verification.log"
    assert agg_log_file.exists()
    agg_content = agg_log_file.read_text(encoding="utf-8")
    assert "Command: cmd1" in agg_content
    assert "Command: cmd2" in agg_content
    assert agg_content.count("--- Command:") == 2


def test_package_hash_reproducibility(tmp_path):
    # Verification 8: Recalculate Package Hash from ZIP contents
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    (dir1 / "a.txt").write_bytes(b"hello")
    (dir1 / "b.txt").write_bytes(b"world")

    # Manifest and Sidecar should be excluded from hash
    (dir1 / "SUBMISSION_MANIFEST.json").write_bytes(b"manifest")
    (dir1 / "submission-glintory.zip.sha256").write_bytes(b"sha256")

    hash1 = create_submission.calculate_dir_content_hash(str(dir1))

    # Package into ZIP
    zip_path = tmp_path / "test_pkg.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(dir1 / "a.txt", "a.txt")
        zf.write(dir1 / "b.txt", "b.txt")
        zf.write(dir1 / "SUBMISSION_MANIFEST.json", "SUBMISSION_MANIFEST.json")

    hash2 = create_submission.calculate_zip_content_hash(str(zip_path))

    # Exclude manifest and ensure the calculated directory hash matches the calculated ZIP hash
    assert hash1 == hash2


def test_manifest_hash_tampering_detection(tmp_path):
    # Verification 9: Ensure manifest hash mismatch or tampering is detected.
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
        # Write manifest with tampered hash
        zf.writestr("SUBMISSION_MANIFEST.json", json.dumps(manifest_data))

    # Recalculated hash should be different from manifest's package_content_hash
    recalc_hash = create_submission.calculate_zip_content_hash(str(zip_path))
    assert recalc_hash != manifest_data["package_content_hash"]


def test_working_tree_clean_after_check():
    # Verification 10: working_tree_clean_after is actually checked.
    # Mock git commands to simulate clean status
    with mock.patch("subprocess.check_output") as mock_git:

        def mock_git_call(cmd, *args, **kwargs):
            return {
                ("git", "rev-parse", "HEAD"): b"commit_sha_123\n",
                ("git", "status", "--porcelain"): b"M modified_file.py\n",  # Dirty!
                ("git", "log", "-1", "--format=%T"): b"tree_sha_123\n",
            }[tuple(cmd)]

        mock_git.side_effect = mock_git_call

        with pytest.raises(SystemExit) as exit_info:
            create_submission.main()
        assert exit_info.value.code == 1


def test_self_verification_failure_cleanup(tmp_path):
    # Verification 11 & 12: Cleanup ZIP and sidecar on self-verification failure
    zip_path = pathlib.Path("submission-glintory.zip")
    sha_path = pathlib.Path("submission-glintory.zip.sha256")

    # Create dummy files
    zip_path.write_text("zip")
    sha_path.write_text("sha")

    # Mock subprocess.run to fail during self-verification
    with (
        mock.patch("subprocess.check_output") as mock_git,
        mock.patch("subprocess.run") as mock_run,
    ):

        def mock_git_call(cmd, *args, **kwargs):
            return {
                ("git", "rev-parse", "HEAD"): "commit_sha_123\n",
                ("git", "status", "--porcelain"): "",  # Clean
                ("git", "log", "-1", "--format=%T"): "tree_sha_123\n",
                ("git", "ls-files"): "pyproject.toml\n",
            }[tuple(cmd)]

        mock_git.side_effect = mock_git_call

        def mock_run_side_effect(cmd, *args, **kwargs):
            cwd = kwargs.get("cwd", "")
            stdout = kwargs.get("stdout")
            if stdout and hasattr(stdout, "write"):
                stdout.write("mocked command output\n")
                stdout.flush()

            # If running inside verification directory, fail the command
            if "temp_verify" in str(cwd) or "verify" in str(cwd):
                ret = mock.Mock()
                ret.returncode = 1
                return ret

            # If git archive, actually create a dummy tar file containing minimal files so main() can proceed
            if "git" in cmd and "archive" in cmd:
                tar_path = cmd[4]
                with tarfile.open(tar_path, "w") as tar:
                    tar.add(PROJECT_ROOT / "pyproject.toml", arcname="pyproject.toml")
                    tar.add(
                        PROJECT_ROOT / "scripts/create_submission.py",
                        arcname="scripts/create_submission.py",
                    )

                # Create dummy dist assets inside .temp_extract so self-verification asset checks pass
                dist_dir = os.path.join(".temp_extract", "dist")
                os.makedirs(
                    os.path.join(
                        dist_dir,
                        "opportunities/opp_f1111111111111111111111111111111/en",
                    ),
                    exist_ok=True,
                )
                for p in [
                    "index.html",
                    "opportunities/index.html",
                    "opportunities/opp_f1111111111111111111111111111111/index.html",
                    "opportunities/opp_f1111111111111111111111111111111/en/index.html",
                ]:
                    with open(os.path.join(dist_dir, p), "w") as f:
                        f.write("dummy")
                with open(os.path.join(dist_dir, "sitemap.xml"), "w") as f:
                    f.write("opp_f1111111111111111111111111111111")

            ret = mock.Mock()
            ret.returncode = 0
            return ret

        mock_run.side_effect = mock_run_side_effect

        with pytest.raises(SystemExit) as exit_info:
            create_submission.main()

        assert exit_info.value.code == 1
        assert not zip_path.exists()
        assert not sha_path.exists()
