import contextlib
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from typing import Any

from glintory.config import settings


def get_db_path_from_url(db_url: str) -> str:
    if not db_url.startswith("sqlite:///"):
        raise ValueError("Only SQLite database is supported for state operations.")
    # sqlite:///./data/glintory.sqlite3 -> ./data/glintory.sqlite3
    path = db_url[10:]
    return path


# Public Safety Audit
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
                    except json.JSONDecodeError:
                        continue

                    # Recursive check key names
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
        for env_var in ["GLINTORY_GITHUB_TOKEN", "GITHUB_TOKEN"]:
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
                cols = [
                    row[1]
                    for row in cursor.fetchall()
                    if any(t in row[2].upper() for t in ("TEXT", "CLOB", "BLOB", "JSON"))
                ]
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


def create_state_snapshot(
    output_path: str,
    run_id: str | None = None,
    run_attempt: str | None = None,
    metadata_file: str | None = None,
    profile: str = "public",
) -> dict:
    db_url = settings.database_url
    db_path = get_db_path_from_url(db_url)
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
        if profile == "public":
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
            try:
                with open(metadata_file) as f:
                    scheduler_result = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

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
            "created_at": datetime.now(UTC).isoformat(),
            "github_run_id": run_id or os.environ.get("GITHUB_RUN_ID"),
            "github_run_attempt": run_attempt or os.environ.get("GITHUB_RUN_ATTEMPT"),
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

        # Ensure output directory exists
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        # 9. Pack into tar.gz
        manifest_path = os.path.join(tmpdir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(tmp_db, arcname="glintory.sqlite3")
            tar.add(manifest_path, arcname="manifest.json")

    return manifest


def verify_state_archive(archive_path: str) -> dict:
    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Open and verify tar file structure
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            names = [m.name for m in members]

            # Allowlist: only glintory.sqlite3 and manifest.json
            allowed = {"glintory.sqlite3", "manifest.json"}
            if set(names) != allowed:
                raise ValueError(
                    f"Archive verification failed: Unrecognized files in archive: {set(names) - allowed or allowed - set(names)}"
                )

            # Prevent Path Traversal, symlinks, hardlinks
            for m in members:
                if m.islnk() or m.issym():
                    raise ValueError(
                        f"Archive verification failed: Links are not allowed in archive (file '{m.name}')"
                    )

                normalized_name = os.path.normpath(m.name)
                if (
                    normalized_name.startswith("..")
                    or normalized_name.startswith("/")
                    or ".." in normalized_name
                ):
                    raise ValueError(
                        f"Archive verification failed: Path traversal detected: {m.name}"
                    )

            # Extract to temporary directory
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmpdir, filter="data")
            else:
                tar.extractall(tmpdir)

        db_file = os.path.join(tmpdir, "glintory.sqlite3")
        manifest_file = os.path.join(tmpdir, "manifest.json")

        # Load and verify manifest
        with open(manifest_file) as f:
            try:
                manifest = json.load(f)
            except json.JSONDecodeError:
                raise ValueError(
                    "Archive verification failed: manifest.json is not valid JSON."
                )

        # Manifest schema validation
        required_keys = {"format_version", "database_sha256", "alembic_revision"}
        if not required_keys.issubset(manifest.keys()):
            raise ValueError(
                f"Archive verification failed: Missing required keys in manifest: {required_keys - manifest.keys()}"
            )

        if manifest.get("format_version") != 1:
            raise ValueError(
                f"Archive verification failed: Unsupported format version: {manifest.get('format_version')}"
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

        return manifest


def restore_state_archive(
    archive_path: str, target_path: str, force: bool = False
) -> dict:
    if os.path.exists(target_path) and not force:
        raise FileExistsError(
            f"Target database file already exists and --force is not specified: {target_path}"
        )

    # Verify first (this extracts and validates everything in tempdir)
    manifest = verify_state_archive(archive_path)

    # Re-extract only glintory.sqlite3 to target location
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(archive_path, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extract("glintory.sqlite3", path=tmpdir, filter="data")
            else:
                tar.extract("glintory.sqlite3", path=tmpdir)

        extracted_db = os.path.join(tmpdir, "glintory.sqlite3")

        # Atomic Rename
        target_dir = os.path.dirname(target_path)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        if os.path.exists(target_path):
            os.replace(extracted_db, target_path)
        else:
            os.rename(extracted_db, target_path)

    return manifest
