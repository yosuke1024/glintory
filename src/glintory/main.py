import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from glintory.web.routes import api, health, opportunities, readiness, signals, today, sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if hasattr(app.state, "http_client"):
        await app.state.http_client.close()
    if hasattr(app.state, "engine"):
        app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Glintory", version="0.1.0", lifespan=lifespan)

    # Initialize runtime components
    from glintory.collectors.defaults import build_default_collector_registry
    from glintory.config import settings
    from glintory.infrastructure.database import _create_engine_instance
    from glintory.infrastructure.http import HttpxHttpClient
    from glintory.services.collection import CollectionService
    from glintory.services.signal_ingestion import SignalIngestionService
    from sqlalchemy.orm import sessionmaker

    engine = _create_engine_instance(settings.database_url)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    registry = build_default_collector_registry(settings)
    http_client = HttpxHttpClient()
    ingestion_service = SignalIngestionService(session_factory)
    collection_service = CollectionService(
        session_factory=session_factory,
        registry=registry,
        ingestion_service=ingestion_service,
        http_client=http_client,
    )

    app.state.session_factory = session_factory
    app.state.registry = registry
    app.state.collection_service = collection_service
    app.state.http_client = http_client
    app.state.engine = engine

    # Include routes
    app.include_router(health.router)
    app.include_router(readiness.router)
    app.include_router(today.router)
    app.include_router(signals.router)
    app.include_router(opportunities.html_router)
    app.include_router(opportunities.api_router)
    app.include_router(opportunities.watchlist_router)
    
    app.include_router(sources.router)
    app.include_router(sources.runs_router)
    
    app.include_router(api.router)
    app.include_router(api.sources_router)
    app.include_router(api.runs_router)

    # Mount static files
    base_dir = pathlib.Path(__file__).parent
    static_dir = base_dir / "web" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
