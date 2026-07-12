import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def get_file_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def calculate_dir_content_hash(directory: str) -> str:
    sha = hashlib.sha256()
    files_to_hash = []

    for root, _, files in os.walk(directory):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, directory)

            if rel_path in (
                "SUBMISSION_MANIFEST.json",
                "submission-glintory.zip.sha256",
            ):
                continue
            if (
                rel_path.startswith(".venv")
                or rel_path.startswith(".temp")
                or ".pytest_cache" in rel_path
                or "__pycache__" in rel_path
            ):
                continue

            files_to_hash.append((rel_path, full_path))

    files_to_hash.sort(key=lambda x: x[0])

    for rel_path, full_path in files_to_hash:
        with open(full_path, "rb") as f:
            content = f.read()
        sha.update(rel_path.encode("utf-8"))
        sha.update(b"\0")
        sha.update(content)
        sha.update(b"\0")

    return sha.hexdigest()


def calculate_zip_content_hash(zip_path: str) -> str:
    sha = hashlib.sha256()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = sorted(zf.namelist())
        for name in names:
            if name in ("SUBMISSION_MANIFEST.json", "submission-glintory.zip.sha256"):
                continue
            if name.endswith("/"):
                continue
            content = zf.read(name)
            sha.update(name.encode("utf-8"))
            sha.update(b"\0")
            sha.update(content)
            sha.update(b"\0")
    return sha.hexdigest()


