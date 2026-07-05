import warnings

warnings.filterwarnings("ignore", message="Using.*httpx.*")

from fastapi.testclient import TestClient  # noqa: E402

from glintory.config import settings  # noqa: E402
from glintory.infrastructure.database import reset_db_connections  # noqa: E402
from glintory.main import create_app  # noqa: E402


def test_readiness_healthy(tmp_path):
    db_file = tmp_path / "test_readyz_healthy.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    reset_db_connections()  # Clear cache to apply the test URL

    app = create_app()
    client = TestClient(app)

    try:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "ok"}
    finally:
        settings.database_url = original_url
        reset_db_connections()  # Clear cache again
        if db_file.exists():
            db_file.unlink()


def test_readiness_unhealthy():
    original_url = settings.database_url
    # Set to a URL that cannot be opened (path to a directory that doesn't exist, and is read-only)
    settings.database_url = "sqlite:////nonexistent_directory_12345/database.sqlite3"
    reset_db_connections()  # Clear cache to apply the test URL

    app = create_app()
    client = TestClient(app)

    try:
        response = client.get("/readyz")
        assert response.status_code == 503
        # Response should not leak internal database urls or exceptions
        assert response.json() == {
            "detail": {"status": "not_ready", "database": "unavailable"}
        }
    finally:
        settings.database_url = original_url
        reset_db_connections()  # Clear cache again
