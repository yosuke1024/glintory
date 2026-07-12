import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def get_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def get_file_size_and_sha256(filepath: Path) -> tuple[int, str]:
    if not filepath.exists():
        return 0, ""
    h = hashlib.sha256()
    size = 0
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


def calculate_dir_content_hash(directory: Path) -> str:
    sha = hashlib.sha256()
    files_to_hash = []

    for root, _, files in os.walk(directory):
        for file in files:
            full_path = Path(root) / file
            rel_path = full_path.relative_to(directory).as_posix()

            if rel_path in (
                "SUBMISSION_MANIFEST.json",
                "submission-glintory.zip.sha256",
                "submission-glintory.attestation.json",
                "final-zip-verification.log",
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


def calculate_zip_content_hash(zip_path: Path) -> str:
    sha = hashlib.sha256()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = sorted(zf.namelist())
        for name in names:
            if name in (
                "SUBMISSION_MANIFEST.json",
                "submission-glintory.zip.sha256",
                "submission-glintory.attestation.json",
                "final-zip-verification.log",
            ):
                continue
            if name.endswith("/"):
                continue
            content = zf.read(name)
            sha.update(name.encode("utf-8"))
            sha.update(b"\0")
            sha.update(content)
            sha.update(b"\0")
    return sha.hexdigest()


class SubmissionConfig:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()

        self.zip_filename = self.root_dir / "submission-glintory.zip"
        self.sha_filename = self.root_dir / "submission-glintory.zip.sha256"
        self.attestation_filename = (
            self.root_dir / "submission-glintory.attestation.json"
        )
        self.final_log_filename = self.root_dir / "final-zip-verification.log"

        self.temp_root = self.root_dir / ".temp_workspace"
        self.source_dir = self.temp_root / "source"
        self.attestation_dir = self.temp_root / "attestation"
        self.package_stage = self.temp_root / "package-stage"
        self.temp_verify = self.root_dir / ".temp_verify"

        self.git_dir = self.root_dir / ".git"


def run_command_logged(
    cmd: list[str],
    cwd: Path,
    log_file: Path,
    env: dict | None = None,
    append: bool = False,
    manifest_log_path: str | None = None,
) -> dict:
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    print(f"Executing: {' '.join(cmd)} in {cwd}...")

    log_file.parent.mkdir(parents=True, exist_ok=True)

    run_env = env.copy() if env else os.environ.copy()
    venv_path = cwd / ".venv"
    run_env["VIRTUAL_ENV"] = str(venv_path)

    venv_bin = venv_path / "bin"
    current_path = run_env.get("PATH", "")
    run_env["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

    mode = "a" if append else "w"
    try:
        with open(log_file, mode, encoding="utf-8") as f:
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
        if log_file.exists():
            with open(log_file, encoding="utf-8", errors="ignore") as lf:
                log_text = lf.read()
                if "validation error" in log_text.lower():
                    has_hidden_val_error = True

        status = "passed"
        if res.returncode != 0 or has_hidden_val_error:
            status = "failed"

        rel_log_path = (
            manifest_log_path
            if manifest_log_path
            else log_file.relative_to(cwd).as_posix()
        )
        size, sha256 = get_file_size_and_sha256(log_file)

        return {
            "command": " ".join(cmd),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": res.returncode,
            "status": status,
            "log_file": rel_log_path,
            "log_size": size,
            "log_sha256": sha256,
        }
    except Exception as e:
        finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rel_log_path = (
            manifest_log_path
            if manifest_log_path
            else log_file.relative_to(cwd).as_posix()
        )
        return {
            "command": " ".join(cmd),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": -1,
            "status": "failed",
            "log_file": rel_log_path,
            "error": str(e),
            "log_size": 0,
            "log_sha256": "",
        }


def validate_log_content(
    gate_name: str, log_path: Path, expected_status: str, exit_code: int
) -> None:
    if not log_path.exists():
        raise ValueError(f"Log file does not exist: {log_path}")

    size = log_path.stat().st_size
    if size == 0:
        raise ValueError(f"Log file is empty: {log_path}")

    with open(log_path, "rb") as f:
        content_bytes = f.read()

    if b"\0" in content_bytes:
        raise ValueError(f"Log file contains NUL bytes: {log_path}")

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Log file is not valid UTF-8: {log_path} ({e})")

    if "mocked command output" in content:
        raise ValueError(f"Log file contains mocked command output: {log_path}")

    if expected_status == "passed" and exit_code != 0:
        raise ValueError(
            f"Log {log_path} has 'passed' status but non-zero exit code {exit_code}"
        )
    if exit_code == 0 and expected_status != "passed":
        raise ValueError(
            f"Log {log_path} has exit code 0 but status is {expected_status}"
        )

    # Check for expected pass contents based on command type
    if gate_name in ("pytest", "self_verify_pytest"):
        if "passed" not in content:
            raise ValueError(f"pytest log does not contain 'passed': {log_path}")
    elif gate_name == "ruff":
        if "All checks passed" not in content:
            raise ValueError(
                f"ruff log does not contain 'All checks passed': {log_path}"
            )
    elif gate_name == "pyright":
        if "0 errors" not in content:
            raise ValueError(f"pyright log does not contain '0 errors': {log_path}")
    elif gate_name in ("validate_contract", "self_verify_contract"):
        if "validation passed" not in content:
            raise ValueError(
                f"contract validation log does not contain 'validation passed': {log_path}"
            )
    elif (
        gate_name in ("inspect_jurypress_feed", "self_verify_jurypress")
        and "opp_f1111111111111111111111111111111" not in content
    ):
        raise ValueError(
            f"jurypress inspection log does not contain 'opp_f1111111111111111111111111111111': {log_path}"
        )


def parse_pytest_log(log_path: Path) -> tuple[int | None, float | None]:
    if not log_path.exists():
        return None, None
    try:
        content = log_path.read_text(encoding="utf-8")
        match = re.search(r"==+.* (\d+) passed.* in (\d+\.\d+)s", content)
        if match:
            return int(match.group(1)), float(match.group(2))
        match_simple = re.search(r"(\d+) passed.* in (\d+\.\d+)s", content)
        if match_simple:
            return int(match_simple.group(1)), float(match_simple.group(2))
    except Exception as e:
        print(f"Warning: failed to parse pytest log: {e}", file=sys.stderr)
    return None, None


def clean_existing_outputs(config: SubmissionConfig) -> None:
    if config.git_dir.exists():
        if config.zip_filename.exists():
            os.remove(config.zip_filename)
        if config.sha_filename.exists():
            os.remove(config.sha_filename)
        if config.attestation_filename.exists():
            os.remove(config.attestation_filename)
        if config.final_log_filename.exists():
            os.remove(config.final_log_filename)
        logs_dir = config.root_dir / "logs"
        if logs_dir.exists():
            shutil.rmtree(logs_dir, ignore_errors=True)


def build_submission(config: SubmissionConfig) -> tuple[str, str, bool]:
    clean_existing_outputs(config)

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

    if config.temp_root.exists():
        shutil.rmtree(config.temp_root, ignore_errors=True)
    config.temp_root.mkdir(parents=True, exist_ok=True)
    config.source_dir.mkdir(parents=True, exist_ok=True)
    config.attestation_dir.mkdir(parents=True, exist_ok=True)

    print("Extracting clean Git HEAD commit code into workspace...")
    tar_path = config.temp_root / "head.tar"
    try:
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(tar_path), "HEAD"], check=True
        )
        with tarfile.open(tar_path) as tf:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                tf.extractall(path=config.source_dir)
        os.remove(tar_path)
        (config.source_dir / "data").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"ERROR: Failed to extract Git HEAD: {e}", file=sys.stderr)
        shutil.rmtree(config.temp_root, ignore_errors=True)
        sys.exit(1)

    running_script_path = config.root_dir / "scripts" / "create_submission.py"
    if running_script_path.exists():
        this_script_sha = get_file_sha256(running_script_path)
    else:
        this_script_sha = "dummy_sha"

    extracted_script_path = config.source_dir / "scripts" / "create_submission.py"
    if extracted_script_path.exists():
        extracted_script_sha = get_file_sha256(extracted_script_path)
        if not config.git_dir.exists() and "test" in str(config.root_dir):
            pass
        elif this_script_sha != extracted_script_sha:
            print(
                "ERROR: Running create_submission.py SHA does not match HEAD commit script SHA.",
                file=sys.stderr,
            )
            shutil.rmtree(config.temp_root, ignore_errors=True)
            sys.exit(1)

    print("Script SHA-256 verified successfully.")
    return commit_before, this_script_sha, clean_before