def run_command_logged(
    cmd: list[str],
    cwd: str,
    log_file: str,
    env: dict | None = None,
    append: bool = False,
) -> dict:
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    print(f"Executing: {' '.join(cmd)} in {cwd}...")

    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = Path(cwd) / log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)

    run_env = env.copy() if env else os.environ.copy()
    abs_cwd = os.path.abspath(cwd)
    venv_path = os.path.join(abs_cwd, ".venv")
    run_env["VIRTUAL_ENV"] = venv_path

    venv_bin = os.path.join(venv_path, "bin")
    current_path = run_env.get("PATH", "")
    run_env["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

    mode = "a" if append else "w"
    try:
        with open(log_path, mode, encoding="utf-8") as f:
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
        if log_path.exists():
            with open(log_path, encoding="utf-8", errors="ignore") as lf:
                log_text = lf.read()
                if "validation error" in log_text.lower():
                    has_hidden_val_error = True

        status = "passed"
        if res.returncode != 0 or has_hidden_val_error:
            status = "failed"

        rel_log_path = os.path.relpath(log_path, cwd)

        return {
            "command": " ".join(cmd),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": res.returncode,
            "status": status,
            "log_file": rel_log_path,
        }
    except Exception as e:
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rel_log_path = os.path.relpath(log_path, cwd)
        return {
            "command": " ".join(cmd),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": -1,
            "status": "failed",
            "log_file": rel_log_path,
            "error": str(e),
        }


def append_to_verification_log(log_dir: str, result: dict) -> None:
    verify_log_path = os.path.join(log_dir, "package_self_verification.log")
    with open(verify_log_path, "a", encoding="utf-8") as f:
        f.write(f"--- Command: {result.get('command')} ---\n")
        f.write(f"Started At:  {result.get('started_at')}\n")
        f.write(f"Finished At: {result.get('finished_at')}\n")
        f.write(f"Exit Code:   {result.get('exit_code')}\n")
        f.write(f"Status:      {result.get('status')}\n")
        f.write(f"Log File:    {result.get('log_file')}\n")
        if "error" in result:
            f.write(f"Error:       {result.get('error')}\n")
        f.write("-" * 40 + "\n\n")


def main() -> None:
    zip_filename = "submission-glintory.zip"
    sha_filename = "submission-glintory.zip.sha256"

    # Clean up local generated artifacts before checking git status
    # ONLY clean up if we are running in the repository root (has .git)
    if os.path.exists(".git"):
        if os.path.exists("logs"):
            shutil.rmtree("logs")
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        if os.path.exists(sha_filename):
            os.remove(sha_filename)

    # 1. Pre-flight Check: Git Status (Before)
    print("Checking Git status before build...")
    try:
        commit_before = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        status_before_raw = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        clean_before = len(status_before_raw) == 0

        if not clean_before:
            print("ERROR: Working tree is dirty. Clean commit first.", file=sys.stderr)
            print(f"git status output:\n{status_before_raw}", file=sys.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to run Git pre-flight check: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Clean Git working copy verified at Commit {commit_before}.")

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
        import warnings

        with tarfile.open(tar_path) as tf, warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tf.extractall(path=temp_workspace)
        os.remove(tar_path)
        os.makedirs(os.path.join(temp_workspace, "data"), exist_ok=True)
    except Exception as e:
        print(f"ERROR: Failed to extract Git HEAD: {e}", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # Compare running script vs extracted script SHA-256
    this_script_sha = get_file_sha256(__file__)
    extracted_script_path = os.path.join(
        temp_workspace, "scripts", "create_submission.py"
    )
    extracted_script_sha = get_file_sha256(extracted_script_path)
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

    # Override DATABASE_URL and GLINTORY_DATABASE_URL
    test_env = os.environ.copy()
    test_env["DATABASE_URL"] = f"sqlite:///{os.path.abspath(fixture_db_path)}"
    test_env["GLINTORY_DATABASE_URL"] = test_env["DATABASE_URL"]

    # Run migrations on default test database location
    if gate_ok:
        res_pre_mig = run_command_logged(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=temp_workspace,
            log_file="logs/pytest_migration.log",
            env=test_env,
        )
        if res_pre_mig["status"] != "passed":
            gate_ok = False

    # 3B. pytest
    if gate_ok:
        res_pytest = run_command_logged(
            ["uv", "run", "pytest"],
            cwd=temp_workspace,
            log_file="logs/pytest.log",
            env=test_env,
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
            env=test_env,
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
            env=test_env,
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
            env=test_env,
        )
        quality_gates["pyright"] = res_pyright
        if res_pyright["status"] != "passed":
            gate_ok = False

    # 3F. alembic migration roundtrip
    if gate_ok:
        migration_log = os.path.join(logs_dir, "migration_roundtrip.log")
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
            "exit_code": 0 if status_migration == "passed" else 1,
        }

        # Seed fixture database
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
        build_log_file = os.path.join(logs_dir, "publish_build.log")
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

    # 4. Self-Verification (Execute tests inside temp_workspace to generate logs in staging)
    print("Running Package Self-Verification...")

    # We execute Self-Verification inside temp_workspace to write logs/self_verify_... directly into temp_workspace
    verify_db = os.path.join(temp_workspace, "verify_glintory.db")
    if os.path.exists(verify_db):
        os.remove(verify_db)

    verify_env = test_env.copy()
    verify_env["DATABASE_URL"] = f"sqlite:///{os.path.abspath(verify_db)}"
    verify_env["GLINTORY_DATABASE_URL"] = verify_env["DATABASE_URL"]

    try:
        # A. uv sync --frozen
        res_v_sync = run_command_logged(
            ["uv", "sync", "--frozen"],
            cwd=temp_workspace,
            log_file="logs/self_verify_uv_sync.log",
            env=verify_env,
        )
        append_to_verification_log(logs_dir, res_v_sync)
        if res_v_sync["status"] != "passed":
            raise ValueError("Self-verify uv sync failed.")

        # B. alembic upgrade head (migration first!)
        res_v_pre_mig = run_command_logged(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=temp_workspace,
            log_file="logs/self_verify_migration.log",
            env=verify_env,
        )
        append_to_verification_log(logs_dir, res_v_pre_mig)
        if res_v_pre_mig["status"] != "passed":
            raise ValueError("Self-verify default database migration failed.")

        # C. pytest
        res_v_pytest = run_command_logged(
            ["uv", "run", "pytest"],
            cwd=temp_workspace,
            log_file="logs/self_verify_pytest.log",
            env=verify_env,
        )
        append_to_verification_log(logs_dir, res_v_pytest)
        if res_v_pytest["status"] != "passed":
            raise ValueError("Self-verify pytest suite failed.")

        # D. fixture seed (seed next!)
        res_v_seed = run_command_logged(
            ["uv", "run", "python", "scripts/insert_fixture_db.py"],
            cwd=temp_workspace,
            log_file="logs/self_verify_fixture.log",
            env=verify_env,
        )
        append_to_verification_log(logs_dir, res_v_seed)
        if res_v_seed["status"] != "passed":
            raise ValueError("Self-verify database seeding failed.")

        # E. validate-contract
        res_v_val = run_command_logged(
            ["uv", "run", "glintory", "publish", "validate-contract", "--dir", "dist"],
            cwd=temp_workspace,
            log_file="logs/self_verify_contract.log",
            env=verify_env,
        )
        append_to_verification_log(logs_dir, res_v_val)
        if res_v_val["status"] != "passed":
            raise ValueError("Self-verify validate-contract failed.")

        # F. inspect-jurypress-feed
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
            cwd=temp_workspace,
            log_file="logs/self_verify_jurypress.log",
            env=verify_env,
        )
        append_to_verification_log(logs_dir, res_v_inspect)
        if res_v_inspect["status"] != "passed":
            raise ValueError("Self-verify inspect-jurypress-feed failed.")

        # Check for HTML and Sitemap file structures
        required_paths = [
            "dist/index.html",
            "dist/opportunities/index.html",
            "dist/opportunities/opp_f1111111111111111111111111111111/index.html",
            "dist/opportunities/opp_f1111111111111111111111111111111/en/index.html",
            "dist/sitemap.xml",
        ]
        for rp in required_paths:
            if not os.path.exists(os.path.join(temp_workspace, rp)):
                raise ValueError(
                    f"Self-verify failed: missing required static asset '{rp}' in build output."
                )

        # Clean up database file generated during verify so it is not packaged
        if os.path.exists(verify_db):
            os.remove(verify_db)
        if os.path.exists(os.path.join(temp_workspace, "verify_glintory.db-shm")):
            os.remove(os.path.join(temp_workspace, "verify_glintory.db-shm"))
        if os.path.exists(os.path.join(temp_workspace, "verify_glintory.db-wal")):
            os.remove(os.path.join(temp_workspace, "verify_glintory.db-wal"))

    except Exception as err:
        print(f"ERROR: Self-Verification failed: {err}", file=sys.stderr)
        # Copy logs to parent logs/ directory for troubleshooting before cleanup
        if os.path.exists(logs_dir):
            os.makedirs("logs", exist_ok=True)
            for file in os.listdir(logs_dir):
                shutil.copy(os.path.join(logs_dir, file), os.path.join("logs", file))
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    print("Package Self-Verification completed successfully.")

    # 5. Build list of files to package and calculate Content Hash
    try:
        tracked_files = subprocess.check_output(
            ["git", "ls-files"], text=True
        ).splitlines()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to retrieve git tracked files: {e}", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # Validate file integrity and safety
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

    files_to_pack = {}  # rel_path -> full_path
    zip_ok = True

    for rel_file in tracked_files:
        source_path = os.path.join(temp_workspace, rel_file)
        if os.path.exists(source_path):
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
                    print(f"ERROR: Forbidden extension: {rel_file}", file=sys.stderr)
                    zip_ok = False
            if lower_name == ".env" or lower_name.endswith("/.env"):
                print(f"ERROR: Forbidden .env file: {rel_file}", file=sys.stderr)
                zip_ok = False

            files_to_pack[rel_file] = source_path

    # Add complete dist/
    dist_src_dir = os.path.join(temp_workspace, "dist")
    if os.path.exists(dist_src_dir):
        for root, _, files in os.walk(dist_src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, dist_src_dir)
                files_to_pack[os.path.join("dist", rel_path)] = full_path

    # Add complete logs/
    log_src_dir = os.path.join(temp_workspace, "logs")
    if os.path.exists(log_src_dir):
        for root, _, files in os.walk(log_src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, log_src_dir)
                files_to_pack[os.path.join("logs", rel_path)] = full_path

    if not zip_ok:
        print("ERROR: Safety verification failed. Build halted.", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # Calculate package content hash
    sha = hashlib.sha256()
    for rel_path in sorted(files_to_pack.keys()):
        if rel_path in ("SUBMISSION_MANIFEST.json", "submission-glintory.zip.sha256"):
            continue
        with open(files_to_pack[rel_path], "rb") as f:
            content = f.read()
        sha.update(rel_path.encode("utf-8"))
        sha.update(b"\0")
        sha.update(content)
        sha.update(b"\0")
    pkg_hash = sha.hexdigest()

    # 6. Verify Git status (After)
    print("Checking Git status after build...")
    try:
        commit_after = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        status_after_raw = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        clean_after = len(status_after_raw) == 0
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to run Git post-build check: {e}", file=sys.stderr)
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    if commit_before != commit_after:
        print(
            f"ERROR: Git HEAD changed during build execution. Before: {commit_before}, After: {commit_after}",
            file=sys.stderr,
        )
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    if not clean_after:
        print(
            f"ERROR: Working tree became dirty during build execution:\n{status_after_raw}",
            file=sys.stderr,
        )
        shutil.rmtree(temp_workspace)
        sys.exit(1)

    # 7. Generate final SUBMISSION_MANIFEST.json
    manifest_path = os.path.join(temp_workspace, "SUBMISSION_MANIFEST.json")

    manifest_data = {
        "git_commit_before": commit_before,
        "git_commit_after": commit_after,
        "working_tree_clean_before": clean_before,
        "working_tree_clean_after": clean_after,
        "submission_script_sha256": this_script_sha,
        "packaged_submission_script_sha256": extracted_script_sha,
        "archive_sha256_sidecar": sha_filename,
        "package_content_hash": pkg_hash,
        "quality_gates": quality_gates,
    }

    # Verify all quality gate logs exist and are valid before packaging
    for gate_name, gate_info in quality_gates.items():
        rel_log_file = gate_info.get("log_file")
        full_log_path = os.path.join(temp_workspace, rel_log_file)
        if not os.path.exists(full_log_path):
            raise ValueError(f"Quality gate log file does not exist: {rel_log_file}")
        if os.path.getsize(full_log_path) == 0:
            raise ValueError(f"Quality gate log file is empty: {rel_log_file}")
        if gate_info.get("exit_code") is not None and gate_info.get("exit_code") != 0:
            raise ValueError(
                f"Quality gate {gate_name} exit code is non-zero: {gate_info.get('exit_code')}"
            )
        if gate_info.get("status") != "passed":
            raise ValueError(
                f"Quality gate {gate_name} status is not passed: {gate_info.get('status')}"
            )
        with open(full_log_path, encoding="utf-8", errors="ignore") as lf:
            log_text = lf.read()
            if "validation error" in log_text.lower():
                raise ValueError(
                    f"Quality gate log {rel_log_file} contains validation error."
                )

    # Write final manifest into staging workspace
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # 8. Package everything into final ZIP
    print("Packaging files into final submission ZIP...")
    with zipfile.ZipFile(zip_filename, "w") as zf:
        for rel_path, full_path in sorted(files_to_pack.items()):
            zf.write(full_path, rel_path)
        # Write the final manifest
        zf.write(manifest_path, "SUBMISSION_MANIFEST.json")

    # Assert exactly 1 Manifest entry in ZIP
    with zipfile.ZipFile(zip_filename, "r") as zf:
        names = zf.namelist()
        if names.count("SUBMISSION_MANIFEST.json") != 1:
            raise AssertionError(
                f"ZIP contains multiple SUBMISSION_MANIFEST.json files: count = {names.count('SUBMISSION_MANIFEST.json')}"
            )

    # 9. Final ZIP Self-Verification (Isolated Extraction Verification)
    print("Extracting final ZIP for post-build verification...")
    temp_verify = ".temp_verify"
    if os.path.exists(temp_verify):
        shutil.rmtree(temp_verify)
    os.makedirs(temp_verify, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_filename, "r") as zf:
            zf.extractall(path=temp_verify)

        # Recalculate package content hash from extracted directory and verify it matches the Manifest value
        recalc_hash = calculate_dir_content_hash(temp_verify)
        if recalc_hash != pkg_hash:
            raise ValueError(
                f"Recalculated package content hash from ZIP extraction does not match Manifest. "
                f"Recalc: {recalc_hash}, Manifest: {pkg_hash}"
            )

        # Run verification suite in fully isolated temp_verify
        verify_db_post = os.path.join(temp_verify, "glintory_fixture.db")
        verify_env_post = os.environ.copy()
        verify_env_post["DATABASE_URL"] = f"sqlite:///{os.path.abspath(verify_db_post)}"
        verify_env_post["GLINTORY_DATABASE_URL"] = verify_env_post["DATABASE_URL"]

        post_sync = run_command_logged(
            ["uv", "sync", "--frozen"],
            cwd=temp_verify,
            log_file="logs/post_verify_uv_sync.log",
            env=verify_env_post,
        )
        if post_sync["status"] != "passed":
            raise ValueError("Post-verify uv sync failed.")

        post_mig = run_command_logged(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=temp_verify,
            log_file="logs/post_verify_migration.log",
            env=verify_env_post,
        )
        if post_mig["status"] != "passed":
            raise ValueError("Post-verify database migration failed.")

        post_pytest = run_command_logged(
            ["uv", "run", "pytest"],
            cwd=temp_verify,
            log_file="logs/post_verify_pytest.log",
            env=verify_env_post,
        )
        if post_pytest["status"] != "passed":
            raise ValueError("Post-verify pytest failed.")

        post_seed = run_command_logged(
            ["uv", "run", "python", "scripts/insert_fixture_db.py"],
            cwd=temp_verify,
            log_file="logs/post_verify_fixture.log",
            env=verify_env_post,
        )
        if post_seed["status"] != "passed":
            raise ValueError("Post-verify database seeding failed.")

        post_val = run_command_logged(
            ["uv", "run", "glintory", "publish", "validate-contract", "--dir", "dist"],
            cwd=temp_verify,
            log_file="logs/post_verify_contract.log",
            env=verify_env_post,
        )
        if post_val["status"] != "passed":
            raise ValueError("Post-verify validate-contract failed.")

        post_inspect = run_command_logged(
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
            log_file="logs/post_verify_jurypress.log",
            env=verify_env_post,
        )
        if post_inspect["status"] != "passed":
            raise ValueError("Post-verify inspect-jurypress-feed failed.")

        # Double check outputs
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
                    f"Post-verify failed: missing required static asset '{rp}' in extracted ZIP."
                )

        # Inspect JuryPress ready items in the generated dist
        sitemap_path = os.path.join(temp_verify, "dist/sitemap.xml")
        with open(sitemap_path, encoding="utf-8") as sf:
            sitemap_content = sf.read()
            if "opp_f1111111111111111111111111111111" not in sitemap_content:
                raise ValueError("Sitemap does not contain the target Opportunity URL.")

        print("Final ZIP Post-Build Self-Verification completed successfully.")

    except Exception as err:
        print(f"ERROR: Final ZIP Self-Verification failed: {err}", file=sys.stderr)
        # Delete ZIP and SHA sidecar
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        if os.path.exists(sha_filename):
            os.remove(sha_filename)

        # Save verification failure logs to the outer logs/ directory for debug
        verify_logs_dir = os.path.join(temp_verify, "logs")
        if os.path.exists(verify_logs_dir):
            os.makedirs("logs", exist_ok=True)
            for file in os.listdir(verify_logs_dir):
                shutil.copy(
                    os.path.join(verify_logs_dir, file), os.path.join("logs", file)
                )

        shutil.rmtree(temp_workspace)
        shutil.rmtree(temp_verify)
        sys.exit(1)

    # Copy logs to parent logs/ directory before cleanup
    if os.path.exists(logs_dir):
        os.makedirs("logs", exist_ok=True)
        for file in os.listdir(logs_dir):
            shutil.copy(os.path.join(logs_dir, file), os.path.join("logs", file))

    # Clean up workspaces
    shutil.rmtree(temp_workspace)
    shutil.rmtree(temp_verify)

    # Calculate final SHA-256
    zip_sha = get_file_sha256(zip_filename)
    with open(sha_filename, "w", encoding="utf-8") as f:
        f.write(f"{zip_sha}\n")

    print(f"SUBMISSION_MANIFEST.json and all quality logs appended to {zip_filename}.")
    print(f"SHA-256 written to {sha_filename}: {zip_sha}")
    print("Verification success. ZIP archive is safe, clean, and fully conforming.")


if __name__ == "__main__":
    main()
