import json
import os
import pathlib
import shutil

# Helper to import scripts
import sys
import tarfile
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.collectors.github import GitHubCollector
from glintory.collectors.registry import CollectorRegistry
from glintory.config import settings
from glintory.domain.enums import Confidence, OpportunityStatus, SignalType
from glintory.domain.models import (
    Decision,
    Note,
    Opportunity,
    OpportunitySignal,
    Signal,
    Source,
)
from glintory.infrastructure.database import reset_db_connections
from glintory.services.state_management import (
    create_state_snapshot,
    restore_state_archive,
    run_public_safety_audit,
    verify_state_archive,
)
from glintory.services.static_publishing import build_static_site
from glintory.services.sync_manifest import sync_manifest_file

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
import scripts.github_issue_notifier as issue_notifier
import scripts.github_state_store as state_store


@pytest.fixture(name="test_db")
def fixture_test_db(tmp_path):
    db_file = tmp_path / "test_issue9c_spec.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    engine = create_engine(db_url)
    with engine.connect() as conn:
        alembic_cfg.attributes["connection"] = conn
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    yield session, db_file, db_url

    session.close()
    engine.dispose()
    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_state_management_lifecycle(test_db, tmp_path):
    session, db_file, db_url = test_db

    # Create dummy source
    src = Source(
        id="src-1", name="Test Src", source_type="github", config={}, enabled=True
    )
    session.add(src)
    session.commit()

    archive_path = str(tmp_path / "snapshot.tar.gz")

    # 1. snapshot
    manifest = create_state_snapshot(
        output_path=archive_path,
        run_id="run-1",
        run_attempt="1",
    )

    assert manifest["format_version"] == 1
    assert manifest["github_run_id"] == "run-1"
    assert manifest["source_count"] == 1

    # 2. verify
    verified_manifest = verify_state_archive(archive_path)
    assert verified_manifest["database_sha256"] == manifest["database_sha256"]

    # 3. restore
    restored_db_path = str(tmp_path / "restored.sqlite3")
    restored_manifest = restore_state_archive(
        archive_path=archive_path,
        target_path=restored_db_path,
        force=True,
    )
    assert restored_manifest["database_sha256"] == manifest["database_sha256"]
    assert os.path.exists(restored_db_path)


def test_state_archive_security_restrictions(test_db, tmp_path):
    session, db_file, db_url = test_db

    # Create empty valid tar.gz with unrecognized files
    bad_archive_path = tmp_path / "bad_archive.tar.gz"
    tmp_txt = tmp_path / "dummy.txt"
    tmp_txt.write_text("dummy content")

    with tarfile.open(bad_archive_path, "w:gz") as tar:
        tar.add(tmp_txt, arcname="dummy.txt")

    with pytest.raises(ValueError, match="Unrecognized files"):
        verify_state_archive(str(bad_archive_path))

    # Test Duplicate Member Reject
    duplicate_archive_path = tmp_path / "duplicate_archive.tar.gz"
    with tarfile.open(duplicate_archive_path, "w:gz") as tar:
        t_info1 = tarfile.TarInfo(name="glintory.sqlite3")
        t_info1.size = 12
        with open(tmp_txt, "rb") as f1:
            tar.addfile(t_info1, fileobj=f1)
        t_info2 = tarfile.TarInfo(name="glintory.sqlite3")
        t_info2.size = 12
        with open(tmp_txt, "rb") as f2:
            tar.addfile(t_info2, fileobj=f2)

    with pytest.raises(ValueError, match="Duplicate member name"):
        verify_state_archive(str(duplicate_archive_path))

    # Test Path Traversal Prevention
    traversal_archive_path = tmp_path / "traversal_archive.tar.gz"
    with tarfile.open(traversal_archive_path, "w:gz") as tar:
        t_info = tarfile.TarInfo(name="../escape.txt")
        t_info.size = 5
        with open(tmp_txt, "rb") as f:
            tar.addfile(t_info, fileobj=f)

    with pytest.raises(ValueError, match="Path traversal detected"):
        verify_state_archive(str(traversal_archive_path))


