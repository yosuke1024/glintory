import contextlib
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

from glintory.config import settings

# Limits
MAX_ARCHIVE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_DB_SIZE = 50 * 1024 * 1024  # 50MB
MAX_MANIFEST_SIZE = 1 * 1024 * 1024  # 1MB


def get_db_path_from_url(db_url: str) -> str:
    if not db_url.startswith("sqlite:///"):
        raise ValueError("Only SQLite database is supported for state operations.")
    return db_url[10:]


def get_known_alembic_revisions() -> set[str | None]:
    revs: set[str | None] = {None}
    # Dynamic reading from migrations directory
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    versions_dir = os.path.join(project_root, "migrations", "versions")
    if os.path.isdir(versions_dir):
        for f in os.listdir(versions_dir):
            if f.endswith(".py") and not f.startswith("__"):
                parts = f.split("_", 1)
                if parts:
                    revs.add(parts[0])
    # Fallback to hardcoded list if empty
    if len(revs) <= 1:
        revs.update(
            [
                "187355bd71bf",
                "45dd6b740edc",
                "7fa513398108",
                "857f65e5567e",
                "9de005508393",
                "b68c7eff7f71",
                "cced3ad721f1",
                "d445f5753c74",
                "da4fadf39e75",
                "e3a24bdd14a1",
            ]
        )
    return revs


def validate_metadata(data: Any) -> None:
    if not isinstance(data, dict):
        raise ValueError("Metadata must be a JSON object.")

    # Check top-level keys
    allowed_top_keys = {"exit_code", "operational_status", "tick"}
    unknown_keys = set(data.keys()) - allowed_top_keys
    if unknown_keys:
        raise ValueError(f"Unknown keys in metadata: {unknown_keys}")

    # validate operational_status
    if "operational_status" in data:
        status = data["operational_status"]
        allowed_statuses = {
            "success",
            "partial",
            "failed",
            "lease_busy",
            "lease_lost",
            "infrastructure_failed",
        }
        if status not in allowed_statuses:
            raise ValueError(f"Invalid operational_status: {status}")

    # validate exit_code
    if "exit_code" in data:
        ec = data["exit_code"]
        if not isinstance(ec, int) or isinstance(ec, bool):
            raise ValueError("exit_code must be an integer.")
        if ec < 0 or ec > 255:
            raise ValueError("exit_code out of range.")

    # validate relation between exit_code and operational_status
    if "exit_code" in data and "operational_status" in data:
        ec = data["exit_code"]
        status = data["operational_status"]
        status_map = {
            0: "success",
            3: "partial",
            4: "failed",
            6: "lease_busy",
            7: "lease_lost",
            1: "infrastructure_failed",
        }
        expected_status = status_map.get(ec)
        if expected_status != status:
            raise ValueError(
                f"Exit code {ec} and operational_status '{status}' are inconsistent."
            )

    # validate tick
    if "tick" in data:
        tick = data["tick"]
        if tick is not None:
            if not isinstance(tick, dict):
                raise ValueError("tick must be a JSON object or null.")

            allowed_tick_keys = {
                "due_schedule_count",
                "claimed_execution_count",
                "succeeded_count",
                "partial_count",
                "failed_count",
                "skipped_busy_count",
                "skipped_disabled_count",
                "abandoned_count",
                "execution_ids",
            }
            unknown_tick_keys = set(tick.keys()) - allowed_tick_keys
            if unknown_tick_keys:
                raise ValueError(f"Unknown keys in tick: {unknown_tick_keys}")

            # validate integer fields
            int_fields = [
                "due_schedule_count",
                "claimed_execution_count",
                "succeeded_count",
                "partial_count",
                "failed_count",
                "skipped_busy_count",
                "skipped_disabled_count",
                "abandoned_count",
            ]
            for field in int_fields:
                if field in tick:
                    val = tick[field]
                    if not isinstance(val, int) or isinstance(val, bool):
                        raise ValueError(f"{field} must be an integer.")
                    if val < 0:
                        raise ValueError(f"{field} cannot be negative.")

            # validate execution_ids
            if "execution_ids" in tick:
                ids = tick["execution_ids"]
                if not isinstance(ids, list):
                    raise ValueError("execution_ids must be a list.")
                if len(ids) > 1000:
                    raise ValueError("execution_ids list size exceeded limit of 1000.")
                for item in ids:
                    if not isinstance(item, (str, int)):
                        raise ValueError(
                            "execution_ids items must be strings or integers."
                        )
                    if isinstance(item, str) and len(item) > 256:
                        raise ValueError(
                            "execution_id string length exceeded limit of 256."
                        )

    # validate relation between tick and operational_status
    if "operational_status" in data:
        status = data["operational_status"]
        tick = data.get("tick")

        if status == "failed":
            if tick is not None:
                failed_count = tick.get("failed_count", 0)
                if failed_count <= 0:
                    raise ValueError(
                        "failed operational_status requires failed_count > 0 when tick is present."
                    )
        elif status == "partial":
            if tick is None:
                raise ValueError(
                    "partial operational_status requires tick not to be null."
                )
            partial_count = tick.get("partial_count", 0)
            if partial_count <= 0:
                raise ValueError(
                    "partial operational_status requires partial_count > 0."
                )
        elif status == "success" and tick is not None:
            failed_count = tick.get("failed_count", 0)
            partial_count = tick.get("partial_count", 0)
            if failed_count != 0 or partial_count != 0:
                raise ValueError(
                    "success operational_status requires failed_count == 0 and partial_count == 0."
                )


