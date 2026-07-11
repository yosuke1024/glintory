import pytest
from datetime import datetime, UTC, timedelta
from fastapi.testclient import TestClient
from glintory.main import app
from glintory.domain.models import Base, Source, SourceSchedule, SchedulerLease, ScheduleExecution
from glintory.domain.scheduling import ScheduleExecutionStatus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def test_app_client(monkeypatch, tmp_path):
    db_file = tmp_path / "web_test.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Override database settings
    monkeypatch.setenv("GLINTORY_DATABASE_URL", db_url)
    monkeypatch.setattr("glintory.config.settings.database_url", db_url)

    # Initialize DB schema
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    # Override app state state session factory
    app.state.session_factory = session_factory

    # Override get_db dependency
    from glintory.infrastructure.database import get_db
    
    def override_get_db():
        db_session = session_factory()
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db] = override_get_db

    # Populate some seed data
    session = session_factory()
    src = Source(id="00000000-0000-0000-0000-000000000001", name="Test Source", source_type="rss", enabled=True)
    session.add(src)
    session.commit()
    session.close()

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()
    if db_file.exists():
        db_file.unlink()

def test_web_schedules_list(test_app_client):
    response = test_app_client.get("/schedules")
    assert response.status_code == 200
    assert "Schedules" in response.text

def test_web_schedule_post_and_management(test_app_client):
    # GET source details to extract CSRF cookie and token
    response = test_app_client.get("/sources/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 200
    
    # CSRF Token validation is required for state-changing endpoints
    # Extract CSRF cookie
    csrf_cookie = response.cookies.get("glintory_csrf")
    assert csrf_cookie is not None

    # Post new schedule interval
    response = test_app_client.post(
        "/sources/00000000-0000-0000-0000-000000000001/schedule",
        data={"interval_minutes": "60", "csrf_token": csrf_cookie},
        cookies={"glintory_csrf": csrf_cookie},
        follow_redirects=False
    )
    # Redirects 303 to source detail
    assert response.status_code == 303
    assert response.headers["location"] == "/sources/00000000-0000-0000-0000-000000000001"

    # Verify schedule details via API
    api_resp = test_app_client.get("/api/v1/schedules/00000000-0000-0000-0000-000000000001")
    assert api_resp.status_code == 200
    data = api_resp.json()
    assert data["interval_minutes"] == 60
    assert data["schedule_enabled"] is True

    # Disable schedule
    response = test_app_client.post(
        "/sources/00000000-0000-0000-0000-000000000001/schedule/disable",
        data={"csrf_token": csrf_cookie},
        cookies={"glintory_csrf": csrf_cookie},
        follow_redirects=False
    )
    assert response.status_code == 303

    # Verify schedule is disabled
    api_resp = test_app_client.get("/api/v1/schedules/00000000-0000-0000-0000-000000000001")
    assert api_resp.json()["schedule_enabled"] is False

    # Enable schedule
    response = test_app_client.post(
        "/sources/00000000-0000-0000-0000-000000000001/schedule/enable",
        data={"csrf_token": csrf_cookie},
        cookies={"glintory_csrf": csrf_cookie},
        follow_redirects=False
    )
    assert response.status_code == 303

    # Verify schedule is enabled again
    api_resp = test_app_client.get("/api/v1/schedules/00000000-0000-0000-0000-000000000001")
    assert api_resp.json()["schedule_enabled"] is True

def test_web_scheduler_api_status(test_app_client):
    # Initially inactive
    response = test_app_client.get("/api/v1/scheduler/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is False
    assert data["due_schedule_count"] == 0
    assert data["running_execution_count"] == 0

    # Mock active lease and due schedule
    session = app.state.session_factory()
    
    # Active lease
    lease = SchedulerLease(
        lease_name="default",
        owner_token="owner-123",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        acquired_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
    )
    session.add(lease)

    # Due Schedule
    sched = SourceSchedule(
        source_id="00000000-0000-0000-0000-000000000001",
        interval_minutes=30,
        next_run_at=datetime.now(UTC) - timedelta(minutes=5),
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(sched)

    # Running execution
    exec_run = ScheduleExecution(
        id="exec-123",
        source_id="00000000-0000-0000-0000-000000000001",
        scheduled_for=datetime.now(UTC),
        started_at=datetime.now(UTC),
        status=ScheduleExecutionStatus.RUNNING.value,
    )
    session.add(exec_run)

    session.commit()
    session.close()

    # Re-fetch status
    response = test_app_client.get("/api/v1/scheduler/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is True
    assert data["due_schedule_count"] == 1
    assert data["running_execution_count"] == 1