def test_public_safety_audit_violations(test_db, tmp_path):
    session, db_file, db_url = test_db

    # Note existence check
    op = Opportunity(
        id="op-1",
        title="AI Automation",
        proposed_solution="Automating all workflows.",
        total_score=90,
        confidence=Confidence.LOW,
        status=OpportunityStatus.INBOX,
        last_scored_at=datetime.now(UTC),
    )
    session.add(op)
    session.commit()

    note = Note(
        id="note-1",
        opportunity_id="op-1",
        body="sensitive info",
        created_at=datetime.now(UTC),
    )
    session.add(note)
    session.commit()
    reset_db_connections()

    with pytest.raises(ValueError, match="Personal review notes exist"):
        run_public_safety_audit(str(db_file))

    # Clean note
    session.delete(note)
    session.commit()
    reset_db_connections()

    # Decision reason check
    dec = Decision(
        id="dec-1",
        opportunity_id="op-1",
        from_status=OpportunityStatus.INBOX,
        to_status=OpportunityStatus.WATCH,
        reason="private details",
        created_at=datetime.now(UTC),
    )
    session.add(dec)
    session.commit()
    reset_db_connections()

    with pytest.raises(ValueError, match="Personal decision reasons exist"):
        run_public_safety_audit(str(db_file))

    # Clean decision
    session.delete(dec)
    session.commit()
    reset_db_connections()

    # Environment Secret Leak Checks
    os.environ["GLINTORY_GITHUB_TOKEN"] = "SUPER_SECRET_TOKEN_XYZ"

    # Put secret in signals text
    sig = Signal(
        id="sig-1",
        source_id="src-1",
        signal_type=SignalType.PROJECT,
        title="Fabulous Signal containing SUPER_SECRET_TOKEN_XYZ",
        canonical_url="https://example.com/signal",
        excerpt="excerpt",
        content_hash="dummy_hash",
        freshness_score=1.0,
        source_quality_score=1.0,
        created_at=datetime.now(UTC),
    )
    # Ensure source exists
    src = Source(
        id="src-1", name="Test Src", source_type="github", config={}, enabled=True
    )
    session.merge(src)
    session.add(sig)
    session.commit()
    reset_db_connections()

    with pytest.raises(ValueError, match="Sensitive secret value detected"):
        run_public_safety_audit(str(db_file))

    # Clean up signal & secret
    session.delete(sig)
    session.commit()
    os.environ.pop("GLINTORY_GITHUB_TOKEN", None)
    reset_db_connections()


def test_sync_manifest_validation(test_db, tmp_path):
    session, db_file, db_url = test_db

    registry = CollectorRegistry()
    registry.register(GitHubCollector(settings))

    # 1. Absolute config path check
    manifest_data = {
        "version": 1,
        "sources": [
            {
                "name": "public-gh",
                "source_type": "github",
                "enabled": True,
                "config_file": "/absolute/path/github.json",  # Absolute path
                "schedule": {"enabled": True, "interval_minutes": 60},
            }
        ],
    }
    manifest_file = tmp_path / "public-sources.json"
    with open(manifest_file, "w") as f:
        json.dump(manifest_data, f)

    # Ensure github.json exists just in case
    github_config = {
        "repository_queries": [{"query": "test-repo"}],
        "issue_queries": [],
        "per_page": 10,
    }
    config_file = tmp_path / "github.json"
    with open(config_file, "w") as f:
        json.dump(github_config, f)

    with pytest.raises(ValueError, match="Absolute path not allowed"):
        sync_manifest_file(session, registry, str(manifest_file))

    # 2. Duplicate source name check
    manifest_data_dup = {
        "version": 1,
        "sources": [
            {
                "name": "public-gh",
                "source_type": "github",
                "enabled": True,
                "config_file": "github.json",
                "schedule": {"enabled": True, "interval_minutes": 60},
            },
            {
                "name": "public-gh",  # Duplicate name
                "source_type": "github",
                "enabled": True,
                "config_file": "github.json",
                "schedule": {"enabled": True, "interval_minutes": 60},
            },
        ],
    }
    with open(manifest_file, "w") as f:
        json.dump(manifest_data_dup, f)

    # Re-write github.json to ensure it is in manifest directory
    with open(config_file, "w") as f:
        json.dump(github_config, f)

    with pytest.raises(ValueError, match="Duplicate source name"):
        sync_manifest_file(session, registry, str(manifest_file))