def audit_dict_for_secrets(data: Any, secret_values: list[str]) -> None:
    if isinstance(data, dict):
        for k, v in data.items():
            k_lower = k.lower()
            for forbidden in [
                "token",
                "secret",
                "password",
                "authorization",
                "cookie",
                "api_key",
                "apikey",
                "private_key",
                "credential",
            ]:
                if forbidden in k_lower:
                    raise ValueError(
                        f"Public Safety Audit Failed: Found forbidden key '{k}' in metadata/manifest."
                    )

            if isinstance(v, str):
                for secret in secret_values:
                    if secret in v:
                        raise ValueError(
                            "Public Safety Audit Failed: Sensitive secret value detected in metadata/manifest."
                        )
            audit_dict_for_secrets(v, secret_values)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                for secret in secret_values:
                    if secret in item:
                        raise ValueError(
                            "Public Safety Audit Failed: Sensitive secret value detected in metadata/manifest."
                        )
            audit_dict_for_secrets(item, secret_values)


def run_public_safety_audit(db_path: str) -> None:
    # 1. Source Config & auth_required check
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        # Check sources table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sources'"
        )
        if cursor.fetchone():
            cursor.execute("SELECT id, name, config, auth_required FROM sources")
            for _src_id, name, config_str, auth_required in cursor.fetchall():
                if auth_required:
                    raise ValueError(
                        f"Public Safety Audit Failed: Source '{name}' requires authentication."
                    )
                if config_str:
                    try:
                        config_data = json.loads(config_str)
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            f"Public Safety Audit Failed: Source '{name}' config has invalid JSON: {e}"
                        ) from e

                    # Recursive check key names and secret values
                    def check_keys(d: Any, source_name: str) -> None:
                        if isinstance(d, dict):
                            for k, v in d.items():
                                k_lower = k.lower()
                                for forbidden in [
                                    "token",
                                    "secret",
                                    "password",
                                    "authorization",
                                    "cookie",
                                    "api_key",
                                    "apikey",
                                    "private_key",
                                    "credential",
                                ]:
                                    if forbidden in k_lower:
                                        raise ValueError(
                                            f"Public Safety Audit Failed: Source '{source_name}' contains "
                                            f"sensitive configuration key '{k}'."
                                        )
                                check_keys(v, source_name)
                        elif isinstance(d, list):
                            for item in d:
                                check_keys(item, source_name)

                    check_keys(config_data, name)

        # Check notes table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
        )
        if cursor.fetchone():
            cursor.execute("SELECT COUNT(*) FROM notes")
            if cursor.fetchone()[0] > 0:
                raise ValueError(
                    "Public Safety Audit Failed: Personal review notes exist in 'notes' table."
                )

        # Check decisions table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        )
        if cursor.fetchone():
            cursor.execute("SELECT COUNT(*) FROM decisions WHERE reason IS NOT NULL")
            if cursor.fetchone()[0] > 0:
                raise ValueError(
                    "Public Safety Audit Failed: Personal decision reasons exist in 'decisions' table."
                )

        # Check opportunity_signals table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_signals'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT COUNT(*) FROM opportunity_signals WHERE review_note IS NOT NULL"
            )
            if cursor.fetchone()[0] > 0:
                raise ValueError(
                    "Public Safety Audit Failed: Personal review notes exist in 'opportunity_signals' table."
                )

        # Check known secrets from environment variables
        secret_values = []
        for env_var in ["GLINTORY_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"]:
            val = os.environ.get(env_var)
            if val and val.strip():
                secret_values.append(val.strip())

        if secret_values:
            # Check DB content via SELECT
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                if table.startswith("sqlite_"):
                    continue
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [row[1] for row in cursor.fetchall()]
                for col in cols:
                    for secret in secret_values:
                        cursor.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{col}" AS TEXT) LIKE ?',
                            (f"%{secret}%",),
                        )
                        if cursor.fetchone()[0] > 0:
                            raise ValueError(
                                "Public Safety Audit Failed: Sensitive secret value detected in database text columns."
                            )

            # Check file bytes as a fallback
            with open(db_path, "rb") as f:
                content = f.read()
                for secret in secret_values:
                    if secret.encode("utf-8") in content:
                        raise ValueError(
                            "Public Safety Audit Failed: Sensitive secret value detected in database raw bytes."
                        )

    finally:
        conn.close()


