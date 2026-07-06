import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from glintory.collectors.defaults import build_default_collector_registry
from glintory.collectors.registry import CollectorRegistry
from glintory.config import Settings
from glintory.infrastructure.database import _create_engine_instance
from glintory.infrastructure.http import HttpxHttpClient
from glintory.services.collection import CollectionService
from glintory.services.signal_ingestion import SignalIngestionService


@dataclass
class GlintoryRuntime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    registry: CollectorRegistry
    collection_service: CollectionService
    http_client: HttpxHttpClient

    async def close(self) -> None:
        await self.http_client.close()
        self.engine.dispose()


@contextlib.asynccontextmanager
async def bootstrap(
    settings: Settings | None = None,
) -> AsyncIterator[GlintoryRuntime]:
    if settings is None:
        settings = Settings()

    engine = _create_engine_instance(settings.database_url)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    http_client = HttpxHttpClient()
    registry = build_default_collector_registry(settings)
    ingestion_service = SignalIngestionService(session_factory)
    collection_service = CollectionService(
        session_factory=session_factory,
        registry=registry,
        ingestion_service=ingestion_service,
        http_client=http_client,
    )

    runtime = GlintoryRuntime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        registry=registry,
        collection_service=collection_service,
        http_client=http_client,
    )
    try:
        yield runtime
    finally:
        await runtime.close()