def test_static_publishing_conformance(test_db, tmp_path):
    session, db_file, db_url = test_db

    # Create active Source
    src = Source(
        id="src-idx", name="GitHub", source_type="github", config={}, enabled=True
    )
    session.add(src)

    # Opportunity with Scored numbers
    op = Opportunity(
        id="op-idx",
        title="AI Automation Opportunity",
        proposed_solution="Automating workflows.",
        evidence_score=30,
        feasibility_score=40,
        penalty_score=-5,
        total_score=65,
        confidence=Confidence.MEDIUM,
        status=OpportunityStatus.INBOX,
        current_scoring_version="v1",
        last_scored_at=datetime.now(UTC),
        evidence_updated_at=datetime.now(UTC),
    )
    session.add(op)

    # Signal 1 (Included)
    sig1 = Signal(
        id="sig-idx1",
        source_id="src-idx",
        signal_type=SignalType.PROJECT,
        title="Active Signal",
        canonical_url="https://example.com/signal1",
        excerpt="Description 1",
        content_hash="hash1",
        freshness_score=1.0,
        source_quality_score=1.0,
        created_at=datetime.now(UTC),
    )
    session.add(sig1)

    # Signal 2 (Excluded)
    sig2 = Signal(
        id="sig-idx2",
        source_id="src-idx",
        signal_type=SignalType.PROJECT,
        title="Excluded Signal",
        canonical_url="https://example.com/signal2",
        excerpt="Description 2",
        content_hash="hash2",
        freshness_score=1.0,
        source_quality_score=1.0,
        created_at=datetime.now(UTC),
    )
    session.add(sig2)
    session.commit()

    # Link signals
    op_sig1 = OpportunitySignal(
        opportunity_id="op-idx",
        signal_id="sig-idx1",
        relevance_score=0.95,
        relation_type="supporting",
        is_excluded=False,
        created_at=datetime.now(UTC),
    )
    op_sig2 = OpportunitySignal(
        opportunity_id="op-idx",
        signal_id="sig-idx2",
        relevance_score=0.90,
        relation_type="supporting",
        is_excluded=True,  # EXCLUDED!
        created_at=datetime.now(UTC),
    )
    session.add(op_sig1)
    session.add(op_sig2)
    session.commit()

    dist_dir = tmp_path / "dist"

    # Deterministic output check
    fixed_time = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
    build_static_site(
        session=session,
        output_dir=str(dist_dir),
        base_path="/glintory",
        site_url="https://public.example.com",
        generated_at=fixed_time,
    )

    index_html = (dist_dir / "index.html").read_text()
    detail_html = (dist_dir / "opportunities" / "op-idx" / "index.html").read_text()

    # Verification: is_excluded Signal 2 must not be shown
    assert "Active Signal" in detail_html
    assert "Excluded Signal" not in detail_html

    # Verification: No external fonts/CDNs should be present
    assert "fonts.googleapis.com" not in index_html
    assert "fonts.googleapis.com" not in detail_html

    # Verification: Sitemap loc must be absolute HTTPS
    sitemap_xml = (dist_dir / "sitemap.xml").read_text()
    assert "<loc>https://public.example.com/glintory/</loc>" in sitemap_xml