def validate_manifest_fields(manifest: dict) -> None:
    required_keys = {
        "format_version",
        "created_at",
        "github_run_id",
        "github_run_attempt",
        "alembic_revision",
        "database_sha256",
        "database_size_bytes",
        "source_count",
        "signal_count",
        "opportunity_count",
        "collection_run_count",
        "schedule_execution_count",
        "scheduler_result",
    }

    # Check required keys
    if not required_keys.issubset(manifest.keys()):
        raise ValueError(
            f"Manifest validation failed: Missing required keys: {required_keys - manifest.keys()}"
        )

    # 1. format_version
    if manifest["format_version"] != 1 or isinstance(manifest["format_version"], bool):
        raise ValueError(
            f"Manifest validation failed: Unsupported format version: {manifest.get('format_version')}"
        )

    # 2. created_at (UTC datetime string validation)
    created_at = manifest["created_at"]
    if not isinstance(created_at, str):
        raise ValueError("Manifest validation failed: created_at must be a string.")

    # Must specify UTC timezone offset or Z
    if not (
        created_at.endswith("Z") or "+00:00" in created_at or "-00:00" in created_at
    ):
        raise ValueError(
            f"Manifest validation failed: created_at must specify UTC timezone: {created_at}"
        )
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            f"Manifest validation failed: Invalid created_at format: {e}"
        ) from e

    # 3. github_run_id / attempt
    if not isinstance(manifest["github_run_id"], str):
        raise ValueError("Manifest validation failed: github_run_id must be a string.")
    if not isinstance(manifest["github_run_attempt"], str):
        raise ValueError(
            "Manifest validation failed: github_run_attempt must be a string."
        )

    # 4. alembic_revision (Validate it belongs to known migration revisions)
    alembic_rev = manifest["alembic_revision"]
    known_revs = get_known_alembic_revisions()
    if alembic_rev not in known_revs:
        raise ValueError(
            f"Manifest validation failed: Unknown alembic revision: {alembic_rev}"
        )

    # 5. database_sha256
    db_sha = manifest["database_sha256"]
    if not isinstance(db_sha, str) or len(db_sha) != 64:
        raise ValueError(
            "Manifest validation failed: database_sha256 must be a 64-character string."
        )
    try:
        int(db_sha, 16)
    except ValueError as e:
        raise ValueError(
            "Manifest validation failed: database_sha256 must be a valid hex string."
        ) from e

    # 6. Non-negative integers
    int_keys = [
        "database_size_bytes",
        "source_count",
        "signal_count",
        "opportunity_count",
        "collection_run_count",
        "schedule_execution_count",
    ]
    for k in int_keys:
        val = manifest[k]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError(f"Manifest validation failed: {k} must be an integer.")
        if val < 0:
            raise ValueError(f"Manifest validation failed: {k} cannot be negative.")

    # 7. scheduler_result
    validate_metadata(manifest["scheduler_result"]) if manifest[
        "scheduler_result"
    ] is not None else None


