import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

sys.path.append(str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from glintory.collectors.registry import CollectorRegistry
from glintory.collectors.rss import RSSCollector
from glintory.config import settings
from glintory.domain.models import Signal, Source
from glintory.services.collection import CollectionService

DB_FILE = "./smoke_rss_persistence_test.sqlite3"
DB_URL = f"sqlite:///{DB_FILE}"


def clean_url_for_display(url: str) -> str:
    # Remove query and fragment for safe logging
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


async def main():
    parser = argparse.ArgumentParser(
        description="Smoke test for RSS collector persistence"
    )
    parser.add_argument("--feed-url", required=True, help="The RSS / Atom feed URL")
    args = parser.parse_args()

    feed_url = args.feed_url
    display_url = clean_url_for_display(feed_url)

    print(
        "WARNING: This script will initiate real network communication to the specified feed URL."
    )
    print(f"Target Feed: {display_url}")
    print(
        "It sets up a temporary SQLite database, runs migrations, and verifies signal persistence."
    )
    print("-" * 50)

    # Set temporary DB URL in settings and environment
    os.environ["GLINTORY_DATABASE_URL"] = DB_URL
    settings.database_url = DB_URL

    # Run migrations
    print("Running Alembic migrations on temporary database...")
    res = os.system("uv run alembic upgrade head > /dev/null")
    if res != 0:
        print("Migration failed.", file=sys.stderr)
        return

    # Set up session factory
    engine = create_engine(DB_URL)
    session_factory = sessionmaker(bind=engine)

    # Insert test Source
    session = session_factory()
    source = Source(
        name="RSS Smoke Persistence",
        source_type="rss",
        config={
            "feed_url": feed_url,
            "max_items": 3,
            "max_entries_to_scan": 10,
            "lookback_days": 180,
            "include_undated": True,
            "signal_type": "trend",
            "default_categories": ["rss", "smoke-test"],
            "default_tags": ["smoke"],
        },
        enabled=True,
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    # Registry and Service
    registry = CollectorRegistry()
    registry.register(RSSCollector(settings))
    service = CollectionService(session_factory, registry)

    # Run 1: Ingest
    print("\n--- Running Collection 1 (Ingestion) ---")
    result1 = await service.run_source(source_id, max_items=3)
    print(f"collection_run_id: {result1.run_id}")
    print(f"status: {result1.status.value}")
    print(f"inserted_count: {result1.inserted_count}")
    print(f"updated_count: {result1.updated_count}")
    print(f"duplicate_count: {result1.duplicate_count}")
    if result1.error_summary:
        print(f"error_count: {result1.error_count}")
        # Note: XML or detailed queries should not be printed, but we can print the error summary if sanitized
        print(f"error_summary: {result1.error_summary}")

    # Display saved signals details (hiding full texts/raw APIs)
    session = session_factory()
    signals = session.scalars(select(Signal)).all()
    for sig in signals:
        print(f"Signal title: {sig.title}")
        print(f"Canonical URL: {clean_url_for_display(sig.canonical_url)}")
        print(f"SignalType: {sig.signal_type.value}")
        print("-" * 20)
    session.close()

    # Run 2: Duplicate check
    print("\n--- Running Collection 2 (Duplicate check) ---")
    result2 = await service.run_source(source_id, max_items=3)
    print(f"collection_run_id: {result2.run_id}")
    print(f"status: {result2.status.value}")
    print(f"inserted_count: {result2.inserted_count}")
    print(f"updated_count: {result2.updated_count}")
    print(f"duplicate_count: {result2.duplicate_count}")

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
