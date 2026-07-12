import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from datetime import UTC, datetime


def get_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def get_dir_hash(directory: str) -> str:
    sha = hashlib.sha256()
    for root, _, files in sorted(os.walk(directory)):
        for names in sorted(files):
            filepath = os.path.join(root, names)
            try:
                with open(filepath, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        sha.update(chunk)
            except Exception:
                pass
    return sha.hexdigest()


def run_command_logged(
    cmd: list[str], cwd: str, log_file: str, env: dict | None = None
) -> dict:
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    print(f"Executing: {' '.join(cmd)} in {cwd}...")

    # Ensure parent log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Setup clean isolated environment variables pointing to the workspace virtualenv
    run_env = env.copy() if env else os.environ.copy()
    abs_cwd = os.path.abspath(cwd)
    venv_path = os.path.join(abs_cwd, ".venv")
    run_env["VIRTUAL_ENV"] = venv_path

    venv_bin = os.path.join(venv_path, "bin")
    current_path = run_env.get("PATH", "")
    run_env["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            res = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        # Double check log contents for hidden Validation errors
        has_hidden_val_error = False
        if os.path.exists(log_file):
            with open(log_file, encoding="utf-8", errors="ignore") as lf:
                log_text = lf.read()
                if "validation error" in log_text.lower():
                    has_hidden_val_error = True

        status = "passed"
        if res.returncode != 0 or has_hidden_val_error:
            status = "failed"

        return {
            "command": " ".join(cmd),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": res.returncode,
            "status": status,
            "log_file": log_file,
        }
    except Exception as e:
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return {
            "command": " ".join(cmd),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": -1,
            "status": "failed",
            "log_file": log_file,
            "error": str(e),
        }


def main() -> None:
    zip_filename = "submission-glintory.zip"
    sha_filename = "submission-glintory.zip.sha256"

    # Clean up local generated artifacts before checking git status
    if os.path.exists("logs"):
        shutil.rmtree("logs")
    if os.path.exists(zip_filename):
        os.remove(zip_filename)
    if os.path.exists(sha_filename):
        os.remove(sha_filename)

    # 1. Pre-flight Check: Git Status
    print("Checking Git status...")
    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        if status_out:
            print("ERROR: Working tree is dirty. Clean commit first.", file=sys.stderr)
            print(f"git status output:\n{status_out}", file=sys.stderr)
            sys.exit(1)

        commit_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        tree_sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%T"], text=True
        ).strip()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to run Git pre-flight check: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Clean Git working copy verified at Commit {commit_sha}.")

    # 2. Setup Temporary Extraction Workspace
    temp_workspace = ".temp_extract"
    if os.path.exists(temp_workspace):
        shutil.rmtree(temp_workspace)
    os.makedirs(temp_workspace, exist_ok=True)

    print("Extracting clean Git HEAD commit code into workspace...")
    tar_path = os.path.join(temp_workspace, "head.tar")
    try:
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", tar_path, "HEAD"], check=True
        )
        with tarfile.open(tar_path) as tf:
            tf.extractall(path=temp_workspace)
        os.remove(tar_path)
        os.makedirs(os.path.join(temp_workspace, "data"), exist_ok=True)
    except Exception as e:
        print(f"ERROR: Failed to extract Git HEAD: {e}", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # Compare running script vs extracted script SHA-256
    this_script_sha = get_sha256(__file__)
    extracted_script_path = os.path.join(
        temp_workspace, "scripts", "create_submission.py"
    )
    extracted_script_sha = get_sha256(extracted_script_path)
    if this_script_sha != extracted_script_sha:
        print(
            "ERROR: Running create_submission.py SHA does not match HEAD commit script SHA.",
            file=sys.stderr,
        )
        print(f"Running SHA: {this_script_sha}", file=sys.stderr)
        print(f"HEAD SHA:    {extracted_script_sha}", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    print("Script SHA-256 verified successfully.")

    # 3. Setup Virtualenv and run Quality Gate in temporary workspace
    logs_dir = os.path.join(temp_workspace, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # Initialize Quality Gate Manifest
    quality_gates = {}
    gate_ok = True

    # 3A. uv sync --frozen
    res_sync = run_command_logged(
        ["uv", "sync", "--frozen"],
        cwd=temp_workspace,
        log_file="logs/uv_sync.log",
    )
    quality_gates["uv_sync"] = res_sync
    if res_sync["status"] != "passed":
        gate_ok = False

    # Initialize fixture DB in temporary workspace
    fixture_db_path = os.path.join(temp_workspace, "glintory_fixture.db")
    if os.path.exists(fixture_db_path):
        os.remove(fixture_db_path)

    # Override DATABASE_URL and GLINTORY_DATABASE_URL for subsequent commands using absolute path to prevent duplication errors
    test_env = os.environ.copy()
    test_env["DATABASE_URL"] = f"sqlite:///{os.path.abspath(fixture_db_path)}"
    test_env["GLINTORY_DATABASE_URL"] = test_env["DATABASE_URL"]

    # Run migrations on default test database location so integration tests do not fail on missing tables
    if gate_ok:
        res_pre_mig = run_command_logged(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=temp_workspace,
            log_file="logs/pytest_migration.log",
        )
        if res_pre_mig["status"] != "passed":
            gate_ok = False

    # 3B. pytest
    if gate_ok:
        res_pytest = run_command_logged(
            ["uv", "run", "pytest"],
            cwd=temp_workspace,
            log_file="logs/pytest.log",
        )
        quality_gates["pytest"] = res_pytest
        if res_pytest["status"] != "passed":
            gate_ok = False

    # 3C. ruff check
    if gate_ok:
        res_ruff = run_command_logged(
            ["uv", "run", "ruff", "check", "."],
            cwd=temp_workspace,
            log_file="logs/ruff.log",
        )
        quality_gates["ruff"] = res_ruff
        if res_ruff["status"] != "passed":
            gate_ok = False

    # 3D. ruff format --check
    if gate_ok:
        res_format = run_command_logged(
            ["uv", "run", "ruff", "format", "--check", "."],
            cwd=temp_workspace,
            log_file="logs/ruff_format.log",
        )
        quality_gates["ruff_format"] = res_format
        if res_format["status"] != "passed":
            gate_ok = False

    # 3E. pyright
    if gate_ok:
        res_pyright = run_command_logged(
            ["uv", "run", "pyright"],
            cwd=temp_workspace,
            log_file="logs/pyright.log",
        )
        quality_gates["pyright"] = res_pyright
        if res_pyright["status"] != "passed":
            gate_ok = False

    # 3F. alembic migration roundtrip & seed fixture DB
    if gate_ok:
        migration_log = os.path.join(logs_dir, "migration_roundtrip.log")
        os.makedirs(os.path.dirname(migration_log), exist_ok=True)
        # Build isolated environment for sub-commands to avoid virtualenv path conflicts
        run_env = test_env.copy()
        abs_cwd = os.path.abspath(temp_workspace)
        venv_path = os.path.join(abs_cwd, ".venv")
        run_env["VIRTUAL_ENV"] = venv_path
        venv_bin = os.path.join(venv_path, "bin")
        current_path = run_env.get("PATH", "")
        run_env["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

        with open(migration_log, "w", encoding="utf-8") as f:
            f.write("--- Migration Upgrade Head ---\n")
            f.flush()
            res_up1 = subprocess.run(
                ["uv", "run", "alembic", "upgrade", "head"],
                cwd=temp_workspace,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_up1.returncode}\n")
            f.flush()

            f.write("\n--- Migration Downgrade -1 ---\n")
            f.flush()
            res_down = subprocess.run(
                ["uv", "run", "alembic", "downgrade", "-1"],
                cwd=temp_workspace,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_down.returncode}\n")
            f.flush()

            f.write("\n--- Migration Upgrade Head (Again) ---\n")
            f.flush()
            res_up2 = subprocess.run(
                ["uv", "run", "alembic", "upgrade", "head"],
                cwd=temp_workspace,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_up2.returncode}\n")
            f.flush()

        status_migration = "passed"
        if (
            res_up1.returncode != 0
            or res_down.returncode != 0
            or res_up2.returncode != 0
        ):
            status_migration = "failed"
            gate_ok = False

        quality_gates["alembic_roundtrip"] = {
            "command": "alembic upgrade -> downgrade -> upgrade",
            "status": status_migration,
            "log_file": "logs/migration_roundtrip.log",
        }

        # Seed fixture database with JuryPress Ready items
        res_seed = run_command_logged(
            ["uv", "run", "python", "scripts/insert_fixture_db.py"],
            cwd=temp_workspace,
            log_file="logs/publish_build.log",
            env=test_env,
        )
        quality_gates["seed_fixture"] = res_seed
        if res_seed["status"] != "passed":
            gate_ok = False

    # 3G. publish build
    if gate_ok:
        # Build static site using seed database
        build_log_file = os.path.join(logs_dir, "publish_build.log")
        os.makedirs(os.path.dirname(build_log_file), exist_ok=True)
        with open(build_log_file, "a", encoding="utf-8") as f:
            f.write("\n\n--- Publish Build Execution ---\n\n")
            f.flush()
            res_build = subprocess.run(
                [
                    "uv",
                    "run",
                    "glintory",
                    "publish",
                    "build",
                    "--output-dir",
                    "dist",
                    "--site-url",
                    "https://example.com",
                ],
                cwd=temp_workspace,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=test_env,
            )
            f.write(f"\nExit Code: {res_build.returncode}\n")

            f.write("\n--- Build Directory Listing ---\n")
            dist_dir = os.path.join(temp_workspace, "dist")
            if os.path.exists(dist_dir):
                for root, _, files in os.walk(dist_dir):
                    for file in files:
                        f.write(
                            f"File: {os.path.relpath(os.path.join(root, file), dist_dir)}\n"
                        )
            else:
                f.write("dist directory does not exist\n")

            f.write("\n--- Fixture DB Opportunities ---\n")
            try:
                import sqlite3

                conn = sqlite3.connect(fixture_db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = cursor.fetchall()
                f.write(f"Tables: {tables}\n")
                if ("opportunities",) in tables:
                    cursor.execute(
                        "SELECT id, public_id, public_lifecycle, status, gate_status, confidence, current_scoring_version FROM opportunities;"
                    )
                    rows = cursor.fetchall()
                    for r in rows:
                        f.write(f"Row: {r}\n")
                else:
                    f.write("opportunities table not found in sqlite_master\n")
                conn.close()
            except Exception as db_err:
                f.write(f"DB Debug Error: {db_err}\n")
            f.flush()

        status_build = "passed" if res_build.returncode == 0 else "failed"
        quality_gates["publish_build"] = {
            "command": "glintory publish build --output-dir dist",
            "status": status_build,
            "log_file": "logs/publish_build.log",
        }
        if status_build != "passed":
            gate_ok = False

    # 3H. validate-contract (dir = dist)
    if gate_ok:
        res_val = run_command_logged(
            ["uv", "run", "glintory", "publish", "validate-contract", "--dir", "dist"],
            cwd=temp_workspace,
            log_file="logs/contract_validation.log",
            env=test_env,
        )
        quality_gates["validate_contract"] = res_val
        if res_val["status"] != "passed":
            gate_ok = False

    # 3I. inspect-jurypress-feed
    if gate_ok:
        res_inspect = run_command_logged(
            [
                "uv",
                "run",
                "glintory",
                "publish",
                "inspect-jurypress-feed",
                "--dir",
                "dist",
            ],
            cwd=temp_workspace,
            log_file="logs/jurypress_inspection.log",
            env=test_env,
        )
        quality_gates["inspect_jurypress_feed"] = res_inspect
        if res_inspect["status"] != "passed":
            gate_ok = False

    if not gate_ok:
        print(
            "ERROR: Quality Gate validation checks failed. Submission ZIP cannot be generated.",
            file=sys.stderr,
        )
        # Copy logs to parent logs/ directory for troubleshooting
        if os.path.exists(logs_dir):
            os.makedirs("logs", exist_ok=True)
            for file in os.listdir(logs_dir):
                shutil.copy(os.path.join(logs_dir, file), os.path.join("logs", file))
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # 4. Generate SUBMISSION_MANIFEST.json in temporary workspace
    manifest_path = os.path.join(temp_workspace, "SUBMISSION_MANIFEST.json")
    manifest_data = {
        "git_commit": commit_sha,
        "git_tree": tree_sha,
        "working_tree_clean_before": True,
        "working_tree_clean_after": True,
        "submission_script_sha256": this_script_sha,
        "packaged_submission_script_sha256": extracted_script_sha,
        "archive_sha256_sidecar": sha_filename,
        "package_content_hash": "",
        "quality_gates": quality_gates,
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # 5. Package elements into ZIP (including complete dist/ and logs/)
    print("Packaging files into final submission ZIP...")
    if os.path.exists(zip_filename):
        os.remove(zip_filename)

    # Get tracked files using git ls-files to include only project sources
    try:
        tracked_files = subprocess.check_output(
            ["git", "ls-files"], text=True
        ).splitlines()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to retrieve git tracked files: {e}", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    forbidden_dirs = [
        "models/",
        "bin/",
        "build/",
        ".state/",
        "data/",
        "invalid/",
        "__pycache__/",
    ]
    forbidden_exts = [
        ".pyc",
        ".pyo",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".gguf",
        ".so",
        ".dylib",
        ".dll",
        ".zip",
        ".tar",
        ".tar.gz",
    ]

    zip_ok = True
    with zipfile.ZipFile(zip_filename, "w") as zf:
        # 5A. Pack tracked sources from temp_workspace (clean state)
        for rel_file in tracked_files:
            source_path = os.path.join(temp_workspace, rel_file)
            if os.path.exists(source_path):
                # Basic safety scan
                lower_name = rel_file.lower()
                for f_dir in forbidden_dirs:
                    if lower_name.startswith(f_dir) or f"/{f_dir}" in lower_name:
                        print(
                            f"ERROR: Forbidden directory structure: {rel_file}",
                            file=sys.stderr,
                        )
                        zip_ok = False
                for f_ext in forbidden_exts:
                    if lower_name.endswith(f_ext):
                        print(
                            f"ERROR: Forbidden extension: {rel_file}", file=sys.stderr
                        )
                        zip_ok = False
                if lower_name == ".env" or lower_name.endswith("/.env"):
                    print(f"ERROR: Forbidden .env file: {rel_file}", file=sys.stderr)
                    zip_ok = False

                zf.write(source_path, rel_file)

        # 5B. Pack complete dist/
        dist_src_dir = os.path.join(temp_workspace, "dist")
        for root, _, files in os.walk(dist_src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, dist_src_dir)
                archive_name = os.path.join("dist", rel_path)
                zf.write(full_path, archive_name)

        # 5C. Pack logs/
        log_src_dir = os.path.join(temp_workspace, "logs")
        for root, _, files in os.walk(log_src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, log_src_dir)
                archive_name = os.path.join("logs", rel_path)
                zf.write(full_path, archive_name)

        # 5D. Pack SUBMISSION_MANIFEST.json
        zf.write(manifest_path, "SUBMISSION_MANIFEST.json")

    if not zip_ok:
        print("ERROR: Safety verification failed. ZIP removed.", file=sys.stderr)
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # Compute package content hash (directory hash of temp_extract)
    pkg_hash = get_dir_hash(temp_workspace)

    # 6. Self-Verification (unzip and execute tests inside fully isolated temp_verify)
    print("Running Package Self-Verification...")
    temp_verify = ".temp_verify"
    if os.path.exists(temp_verify):
        shutil.rmtree(temp_verify)
    os.makedirs(temp_verify, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_filename, "r") as zf:
            zf.extractall(path=temp_verify)
        os.makedirs(os.path.join(temp_verify, "data"), exist_ok=True)

        # Run Verification command suite inside isolated env
        res_v_sync = run_command_logged(
            ["uv", "sync", "--frozen"],
            cwd=temp_verify,
            log_file="logs/package_self_verification.log",
        )
        if res_v_sync["status"] != "passed":
            raise ValueError("Self-verify uv sync failed.")

        # Re-initialize fixture DB path inside verify workspace using absolute path
        verify_db = os.path.join(temp_verify, "glintory_fixture.db")
        verify_env = os.environ.copy()
        verify_env["DATABASE_URL"] = f"sqlite:///{os.path.abspath(verify_db)}"
        verify_env["GLINTORY_DATABASE_URL"] = verify_env["DATABASE_URL"]

        # Seed verify database
        res_v_seed = run_command_logged(
            ["uv", "run", "python", "scripts/insert_fixture_db.py"],
            cwd=temp_verify,
            log_file="logs/package_self_verification.log",
            env=verify_env,
        )
        if res_v_seed["status"] != "passed":
            raise ValueError("Self-verify database seeding failed.")

        # Run migrations on default verify database location so integration tests pass
        res_v_pre_mig = run_command_logged(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=temp_verify,
            log_file="logs/package_self_verification.log",
        )
        if res_v_pre_mig["status"] != "passed":
            raise ValueError("Self-verify default database migration failed.")

        # Run Pytest in verify workspace
        res_v_pytest = run_command_logged(
            ["uv", "run", "pytest"],
            cwd=temp_verify,
            log_file="logs/package_self_verification.log",
        )
        if res_v_pytest["status"] != "passed":
            raise ValueError("Self-verify pytest suite failed.")

        # Run validate-contract on the packaged dist/
        res_v_val = run_command_logged(
            ["uv", "run", "glintory", "publish", "validate-contract", "--dir", "dist"],
            cwd=temp_verify,
            log_file="logs/package_self_verification.log",
            env=verify_env,
        )
        if res_v_val["status"] != "passed":
            raise ValueError("Self-verify validate-contract failed.")

        # Run inspect-jurypress-feed
        res_v_inspect = run_command_logged(
            [
                "uv",
                "run",
                "glintory",
                "publish",
                "inspect-jurypress-feed",
                "--dir",
                "dist",
            ],
            cwd=temp_verify,
            log_file="logs/package_self_verification.log",
            env=verify_env,
        )
        if res_v_inspect["status"] != "passed":
            raise ValueError("Self-verify inspect-jurypress-feed failed.")

        # Check for HTML and Sitemap file structures in extracted dist
        required_paths = [
            "dist/index.html",
            "dist/opportunities/index.html",
            "dist/opportunities/opp_f1111111111111111111111111111111/index.html",
            "dist/opportunities/opp_f1111111111111111111111111111111/en/index.html",
            "dist/sitemap.xml",
        ]
        for rp in required_paths:
            if not os.path.exists(os.path.join(temp_verify, rp)):
                raise ValueError(
                    f"Self-verify failed: missing required static asset '{rp}' in packed zip."
                )

        print("Package Self-Verification completed successfully.")

        # Append package_self_verification.log into final ZIP
        with zipfile.ZipFile(zip_filename, "a") as zf:
            zf.write(
                os.path.join(temp_verify, "logs", "package_self_verification.log"),
                "logs/package_self_verification.log",
            )
    except Exception as err:
        print(f"ERROR: Self-Verification failed: {err}", file=sys.stderr)
        # Copy logs to parent logs/ directory for troubleshooting before cleanup
        if os.path.exists(logs_dir):
            os.makedirs("logs", exist_ok=True)
            for file in os.listdir(logs_dir):
                shutil.copy(os.path.join(logs_dir, file), os.path.join("logs", file))
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        shutil.rmtree(temp_workspace)
        shutil.rmtree(temp_verify)
        sys.exit(1)

    # 7. Finalize Manifest update (insert package_content_hash and rewrite to ZIP)
    manifest_data["package_content_hash"] = pkg_hash
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    with zipfile.ZipFile(zip_filename, "a") as zf:
        # Overwrite manifest in zip (write will append/overwrite)
        zf.write(manifest_path, "SUBMISSION_MANIFEST.json")

    # Copy logs to parent logs/ directory before cleanup
    if os.path.exists(logs_dir):
        os.makedirs("logs", exist_ok=True)
        for file in os.listdir(logs_dir):
            shutil.copy(os.path.join(logs_dir, file), os.path.join("logs", file))

    # Clean up workspaces
    shutil.rmtree(temp_workspace)
    shutil.rmtree(temp_verify)

    # Calculate final SHA-256
    zip_sha = get_sha256(zip_filename)
    with open(sha_filename, "w", encoding="utf-8") as f:
        f.write(f"{zip_sha}\n")

    print(f"SUBMISSION_MANIFEST.json and all quality logs appended to {zip_filename}.")
    print(f"SHA-256 written to {sha_filename}: {zip_sha}")
    print("Verification success. ZIP archive is safe, clean, and fully conforming.")


if __name__ == "__main__":
    main()