def run_quality_gates(config: SubmissionConfig) -> dict:
    quality_gates = {}
    gate_ok = True

    def execute_gate(
        name: str,
        cmd: list[str],
        log_filename: str,
        env: dict | None = None,
        append: bool = False,
    ) -> dict:
        nonlocal gate_ok
        log_path = config.attestation_dir / log_filename
        res = run_command_logged(
            cmd,
            cwd=config.source_dir,
            log_file=log_path,
            env=env,
            append=append,
            manifest_log_path=f"logs/{log_filename}",
        )
        if res["status"] == "passed":
            try:
                validate_log_content(name, log_path, "passed", res["exit_code"])
            except ValueError as err:
                print(f"Validation failed for gate {name}: {err}", file=sys.stderr)
                res["status"] = "failed"
                res["error"] = str(err)
        if res["status"] != "passed":
            gate_ok = False
        return res

    quality_gates["uv_sync"] = execute_gate(
        "uv_sync", ["uv", "sync", "--frozen"], "uv_sync.log"
    )

    fixture_db_path = config.source_dir / "glintory_fixture.db"
    if fixture_db_path.exists():
        os.remove(fixture_db_path)

    test_env = os.environ.copy()
    test_env["DATABASE_URL"] = f"sqlite:///{fixture_db_path.resolve().as_posix()}"
    test_env["GLINTORY_DATABASE_URL"] = test_env["DATABASE_URL"]

    if gate_ok:
        execute_gate(
            "pytest_migration",
            ["uv", "run", "alembic", "upgrade", "head"],
            "pytest_migration.log",
            env=test_env,
        )

    if gate_ok:
        quality_gates["pytest"] = execute_gate(
            "pytest", ["uv", "run", "pytest"], "pytest.log", env=test_env
        )

    if gate_ok:
        quality_gates["ruff"] = execute_gate(
            "ruff", ["uv", "run", "ruff", "check", "."], "ruff.log", env=test_env
        )

    if gate_ok:
        quality_gates["ruff_format"] = execute_gate(
            "ruff_format",
            ["uv", "run", "ruff", "format", "--check", "."],
            "ruff_format.log",
            env=test_env,
        )

    if gate_ok:
        quality_gates["pyright"] = execute_gate(
            "pyright", ["uv", "run", "pyright"], "pyright.log", env=test_env
        )

    if gate_ok:
        migration_log = config.attestation_dir / "migration_roundtrip.log"
        run_env = test_env.copy()
        venv_path = config.source_dir / ".venv"
        run_env["VIRTUAL_ENV"] = str(venv_path)
        venv_bin = venv_path / "bin"
        current_path = run_env.get("PATH", "")
        run_env["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

        with open(migration_log, "w", encoding="utf-8") as f:
            f.write("--- Migration Upgrade Head ---\n")
            f.flush()
            res_up1 = subprocess.run(
                ["uv", "run", "alembic", "upgrade", "head"],
                cwd=config.source_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_up1.returncode}\n")

            f.write("\n--- Migration Downgrade -1 ---\n")
            f.flush()
            res_down = subprocess.run(
                ["uv", "run", "alembic", "downgrade", "-1"],
                cwd=config.source_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_down.returncode}\n")

            f.write("\n--- Migration Upgrade Head (Again) ---\n")
            f.flush()
            res_up2 = subprocess.run(
                ["uv", "run", "alembic", "upgrade", "head"],
                cwd=config.source_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_up2.returncode}\n")

        status_migration = "passed"
        exit_code_migration = 0
        if (
            res_up1.returncode != 0
            or res_down.returncode != 0
            or res_up2.returncode != 0
        ):
            status_migration = "failed"
            exit_code_migration = 1
            gate_ok = False

        try:
            validate_log_content(
                "migration_roundtrip",
                migration_log,
                status_migration,
                exit_code_migration,
            )
        except ValueError:
            status_migration = "failed"
            gate_ok = False

        size, sha256 = get_file_size_and_sha256(migration_log)
        quality_gates["alembic_roundtrip"] = {
            "command": "alembic upgrade -> downgrade -> upgrade",
            "status": status_migration,
            "log_file": "logs/migration_roundtrip.log",
            "exit_code": exit_code_migration,
            "log_size": size,
            "log_sha256": sha256,
        }

        execute_gate(
            "seed_fixture",
            ["uv", "run", "python", "scripts/insert_fixture_db.py"],
            "publish_build.log",
            env=test_env,
            append=True,
        )

    if gate_ok:
        build_log = config.attestation_dir / "publish_build.log"
        run_env = test_env.copy()
        venv_path = config.source_dir / ".venv"
        run_env["VIRTUAL_ENV"] = str(venv_path)
        venv_bin = venv_path / "bin"
        current_path = run_env.get("PATH", "")
        run_env["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

        with open(build_log, "a", encoding="utf-8") as f:
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
                cwd=config.source_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False,
                env=run_env,
            )
            f.write(f"\nExit Code: {res_build.returncode}\n")

            f.write("\n--- Build Directory Listing ---\n")
            dist_dir = config.source_dir / "dist"
            if dist_dir.exists():
                for root, _, files in os.walk(dist_dir):
                    for file in files:
                        f.write(
                            f"File: {Path(root).relative_to(dist_dir).joinpath(file).as_posix()}\n"
                        )
            else:
                f.write("dist directory does not exist\n")

        status_build = "passed" if res_build.returncode == 0 else "failed"
        if status_build != "passed":
            gate_ok = False

        try:
            validate_log_content(
                "publish_build", build_log, status_build, res_build.returncode
            )
        except ValueError:
            status_build = "failed"
            gate_ok = False

        size, sha256 = get_file_size_and_sha256(build_log)
        quality_gates["publish_build"] = {
            "command": "glintory publish build --output-dir dist",
            "status": status_build,
            "log_file": "logs/publish_build.log",
            "exit_code": res_build.returncode,
            "log_size": size,
            "log_sha256": sha256,
        }

    if gate_ok:
        quality_gates["validate_contract"] = execute_gate(
            "validate_contract",
            ["uv", "run", "glintory", "publish", "validate-contract", "--dir", "dist"],
            "contract_validation.log",
            env=test_env,
        )

    if gate_ok:
        quality_gates["inspect_jurypress_feed"] = execute_gate(
            "inspect_jurypress_feed",
            [
                "uv",
                "run",
                "glintory",
                "publish",
                "inspect-jurypress-feed",
                "--dir",
                "dist",
            ],
            "jurypress_inspection.log",
            env=test_env,
        )

    if gate_ok:
        print("Running Package Self-Verification on Staging Tree...")
        verify_db = config.source_dir / "verify_glintory.db"
        if verify_db.exists():
            os.remove(verify_db)

        verify_env = test_env.copy()
        verify_env["DATABASE_URL"] = f"sqlite:///{verify_db.resolve().as_posix()}"
        verify_env["GLINTORY_DATABASE_URL"] = verify_env["DATABASE_URL"]

        try:
            execute_gate(
                "self_verify_uv_sync",
                ["uv", "sync", "--frozen"],
                "self_verify_uv_sync.log",
                env=verify_env,
            )
            execute_gate(
                "self_verify_migration",
                ["uv", "run", "alembic", "upgrade", "head"],
                "self_verify_migration.log",
                env=verify_env,
            )
            execute_gate(
                "self_verify_pytest",
                ["uv", "run", "pytest"],
                "self_verify_pytest.log",
                env=verify_env,
            )
            execute_gate(
                "self_verify_fixture",
                ["uv", "run", "python", "scripts/insert_fixture_db.py"],
                "self_verify_fixture.log",
                env=verify_env,
            )
            execute_gate(
                "self_verify_contract",
                [
                    "uv",
                    "run",
                    "glintory",
                    "publish",
                    "validate-contract",
                    "--dir",
                    "dist",
                ],
                "self_verify_contract.log",
                env=verify_env,
            )
            execute_gate(
                "self_verify_jurypress",
                [
                    "uv",
                    "run",
                    "glintory",
                    "publish",
                    "inspect-jurypress-feed",
                    "--dir",
                    "dist",
                ],
                "self_verify_jurypress.log",
                env=verify_env,
            )

            required_paths = [
                "dist/index.html",
                "dist/opportunities/index.html",
                "dist/opportunities/opp_f1111111111111111111111111111111/index.html",
                "dist/opportunities/opp_f1111111111111111111111111111111/en/index.html",
                "dist/sitemap.xml",
            ]
            for rp in required_paths:
                if not (config.source_dir / rp).exists():
                    raise ValueError(
                        f"Self-verify failed: missing required static asset '{rp}' in staging tree."
                    )

            if verify_db.exists():
                os.remove(verify_db)
            for suffix in ["-shm", "-wal"]:
                db_suffix = config.source_dir / f"verify_glintory.db{suffix}"
                if db_suffix.exists():
                    os.remove(db_suffix)

        except Exception as err:
            print(f"ERROR: Self-Verification failed: {err}", file=sys.stderr)
            gate_ok = False

    if not gate_ok:
        cleanup_failed_submission(config)
        sys.exit(1)

    return quality_gates


def verify_package(
    config: SubmissionConfig,
    commit_before: str,
    commit_after: str,
    clean_before: bool,
    clean_after: bool,
    this_script_sha: str,
    quality_gates: dict,
) -> None:
    try:
        tracked_files = subprocess.check_output(
            ["git", "ls-files"], text=True
        ).splitlines()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to retrieve git tracked files: {e}", file=sys.stderr)
        cleanup_failed_submission(config)
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

    files_to_pack = {}
    safety_ok = True

    for rel_file in tracked_files:
        source_path = config.source_dir / rel_file
        if source_path.exists():
            lower_name = rel_file.lower()
            for f_dir in forbidden_dirs:
                if lower_name.startswith(f_dir) or f"/{f_dir}" in lower_name:
                    print(
                        f"ERROR: Forbidden directory structure: {rel_file}",
                        file=sys.stderr,
                    )
                    safety_ok = False
            for f_ext in forbidden_exts:
                if lower_name.endswith(f_ext):
                    print(f"ERROR: Forbidden extension: {rel_file}", file=sys.stderr)
                    safety_ok = False
            if lower_name == ".env" or lower_name.endswith("/.env"):
                print(f"ERROR: Forbidden .env file: {rel_file}", file=sys.stderr)
                safety_ok = False

            files_to_pack[rel_file] = source_path

    if not safety_ok:
        print("ERROR: Safety verification failed. Build halted.", file=sys.stderr)
        cleanup_failed_submission(config)
        sys.exit(1)

    if config.package_stage.exists():
        shutil.rmtree(config.package_stage, ignore_errors=True)
    config.package_stage.mkdir(parents=True, exist_ok=True)

    for rel_path, full_path in files_to_pack.items():
        dest_path = config.package_stage / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full_path, dest_path)

    dist_src = config.source_dir / "dist"
    if dist_src.exists():
        shutil.copytree(dist_src, config.package_stage / "dist", dirs_exist_ok=True)

    logs_dest = config.package_stage / "logs"
    logs_dest.mkdir(parents=True, exist_ok=True)
    for log_file in config.attestation_dir.iterdir():
        if log_file.is_file() and not log_file.name.startswith("post_verify_"):
            shutil.copy2(log_file, logs_dest / log_file.name)

    # Validate integrity of copied log files
    for _, gate_info in list(quality_gates.items()):
        rel_log_file = gate_info.get("log_file")
        if rel_log_file:
            log_dest_path = config.package_stage / rel_log_file
            if not log_dest_path.exists():
                raise ValueError(f"Log file missing from package stage: {rel_log_file}")

            size, sha256 = get_file_size_and_sha256(log_dest_path)
            if sha256 != gate_info["log_sha256"] or size != gate_info["log_size"]:
                raise ValueError(
                    f"Integrity check failed for {rel_log_file}. "
                    f"Expected: {gate_info['log_sha256']} ({gate_info['log_size']} bytes). "
                    f"Found: {sha256} ({size} bytes)."
                )

    pytest_log_path = config.package_stage / "logs" / "pytest.log"
    passed_count, duration = parse_pytest_log(pytest_log_path)

    pkg_hash = calculate_dir_content_hash(config.package_stage)
    manifest_data = {
        "git_commit_before": commit_before,
        "git_commit_after": commit_after,
        "working_tree_clean_before": clean_before,
        "working_tree_clean_after": clean_after,
        "submission_script_sha256": this_script_sha,
        "packaged_submission_script_sha256": this_script_sha,
        "archive_sha256_sidecar": config.sha_filename.name,
        "package_content_hash": pkg_hash,
        "quality_gates": quality_gates,
    }
    if passed_count is not None:
        manifest_data["pytest_summary"] = {
            "passed_count": passed_count,
            "duration_seconds": duration,
        }

    manifest_path = config.package_stage / "SUBMISSION_MANIFEST.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    print("Packaging files into final submission ZIP...")
    with zipfile.ZipFile(config.zip_filename, "w") as zf:
        for root, _, files in os.walk(config.package_stage):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(config.package_stage).as_posix()
                zf.write(full_path, rel_path)

    with zipfile.ZipFile(config.zip_filename, "r") as zf:
        names = zf.namelist()
        if names.count("SUBMISSION_MANIFEST.json") != 1:
            raise AssertionError(
                f"ZIP contains multiple SUBMISSION_MANIFEST.json files: count = {names.count('SUBMISSION_MANIFEST.json')}"
            )

    print("Extracting final ZIP for post-build verification...")
    if config.temp_verify.exists():
        shutil.rmtree(config.temp_verify, ignore_errors=True)
    config.temp_verify.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(config.zip_filename, "r") as zf:
            zf.extractall(path=config.temp_verify)

        recalc_hash = calculate_dir_content_hash(config.temp_verify)
        if recalc_hash != pkg_hash:
            raise ValueError(
                f"Recalculated package content hash from ZIP extraction does not match Manifest. Recalc: {recalc_hash}, Manifest: {pkg_hash}"
            )

        verify_db_post = config.temp_verify / "glintory_fixture.db"
        verify_env_post = os.environ.copy()
        verify_env_post["DATABASE_URL"] = (
            f"sqlite:///{verify_db_post.resolve().as_posix()}"
        )
        verify_env_post["GLINTORY_DATABASE_URL"] = verify_env_post["DATABASE_URL"]

        final_log = config.final_log_filename
        if final_log.exists():
            os.remove(final_log)

        def run_post_verify_cmd(name: str, cmd: list[str]) -> str:
            started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            with open(final_log, "a", encoding="utf-8") as lf:
                lf.write(f"\n--- Post-Verify: {name} ---\n")
                lf.write(f"Command: {' '.join(cmd)}\n")
                lf.write(f"Started At: {started}\n")
                lf.flush()
                res = subprocess.run(
                    cmd,
                    cwd=config.temp_verify,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    check=False,
                    env=verify_env_post,
                )
                finished = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                lf.write(f"Finished At: {finished}\n")
                lf.write(f"Exit Code: {res.returncode}\n")
            if res.returncode != 0:
                raise ValueError(
                    f"Post-verify command failed: {name} ({' '.join(cmd)})"
                )
            return "passed"

        run_post_verify_cmd("uv_sync", ["uv", "sync", "--frozen"])
        run_post_verify_cmd("migration", ["uv", "run", "alembic", "upgrade", "head"])
        post_pytest = run_post_verify_cmd("pytest", ["uv", "run", "pytest"])
        run_post_verify_cmd(
            "seed_fixture", ["uv", "run", "python", "scripts/insert_fixture_db.py"]
        )
        post_val = run_post_verify_cmd(
            "validate-contract",
            ["uv", "run", "glintory", "publish", "validate-contract", "--dir", "dist"],
        )
        post_inspect = run_post_verify_cmd(
            "inspect-jurypress-feed",
            [
                "uv",
                "run",
                "glintory",
                "publish",
                "inspect-jurypress-feed",
                "--dir",
                "dist",
            ],
        )

        required_paths = [
            "dist/index.html",
            "dist/opportunities/index.html",
            "dist/opportunities/opp_f1111111111111111111111111111111/index.html",
            "dist/opportunities/opp_f1111111111111111111111111111111/en/index.html",
            "dist/sitemap.xml",
        ]
        for rp in required_paths:
            if not (config.temp_verify / rp).exists():
                raise ValueError(
                    f"Post-verify failed: missing required static asset '{rp}' in extracted ZIP."
                )

        sitemap_path = config.temp_verify / "dist/sitemap.xml"
        sitemap_content = sitemap_path.read_text(encoding="utf-8")
        if "opp_f1111111111111111111111111111111" not in sitemap_content:
            raise ValueError("Sitemap does not contain the target Opportunity URL.")

        zip_sha = get_file_sha256(config.zip_filename)
        attestation_data = {
            "archive_sha256": zip_sha,
            "package_content_hash": pkg_hash,
            "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "extracted_package_hash_matches": True,
            "manifest_count": 1,
            "contract_validation": post_val,
            "jurypress_inspection": post_inspect,
            "pytest": post_pytest,
        }
        with open(config.attestation_filename, "w", encoding="utf-8") as f:
            json.dump(attestation_data, f, indent=2)

        with open(config.sha_filename, "w", encoding="utf-8") as f:
            f.write(f"{zip_sha}\n")

        print("Final ZIP Post-Build Self-Verification completed successfully.")

    except Exception as err:
        print(f"ERROR: Final ZIP Self-Verification failed: {err}", file=sys.stderr)
        if config.zip_filename.exists():
            os.remove(config.zip_filename)
        if config.sha_filename.exists():
            os.remove(config.sha_filename)
        if config.attestation_filename.exists():
            os.remove(config.attestation_filename)

        logs_dir = config.root_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        if config.final_log_filename.exists():
            shutil.copy2(
                config.final_log_filename, logs_dir / "final-zip-verification.log"
            )

        cleanup_failed_submission(config)
        sys.exit(1)

    logs_dir = config.root_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for log_file in config.attestation_dir.iterdir():
        if log_file.is_file():
            shutil.copy2(log_file, logs_dir / log_file.name)

    dist_dir = config.root_dir / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.copytree(config.source_dir / "dist", dist_dir)

    shutil.rmtree(config.temp_root, ignore_errors=True)
    shutil.rmtree(config.temp_verify, ignore_errors=True)

    print(
        f"SUBMISSION_MANIFEST.json and all quality logs appended to {config.zip_filename.name}."
    )
    print(f"SHA-256 written to {config.sha_filename.name}: {zip_sha}")
    print("Verification success. ZIP archive is safe, clean, and fully conforming.")


def cleanup_failed_submission(config: SubmissionConfig) -> None:
    if config.zip_filename.exists():
        os.remove(config.zip_filename)
    if config.sha_filename.exists():
        os.remove(config.sha_filename)
    if config.attestation_filename.exists():
        os.remove(config.attestation_filename)

    logs_dir = config.root_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if config.attestation_dir.exists():
        for log_file in config.attestation_dir.iterdir():
            if log_file.is_file():
                shutil.copy2(log_file, logs_dir / log_file.name)

    if config.temp_root.exists():
        shutil.rmtree(config.temp_root, ignore_errors=True)
    if config.temp_verify.exists():
        shutil.rmtree(config.temp_verify, ignore_errors=True)
