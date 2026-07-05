import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.collectors.github import GitHubCollector
from glintory.collectors.registry import CollectorRegistry
from glintory.config import settings
from glintory.domain.models import Source
from glintory.services.collection import CollectionService

DB_FILE = "./smoke_persistence_test.sqlite3"
DB_URL = f"sqlite:///{DB_FILE}"


async def main():
    print(
        "WARNING: This script will initiate real network communication to the GitHub API."
    )
    print(
        "It sets up a temporary SQLite database, runs migrations, and verifies signal persistence."
    )
    print("-" * 50)

    # Set temporary DB URL in settings and environment
    os.environ["GLINTORY_DATABASE_URL"] = DB_URL
    settings.database_url = DB_URL

    # Run migrations
    print("Running Alembic migrations on temporary database...")
    res = os.system("uv run alembic upgrade head")
    if res != 0:
        print("Migration failed.", file=sys.stderr)
        return

    # Check for github token
    token = os.environ.get("GLINTORY_GITHUB_TOKEN") or settings.github_token
    if token:
        print("Using GitHub Token: [MASKED]")
        settings.github_token = token
    else:
        print("Using GitHub API without Token (rate limits apply).")

    # Set up session factory
    engine = create_engine(DB_URL)
    session_factory = sessionmaker(bind=engine)

    # Insert test Source
    session = session_factory()
    source = Source(
        name="GitHub Smoke Persistence",
        source_type="github",
        config={
            "repository_queries": [{"query": "topic:self-hosted", "max_items": 1}],
            "issue_queries": [{"query": "bug label:bug", "max_items": 1}],
            "per_page": 2,
        },
        enabled=True,
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    # Registry and Service
    registry = CollectorRegistry()
    registry.register(GitHubCollector(settings))
    service = CollectionService(session_factory, registry)

    # Run 1: Ingest
    print("\n--- Running Collection 1 (Ingestion) ---")
    result1 = await service.run_source(source_id, max_items=2)
    print(f"Status: {result1.status}")
    print(f"Fetched items: {result1.fetched_count}")
    print(f"Inserted: {result1.inserted_count}")
    print(f"Updated: {result1.updated_count}")
    print(f"Duplicate: {result1.duplicate_count}")
    print(f"Errors: {result1.error_count}")
    if result1.error_summary:
        print(f"Error summary: {result1.error_summary}")

    # Run 2: Duplicate check
    print("\n--- Running Collection 2 (Duplicate check) ---")
    result2 = await service.run_source(source_id, max_items=2)
    print(f"Status: {result2.status}")
    print(f"Fetched items: {result2.fetched_count}")
    print(f"Inserted: {result2.inserted_count}")
    print(f"Updated: {result2.updated_count}")
    print(f"Duplicate: {result2.duplicate_count}")

    # Clean up temporary database files
    print("\nCleaning up temporary database files...")
    engine.dispose()

    # Remove files if exist
    for ext in ["", "-wal", "-shm"]:
        p = Path(DB_FILE + ext)
        if p.exists():
            try:
                p.unlink()
            except Exception as e:
                print(f"Failed to remove {p}: {e}")

    print("Cleanup done. Smoke test completed.")


if __name__ == "__main__":
    asyncio.run(main())
