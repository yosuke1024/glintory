import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from glintory.domain.models import Base, Source
from glintory.services.source_operations import SourceOperationsService
from glintory.collectors.registry import CollectorRegistry
from glintory.services.collection import CollectionService
from unittest.mock import MagicMock

class DummyCollector:
    def __init__(self, source_type: str):
        self.source_type = source_type

    def get_config_summary(self, config):
        return {"summary": "dummy"}

@pytest.fixture
def db_env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory

def test_list_sources_no_n_plus_one(db_env):
    engine, session_factory = db_env
    session = session_factory()

    # 1. Insert 100 sources
    for i in range(100):
        src = Source(
            name=f"Source {i}",
            source_type="rss",
            config={"feed_url": f"https://example.com/feed{i}.xml"}
        )
        session.add(src)
    session.commit()
    session.close()

    # 2. Instrument engine to count queries
    queries = []
    @event.listens_for(engine, "before_cursor_execute")
    def count_query(conn, cursor, statement, parameters, context, executemany):
        # We only count SELECT queries to verify data loading
        if statement.strip().upper().startswith("SELECT"):
            queries.append(statement)

    # 3. Call list_sources
    registry = CollectorRegistry()
    registry.register(DummyCollector("rss"))
    collection_service = MagicMock(spec=CollectionService)
    service = SourceOperationsService(session_factory, registry, collection_service)

    results = service.list_sources()

    assert len(results) == 100
    # Expected queries:
    # 1. Fetch all sources (1 query)
    # 2. Fetch the latest run for each source in 1 query (1 query)
    # Total = 2 SELECT queries
    select_query_count = len(queries)
    assert select_query_count == 2, f"Expected 2 queries, but got {select_query_count}. Queries: {queries}"
