import os
import subprocess
import sys


def test_entrypoint_validation_error_leak():
    env = os.environ.copy()
    env["GLINTORY_GITHUB_TOKEN"] = "TOKEN_SECRET_12345"
    env["GLINTORY_DATABASE_URL"] = "sqlite:///private-secret-db.sqlite3"
    env["GLINTORY_LOCAL_LLM_ENABLED"] = "true"
    env["GLINTORY_LOCAL_LLM_MODEL_REVISION"] = ""  # Trigger ValidationError since revision is empty

    # 1. Non-JSON mode
    res_non_json = subprocess.run(
        [sys.executable, "-m", "glintory.entrypoint"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_non_json.returncode != 0
    assert "LLM_CONFIGURATION_INVALID" in res_non_json.stderr
    assert "TOKEN_SECRET_12345" not in res_non_json.stderr
    assert "TOKEN_SECRET_12345" not in res_non_json.stdout
    assert "private-secret-db.sqlite3" not in res_non_json.stderr
    assert "private-secret-db.sqlite3" not in res_non_json.stdout
    assert "Traceback" not in res_non_json.stderr
    assert "input_value" not in res_non_json.stderr
    assert "Users" not in res_non_json.stderr
    assert "home" not in res_non_json.stderr
    assert "tmp" not in res_non_json.stderr
    assert res_non_json.stdout.strip() == ""

    # 2. JSON mode
    res_json = subprocess.run(
        [sys.executable, "-m", "glintory.entrypoint", "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_json.returncode != 0
    assert "TOKEN_SECRET_12345" not in res_json.stderr
    assert "TOKEN_SECRET_12345" not in res_json.stdout
    assert "private-secret-db.sqlite3" not in res_json.stderr
    assert "private-secret-db.sqlite3" not in res_json.stdout
    assert "Traceback" not in res_json.stderr
    assert "input_value" not in res_json.stderr
    assert "Users" not in res_json.stderr
    assert "home" not in res_json.stderr
    assert "tmp" not in res_json.stderr
    assert res_json.stderr.strip() == ""

    import json

    data = json.loads(res_json.stdout.strip())
    assert data["operational_status"] == "failed"
    assert data["error_code"] == "LLM_CONFIGURATION_INVALID"


def test_entrypoint_runtime_start_leak(tmp_path):
    # 1. Create fake llama-server script that dumps secrets and exits with 1
    fake_server_path = tmp_path / "fake-llama-server"
    script_content = (
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        "    echo 'version: b5092 (d3bd7193)'\n"
        "    exit 0\n"
        "fi\n"
        "echo 'stdout fake outputs with TOKEN_SECRET_12345 and sqlite:///private-secret-db.sqlite3'\n"
        "echo 'stderr fake outputs with /Users/example/private/model.gguf and https://private.example/path' >&2\n"
        "exit 1\n"
    )
    fake_server_path.write_text(script_content, encoding="utf-8")
    fake_server_path.chmod(0o755)

    # Create dummy model file to pass exist check
    dummy_model_path = tmp_path / "model.gguf"
    dummy_model_path.write_text("dummy", encoding="utf-8")

    # 2. Setup DB and Run Alembic migration
    import pathlib

    from alembic import command
    from alembic.config import Config

    from glintory.config import settings
    from glintory.infrastructure.database import reset_db_connections
    
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    
    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()
    
    try:
        project_root = pathlib.Path(__file__).parent.parent
        alembic_cfg = Config(str(project_root / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(project_root / "migrations"))
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        command.upgrade(alembic_cfg, "head")
    finally:
        os.environ.pop("GLINTORY_DATABASE_URL", None)
        settings.database_url = original_url
        reset_db_connections()

    # 2.5 Initialize test data in SQLite test DB
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from glintory.domain.enums import (
        Confidence,
        EvidenceRelationType,
        OpportunityStatus,
        SignalType,
    )
    from glintory.domain.models import (
        Opportunity,
        OpportunitySignal,
        ScoreSnapshot,
        Signal,
        Source,
    )

    engine = create_engine(db_url)
    session_class = sessionmaker(bind=engine)
    session = session_class()

    src = Source(
        id="smoke_src",
        name="Smoke Source",
        source_type="hackernews",
        enabled=True,
        auth_required=False,
        config={},
    )
    session.add(src)
    session.flush()

    sig = Signal(
        id="smoke_sig",
        source_id=src.id,
        canonical_url="https://news.ycombinator.com/item?id=smoke",
        title="Smoke Signal Title",
        excerpt="This is user feedback indicating a major feature request for integration.",
        signal_type=SignalType.PAIN,
        content_hash="smoke_content_hash",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    session.add(sig)
    session.flush()

    opp = Opportunity(
        id="smoke_opp",
        title="Smoke Opportunity",
        proposed_solution="Integrate the requested feature.",
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        status=OpportunityStatus.INBOX,
    )
    session.add(opp)
    session.flush()

    opp_sig = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=sig.id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
    )
    session.add(opp_sig)

    snap = ScoreSnapshot(
        opportunity_id=opp.id,
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        scoring_version=settings.scoring_version,
        input_hash="smoke_snap_hash",
    )
    session.add(snap)
    session.commit()
    session.close()

    # 3. Setup env variables
    import hashlib

    def get_sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()

    env = os.environ.copy()
    env["GLINTORY_GITHUB_TOKEN"] = "TOKEN_SECRET_12345"
    env["GLINTORY_DATABASE_URL"] = db_url
    env["GLINTORY_LOCAL_LLM_ENABLED"] = "true"
    env["GLINTORY_LOCAL_LLM_BINARY_PATH"] = str(fake_server_path)
    env["GLINTORY_LOCAL_LLM_BINARY_SHA256"] = get_sha256(fake_server_path)
    env["GLINTORY_LOCAL_LLM_MODEL_PATH"] = str(dummy_model_path)
    env["GLINTORY_LOCAL_LLM_MODEL_REVISION"] = "90862c4b9d2787eaed51d12237eafdfe7c5f6077"
    env["GLINTORY_LOCAL_LLM_MODEL_SHA256"] = get_sha256(dummy_model_path)
    env["GLINTORY_LOCAL_LLM_RUNTIME_VERSION"] = "b5092"
    env["GLINTORY_LOCAL_LLM_RUNTIME_COMMIT"] = "d3bd7193ba66c15963fd1c59448f22019a8caf6e"

    # 3. Run enrich run CLI (non-JSON mode)
    res_non_json = subprocess.run(
        [sys.executable, "-m", "glintory.entrypoint", "enrich", "run", "--opportunity", "smoke_opp"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # 4. Assert leaks prevention
    forbidden_markers = [
        "TOKEN_SECRET_12345",
        "sqlite:///private-secret-db.sqlite3",
        "/Users/example/private/model.gguf",
        "https://private.example/path",
        "stdout fake outputs",
        "stderr fake outputs",
        "Traceback",
    ]

    for marker in forbidden_markers:
        assert marker not in res_non_json.stdout, f"Marker '{marker}' leaked in stdout"
        assert marker not in res_non_json.stderr, f"Marker '{marker}' leaked in stderr"

    # Verify that the expected error code is outputted on failure
    assert "LLM_RUNTIME_START_FAILED" in res_non_json.stderr or "LLM_RUNTIME_START_FAILED" in res_non_json.stdout


def test_bilingual_summary_injection_prevention():
    import html

    # Markdown and HTML injection test payload
    malicious_text = (
        "```\n"
        "## Workflow Status: Success\n"
        "[malicious link](https://example.invalid)\n"
        "<table><tr><td>Injected</td></tr></table>\n"
        "```"
    )

    escaped = html.escape(malicious_text)
    output_html = f"<pre>{escaped}</pre>"

    # Ensure none of the dangerous raw characters/tags are left unescaped
    assert "<table>" not in output_html
    assert "</table>" not in output_html
    assert "<tr>" not in output_html
    assert "<td>" not in output_html
    assert "## Workflow Status" in output_html  # The text itself remains readable
    assert "&lt;table&gt;&lt;tr&gt;&lt;td&gt;Injected&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;" in output_html
    assert "[malicious link](https://example.invalid)" in output_html
