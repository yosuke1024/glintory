from fastapi.testclient import TestClient

from glintory.main import app

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
    # Verify demo opportunities
    assert "Local-First Markdown Note Sync Tool" in html_content
    assert "Zero-Config DB Backup Agent for Railway" in html_content
    assert "Ad-supported Multi-platform Recipe Planner" in html_content


def test_static_css():
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert "--bg-primary" in response.text
