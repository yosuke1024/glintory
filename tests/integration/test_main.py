import os
import pathlib
import warnings

warnings.filterwarnings("ignore", message="Using.*httpx.*")

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.infrastructure.database import get_db, reset_db_connections
from glintory.main import app  # noqa: E402

client = TestClient(app)

@pytest.fixture
def test_web_db(tmp_path):
    """Sets up temporary database for testing the web routing layer."""
    db_file = tmp_path / "test_web.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Override database_url
    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    # Apply migrations
    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Override get_db dependency in FastAPI app
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield session

    session.close()
    app.dependency_overrides.clear()

    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_today_page(test_web_db):
    response = client.get("/")
    assert response.status_code == 200
    html_content = response.text
    # Verify name
    assert "Glintory" in html_content
    # Verify tagline
    assert "Find the signals worth building on." in html_content
    # Verify button
    assert "Collect Now" in html_content
    # Verify opportunities empty state is shown
    assert "No scored opportunities yet." in html_content


def test_static_css():
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert "--bg-primary" in response.text
