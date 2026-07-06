import warnings

warnings.filterwarnings("ignore", message="Using.*httpx.*")

from fastapi.testclient import TestClient  # noqa: E402

from glintory.main import app  # noqa: E402

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_today_page():
    response = client.get("/")
    assert response.status_code == 200
    html_content = response.text
    # Verify name
    assert "Glintory" in html_content
    # Verify tagline
    assert "Find the signals worth building on." in html_content
    # Verify button
    assert "Collect Now" in html_content
    # Verify placeholder is shown instead of demo opportunities
    assert "Opportunity analysis is not available yet." in html_content


def test_static_css():
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert "--bg-primary" in response.text