def test_github_state_store_cli(test_db, tmp_path):
    session, db_file, db_url = test_db

    def sqlite_connect(path):
        import sqlite3

        return sqlite3.connect(path)

    def compute_sha256(file_path):
        import hashlib

        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    # Mock client actions
    class DummyGitHubClient(state_store.GitHubClient):
        def __init__(self):
            self.assets = []
            self.uploaded_files = {}
            self.pruned_ids = []

        def get_release_assets(self, tag: str) -> list[dict]:  # noqa: ARG002
            return self.assets

        def download_asset(self, tag: str, name: str, output_dir: str) -> str:  # noqa: ARG002
            archive_path = os.path.join(output_dir, name)
            if name in self.uploaded_files:
                with open(archive_path, "wb") as f:
                    f.write(self.uploaded_files[name])
                return archive_path

            # Create a mock zip/tar state archive for download-latest fallback
            # Parse run_id and attempt from name: glintory-state-{run_id}-{attempt}.tar.gz
            parts = (
                name.replace("glintory-state-", "").replace(".tar.gz", "").split("-")
            )
            r_id = parts[0] if len(parts) > 0 else "123"
            r_att = parts[1] if len(parts) > 1 else "1"

            with tarfile.open(archive_path, "w:gz") as tar:
                # We need a temporary db to bundle
                tmp_sqlite = os.path.join(output_dir, "glintory.sqlite3")
                conn = sqlite_connect(tmp_sqlite)
                # Create initial schema or just copy db_file
                conn.close()
                shutil.copy(str(db_file), tmp_sqlite)

                # manifest
                manifest = {
                    "format_version": 1,
                    "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "github_run_id": r_id,
                    "github_run_attempt": r_att,
                    "alembic_revision": None,
                    "database_sha256": compute_sha256(tmp_sqlite),
                    "database_size_bytes": os.path.getsize(tmp_sqlite),
                    "source_count": 0,
                    "signal_count": 0,
                    "opportunity_count": 0,
                    "collection_run_count": 0,
                    "schedule_execution_count": 0,
                    "scheduler_result": None,
                }
                tmp_manifest = os.path.join(output_dir, "manifest.json")
                with open(tmp_manifest, "w") as f:
                    json.dump(manifest, f)

                tar.add(tmp_sqlite, arcname="glintory.sqlite3")
                tar.add(tmp_manifest, arcname="manifest.json")

                os.remove(tmp_sqlite)
                os.remove(tmp_manifest)

            return archive_path

        def create_release_if_not_exists(self, tag: str) -> None:  # noqa: ARG002
            pass

        def upload_asset(self, tag: str, file_path: str) -> None:  # noqa: ARG002
            name = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                self.uploaded_files[name] = f.read()

        def delete_asset(self, asset_id: int) -> None:
            self.pruned_ids.append(asset_id)

    client = DummyGitHubClient()
    client.assets = [
        {
            "name": "glintory-state-123-1.tar.gz",
            "id": 101,
            "created_at": "2026-07-11T12:00:00Z",
        },
        {
            "name": "glintory-state-122-1.tar.gz",
            "id": 100,
            "created_at": "2026-07-11T11:00:00Z",
        },
    ]

    # Test download-latest
    state_store.handle_download_latest(client, str(tmp_path / "store"), db_url)
    assert os.path.exists(str(db_file))

    # Test upload and verify
    os.environ["GITHUB_RUN_ID"] = "124"
    os.environ["GITHUB_RUN_ATTEMPT"] = "1"

    state_store.handle_upload_and_verify(client, str(tmp_path / "store"), db_url, None)

    assert "glintory-state-124-1.tar.gz" in client.uploaded_files


def test_github_issue_notifier_cli(tmp_path):
    # Mock gh commands
    class DummyNotifier:
        def __init__(self):
            self.issues = []
            self.comments = []
            self.closed_issues = []

        def run_gh(self, args: list[str]) -> str:
            # Stub issue list
            if args[0] == "issue" and args[1] == "list":
                return json.dumps(self.issues)
            if args[0] == "issue" and args[1] == "comment":
                self.comments.append((args[2], args[4]))
                return ""
            if args[0] == "issue" and args[1] == "close":
                self.closed_issues.append(args[2])
                return ""
            if args[0] == "issue" and args[1] == "create":
                # mock creation
                num = len(self.issues) + 1
                self.issues.append({"number": num, "title": args[3]})
                return ""
            return ""

    notifier = DummyNotifier()

    # Patch issue_notifier's run_gh
    original_run_gh = issue_notifier.run_gh
    issue_notifier.run_gh = notifier.run_gh

    os.environ["GITHUB_RUN_ID"] = "123"
    os.environ["GITHUB_RUN_ATTEMPT"] = "1"
    os.environ["GITHUB_REPOSITORY"] = "google/glintory"

    try:
        # 1. Test Failure creates new issue
        issue_notifier.handle_failure()
        assert len(notifier.issues) == 1
        assert notifier.issues[0]["title"] == "[Glintory Automation] Failure"

        # 2. Test subsequent Failure adds comment only
        notifier.issues = [{"number": 1, "title": "[Glintory Automation] Failure"}]
        issue_notifier.handle_failure()
        assert len(notifier.comments) == 1
        assert notifier.comments[0][0] == "1"

        # 3. Test Success closes open failure issue
        issue_notifier.handle_success()
        assert "1" in notifier.closed_issues
    finally:
        issue_notifier.run_gh = original_run_gh
