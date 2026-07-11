import os
import json
import pytest
import pathlib
from datetime import datetime, UTC
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config

from glintory.config import settings
from glintory.infrastructure.database import reset_db_connections
from glintory.domain.enums import OpportunityStatus, Confidence
from glintory.domain.models import Source, SourceSchedule, Opportunity, Signal, OpportunitySignal, Note, Decision
from glintory.collectors.registry import CollectorRegistry
from glintory.collectors.github import GitHubCollector
from glintory.services.state_management import (
    create_state_snapshot,
    restore_state_archive,
    verify_state_archive,
    run_public_safety_audit,
)
from glintory.services.sync_manifest import sync_manifest_file
from glintory.services.static_publishing import build_static_site

@pytest.fixture(name="test_db")
def fixture_test_db(tmp_path):
    db_file = tmp_path / "test_issue9c.sqlite3"
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

    yield session, db_file

    session.close()
    engine.dispose()
    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()

def test_state_management_lifecycle(test_db, tmp_path):
    session, db_file = test_db
    
    # Create some dummy data in DB
    src = Source(id="src-1", name="Test Src", source_type="github", config={}, enabled=True)
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

def test_public_safety_audit(test_db, tmp_path):
    session, db_file = test_db

    # Setup normal data
    src = Source(id="src-2", name="Normal", source_type="github", config={"query": "test"}, enabled=True)
    session.add(src)
    session.commit()

    # Verify normal runs successfully
    run_public_safety_audit(str(db_file))

    def refresh_db():
        nonlocal session
        session.commit()
        session.close()
        reset_db_connections()
        engine = create_engine(settings.database_url)
        session_factory = sessionmaker(bind=engine)
        session = session_factory()

    # Test auth_required = true
    src = session.query(Source).filter_by(id="src-2").first()
    assert src is not None
    src.auth_required = True
    refresh_db()
    with pytest.raises(ValueError, match="requires authentication"):
        run_public_safety_audit(str(db_file))
    
    src = session.query(Source).filter_by(id="src-2").first()
    assert src is not None
    src.auth_required = False
    refresh_db()

    # Test forbidden keys in source config
    src = session.query(Source).filter_by(id="src-2").first()
    assert src is not None
    src.config = {"api_key": "some-secret"}
    refresh_db()
    with pytest.raises(ValueError, match="contains sensitive configuration key"):
        run_public_safety_audit(str(db_file))
        
    src = session.query(Source).filter_by(id="src-2").first()
    assert src is not None
    src.config = {"query": "test"}
    refresh_db()

    # Create dummy opportunity for foreign key constraints of Note and Decision
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
    refresh_db()

    # Test personal review notes (uses Note.body field instead of content)
    note = Note(id="note-1", opportunity_id="op-1", body="internal note", created_at=datetime.now(UTC))
    session.add(note)
    refresh_db()
    with pytest.raises(ValueError, match="notes exist"):
        run_public_safety_audit(str(db_file))
        
    note = session.query(Note).filter_by(id="note-1").first()
    session.delete(note)
    refresh_db()

    # Test decisions reason (uses to_status instead of status)
    dec = Decision(id="dec-1", opportunity_id="op-1", from_status=OpportunityStatus.INBOX, to_status=OpportunityStatus.VALIDATE, reason="secret reason", created_at=datetime.now(UTC))
    session.add(dec)
    refresh_db()
    with pytest.raises(ValueError, match="decision reasons exist"):
        run_public_safety_audit(str(db_file))
        
    dec = session.query(Decision).filter_by(id="dec-1").first()
    session.delete(dec)
    refresh_db()

    # Test secrets from env
    os.environ["GLINTORY_GITHUB_TOKEN"] = "MY_SECRET_GH_TOKEN"
    src = session.query(Source).filter_by(id="src-2").first()
    assert src is not None
    src.config = {"query": "MY_SECRET_GH_TOKEN"}
    refresh_db()
    with pytest.raises(ValueError, match="Sensitive secret value detected"):
        run_public_safety_audit(str(db_file))
    os.environ.pop("GLINTORY_GITHUB_TOKEN", None)

def test_sync_manifest(test_db, tmp_path):
    session, db_file = test_db

    # Create dummy manifest files
    manifest_data = {
        "version": 1,
        "sources": [
            {
                "name": "public-gh",
                "source_type": "github",
                "enabled": True,
                "config_file": "github.json",
                "schedule": {
                    "enabled": True,
                    "interval_minutes": 60
                }
            }
        ]
    }
    github_config = {
        "repository_queries": [{"query": "test-repo"}],
        "issue_queries": [],
        "per_page": 10,
    }

    manifest_file = tmp_path / "public-sources.json"
    config_file = tmp_path / "github.json"

    with open(manifest_file, "w") as f:
        json.dump(manifest_data, f)
    with open(config_file, "w") as f:
        json.dump(github_config, f)

    registry = CollectorRegistry()
    registry.register(GitHubCollector(settings))

    res = sync_manifest_file(session, registry, str(manifest_file))
    assert res["created_count"] == 1
    assert res["total_sources"] == 1

    # Verify DB state
    src = session.query(Source).filter_by(name="public-gh").first()
    assert src is not None
    assert src.source_type == "github"
    assert src.config["per_page"] == 10

    # Schedule should also be created
    sched = session.query(SourceSchedule).filter_by(source_id=src.id).first()
    assert sched is not None
    assert sched.interval_minutes == 60

def test_static_publishing(test_db, tmp_path):
    session, db_file = test_db

    # Create Source first to satisfy foreign key constraint of Signal
    src = Source(id="src-idx", name="GitHub", source_type="github", config={}, enabled=True)
    session.add(src)

    # Create some dummy opportunities and signals
    from glintory.domain.enums import SignalType
    op = Opportunity(
        id="op-idx",
        title="AI Automation",
        proposed_solution="Automating all workflows.",
        total_score=90,
        confidence=Confidence.LOW,
        status=OpportunityStatus.INBOX,
        last_scored_at=datetime.now(UTC),
    )
    session.add(op)

    sig = Signal(
        id="sig-idx",
        source_id="src-idx",
        signal_type=SignalType.PROJECT,
        title="Fabulous Signal",
        canonical_url="https://example.com/signal",
        excerpt="Important signal description",
        content_hash="dummy_hash",
        freshness_score=1.0,
        source_quality_score=1.0,
        created_at=datetime.now(UTC),
    )
    session.add(sig)
    session.commit()

    op_sig = OpportunitySignal(
        opportunity_id="op-idx",
        signal_id="sig-idx",
        relevance_score=0.95,
        relation_type="supporting",
        created_at=datetime.now(UTC),
    )
    session.add(op_sig)
    session.commit()

    dist_dir = tmp_path / "dist"
    res = build_static_site(
        session=session,
        output_dir=str(dist_dir),
        base_path="/glintory",
        site_url="https://public.example.com",
    )

    assert res["opportunities_generated"] == 1
    
    # Check generated files
    assert (dist_dir / "index.html").exists()
    assert (dist_dir / "opportunities" / "index.html").exists()
    assert (dist_dir / "opportunities" / "op-idx" / "index.html").exists()
    assert (dist_dir / "assets" / "app.css").exists()
    assert (dist_dir / "robots.txt").exists()
    assert (dist_dir / "sitemap.xml").exists()
    assert (dist_dir / ".nojekyll").exists()
    assert (dist_dir / "data" / "latest.json").exists()
    assert (dist_dir / "data" / "opportunities.json").exists()