def create_state_snapshot(
    output_path: str,
    *,
    database_url: str | None = None,
    run_id: str | None = None,
    run_attempt: str | None = None,
    metadata_file: str | None = None,
    profile: str = "public",
) -> dict:
    if profile != "public":
        raise ValueError(
            f"Unsupported profile specified: {profile}. Only 'public' is allowed."
        )

    effective_database_url = database_url or settings.database_url
    db_path = get_db_path_from_url(effective_database_url)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Source database file not found: {db_path}")

    # Use SQLite Backup API to create a consistent temporary backup
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db = os.path.join(tmpdir, "snapshot.sqlite3")

        # 1. WAL Checkpoint on original db
        orig_conn = sqlite3.connect(db_path)
        with contextlib.suppress(sqlite3.Error):
            orig_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # 2. Backup to temporary DB
        dest_conn = sqlite3.connect(tmp_db)
        try:
            orig_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            orig_conn.close()

        # 3. Integrity check
        conn = sqlite3.connect(tmp_db)
        try:
            res = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if res != "ok":
                raise ValueError(f"Integrity check failed: {res}")
        finally:
            conn.close()

        # 4. Public Safety Audit
        run_public_safety_audit(tmp_db)

        # 5. Calculate statistics and metadata
        conn = sqlite3.connect(tmp_db)
        try:
            cursor = conn.cursor()

            # Alembic revision
            alembic_rev = None
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            )
            if cursor.fetchone():
                cursor.execute("SELECT version_num FROM alembic_version")
                row = cursor.fetchone()
                if row:
                    alembic_rev = row[0]

            # Statistics
            def count_rows(table: str) -> int:
                cursor.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
                )
                if cursor.fetchone():
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    return cursor.fetchone()[0]
                return 0

            source_count = count_rows("sources")
            signal_count = count_rows("signals")
            opportunity_count = count_rows("opportunities")
            collection_run_count = count_rows("collection_runs")
            schedule_execution_count = count_rows("schedule_executions")
        finally:
            conn.close()

        # 6. Read scheduler metadata if provided
        scheduler_result = None
        if metadata_file and os.path.exists(metadata_file):
            if os.path.getsize(metadata_file) > MAX_MANIFEST_SIZE:
                raise ValueError("Metadata file size exceeds safety limit.")
            with open(metadata_file) as f:
                scheduler_result = json.load(f)
            validate_metadata(scheduler_result)

        # 7. Calculate SHA-256
        sha256 = hashlib.sha256()
        with open(tmp_db, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        db_hash = sha256.hexdigest()
        db_size = os.path.getsize(tmp_db)

        # 8. Create manifest
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "github_run_id": run_id or os.environ.get("GITHUB_RUN_ID") or "unknown",
            "github_run_attempt": run_attempt
            or os.environ.get("GITHUB_RUN_ATTEMPT")
            or "1",
            "alembic_revision": alembic_rev,
            "database_sha256": db_hash,
            "database_size_bytes": db_size,
            "source_count": source_count,
            "signal_count": signal_count,
            "opportunity_count": opportunity_count,
            "collection_run_count": collection_run_count,
            "schedule_execution_count": schedule_execution_count,
            "scheduler_result": scheduler_result,
        }

        # Verify Manifest fields schema
        validate_manifest_fields(manifest)

        # Audit Manifest / Metadata for secrets
        secret_values = []
        for env_var in ["GLINTORY_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"]:
            val = os.environ.get(env_var)
            if val and val.strip():
                secret_values.append(val.strip())
        audit_dict_for_secrets(manifest, secret_values)

        # 9. Pack into tar.gz Atomically
        manifest_path = os.path.join(tmpdir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        tmp_archive = os.path.join(out_dir, f".tmp-{uuid.uuid4().hex}.tar.gz")
        try:
            with tarfile.open(tmp_archive, "w:gz") as tar:
                tar.add(tmp_db, arcname="glintory.sqlite3")
                tar.add(manifest_path, arcname="manifest.json")

            # fsync the written archive
            fd = os.open(tmp_archive, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

            os.replace(tmp_archive, output_path)
        except Exception:
            if os.path.exists(tmp_archive):
                os.remove(tmp_archive)
            raise

    return manifest


def verify_state_archive_content(extracted_dir: str, archive_size: int) -> dict:
    """Core verification logic executed on an already extracted directory to prevent TOCTOU."""
    db_file = os.path.join(extracted_dir, "glintory.sqlite3")
    manifest_file = os.path.join(extracted_dir, "manifest.json")

    if not os.path.exists(db_file) or not os.path.exists(manifest_file):
        raise ValueError(
            "Archive verification failed: Missing glintory.sqlite3 or manifest.json."
        )

    # Size checks
    if os.path.getsize(manifest_file) > MAX_MANIFEST_SIZE:
        raise ValueError(
            "Archive verification failed: manifest.json size exceeds safety limit."
        )
    if os.path.getsize(db_file) > MAX_DB_SIZE:
        raise ValueError(
            "Archive verification failed: glintory.sqlite3 size exceeds safety limit."
        )

    # Load and verify manifest JSON
    with open(manifest_file) as f:
        try:
            manifest = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Archive verification failed: manifest.json is not valid JSON: {e}"
            ) from e

    # Manifest schema validation
    validate_manifest_fields(manifest)

    # Size matching
    db_size = os.path.getsize(db_file)
    if db_size != manifest["database_size_bytes"]:
        raise ValueError(
            "Archive verification failed: Database size does not match manifest."
        )

    # SQLite Header validation (16 bytes)
    with open(db_file, "rb") as f:
        header = f.read(16)
        if header != b"SQLite format 3\x00":
            raise ValueError(
                "Archive verification failed: Database file is not a valid SQLite 3 database."
            )

    # SHA-256 validation
    sha256 = hashlib.sha256()
    with open(db_file, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    calc_hash = sha256.hexdigest()

    if calc_hash != manifest["database_sha256"]:
        raise ValueError("Archive verification failed: SHA-256 mismatch.")

    # SQLite integrity check
    conn = sqlite3.connect(db_file)
    try:
        res = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if res != "ok":
            raise ValueError(
                f"Archive verification failed: SQLite integrity check failed: {res}"
            )
    finally:
        conn.close()

    # Public Safety Audit on the extracted DB
    run_public_safety_audit(db_file)

    # Audit Manifest for secrets
    secret_values = []
    for env_var in ["GLINTORY_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"]:
        val = os.environ.get(env_var)
        if val and val.strip():
            secret_values.append(val.strip())
    audit_dict_for_secrets(manifest, secret_values)

    return manifest


def validate_archive_structure(tar: tarfile.TarFile) -> None:
    members = tar.getmembers()
    names = set()
    allowed = {"glintory.sqlite3", "manifest.json"}
    total_size = 0

    for m in members:
        # 1. Duplicate check
        if m.name in names:
            raise ValueError(
                f"Archive verification failed: Duplicate member name detected: {m.name}"
            )
        names.add(m.name)

        # 2. Allowed member name check (Unknown Member)
        if m.name not in allowed:
            raise ValueError(
                f"Archive verification failed: Unrecognized files in archive: {m.name}"
            )

        # 3. Link prevention
        if m.islnk() or m.issym():
            raise ValueError(
                f"Archive verification failed: Links are not allowed in archive (file '{m.name}')"
            )

        # 4. Special file prevention (Device, FIFO, Directory)
        if m.ischr() or m.isblk():
            raise ValueError(
                f"Archive verification failed: Device files are not allowed in archive (file '{m.name}')"
            )
        if m.isfifo():
            raise ValueError(
                f"Archive verification failed: FIFO files are not allowed in archive (file '{m.name}')"
            )
        if m.isdir():
            raise ValueError(
                f"Archive verification failed: Directories are not allowed in archive (file '{m.name}')"
            )

        # 5. Sparse File check
        if m.type == b"S" or getattr(m, "sparse", None) is not None:
            raise ValueError(
                f"Archive verification failed: Sparse files are not allowed (file '{m.name}')"
            )

        # 6. Regular file verification
        if not m.isreg():
            raise ValueError(
                f"Archive verification failed: Non-regular file detected in archive: {m.name}"
            )

        # 7. Size verification
        if m.size < 0:
            raise ValueError(
                f"Archive verification failed: Negative size detected: {m.name}"
            )

        if m.name == "glintory.sqlite3" and m.size > MAX_DB_SIZE:
            raise ValueError(
                "Archive verification failed: glintory.sqlite3 size exceeds safety limit."
            )
        if m.name == "manifest.json" and m.size > MAX_MANIFEST_SIZE:
            raise ValueError(
                "Archive verification failed: manifest.json size exceeds safety limit."
            )

        total_size += m.size

        # 8. Path traversal check
        normalized_name = os.path.normpath(m.name)
        if (
            normalized_name.startswith("..")
            or normalized_name.startswith("/")
            or ".." in normalized_name
        ):
            raise ValueError(
                f"Archive verification failed: Path traversal detected: {m.name}"
            )

    # 9. Required files check
    if names != allowed:
        raise ValueError(
            f"Archive verification failed: Unrecognized files in archive: {names - allowed or allowed - names}"
        )

    # 10. Total size verification
    if total_size > MAX_DB_SIZE + MAX_MANIFEST_SIZE:
        raise ValueError(
            "Archive verification failed: Total member size exceeds safety limit."
        )


def verify_state_archive(archive_path: str) -> dict:
    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    # Maximum archive size check
    archive_size = os.path.getsize(archive_path)
    if archive_size > MAX_ARCHIVE_SIZE:
        raise ValueError("Archive size exceeds safety limit.")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Open and verify tar file structure
        with tarfile.open(archive_path, "r:gz") as tar:
            validate_archive_structure(tar)
            # Extract to temporary directory safely
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmpdir, filter="data")
            else:
                tar.extractall(tmpdir)

        return verify_state_archive_content(tmpdir, archive_size)


def restore_state_archive(
    archive_path: str, target_path: str, force: bool = False
) -> dict:
    if os.path.exists(target_path) and not force:
        raise FileExistsError(
            f"Target database file already exists and --force is not specified: {target_path}"
        )

    # Maximum archive size check
    archive_size = os.path.getsize(archive_path)
    if archive_size > MAX_ARCHIVE_SIZE:
        raise ValueError("Archive size exceeds safety limit.")

    # Target directory prep
    target_dir = os.path.dirname(os.path.abspath(target_path))
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    # Single extraction and verification to prevent TOCTOU
    # Extract into a temporary directory situated inside the target directory
    # so that os.replace remains atomic (same filesystem)
    with tempfile.TemporaryDirectory(dir=target_dir) as tmpdir:
        with tarfile.open(archive_path, "r:gz") as tar:
            validate_archive_structure(tar)
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmpdir, filter="data")
            else:
                tar.extractall(tmpdir)

        # Verify extracted content
        manifest = verify_state_archive_content(tmpdir, archive_size)

        extracted_db = os.path.join(tmpdir, "glintory.sqlite3")

        # fsync the extracted DB before replace
        fd = os.open(extracted_db, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

        # Atomic Rename
        os.replace(extracted_db, target_path)

    return manifest
