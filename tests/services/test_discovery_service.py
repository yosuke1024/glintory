import json
from datetime import date, UTC, datetime
import pytest
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.models import (
    Base,
    DiscoveryReport,
    DiscoveryLead,
    DiscoveryLeadOccurrence,
    Signal,
    Source,
)
from glintory.services.discovery_service import AgentsRadarDiscoveryService


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.mark.asyncio
async def test_extract_leads_and_normalization():
    service = AgentsRadarDiscoveryService(lambda: None)
    
    md = """
    # Report
    - Issue: [Issue title](https://github.com/owner/repo/issues/123)
    - Recursive: [Radar](https://github.com/duanyytop/agents-radar)
    - HTML entity: [Title](https://github.com/owner/repo/pull/456?utm_source=radar&amp;arg=1)
    - Zero-width space: [ZW](https://github.com/owner\u200b/repo/issues/789)
    """
    
    leads = service.extract_leads(md)
    
    assert len(leads) == 3
    assert leads[0][1] == "https://github.com/owner/repo/issues/123"
    assert leads[1][1] == "https://github.com/owner/repo/pull/456?arg=1"
    assert leads[2][1] == "https://github.com/owner/repo/issues/789"


@pytest.mark.asyncio
async def test_run_discovery_flow():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)

    manifest_data = {
        "digests": [
            {
                "date": "2026-07-12",
                "path": "digests/2026-07-12/report.md",
                "count": 2
            }
        ]
    }
    
    report_md = """
    - [Issue 1](https://github.com/owner/repo/issues/1)
    - [HN 1](https://news.ycombinator.com/item?id=12345)
    """
    
    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "manifest.json" in url_str:
            return httpx.Response(200, json=manifest_data)
        elif "digests/2026-07-12/report.md" in url_str:
            return httpx.Response(200, text=report_md)
        elif "repos/owner/repo/issues/1" in url_str:
            return httpx.Response(200, json={
                "title": "Fetched Issue Title",
                "body": "Issue body description",
                "created_at": "2026-07-12T00:00:00Z",
                "user": {"login": "issue_author"}
            })
        elif "item/12345.json" in url_str:
            return httpx.Response(200, json={
                "title": "Fetched HN Title",
                "text": "HN text",
                "by": "hn_author",
                "time": 1783857600
            })
        return httpx.Response(404)
        
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    
    service = AgentsRadarDiscoveryService(session_local, http_client=mock_client)
    
    res = await service.run_discovery()
    assert res["status"] == "success"
    assert res["reports_processed"] == 1
    assert res["leads_processed"] == 2
    assert res["signals_created"] == 2
    
    # Verify DB state with a fresh session
    verify_session = session_local()
    try:
        reports = verify_session.query(DiscoveryReport).all()
        assert len(reports) == 1
        assert reports[0].manifest_date == date(2026, 7, 12)
        assert reports[0].status == "processed"
        
        leads = verify_session.query(DiscoveryLead).all()
        assert len(leads) == 2
        assert leads[0].verification_status == "verified"
        assert leads[0].dispatch_status == "dispatched"
        
        signals = verify_session.query(Signal).all()
        assert len(signals) == 2
        
        gh_sig = verify_session.query(Signal).filter(Signal.canonical_url.contains("github.com")).first()
        assert gh_sig is not None
        assert gh_sig.title == "Fetched Issue Title"
        assert gh_sig.opportunity_anchor is True
        assert gh_sig.discovery_eligible is True
        
        hn_sig = verify_session.query(Signal).filter(Signal.canonical_url.contains("news.ycombinator.com")).first()
        assert hn_sig is not None
        assert hn_sig.title == "Fetched HN Title"
    finally:
        verify_session.close()
