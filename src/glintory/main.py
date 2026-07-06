import pathlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from glintory.web.routes import api, health, readiness, signals, today, opportunities


def create_app() -> FastAPI:
    app = FastAPI(title="Glintory", version="0.1.0")

    # Include routes
    app.include_router(health.router)
    app.include_router(readiness.router)
    app.include_router(today.router)
    app.include_router(signals.router)
    app.include_router(opportunities.html_router)
    app.include_router(opportunities.api_router)
    app.include_router(api.router)

    # Mount static files
    base_dir = pathlib.Path(__file__).parent
    static_dir = base_dir / "web" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
